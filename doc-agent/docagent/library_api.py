"""Local library API and one bounded background worker."""
import json
import os
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from .document_auth import protect_existing
from fastapi.responses import FileResponse, HTMLResponse, Response, RedirectResponse
from pydantic import BaseModel

from . import config
from .library import Store, worker

router = APIRouter(dependencies=[Depends(protect_existing)])
answer_slots = threading.BoundedSemaphore(2)
store = Store(config.WORK_DIR / 'library')
from .conversations import Chats
chats=Chats(store)
chat_slots=threading.Lock()
stop = threading.Event()
thread = None


class ChatMessage(BaseModel):
    question: str
    request_id: str


@router.get('/api/chats')
def chat_list():return chats.list()


@router.post('/api/chats')
def chat_new():return chats.create()


@router.get('/api/chats/{identity}')
def chat_get(identity: str):
    try:return chats.get(identity)
    except KeyError:raise HTTPException(404,'Conversation not found')


@router.delete('/api/chats/{identity}')
def chat_delete(identity: str):
    if not chat_slots.acquire(blocking=False):raise HTTPException(409,'Wait for the active reply before deleting a conversation')
    try:
        chats.delete(identity)
        return {'deleted':True}
    except KeyError:raise HTTPException(404,'Conversation not found')
    finally:chat_slots.release()


@router.post('/api/chats/{identity}/messages')
def chat_send(identity: str,body: ChatMessage):
    if not chat_slots.acquire(blocking=False):raise HTTPException(429,'Assistant busy; retry shortly')
    try:return chats.send(identity,body.question,body.request_id)
    except KeyError:raise HTTPException(404,'Conversation or source not found')
    except ValueError as exc:raise HTTPException(400,str(exc))
    except Exception:raise HTTPException(503,'Reply interrupted. Retry this message; your conversation is saved.')
    finally:chat_slots.release()


class Question(BaseModel):
    question: str


@router.delete('/api/library')
def clear_library():
    try: return {'cleared':store.clear()}
    except ValueError as exc: raise HTTPException(409,str(exc))


@router.post('/api/library-ask')
def ask(body: Question):
    from .answers import answer
    if not body.question.strip() or len(body.question)>2000:
        raise HTTPException(400, 'Enter a question of 1–2000 characters')
    if not answer_slots.acquire(blocking=False):
        raise HTTPException(429, 'AI is busy; try again shortly')
    try:
        return answer(store, body.question)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    finally:
        answer_slots.release()


def read_sources(job, progress):
    from .acquire import acquire
    from .detect import detect
    path = Path(job['path'])
    det = detect(path, job['filename'])
    rec = acquire(path, det)
    sources = []
    for index, page in enumerate(rec.pages):
        location = dict(page.location)
        if det.path == 'C':
            location = {'converted_page': location.get('page'), 'precision': 'Converted rendition; original page may differ'}
        elif det.path == 'E': location = {'image': job['filename'], 'precision': 'Image text; no bounding boxes retained'}
        elif not location: location = {'precision': 'Text order only; original location unavailable'}
        # Stable text chunks; no fabricated page or block coordinates.
        text = '\n'.join(page.lines)
        for offset in range(0, len(text), 6000):
            excerpt = text[offset:offset+6000]
            if excerpt.strip():
                sources.append({'id': f"{job['version']}:p{index}:c{offset//6000}", 'document_id':job['id'],
                                'version':job['version'], 'location': location, 'method':page.method, 'text':excerpt})
        progress(index+1, len(rec.pages))
    return sources


def start_worker():
    global thread
    if thread is None or not thread.is_alive():
        chats.recover()
        stop.clear()
        thread = threading.Thread(target=worker, args=(store,read_sources,stop), daemon=True)
        thread.start()


@router.get('/library', response_class=HTMLResponse)
def page(document: str=''):
    from urllib.parse import urlencode
    return RedirectResponse('/documents'+('?' + urlencode({'document':document}) if document else ''),status_code=307)


@router.get('/assistant', response_class=HTMLResponse)
def assistant_page():
    from .ui import render_page
    return render_page('assistant')


@router.post('/api/library')
def upload(file: UploadFile = File(...)):
    filename = (file.filename or 'document').replace('\\','/')
    handle, name = tempfile.mkstemp(dir=store.root)
    try:
        with os.fdopen(handle, 'wb') as target:
            while chunk := file.file.read(1 << 20): target.write(chunk)
        identity = store.ingest(name, filename)
        return {'id':identity}
    finally:
        Path(name).unlink(missing_ok=True)


@router.get('/api/library')
def search(q: str='', category: str='', status: str='', review_status: str='', date_from: str='', date_to: str=''):
    return store.search(q,category,status,review_status,date_from,date_to)


def document(identity):
    try: return store.get(identity)
    except KeyError: raise HTTPException(404, 'Document not found')


@router.get('/api/library/{identity}')
def detail(identity: str):
    result = document(identity)
    result.pop('path', None)
    return result


@router.get('/api/library/{identity}/original')
def original(identity: str):
    row = document(identity)
    path=Path(row['path']).resolve()
    if path.parent!=store.root.resolve():raise HTTPException(403,'Stored original is outside managed storage')
    if not path.is_file():raise HTTPException(404,'Original document is unavailable')
    return FileResponse(path, filename=Path(row['filename']).name, content_disposition_type='inline',
                        headers={'X-Content-Type-Options':'nosniff', 'Content-Security-Policy':"sandbox"})


@router.post('/api/library/{identity}/retry')
def retry(identity: str):
    document(identity)
    try: store.retry(identity)
    except ValueError as exc: raise HTTPException(409, str(exc))
    return {'queued':True}


class Review(BaseModel):
    output: dict
    status: str
    actor: str


@router.put('/api/library/{identity}/review')
def review(identity: str, body: Review):
    document(identity)
    try: store.review(identity, body.output, body.status, body.actor)
    except ValueError as exc: raise HTTPException(400, str(exc))
    return {'saved':True}


@router.get('/api/library-export')
def export(x_export_token: str = Header(default='')):
    import secrets
    token = os.environ.get('DOCAGENT_EXPORT_TOKEN')
    from .demo import demo_mode
    if not demo_mode() and (not token or not secrets.compare_digest(token, x_export_token)):
        raise HTTPException(403, 'Export requires the configured server export token')
    return Response('\n'.join(json.dumps(row) for row in store.export()), media_type='application/x-ndjson',
                    headers={'Content-Disposition':'attachment; filename="approved-examples.jsonl"'})
