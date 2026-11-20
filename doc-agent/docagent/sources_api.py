"""Sources API: every management/read operation requires an administrator token."""
import json
import os
import secrets
import threading
from pathlib import Path
from fastapi import APIRouter,Depends,Header,HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from .sources import Sources,SourcePolicy
from .library_api import store

router=APIRouter()
scan_stop=threading.Event()
scan_thread=None


def scanner():
    from .scanning import Scans
    from . import config
    return Scans(service(),excluded=(config.WORK_DIR,config.OUT_DIR,config.REVIEW_DIR))


def start_scanner():
    global scan_thread
    from .demo import demo_mode
    if not os.environ.get('DOCAGENT_SOURCES_ADMIN_TOKEN') and not demo_mode():return
    try:engine=scanner()
    except HTTPException:return
    if scan_thread is None or not scan_thread.is_alive():
        scan_stop.clear();scan_thread=threading.Thread(target=engine.work,args=(scan_stop,),daemon=True);scan_thread.start()


def authorized(x_sources_admin_token: str=Header(default='')):
    from .demo import demo_mode
    if demo_mode():return
    expected=os.environ.get('DOCAGENT_SOURCES_ADMIN_TOKEN','')
    if not expected:raise HTTPException(503,'Sources administration is disabled. Configure DOCAGENT_SOURCES_ADMIN_TOKEN on the server')
    if not secrets.compare_digest(expected.encode(),x_sources_admin_token.encode()):raise HTTPException(403,'Administrator authorization is required')


def service():
    try:return Sources(store,SourcePolicy(json.loads(os.environ.get('DOCAGENT_SOURCE_ROOTS','[]'))))
    except (ValueError,TypeError):raise HTTPException(503,'Configure DOCAGENT_SOURCE_ROOTS as a JSON array of absolute allowed folders on the server')


class SourceInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str=Field(min_length=1,max_length=120)
    source_type: str
    path: str=Field(min_length=1,max_length=4096)
    enabled: StrictBool=True
    description: str=Field(default='',max_length=2000)


class EnabledInput(BaseModel):
    enabled: StrictBool


class ScanInput(BaseModel):
    full: StrictBool=False


@router.post('/api/sources/scan-all',dependencies=[Depends(authorized)])
def scan_all(body: ScanInput):
    engine=scanner();jobs=[];errors=[]
    for source in engine.sources.list():
        if not source['enabled']:continue
        try:jobs.append(engine.queue(source['id'],body.full))
        except ValueError as exc:errors.append({'source_id':source['id'],'error':str(exc)})
    return {'jobs':jobs,'errors':errors}


@router.post('/api/sources/{identity}/scans',dependencies=[Depends(authorized)])
def scan_source(identity: str,body: ScanInput):return call(lambda:scanner().queue(identity,body.full))


@router.get('/api/source-scans',dependencies=[Depends(authorized)])
def scan_list():return call(lambda:scanner().list())


@router.get('/api/source-scans/{identity}',dependencies=[Depends(authorized)])
def scan_detail(identity: str):return call(lambda:scanner().get(identity))


@router.post('/api/source-scans/{identity}/cancel',dependencies=[Depends(authorized)])
def scan_cancel(identity: str):return call(lambda:scanner().cancel(identity))


def call(operation):
    try:return operation()
    except KeyError:raise HTTPException(404,'Source registration not found')
    except ValueError as exc:raise HTTPException(400,str(exc))


@router.get('/sources',response_class=HTMLResponse)
def page():
    from .ui import render_page
    return render_page('sources')


@router.get('/api/sources',dependencies=[Depends(authorized)])
def list_sources():return call(lambda:service().list())


@router.post('/api/sources',dependencies=[Depends(authorized)])
def create_source(body: SourceInput):return call(lambda:service().save(body.model_dump()))


@router.put('/api/sources/{identity}',dependencies=[Depends(authorized)])
def edit_source(identity: str,body: SourceInput):return call(lambda:service().save(body.model_dump(),identity))


@router.patch('/api/sources/{identity}/enabled',dependencies=[Depends(authorized)])
def enable_source(identity: str,body: EnabledInput):return call(lambda:service().set_enabled(identity,body.enabled))


@router.delete('/api/sources/{identity}',dependencies=[Depends(authorized)])
def delete_source(identity: str):
    call(lambda:service().remove(identity))
    return {'removed':True,'original_files_unchanged':True}
