"""Isolated Sources HTTP/authorization test. No real folders or databases are modified."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def main():
    base='http://127.0.0.1:8012'
    try:urllib.request.urlopen(base+'/sources',timeout=1)
    except urllib.error.URLError:pass
    else:raise RuntimeError('Test port 8012 is already in use')
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);allowed=root/'approved';allowed.mkdir()
        source=allowed/'engineering';source.mkdir();original=source/'keep.txt';original.write_text('Synthetic source; must remain unchanged')
        token=secrets.token_urlsafe(32)
        env=dict(os.environ,DOCAGENT_ROOT=str(root/'app'),DOCAGENT_SOURCE_ROOTS=json.dumps([str(allowed)]),DOCAGENT_SOURCES_ADMIN_TOKEN=token)
        def api(path,method='GET',body=None,authorized=True):
            headers={'X-Sources-Admin-Token':token} if authorized else {}
            if body is not None:body=json.dumps(body).encode();headers['Content-Type']='application/json'
            with urllib.request.urlopen(urllib.request.Request(base+path,data=body,method=method,headers=headers),timeout=15) as response:
                data=response.read()
                return json.loads(data) if 'json' in response.headers.get('Content-Type','') else data
        with (root/'test.log').open('wb') as log:
            def start():
                process=subprocess.Popen([sys.executable,'-m','uvicorn','docagent.web:app','--host','127.0.0.1','--port','8012'],cwd=Path(__file__).resolve().parents[1],env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                for _ in range(40):
                    try:api('/sources',authorized=False);return process
                    except urllib.error.URLError:time.sleep(.25)
                raise RuntimeError('Temporary test server failed to start')
            def stop(process):
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
                else:process.terminate()
                process.wait(timeout=10)
            process=start()
            try:
                try:api('/api/sources',authorized=False)
                except urllib.error.HTTPError as error:assert error.code==403
                else:raise AssertionError('Unauthorized listing was allowed')
                data={'name':'Synthetic Engineering','path':str(source),'source_type':'local','enabled':True,'description':'Isolated Rescon demo test'}
                row=api('/api/sources','POST',data)
                assert row['accessibility']=='accessible' and row['indexed_files']==0 and row['last_scan_at'] is None
                identity=row['id']
                data['name']='Updated Source';api('/api/sources/'+identity,'PUT',data)
                api('/api/sources/'+identity+'/enabled','PATCH',{'enabled':False})
                stop(process);process=start()
                restored=api('/api/sources')[0]
                assert restored['id']==identity and restored['name']=='Updated Source' and not restored['enabled']
                try:api('/api/sources','POST',dict(data,path=str(root)))
                except urllib.error.HTTPError as error:assert error.code==400
                else:raise AssertionError('Out-of-root registration was allowed')
                api('/api/sources/'+identity,'DELETE')
                assert api('/api/sources')==[]
                assert original.read_text()=='Synthetic source; must remain unchanged'
                assert api('/api/library')==[]
                print('PASS: authorized HTTP CRUD, access checks, out-of-root rejection, restart persistence, no indexed documents and unchanged source file')
            finally:stop(process)


if __name__=='__main__':main()
