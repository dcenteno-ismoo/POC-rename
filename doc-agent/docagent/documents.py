"""Unified, evidence-based search over existing document records. No model calls."""
import json
import re
import sqlite3
from datetime import date
from pathlib import Path


def record_origin(db,identity,kind,reference,filename,source_id=None,source_path=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='document_origins'").fetchone():return
    db.execute('INSERT OR IGNORE INTO document_origins (document_id,kind,reference,original_filename,source_id,source_path) VALUES (?,?,?,?,?,?)',(identity,kind,reference,filename,source_id,source_path))


class DocumentLibrary:
    def retrieval_candidates(self,terms,document_ids=None,limit=50):
        """Rank only stored, acquired document text; caller authorization precedes this."""
        if not terms and document_ids is None:return []
        with self.store.db() as db:
            if document_ids is not None:
                if not document_ids:return []
                rows=db.execute('SELECT id FROM documents WHERE id IN ('+','.join('?' for _ in document_ids)+') AND sources IS NOT NULL',document_ids).fetchall()
            elif self.fts:
                match=' OR '.join('"'+t+'"' for t in terms[:20])
                rows=db.execute('SELECT d.id FROM document_search_fts f JOIN documents d ON d.id=f.document_id WHERE document_search_fts MATCH ? AND d.sources IS NOT NULL ORDER BY bm25(document_search_fts) LIMIT ?',(match,limit)).fetchall()
            else:
                rows=db.execute('SELECT d.id FROM documents d JOIN document_search s ON s.document_id=d.id WHERE d.sources IS NOT NULL AND ('+' OR '.join('instr(lower(s.text||\' \'||s.metadata||\' \'||s.filename),?)>0' for _ in terms)+') LIMIT ?',(*[t.casefold() for t in terms],limit)).fetchall()
        return [row['id'] for row in rows]

    def __init__(self,store):
        self.store=store
        with store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS document_library_migrations (version INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS document_origins (
              document_id TEXT NOT NULL,kind TEXT NOT NULL,reference TEXT NOT NULL,original_filename TEXT NOT NULL,
              source_id TEXT,source_path TEXT,UNIQUE(document_id,kind,reference));
            CREATE INDEX IF NOT EXISTS origin_document ON document_origins(document_id);
            CREATE TABLE IF NOT EXISTS document_search (document_id TEXT PRIMARY KEY,filename TEXT NOT NULL,text TEXT NOT NULL,metadata TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS document_search_insert AFTER INSERT ON documents BEGIN
              INSERT OR REPLACE INTO document_search VALUES (NEW.id,NEW.filename||' '||COALESCE((SELECT group_concat(original_filename,' ') FROM document_origins WHERE document_id=NEW.id),''),
                COALESCE((SELECT group_concat(json_extract(value,'$.text'),char(10)) FROM json_each(COALESCE(NEW.sources,'[]'))),NEW.search_text,''),COALESCE(NEW.reviewed,NEW.machine,'')); END;
            CREATE TRIGGER IF NOT EXISTS document_search_update AFTER UPDATE OF filename,sources,machine,reviewed,search_text ON documents BEGIN
              INSERT INTO document_search VALUES (NEW.id,NEW.filename||' '||COALESCE((SELECT group_concat(original_filename,' ') FROM document_origins WHERE document_id=NEW.id),''),
                COALESCE((SELECT group_concat(json_extract(value,'$.text'),char(10)) FROM json_each(COALESCE(NEW.sources,'[]'))),NEW.search_text,''),COALESCE(NEW.reviewed,NEW.machine,''))
              ON CONFLICT(document_id) DO UPDATE SET filename=excluded.filename,text=excluded.text,metadata=excluded.metadata; END;
            CREATE TRIGGER IF NOT EXISTS document_search_delete AFTER DELETE ON documents BEGIN
              DELETE FROM document_search WHERE document_id=OLD.id; DELETE FROM document_origins WHERE document_id=OLD.id; END;
            ''')
            db.executescript('''CREATE TRIGGER IF NOT EXISTS origin_search_insert AFTER INSERT ON document_origins BEGIN
              UPDATE document_search SET filename=(SELECT d.filename||' '||COALESCE((SELECT group_concat(original_filename,' ') FROM document_origins WHERE document_id=NEW.document_id),'') FROM documents d WHERE d.id=NEW.document_id) WHERE document_id=NEW.document_id; END;''')
            try:
                db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS document_search_fts USING fts5(document_id UNINDEXED,filename,text,metadata)')
                self.fts=True
                db.executescript('''
                CREATE TRIGGER IF NOT EXISTS search_fts_insert AFTER INSERT ON document_search BEGIN
                  INSERT INTO document_search_fts VALUES (NEW.document_id,NEW.filename,NEW.text,NEW.metadata); END;
                CREATE TRIGGER IF NOT EXISTS search_fts_update AFTER UPDATE ON document_search BEGIN
                  DELETE FROM document_search_fts WHERE document_id=OLD.document_id;
                  INSERT INTO document_search_fts VALUES (NEW.document_id,NEW.filename,NEW.text,NEW.metadata); END;
                CREATE TRIGGER IF NOT EXISTS search_fts_delete AFTER DELETE ON document_search BEGIN
                  DELETE FROM document_search_fts WHERE document_id=OLD.document_id; END;
                ''')
            except sqlite3.OperationalError as exc:
                if 'no such module' not in str(exc).lower():raise
                self.fts=False
            if not db.execute('SELECT 1 FROM document_library_migrations WHERE version=1').fetchone():
                db.execute('INSERT OR IGNORE INTO document_search SELECT id,filename,COALESCE((SELECT group_concat(json_extract(value,\'$.text\'),char(10)) FROM json_each(COALESCE(d.sources,\'[]\'))),search_text,\'\'),COALESCE(reviewed,machine,\'\') FROM documents d')
                db.execute("INSERT OR IGNORE INTO document_origins (document_id,kind,reference,original_filename) SELECT id,'legacy',id,filename FROM documents")
                db.execute('INSERT INTO document_library_migrations VALUES (1)')
            if self.fts and not db.execute('SELECT 1 FROM document_library_migrations WHERE version=2').fetchone():
                db.execute('DELETE FROM document_search_fts')
                db.execute('INSERT INTO document_search_fts SELECT * FROM document_search')
                db.execute('INSERT INTO document_library_migrations VALUES (2)')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='source_file_state'").fetchone():
                for row in db.execute('SELECT * FROM source_file_state').fetchall():
                    record_origin(db,row['document_id'],'source',row['source_id']+'|'+row['root']+'|'+row['path'],Path(row['path']).name,row['source_id'],str(Path(row['root'])/row['path']))

    def origins(self,db,identity):
        return [dict(r) for r in db.execute('SELECT kind,reference,original_filename,source_id,source_path FROM document_origins WHERE document_id=?',(identity,))]

    def describe(self,row,origins):
        raw=row['reviewed'] or row['machine'] or {}
        output=json.loads(raw) if isinstance(raw,str) else raw
        raw_sources=row['sources'] or []
        sources=json.loads(raw_sources) if isinstance(raw_sources,str) else raw_sources
        segments=output.get('segments',[]);fields=[s.get('fields',{}) for s in segments]
        def values(names,safe=False):
            return list(dict.fromkeys(str(v.get('normalized') or v.get('raw')) for f in fields for k,v in f.items() if k in names and v and (not safe or not v.get('flags'))))
        original=Path(row['path']).resolve()
        available=original.parent==self.store.root.resolve() and original.is_file()
        scores=[s['confidence'] for s in sources if s.get('confidence') is not None]
        return {'id':row['id'],'filename':row['filename'],'hash':row['version'],'category':output.get('category','Unknown'),
                'categories':list(dict.fromkeys(s['category'] for s in segments)),
                'reference_numbers':values({'document_number','invoice_number'}),'companies':values({'issuing_organization','recipient_organization','company','buyer','supplier','customer'}),
                'dates':values({'document_date','invoice_date'},safe=True),'review_status':row['review_status'],'processing_status':row['status'],
                'confidence':min(scores) if scores else None,'origins':origins,'original_available':available,
                'original_url':'/api/documents/'+row['id']+'/original' if available else None,'quality_flags':output.get('quality_flags',[])}

    def search(self,q='',category='',company='',reference='',date_from='',date_to='',source='',status='',review_status='',limit=50,offset=0):
        if len(q)>500 or not 1<=limit<=100 or offset<0:raise ValueError('Invalid search query or page size')
        for bound in (date_from,date_to):
            if bound:date.fromisoformat(bound)
        if date_from and date_to and date_from>date_to:raise ValueError('Date range starts after it ends')
        tokens=re.findall(r'\w+',q)[:20];items=[]
        with self.store.db() as db:
            if self.fts and tokens:
                match=' AND '.join('"'+t+'"' for t in tokens)
                rows=db.execute('SELECT d.* FROM document_search_fts f JOIN documents d ON d.id=f.document_id WHERE document_search_fts MATCH ? ORDER BY bm25(document_search_fts)',(match,)).fetchall()
            else:
                rows=db.execute('SELECT d.* FROM documents d JOIN document_search s ON s.document_id=d.id WHERE (?=\'\' OR instr(lower(s.filename||\' \'||s.text||\' \'||s.metadata),lower(?))>0) ORDER BY d.updated DESC',(q,q)).fetchall()
            # Exact identifiers are checked separately from tokenized FTS matching.
            if reference or q:
                exact=[]
                for row in db.execute('SELECT * FROM documents'):
                    item=self.describe(row,[])
                    if any(v.casefold()==(reference or q).casefold() for v in item['reference_numbers']):exact.append(row)
                if reference:rows=exact
                elif exact:
                    seen={r['id'] for r in exact};rows=exact+[r for r in rows if r['id'] not in seen]
            for row in rows:
                origins=self.origins(db,row['id']);item=self.describe(row,origins)
                if category and category!=item['category'] and category not in item['categories']:continue
                if company and not any(company.casefold() in c.casefold() for c in item['companies']):continue
                if source and not any(o['source_id']==source or o['kind']==source for o in origins):continue
                if status and item['processing_status']!=status:continue
                if review_status and item['review_status']!=review_status:continue
                if date_from or date_to:
                    if not any(re.fullmatch(r'\d{4}-\d{2}-\d{2}',d) and (not date_from or d>=date_from) and (not date_to or d<=date_to) for d in item['dates']):continue
                passages=json.loads(row['sources'] or '[]')
                best=max(passages,key=lambda s:sum(t.casefold() in s['text'].casefold() for t in tokens),default=None)
                if best and tokens and not any(t.casefold() in best['text'].casefold() for t in tokens):
                    # A corrected/normalized metadata value may not appear verbatim
                    # in OCR. Prefer its real evidence, never fabricate a passage.
                    chosen=None;output=json.loads(row['reviewed'] or row['machine'] or '{}')
                    for segment in output.get('segments',[]):
                        for value in segment.get('fields',{}).values():
                            if not value or not any(t.casefold() in str(value.get('normalized') or value.get('raw')).casefold() for t in tokens):continue
                            for evidence in value.get('evidence',[]):
                                original=next((s for s in passages if s['id']==evidence['source_id'] and evidence['excerpt'] in s['text']),None)
                                if original:chosen=dict(original,text=evidence['excerpt']);break
                    best=chosen
                if best:
                    positions=[best['text'].casefold().find(t.casefold()) for t in tokens if t.casefold() in best['text'].casefold()]
                    start=max(0,min(positions)-100) if positions else 0
                    item['snippet']={'text':best['text'][start:start+450],'source_id':best['id'],'location':best.get('location',{})}
                else:item['snippet']={'text':'Matched filename/metadata only; no matching text excerpt is available.','source_id':None,'location':{}}
                items.append(item)
        return {'items':items[offset:offset+limit],'total':len(items),'fts5':self.fts,'limit':limit,'offset':offset}

    def detail(self,identity):
        row=self.store.get(identity)
        with self.store.db() as db:origins=self.origins(db,identity)
        result=self.describe(row,origins)
        result.update(sources=row['sources'] or [],extraction=row['reviewed'] or row['machine'],machine=row['machine'],reviewed=row['reviewed'],error=row['error'],attempt=row['attempt'])
        return result

    def original(self,identity):
        row=self.store.get(identity);path=Path(row['path']).resolve()
        if path.parent!=self.store.root.resolve():raise ValueError('Stored original is outside managed storage')
        if not path.is_file():raise FileNotFoundError('The stored original is unavailable')
        return path,row['filename']
