"""Live read-only scanner test with synthetic native/scanned PDFs and isolated storage."""
import hashlib
import io
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
import pymupdf
from PIL import Image,ImageDraw,ImageFont


def main():
    base='http://127.0.0.1:8013'
    try:urllib.request.urlopen(base+'/sources',timeout=1)
    except urllib.error.URLError:pass
    else:raise RuntimeError('Test port 8013 already in use')
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);source=root/'documents';source.mkdir()
        text=source/'order.txt';text.write_text('PURCHASE ORDER\nPO No: PO-0001\nDate: November 18, 2026')
        (source/'duplicate.txt').write_bytes(text.read_bytes())
        (source/'unsupported.bin').write_bytes(b'unsupported')
        (source/'corrupted.pdf').write_bytes(b'%PDF-1.7\ntruncated')
        pdf=pymupdf.open();page=pdf.new_page();page.insert_text((40,50),'INVOICE\nInvoice No: INV-0001\nDate: November 18, 2026');pdf.save(source/'native.pdf');pdf.close()
        image=Image.new('RGB',(1600,1000),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',48)
        draw.multiline_text((100,100),'PURCHASE ORDER\nPO No: PO-SCAN-0002\nDate: November 18, 2026\nSupplier: Demo Supply',font=font,fill='black',spacing=35)
        image.save(source/'photo.png')
        buffer=io.BytesIO();image.save(buffer,format='PNG')
        pdf=pymupdf.open();page=pdf.new_page(width=800,height=500);page.insert_image(page.rect,stream=buffer.getvalue());pdf.save(source/'scanned.pdf');pdf.close()
        original={p.name:(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns) for p in source.iterdir()}
        token=secrets.token_urlsafe(32)
        env=dict(os.environ,DOCAGENT_ROOT=str(root/'app'),DOCAGENT_SOURCE_ROOTS=json.dumps([str(source)]),DOCAGENT_SOURCES_ADMIN_TOKEN=token)
        def api(path,method='GET',body=None,admin=True):
            headers={'X-Sources-Admin-Token':token} if admin else {}
            if body is not None:body=json.dumps(body).encode();headers['Content-Type']='application/json'
            with urllib.request.urlopen(urllib.request.Request(base+path,data=body,method=method,headers=headers),timeout=30) as response:return json.load(response)
        with (root/'server.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,'-m','uvicorn','docagent.web:app','--host','127.0.0.1','--port','8013'],cwd=Path(__file__).resolve().parents[1],env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            try:
                for _ in range(60):
                    try:api('/api/sources');break
                    except urllib.error.URLError:time.sleep(.25)
                registered=api('/api/sources','POST',{'name':'Synthetic scan demo','path':str(source),'source_type':'local','enabled':True})
                def scan(full=False):
                    job=api('/api/sources/'+registered['id']+'/scans','POST',{'full':full})
                    deadline=time.monotonic()+240;last=None
                    while time.monotonic()<deadline:
                        result=api('/api/source-scans/'+job['id'])
                        summary=(result['status'],result['counts']['processed'])
                        if summary!=last:print('Scan progress',summary,flush=True);last=summary
                        if result['status'] in ('Completed','Failed','Cancelled'):return result
                        time.sleep(.5)
                    raise RuntimeError('Synthetic OCR scan timed out')
                first=scan();assert first['status']=='Completed',first
                assert first['counts']['successful']==5,first
                assert first['counts']['failed']==1 and first['counts']['skipped']==1,first
                ocr=[r for r in first['files'] if r['path'] in ('photo.png','scanned.pdf')]
                assert all(r['confidence'] is not None for r in ocr),ocr
                for name,(digest,mtime) in original.items():
                    p=source/name;assert hashlib.sha256(p.read_bytes()).hexdigest()==digest and p.stat().st_mtime_ns==mtime
                repeat=scan();assert sum(r['change_kind']=='unchanged' and r['status']=='Skipped' for r in repeat['files'])==5,repeat
                text.write_text(text.read_text().replace('PO-0001','PO-0003'))
                modified=scan();assert any(r['path']=='order.txt' and r['change_kind']=='modified' and r['status']=='Indexed' for r in modified['files']),modified
                full=scan(True);assert full['counts']['successful']==5,full
                library=api('/api/library')
                assert len(library)==6,len(library) # 4 unique good contents, 1 corrupted snapshot, 1 changed version.
                print('PASS: real native PDF, scanned PDF OCR, image OCR, repeat/modified/full scans, independent corrupt/unsupported results, deduplication and unchanged source hashes/mtimes',flush=True)
            finally:
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
                else:process.terminate()
                process.wait(timeout=10)


if __name__=='__main__':main()
