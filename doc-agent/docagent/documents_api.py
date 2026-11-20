from pathlib import Path
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from fastapi.responses import FileResponse,HTMLResponse
from .document_auth import authorized,session_cookie,COOKIE
from .documents import DocumentLibrary
from .library_api import store

router=APIRouter()
library=DocumentLibrary(store)

@router.get('/documents',response_class=HTMLResponse)
def workspace():
    from .ui import render_page
    return render_page('documents')

@router.post('/api/documents/session')
def unlock(request: Request,response: Response):
    authorized(request)
    from .demo import demo_mode
    if demo_mode():return {'authorized':True,'demo':True}
    response.set_cookie(COOKIE,session_cookie(),httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=8*3600,path='/')
    return {'authorized':True}

@router.delete('/api/documents/session')
def lock(response: Response):
    response.delete_cookie(COOKIE,path='/');return {'locked':True}

@router.get('/api/documents',dependencies=[Depends(authorized)])
def search(q: str='',category: str='',company: str='',reference: str='',date_from: str='',date_to: str='',source: str='',status: str='',review_status: str='',limit: int=50,offset: int=0):
    try:return library.search(q,category,company,reference,date_from,date_to,source,status,review_status,limit,offset)
    except ValueError as exc:raise HTTPException(400,str(exc))

@router.get('/api/documents/sources',dependencies=[Depends(authorized)])
def origins():
    with store.db() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='project_sources'").fetchone():
            return [dict(row) for row in db.execute('SELECT DISTINCT o.kind,o.source_id,s.name AS source_name FROM document_origins o LEFT JOIN project_sources s ON s.id=o.source_id ORDER BY o.kind,s.name')]
        return [dict(row) for row in db.execute('SELECT DISTINCT kind,source_id FROM document_origins ORDER BY kind,source_id')]

@router.get('/api/documents/{identity}',dependencies=[Depends(authorized)])
def detail(identity: str):
    try:return library.detail(identity)
    except KeyError:raise HTTPException(404,'Document not found')

@router.get('/api/documents/{identity}/original',dependencies=[Depends(authorized)])
def original(identity: str):
    try:path,name=library.original(identity)
    except (KeyError,FileNotFoundError):raise HTTPException(404,'Original document is unavailable')
    except ValueError:raise HTTPException(403,'Stored original path is not authorized')
    return FileResponse(path,filename=Path(name).name,content_disposition_type='inline',headers={'X-Content-Type-Options':'nosniff','Content-Security-Policy':'sandbox'})
