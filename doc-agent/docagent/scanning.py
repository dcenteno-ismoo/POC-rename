"""Durable, bounded read-only source scans. Extraction uses managed snapshots only."""
import hashlib
import json
import os
import stat
import tempfile
import threading
import time
import uuid
from pathlib import Path


class Cancelled(Exception):pass
class Interrupted(Exception):pass
class LeaseLost(Interrupted):pass


class Scans:
    def __init__(self,sources,excluded=()):
        self.sources=sources;self.store=sources.store
        self.excluded=[Path(p).resolve() for p in (self.store.root,*excluded)]
        with self.store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS source_scan_jobs (
              id TEXT PRIMARY KEY,source_id TEXT NOT NULL,root TEXT NOT NULL,full INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'Queued',created REAL NOT NULL,started REAL,finished REAL,
              cancel INTEGER NOT NULL DEFAULT 0,error TEXT,discovery_complete INTEGER NOT NULL DEFAULT 0);
            CREATE UNIQUE INDEX IF NOT EXISTS source_scan_active ON source_scan_jobs(source_id) WHERE status IN ('Queued','Running');
            CREATE TABLE IF NOT EXISTS source_scan_files (
              scan_id TEXT NOT NULL,path TEXT NOT NULL,change_kind TEXT NOT NULL,status TEXT NOT NULL,
              document_id TEXT,error TEXT,confidence REAL,needs_review INTEGER NOT NULL DEFAULT 0,
              size INTEGER,mtime_ns INTEGER,PRIMARY KEY(scan_id,path));
            CREATE TABLE IF NOT EXISTS source_file_state (
              source_id TEXT NOT NULL,root TEXT NOT NULL,path TEXT NOT NULL,size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,
              fingerprint TEXT NOT NULL,document_id TEXT NOT NULL,last_scan_id TEXT NOT NULL,
              PRIMARY KEY(source_id,root,path));
            CREATE TABLE IF NOT EXISTS source_scan_lease (id INTEGER PRIMARY KEY CHECK(id=1),owner TEXT NOT NULL,expires REAL NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO source_schema_migrations VALUES (?,?)',(2,sources.now()))

    def queue(self,source_id,full=False):
        if not isinstance(full,bool):raise ValueError('Full rescan must be true or false')
        source=self.sources.get(source_id)
        if not source['enabled']:raise ValueError('Enable this source before scanning')
        if source['accessibility']!='accessible':raise ValueError(source['accessibility_message'])
        if any(self.sources.policy.within(Path(source['resolved_path']),p) for p in self.excluded):raise ValueError('Application-managed storage cannot be scanned as a source')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute("SELECT id FROM source_scan_jobs WHERE source_id=? AND status IN ('Queued','Running')",(source_id,)).fetchone()
            if existing:return self.get(existing['id'])
            identity=uuid.uuid4().hex
            db.execute('INSERT INTO source_scan_jobs (id,source_id,root,full,created) VALUES (?,?,?,?,?)',(identity,source_id,source['resolved_path'],int(full),time.time()))
        return self.get(identity)

    def get(self,identity):
        with self.store.db() as db:
            row=db.execute('SELECT * FROM source_scan_jobs WHERE id=?',(identity,)).fetchone()
            if not row:raise KeyError(identity)
            result=dict(row)
            result['files']=[dict(r) for r in db.execute('SELECT * FROM source_scan_files WHERE scan_id=? ORDER BY path',(identity,))]
        files=[f for f in result['files'] if f['change_kind']!='directory'];result['counts']={'discovered':len(files),'processed':sum(f['status']!='Pending' for f in files),
            'successful':sum(f['status']=='Indexed' for f in files),'skipped':sum(f['status']=='Skipped' for f in files),
            'failed':sum(f['status']=='Failed' for f in files),'needs_review':sum(bool(f['needs_review']) for f in files),
            'directory_errors':sum(f['change_kind']=='directory' and bool(f['error']) for f in result['files'])}
        return result

    def list(self,source_id=None):
        with self.store.db() as db:
            rows=db.execute('SELECT id FROM source_scan_jobs'+(' WHERE source_id=?' if source_id else '')+' ORDER BY created DESC LIMIT 50',(source_id,) if source_id else ()).fetchall()
        return [{k:v for k,v in self.get(row['id']).items() if k!='files'} for row in rows]

    def cancel(self,identity):
        self.get(identity)
        with self.store.db() as db:
            db.execute("UPDATE source_scan_jobs SET cancel=1,status=CASE WHEN status='Queued' THEN 'Cancelled' ELSE status END WHERE id=? AND status IN ('Queued','Running')",(identity,))
        return self.get(identity)

    def lease(self,owner):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT * FROM source_scan_lease WHERE id=1').fetchone()
            if current and current['owner']!=owner and current['expires']>time.time():return False
            db.execute('INSERT OR REPLACE INTO source_scan_lease VALUES (1,?,?)',(owner,time.time()+30))
        return True

    def checkpoint(self,job,owner):
        if getattr(self,'stop_event',None) is not None and self.stop_event.is_set():raise Interrupted()
        with self.store.db() as db:
            lease=db.execute('SELECT * FROM source_scan_lease WHERE id=1').fetchone()
            row=db.execute('SELECT cancel FROM source_scan_jobs WHERE id=?',(job['id'],)).fetchone()
        if not lease or lease['owner']!=owner or lease['expires']<time.time():raise LeaseLost('Scan worker lease lost')
        if not row or row['cancel']:raise Cancelled('Cancellation requested')
        source=self.sources.get(job['source_id'])
        if not source['enabled']:raise Cancelled('Source disabled')
        if source['accessibility']!='accessible' or os.path.normcase(source['resolved_path'])!=os.path.normcase(job['root']):raise ValueError('Source is inaccessible or its registered target changed')

    def result(self,job,path,kind,status,**values):
        allowed={'document_id','error','confidence','needs_review','size','mtime_ns'}
        if set(values)-allowed:raise ValueError('Invalid scan result')
        with self.store.db() as db:
            if getattr(self,'active_owner',None):self.guard(db,self.active_owner)
            db.execute('INSERT INTO source_scan_files (scan_id,path,change_kind,status) VALUES (?,?,?,?) ON CONFLICT(scan_id,path) DO UPDATE SET change_kind=excluded.change_kind,status=excluded.status',(job['id'],path,kind,status))
            if values:db.execute('UPDATE source_scan_files SET '+','.join(k+'=?' for k in values)+' WHERE scan_id=? AND path=?',(*values.values(),job['id'],path))

    def guard(self,db,owner):
        if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
        lease=db.execute('SELECT owner,expires FROM source_scan_lease WHERE id=1').fetchone()
        if not lease or lease['owner']!=owner or lease['expires']<=time.time():raise LeaseLost('Scan worker lease lost')

    def safe_path(self,path,root):
        # Reject links/reparse points rather than following them, including inside the root.
        info=path.lstat()
        if path.is_symlink() or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024):raise ValueError('Symbolic links and junctions are skipped')
        resolved=path.resolve(strict=True)
        if not self.sources.policy.within(resolved,root):raise ValueError('Path escapes the registered source root')
        if any(self.sources.policy.within(resolved,p) for p in self.excluded):raise ValueError('Application-managed folder is excluded')
        self.sources.policy.validate(str(path.parent if path.is_file() else path))
        return resolved

    def handle_path(self,stream,path,root):
        if os.name=='nt':
            import ctypes,msvcrt
            function=ctypes.windll.kernel32.GetFinalPathNameByHandleW
            function.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32]
            function.restype=ctypes.c_uint32
            buffer=ctypes.create_unicode_buffer(32768)
            length=function(msvcrt.get_osfhandle(stream.fileno()),buffer,len(buffer),0)
            if not length or length>=len(buffer):raise ValueError('Cannot validate opened file location')
            name=buffer.value
            if name.startswith('\\\\?\\UNC\\'):name='\\\\'+name[8:]
            elif name.startswith('\\\\?\\'):name=name[4:]
            actual=Path(name)
        elif Path('/proc/self/fd').exists():actual=Path(os.readlink('/proc/self/fd/'+str(stream.fileno())))
        else:actual=path.resolve(strict=True)
        if not self.sources.policy.within(actual,root):raise ValueError('Opened file is outside the registered source root')

    def snapshot(self,path,root,job,owner):
        self.safe_path(path,root)
        fd,name=tempfile.mkstemp(dir=self.store.root);os.close(fd)
        try:
            digest=hashlib.sha256()
            with path.open('rb') as source,open(name,'wb') as target:
                self.handle_path(source,path,root);before=os.fstat(source.fileno())
                while chunk:=source.read(1<<20):
                    self.checkpoint(job,owner);target.write(chunk);digest.update(chunk)
                after=os.fstat(source.fileno())
                self.handle_path(source,path,root)
            current=path.stat()
            if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino) or (current.st_size,current.st_mtime_ns,current.st_ino)!=(before.st_size,before.st_mtime_ns,before.st_ino):raise ValueError('File changed while reading; retry the scan when it is stable')
            return Path(name),digest.hexdigest(),before
        except BaseException:Path(name).unlink(missing_ok=True);raise

    def file(self,job,path,root,owner):
        relative=str(path.relative_to(root));kind='new';temporary=None;failed=False
        try:
            self.checkpoint(job,owner);self.safe_path(path,root);info=path.stat()
            from .detect import TEXT_EXTS,OFFICE_NATIVE,OFFICE_CONVERT,IMAGE_EXTS
            supported=TEXT_EXTS|OFFICE_NATIVE|OFFICE_CONVERT|IMAGE_EXTS|{'pdf'}
            if path.suffix.lower().lstrip('.') not in supported:
                self.result(job,relative,'unsupported','Skipped',error='Unsupported extension');return
            with self.store.db() as db:
                previous=db.execute('SELECT * FROM source_file_state WHERE source_id=? AND root=? AND path=?',(job['source_id'],job['root'],relative)).fetchone()
                finished=db.execute('SELECT * FROM source_scan_files WHERE scan_id=? AND path=?',(job['id'],relative)).fetchone()
            if finished and finished['status']=='Indexed' and (finished['size'],finished['mtime_ns'])==(info.st_size,info.st_mtime_ns):return
            if previous and not job['full'] and (previous['size'],previous['mtime_ns'])==(info.st_size,info.st_mtime_ns):
                try:document=self.store.get(previous['document_id'])
                except KeyError:document=None
                if document and document['status']=='Completed':
                    from .procurement import refresh_saved_scan
                    refresh_saved_scan(self.store,document['id'])
                    self.result(job,relative,'unchanged','Skipped',document_id=document['id'],size=info.st_size,mtime_ns=info.st_mtime_ns);return
            if previous:kind='modified'
            self.result(job,relative,kind,'Pending')
            temporary,fingerprint,info=self.snapshot(path,root,job,owner)
            if previous and fingerprint==previous['fingerprint']:kind='unchanged'
            document_id=self.store.ingest(temporary,path.name,initial_status='Scan indexing',origin_kind='source');temporary=None
            document=self.store.get(document_id)
            if document['status']=='Failed' and document['machine'] is None:
                with self.store.db() as db:
                    db.execute("UPDATE documents SET status='Scan indexing',attempt=attempt+1,error=NULL WHERE id=? AND status='Failed'",(document_id,))
                document=self.store.get(document_id)
            if document['status']=='Scan indexing':
                with self.store.db() as db:
                    self.guard(db,owner)
                    db.execute('UPDATE documents SET attempt=attempt+1 WHERE id=?',(document_id,))
                document=self.store.get(document_id)
                from .acquire import acquire
                from .detect import detect
                from .context_index import index_context
                from .procurement import RULES_VERSION
                from .library import validate
                rec=acquire(Path(document['path']),detect(Path(document['path']),path.name));sources=[]
                for index,page in enumerate(rec.pages):
                    self.checkpoint(job,owner)
                    text='\n'.join(page.lines)
                    location=dict(page.location) or {'precision':'Text order only; no original page/cell locations'}
                    if document['path'].lower().endswith(('.doc','.rtf','.odt','.ods','.odp','.xls','.ppt','.wpd')):location={'precision':'Converted rendition; original page locations unavailable'}
                    if path.suffix.lower().lstrip('.') in IMAGE_EXTS:location={'image':path.name,'precision':'Image text; no bounding boxes retained'}
                    for offset in range(0,len(text),6000):
                        if text[offset:offset+6000].strip():sources.append({'id':fingerprint+':p'+str(index)+':c'+str(offset//6000),'document_id':document_id,'version':fingerprint,'location':location,'method':page.method,'confidence':page.confidence if page.method=='ocr' else None,'text':text[offset:offset+6000]})
                if not sources:raise ValueError('No readable text found')
                output=validate(index_context(sources),sources)
                self.checkpoint(job,owner)
                self.store.stage(document,'Completed',scan_owner=owner,sources=json.dumps(sources),machine=json.dumps(output),search_text='\n'.join(s['text'] for s in sources),model='deterministic-source-rules',prompt_version=RULES_VERSION,finished=time.time())
                document=self.store.get(document_id)
                confidence=rec.ocr_confidence
            elif document['status']=='Completed':
                from .procurement import refresh_saved_scan
                refresh_saved_scan(self.store,document_id)
                document=self.store.get(document_id)
                scores=[s['confidence'] for s in document['sources'] or [] if s.get('confidence') is not None]
                confidence=min(scores) if scores else None
            else:
                raise ValueError('Existing document processing is incomplete; retry after its Library job finishes')
            needs_review=document['review_status']!='Verified'
            with self.store.db() as db:
                self.guard(db,owner)
                db.execute('INSERT INTO source_file_state VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source_id,root,path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,fingerprint=excluded.fingerprint,document_id=excluded.document_id,last_scan_id=excluded.last_scan_id',(job['source_id'],job['root'],relative,info.st_size,info.st_mtime_ns,fingerprint,document_id,job['id']))
                from .documents import record_origin
                record_origin(db,document_id,'source',job['source_id']+'|'+job['root']+'|'+relative,path.name,job['source_id'],str(path))
            self.result(job,relative,kind,'Indexed',document_id=document_id,confidence=confidence,needs_review=int(needs_review),size=info.st_size,mtime_ns=info.st_mtime_ns,error=None)
        except Cancelled:
            if 'document' in locals() and document['status']=='Scan indexing':
                self.store.stage(document,'Failed',scan_owner=owner,error='Scan cancelled before indexing completed',finished=time.time())
            raise
        except Interrupted:raise
        except Exception as exc:
            failed=True
            # Never log extracted content or exception strings from document libraries.
            error=str(exc) if isinstance(exc,(ValueError,PermissionError)) else 'Processing failed ('+type(exc).__name__+')'
            if 'document' in locals() and document['status']=='Scan indexing':
                self.store.stage(document,'Failed',scan_owner=owner,error=error,finished=time.time())
            self.result(job,relative,kind,'Failed',error=error,needs_review=1)
        finally:
            if temporary:temporary.unlink(missing_ok=True)
            if failed:
                # Some PDF parsers retain a partially opened document in an
                # exception cycle; release it after the exception scope ends.
                import gc
                gc.collect()

    def run(self,identity,owner):
        self.active_owner=owner
        job=self.get(identity);root=Path(job['root']);status='Completed';error=None
        with self.store.db() as db:
            self.guard(db,owner)
            db.execute("UPDATE source_scan_jobs SET status='Running',started=COALESCE(started,?) WHERE id=?",(time.time(),identity))
        try:
            self.checkpoint(job,owner);directories=[root]
            while directories:
                directory=directories.pop();self.checkpoint(job,owner)
                try:
                    self.safe_path(directory,root)
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            self.checkpoint(job,owner);path=Path(entry.path)
                            is_directory=False
                            try:
                                is_directory=entry.is_dir(follow_symlinks=False)
                                self.safe_path(path,root)
                                if is_directory:directories.append(path)
                                elif entry.is_file(follow_symlinks=False):self.file(job,path,root,owner)
                            except (Cancelled,Interrupted):raise
                            except Exception as exc:self.result(job,str(path.relative_to(root)),'directory' if is_directory else 'excluded' if isinstance(exc,ValueError) else 'failed','Skipped' if isinstance(exc,ValueError) else 'Failed',error=str(exc) if isinstance(exc,ValueError) else 'Filesystem access failed',needs_review=1)
                except (Cancelled,Interrupted):raise
                except Exception:self.result(job,str(directory.relative_to(root)) or '.','directory','Failed',error='Cannot read source directory',needs_review=1)
            with self.store.db() as db:db.execute('UPDATE source_scan_jobs SET discovery_complete=1 WHERE id=?',(identity,))
        except Cancelled as exc:status='Cancelled';error=str(exc)
        except LeaseLost:return
        except Interrupted:status='Queued';error='Worker stopped; scan will resume after restart'
        except Exception:status='Failed';error='Source became inaccessible or registration changed'
        with self.store.db() as db:
            self.guard(db,owner)
            db.execute('UPDATE source_scan_jobs SET status=?,error=?,finished=? WHERE id=?',(status,error,time.time(),identity))
            count=db.execute('SELECT count(*) FROM source_file_state WHERE source_id=? AND root=?',(job['source_id'],job['root'])).fetchone()[0]
            db.execute('UPDATE project_sources SET last_scan_at=?,indexed_files=? WHERE id=? AND resolved_path=?',(self.sources.now(),count,job['source_id'],job['root']))

    def work(self,stop):
        self.stop_event=stop
        owner=uuid.uuid4().hex
        while not stop.is_set():
            if not self.lease(owner):stop.wait(2);continue
            heartbeat_stop=threading.Event()
            def heartbeat():
                while not heartbeat_stop.wait(5):
                    try:
                        if not self.lease(owner):return
                    except Exception:return
            heartbeat_thread=threading.Thread(target=heartbeat,daemon=True);heartbeat_thread.start()
            try:
                # We hold the only scan lease: resume interrupted runs idempotently.
                with self.store.db() as db:
                    row=db.execute("SELECT id FROM source_scan_jobs WHERE status IN ('Running','Queued') ORDER BY created LIMIT 1").fetchone()
                if row:self.run(row['id'],owner)
                else:stop.wait(1)
            except Exception:stop.wait(1)
            finally:
                heartbeat_stop.set();heartbeat_thread.join(timeout=1)
        with self.store.db() as db:db.execute('DELETE FROM source_scan_lease WHERE owner=?',(owner,))
