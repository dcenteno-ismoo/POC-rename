"""Isolated live HTTP test: real PDF acquisition, synthetic reviewed facts, local Qwen.

Uses a temporary database/server on port 8011. Leaves the real Library untouched.
AI structured extraction is explicitly disabled; synthetic expected facts are reviewed
via the normal API. Grounded explanation uses the actual local model.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pymupdf

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from docagent.library import FIELDS

BASE='http://127.0.0.1:8011'


def api(path,method='GET',body=None,headers=None):
    headers=headers or {}
    if isinstance(body,dict):body=json.dumps(body).encode();headers['Content-Type']='application/json'
    request=urllib.request.Request(BASE+path,data=body,method=method,headers=headers)
    with urllib.request.urlopen(request,timeout=210) as response:
        data=response.read()
        return json.loads(data) if 'json' in response.headers.get('Content-Type','') else data


def main():
    # Do not touch any existing server on this test port.
    try:
        urllib.request.urlopen(BASE+'/api/info',timeout=1)
    except urllib.error.URLError:pass
    else:raise SystemExit('Port 8011 already has a server; refusing to run')
    with tempfile.TemporaryDirectory() as folder:
        env=dict(os.environ,DOCAGENT_ROOT=folder,DOCAGENT_AI_EXTRACT_ENABLED='0',DOCAGENT_AI_URL='http://127.0.0.1:11434/v1/chat/completions',DOCAGENT_AI_MODEL='qwen2.5:3b',DOCAGENT_AI_TIMEOUT='60')
        log=Path(folder)/'server.log'
        with log.open('wb') as output:
            server=subprocess.Popen([sys.executable,'-m','uvicorn','docagent.web:app','--host','127.0.0.1','--port','8011'],cwd=Path(__file__).resolve().parents[1],env=env,stdout=output,stderr=output,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            try:
                for _ in range(40):
                    try:api('/api/chats');break
                    except urllib.error.URLError:time.sleep(.5)
                else:raise RuntimeError('Temporary server failed to start; check dependencies')
                identities=[]
                for number,day,total in [('PO-DEMO-0001','2026-11-17','100.25'),('PO-DEMO-0002','2026-11-18','250.50')]:
                    text=f'SYNTHETIC LIVE TEST\nPURCHASE ORDER\nPO No: {number}\nDocument Date: {day}\nSupplier: Example Supply\nTotal: {total}\nCurrency: PHP\nPayment Terms: Net 30 days'
                    pdf=pymupdf.open();page=pdf.new_page();page.insert_text((50,50),text);data=pdf.tobytes();pdf.close()
                    boundary='RICH-DEMO-BOUNDARY'
                    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{number}.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode()+data+f'\r\n--{boundary}--\r\n'.encode()
                    identity=api('/api/library','POST',body,{'Content-Type':'multipart/form-data; boundary='+boundary})['id'];identities.append(identity)
                    for _ in range(80):
                        row=api('/api/library/'+identity)
                        if row['status']=='Failed' and row['sources']:break
                        time.sleep(.25)
                    else:raise RuntimeError('PDF acquisition did not finish')
                    source=row['sources'][0]
                    def value(raw):
                        assert raw in source['text']
                        return {'raw':raw,'normalized':raw,'evidence':[{'source_id':source['id'],'excerpt':raw}],'flags':[]}
                    fields=dict.fromkeys(FIELDS)
                    for key,raw in [('document_number',number),('document_date',day),('totals',total),('currency','PHP'),('supplier','Example Supply'),('payment_terms','Net 30 days')]:fields[key]=value(raw)
                    evidence=[{'source_id':source['id'],'excerpt':'PURCHASE ORDER'}]
                    reviewed={'category':'Purchase Order (PO)','category_evidence':evidence,'segments':[{'category':'Purchase Order (PO)','category_evidence':evidence,'fields':fields,'line_items':[]}],'summary':[],'tags':[],'quality_flags':['Synthetic reviewer fixture; not model-extracted facts']}
                    api('/api/library/'+identity+'/review','PUT',{'output':reviewed,'status':'Verified','actor':'Synthetic live test reviewer'})
                chat=api('/api/chats','POST');cid=chat['id']
                questions=['Show my latest PO.','How much is it?','Who is the supplier?','Compare it with the previous PO.','Explain the payment terms for PO-DEMO-0002.']
                for i,question in enumerate(questions):
                    chat=api('/api/chats/'+cid+'/messages','POST',{'question':question,'request_id':'live-'+str(i)})
                    response=chat['turns'][-1]['response'];print(question,' | '.join(c['text'] for c in response.get('claims',[])),flush=True)
                    assert response['claims'],response
                    if i==0:assert 'PO-DEMO-0002' in response['claims'][0]['text']
                    if i==1:assert '250.50' in response['claims'][0]['text']
                    if i==2:assert 'Example Supply' in response['claims'][0]['text']
                    if i==3:assert any('150.25' in c['text'] for c in response['claims'])
                    if i==4:assert 'unavailable' not in response.get('message',''),response
                    for claim in response['claims']:
                        for ref in claim['references']:assert api(ref['url'].split('#')[0]).startswith(b'%PDF')
                assert len(api('/api/chats/'+cid)['turns'])==5
                api('/api/chats/'+cid,'DELETE')
                assert len(api('/api/library'))==2
                print('PASS: native PDF acquisition, reviewed-fact HTTP lookups/follow-ups, Decimal comparison, live local grounded Q&A, source links and persistent reopen. No browser UI test; no live AI structured extraction.',flush=True)
            finally:
                if os.name=='nt':
                    # Windows venv launchers can spawn a child Python process.
                    subprocess.run(['taskkill','/PID',str(server.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
                else:server.terminate()
                try:server.wait(timeout=10)
                except subprocess.TimeoutExpired:server.kill();server.wait()


if __name__=='__main__':main()
