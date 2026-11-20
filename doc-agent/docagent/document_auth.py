"""Local document-reader authorization; signed cookies allow normal citation links."""
import hashlib
import hmac
import os
import secrets
import time
from fastapi import HTTPException,Request

COOKIE='rich_document_session'


def keys():return [v for v in (os.environ.get('DOCAGENT_DOCUMENTS_TOKEN'),os.environ.get('DOCAGENT_SOURCES_ADMIN_TOKEN')) if v]


def authorized(request: Request,required=True):
    from .demo import demo_mode
    if demo_mode():return
    allowed=keys()
    if not allowed:
        if required:raise HTTPException(503,'Configure DOCAGENT_DOCUMENTS_TOKEN or DOCAGENT_SOURCES_ADMIN_TOKEN on the server')
        return
    supplied=request.headers.get('X-Documents-Token') or request.headers.get('X-Sources-Admin-Token','')
    if supplied and any(secrets.compare_digest(supplied.encode(),key.encode()) for key in allowed):return
    value=request.cookies.get(COOKIE,'')
    try:
        expires,signature=value.split('.',1)
        expected=hmac.new(allowed[0].encode(),expires.encode(),hashlib.sha256).hexdigest()
        if int(expires)>time.time() and secrets.compare_digest(signature,expected):return
    except (ValueError,TypeError):pass
    raise HTTPException(403,'Open Documents and unlock document access first')


def protect_existing(request: Request):
    if request.url.path in ('/library','/assistant','/training'):return
    return authorized(request,required=False)


def session_cookie():
    expires=str(int(time.time()+8*3600))
    return expires+'.'+hmac.new(keys()[0].encode(),expires.encode(),hashlib.sha256).hexdigest()
