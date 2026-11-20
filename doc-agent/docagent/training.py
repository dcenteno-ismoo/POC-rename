"""Persistent, human-reviewed examples for the AI beta. No external service calls."""
import json
import sqlite3
import uuid
import shutil
import tempfile
import os
from contextlib import contextmanager
from datetime import date
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from .document_auth import protect_existing
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from . import config, lookup
from .acquire import acquire
from .categorize import find_category
from .detect import detect
from .extract import find_date
from .record import sha256_file

router = APIRouter(dependencies=[Depends(protect_existing)])


@contextmanager
def connection():
    db = sqlite3.connect(config.WORK_DIR / 'training.sqlite3')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS examples (id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE, source TEXT, text TEXT, labels TEXT, approved INTEGER DEFAULT 0)')
    columns={r['name'] for r in db.execute('PRAGMA table_info(examples)')}
    for name in ('machine_labels', 'refreshed_at', 'refresh_error', 'context_document_id','context_save_error'):
        if name not in columns:
            db.execute('ALTER TABLE examples ADD COLUMN '+name+' TEXT')
    if 'archived' not in columns:
        db.execute('ALTER TABLE examples ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
        db.execute('UPDATE examples SET archived=1 WHERE approved=1')
    try:
        with db:
            yield db
    finally:
        db.close()


@router.get('/api/training')
def examples(include_archived: bool=False):
    with connection() as db:
        rows=[dict(r, labels=json.loads(r['labels'])) for r in db.execute('SELECT * FROM examples WHERE (? OR archived=0) ORDER BY rowid DESC',(int(include_archived),))]
        for row in rows:
            row['labels'].pop('code',None)  # Legacy approved records remain intact in storage.
            row['context']={'status':'Not saved to Assistant context','ready':False}
            if row['context_save_error']:row['context']={'status':'Context save failed','ready':False,'error':row['context_save_error']}
            if row['context_document_id']:
                from .library_api import store
                try:
                    document=store.get(row['context_document_id'])
                    ready=bool(document['sources'])
                    row['context']={'status':'Saved to Assistant context' if ready else document['status'],
                                    'ready':ready,'done':document['done'],'total':document['total'],
                                    'error':document['error'],'document_id':document['id']}
                except KeyError:row['context']={'status':'Context removed from Library; save again to restore','ready':False}
        return rows


@router.post('/api/training/hide')
def hide_training():
    with connection() as db:
        result=db.execute('UPDATE examples SET archived=1 WHERE archived=0')
    return {'hidden':result.rowcount,'context_preserved':True}


@router.post('/api/training/{identity}/context')
def save_context(identity: str):
    from .library_api import store
    with connection() as db:row=db.execute('SELECT * FROM examples WHERE id=?',(identity,)).fetchone()
    if row is None:raise HTTPException(404,'Example not found')
    folder=config.WORK_DIR/'training-files'
    path=folder/(identity+Path(row['source']).suffix.lower())
    if not path.is_file():path=folder/identity
    if not path.is_file():raise HTTPException(400,'Stored source is missing')
    if sha256_file(path)!=row['fingerprint']:raise HTTPException(409,'Source changed; upload the new version')
    handle,name=tempfile.mkstemp(dir=store.root);os.close(handle)
    try:
        shutil.copyfile(path,name)
        document_id=store.ingest(name,row['source'],origin_kind='training')
        # Training text is already acquired. Reuse it immediately rather than
        # waiting behind sequential, possibly slow model extraction jobs.
        text=row['text'] or ''
        if text.strip():
            chunks=[{'id':row['fingerprint']+':training:'+str(i//6000),'document_id':document_id,
                     'version':row['fingerprint'],'location':{'precision':'Saved Training text; original page/cell locations were not retained'},
                     'method':'training_cached_text','text':text[i:i+6000]} for i in range(0,len(text),6000) if text[i:i+6000].strip()]
            with store.db() as library_db:
                library_db.execute('UPDATE documents SET sources=?,search_text=? WHERE id=? AND sources IS NULL',(json.dumps(chunks),text,document_id))
        document=store.get(document_id)
        if document['sources']:
            marked=[dict(s,training_context=True) for s in document['sources']]
            with store.db() as library_db:
                library_db.execute('UPDATE documents SET sources=? WHERE id=? AND sources=?',(json.dumps(marked),document_id,json.dumps(document['sources'])))
        if document['status']=='Failed' and not document['sources']:store.retry(document_id)
        with connection() as db:db.execute('UPDATE examples SET context_document_id=?,context_save_error=NULL WHERE id=?',(document_id,identity))
    finally:Path(name).unlink(missing_ok=True)
    return {'document_id':document_id,'status':'Saved; Library processing status will update automatically'}


def queue_context(identity):
    try:save_context(identity)
    except Exception as exc:
        message=exc.detail if isinstance(exc,HTTPException) else 'Context save failed ('+type(exc).__name__+')'
        with connection() as db:db.execute('UPDATE examples SET context_save_error=? WHERE id=?',(message,identity))


@router.post('/api/training/{identity}/hide')
def hide_example(identity: str):
    with connection() as db:
        result=db.execute('UPDATE examples SET archived=1 WHERE id=?',(identity,))
        if not result.rowcount:raise HTTPException(404,'Example not found')
    return {'hidden':True,'context_preserved':True}


@router.delete('/api/training')
def clear_training():
    folder=(config.WORK_DIR/'training-files').resolve()
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        rows=db.execute('SELECT id,source FROM examples').fetchall()
        paths=[]
        for row in rows:
            for name in (row['id'],row['id']+Path(row['source']).suffix.lower()):
                path=(folder/name).resolve()
                if path.parent!=folder: raise HTTPException(409,'Stored source path is outside Training storage')
                paths.append(path)
        for path in set(paths): path.unlink(missing_ok=True)
        db.execute('DELETE FROM examples')
    return {'cleared':len(rows)}


@router.post('/api/training/{identity}/restore')
def restore_example(identity: str):
    with connection() as db:
        result=db.execute('UPDATE examples SET archived=0 WHERE id=?',(identity,))
        if not result.rowcount:raise HTTPException(404,'Example not found')
    return {'restored':True}


def analyse(path, source):
    if not path.is_file(): raise ValueError('Stored source file is missing')
    detected=detect(path,source.rsplit('/',1)[-1])
    if detected.path=='X': raise ValueError('Unsupported file type')
    record=acquire(path,detected)
    if not any(line.strip() for line in record.lines): raise ValueError('No text found')
    category=find_category(record.lines)
    dt=find_date(record.lines,record.meta)
    company,_=lookup.find_client(record.lines) if lookup.sheet_exists() else (None,[])
    labels={'company':company.name if company else '',
            'category':category.name if category else '', 'date':dt.value.isoformat() if dt else ''}
    from .references import find_references
    refs=find_references([{'id':'training','text':'\n'.join(record.lines)}],labels['category'])
    labels['reference_number']=refs[0]['normalized'] if len(refs)==1 else ''
    return '\n'.join(record.lines),labels


@router.post('/api/training/{identity}/refresh')
def refresh_example(identity: str):
    with connection() as db:
        row=db.execute('SELECT * FROM examples WHERE id=?',(identity,)).fetchone()
    if row is None: raise HTTPException(404,'Example not found')
    folder=config.WORK_DIR/'training-files'
    candidates=[folder/(identity+Path(row['source']).suffix.lower()),folder/identity]
    path=next((p for p in candidates if p.is_file()),candidates[0])
    now=datetime.now(timezone.utc).isoformat()
    try:
        if sha256_file(path)!=row['fingerprint']:
            raise ValueError('Stored source has changed; upload it as a new document version')
        text,labels=analyse(path,row['source'])
        with connection() as db:
            # Conditional SQL also protects approval made while OCR was running.
            db.execute('UPDATE examples SET text=?,machine_labels=?,labels=CASE WHEN approved=0 THEN ? ELSE labels END,refreshed_at=?,refresh_error=NULL WHERE id=?',
                       (text,json.dumps(labels),json.dumps(labels),now,identity))
        return {'id':identity,'refreshed':True,'approved_labels_preserved':bool(row['approved'])}
    except Exception as exc:
        message=str(exc) if isinstance(exc,ValueError) else 'Could not refresh source ('+type(exc).__name__+')'
        with connection() as db:
            db.execute('UPDATE examples SET refreshed_at=?,refresh_error=? WHERE id=?',(now,message,identity))
        raise HTTPException(400,message) from exc


@router.post('/api/training/ingest')
def ingest(file: UploadFile = File(...), background_tasks: BackgroundTasks=None):
    source = (file.filename or 'upload').replace('\\', '/')
    identity = uuid.uuid4().hex
    folder = config.WORK_DIR / 'training-files'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (identity + Path(source).suffix.lower())
    try:
        with path.open('wb') as target:
            while chunk := file.file.read(1 << 20):
                target.write(chunk)
        fingerprint = sha256_file(path)
        with connection() as db:
            existing = db.execute('SELECT id,source FROM examples WHERE fingerprint=?', (fingerprint,)).fetchone()
            if existing:
                stored=(folder/(existing['id']+Path(existing['source']).suffix.lower())).resolve()
                if stored.parent!=folder.resolve():raise ValueError('Invalid stored source path')
                legacy=(folder/existing['id']).resolve()
                current=stored if stored.exists() else legacy
                if not current.is_file() or sha256_file(current)!=fingerprint:
                    path.replace(stored)
                else:path.unlink()
                db.execute('UPDATE examples SET archived=0 WHERE id=?',(existing['id'],))
                if background_tasks is not None:background_tasks.add_task(queue_context,existing['id'])
                return {'id': existing['id'], 'duplicate': True, 'restored_to_view':True}
        detected = detect(path, source.rsplit('/', 1)[-1])
        if detected.path == 'X':
            raise ValueError('Unsupported file type')
        record = acquire(path, detected)
        if not any(line.strip() for line in record.lines):
            raise ValueError('No text found')
        category = find_category(record.lines)
        dt = find_date(record.lines, record.meta)
        company, _ = lookup.find_client(record.lines) if lookup.sheet_exists() else (None, [])
        labels = {'company': company.name if company else '',
                  'category': category.name if category else '', 'date': dt.value.isoformat() if dt else ''}
        from .references import find_references
        references = find_references([{'id':'training','text':'\n'.join(record.lines)}], labels['category'])
        labels['reference_number'] = references[0]['normalized'] if len(references)==1 else ''
        with connection() as db:
            db.execute('INSERT INTO examples (id,fingerprint,source,text,labels) VALUES (?,?,?,?,?)',
                       (identity, fingerprint, source, '\n'.join(record.lines), json.dumps(labels)))
        if background_tasks is not None:background_tasks.add_task(queue_context,identity)
        return {'id': identity, 'duplicate': False}
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc


class Labels(BaseModel):
    reference_number: str = ''
    company: str
    category: str
    date: date


@router.put('/api/training/{identity}/approve')
def approve(identity: str, labels: Labels):
    if labels.category not in ('Request For Quotation', 'Purchase Order', 'Purchase Requisition', 'Invoice', 'Quotation', 'Design Document', 'Bill of Materials (BOM)', 'Mixed document', 'Unknown'):
        raise HTTPException(400, 'Choose a supported category')
    with connection() as db:
        result = db.execute('UPDATE examples SET labels=?, approved=1 WHERE id=?',
                            (labels.model_dump_json(), identity))
        if not result.rowcount:
            raise HTTPException(404, 'Example not found')
    return {'approved': True}


@router.get('/training', response_class=HTMLResponse)
def training_page():
    return RedirectResponse('/documents#reference-activity',status_code=307)
