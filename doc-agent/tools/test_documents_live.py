"""Isolated HTTP test: uploads + scans + FTS + review + authorized originals. No AI."""
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from docagent.library import FIELDS


def main():
    base='http://127.0.0.1:8014'
    try:urllib.request.urlopen(base+'/documents',timeout=1)
    except urllib.error.URLError:pass
    else:raise RuntimeError('Port 8014 already in use')
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);source=root/'source';source.mkdir()
        (source/'scanned.txt').write_text('INVOICE\nInvoice No: INV-SCAN-0002\nDate: November 18, 2026\nSupplier: Scanned Supply\nMaterial: stainless steel')
        reader=secrets.token_urlsafe(32);admin=secrets.token_urlsafe(32)
        env=dict(os.environ,DOCAGENT_ROOT=str(root/'app'),DOCAGENT_SOURCE_ROOTS=json.dumps([str(source)]),DOCAGENT_SOURCES_ADMIN_TOKEN=admin,DOCAGENT_DOCUMENTS_TOKEN=reader,DOCAGENT_AI_EXTRACT_ENABLED='0')
        opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def api(path,method='GET',body=None,headers=None,anonymous=False):
            headers=dict(headers or {})
            if isinstance(body,dict):body=json.dumps(body).encode();headers['Content-Type']='application/json'
            request=urllib.request.Request(base+path,data=body,method=method,headers=headers)
            with (urllib.request.urlopen(request,timeout=20) if anonymous else opener.open(request,timeout=20)) as response:
                data=response.read();return json.loads(data) if 'json' in response.headers.get('Content-Type','') else data
        with (root/'server.log').open('wb') as log:
            server=subprocess.Popen([sys.executable,'-m','uvicorn','docagent.web:app','--host','127.0.0.1','--port','8014'],cwd=Path(__file__).resolve().parents[1],env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            try:
                for _ in range(50):
                    try:api('/documents',anonymous=True);break
                    except urllib.error.URLError:time.sleep(.25)
                for page in ('/','/sources','/documents','/library','/training','/assistant'):
                    html=api(page,anonymous=True)
                    assert b'aria-label="Main navigation"' in html and b'<rich-shell>' not in html,page
                    assert html.count(b'aria-current="page"')==1,page
                assert b'.rich-header' in api('/assets/app.css',anonymous=True)
                for path in ['/api/documents','/api/library','/api/chats','/api/training']:
                    try:api(path,anonymous=True)
                    except urllib.error.HTTPError as error:assert error.code==403,(path,error.code)
                    else:raise AssertionError('Anonymous access allowed: '+path)
                api('/api/documents/session','POST',headers={'X-Documents-Token':reader})
                conversation=api('/api/chats','POST')
                for endpoint in ('/api/library-ask','/api/chats/'+conversation['id']+'/messages'):
                    try:api(endpoint,'POST',{'question':'Search all documents','request_id':'unauthorized-retrieval'},anonymous=True)
                    except urllib.error.HTTPError as error:assert error.code==403
                    else:raise AssertionError('Chat retrieval was accessible without document authorization')
                api('/api/chats/'+conversation['id'],'DELETE')
                content=b'INVOICE\nInvoice No: INV-UP-0001\nDate: 2026-11-18\nSupplier: Upload Supply\nMaterial: stainless steel'
                def upload(name):
                    boundary='RICH-DOC-TEST'
                    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n\r\n').encode()+content+f'\r\n--{boundary}--\r\n'.encode()
                    return api('/api/library','POST',body,{'Content-Type':'multipart/form-data; boundary='+boundary})['id']
                identity=upload('upload.txt');assert upload('duplicate-name.txt')==identity
                for _ in range(100):
                    row=api('/api/library/'+identity)
                    if row['sources']:break
                    time.sleep(.1)
                field=dict.fromkeys(FIELDS);source_text=row['sources'][0]
                def value(raw):return {'raw':raw,'normalized':raw,'evidence':[{'source_id':source_text['id'],'excerpt':raw}],'flags':[]}
                for key,raw in [('document_number','INV-UP-0001'),('document_date','2026-11-18'),('supplier','Upload Supply')]:field[key]=value(raw)
                evidence=[{'source_id':source_text['id'],'excerpt':'INVOICE'}]
                output={'category':'Invoice','category_evidence':evidence,'segments':[{'category':'Invoice','category_evidence':evidence,'fields':field,'line_items':[]}],'summary':[],'tags':[],'quality_flags':[]}
                api('/api/library/'+identity+'/review','PUT',{'output':output,'status':'Verified','actor':'Synthetic test reviewer'})
                registration=api('/api/sources','POST',{'name':'Demo Source','path':str(source),'source_type':'local'},headers={'X-Sources-Admin-Token':admin})
                job=api('/api/sources/'+registration['id']+'/scans','POST',{'full':False},headers={'X-Sources-Admin-Token':admin})
                for _ in range(100):
                    scan=api('/api/source-scans/'+job['id'],headers={'X-Sources-Admin-Token':admin})
                    if scan['status']=='Completed':break
                    time.sleep(.1)
                found=api('/api/documents?q=stainless%20steel');assert found['total']==2 and found['fts5'],found
                assert api('/api/documents?source='+registration['id'])['total']==1
                assert api('/api/documents?reference=INV-UP-0001&company=Upload&review_status=Verified&date_from=2026-11-01')['total']==1
                assert api('/api/documents?q=duplicate-name')['total']==1
                detail=api('/api/documents/'+identity);assert detail['original_available']
                assert api(detail['original_url'])==content
                try:api(detail['original_url'],anonymous=True)
                except urllib.error.HTTPError as error:assert error.code==403
                else:raise AssertionError('Anonymous original download allowed')
                (root/'app'/'work'/'library'/(identity+'.txt')).unlink()
                assert not api('/api/documents/'+identity)['original_available']
                assert api('/api/documents?q=stainless')['total']==2
                try:api(detail['original_url'])
                except urllib.error.HTTPError as error:assert error.code==404
                else:raise AssertionError('Missing original was returned')
                print('PASS: unified uploads/scans, FTS5, reference/company/date/source filters, review updates, duplicate aliases, cookie authorization, legacy route protection and missing originals; no model calls')
            finally:
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(server.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
                else:server.terminate()
                server.wait(timeout=10)


if __name__=='__main__':main()
