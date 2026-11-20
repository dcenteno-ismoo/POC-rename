"""Live local-model smoke test with clearly synthetic source text; no OCR required.

Run: python tools/test_local_ai.py
"""
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docagent.answers import answer
from docagent.library import Store, model_extract, process

os.environ['DOCAGENT_AI_URL'] = 'http://127.0.0.1:11434/v1/chat/completions'
os.environ.setdefault('DOCAGENT_AI_MODEL', 'qwen2.5:3b')
os.environ.pop('DOCAGENT_AI_KEY', None)

with tempfile.TemporaryDirectory() as folder:
    store = Store(folder)
    path = Path(folder) / 'sample.txt'
    path.write_text('SYNTHETIC TEST DOCUMENT\nPurchase Order\nPO Number: 000123\nSupplier: Example Pumps\nPayment Terms: Net 30 days\n', encoding='utf-8')
    identity = store.ingest(path, 'synthetic-purchase-order.txt')
    def reader(job, progress):
        return [{'id':job['version']+':0','document_id':job['id'], 'version':job['version'],
                 'location':{'precision':'Synthetic text fixture; no page location'},
                 'text':Path(job['path']).read_text('utf-8')}]
    process(store, store.claim(), reader, model_extract)
    row = store.get(identity)
    print('Live local extraction:', row['status'])
    if row['status'] != 'Completed':
        print(row['error'])
        print('Testing assistant independently against the retained synthetic OCR source.')
    result = answer(store, 'What are the payment terms for PO 000123?')
    print(json.dumps(result, indent=2))
    if not result['claims']:
        raise SystemExit('No supported answer returned')
    print('PASS: live local answer with validated file references (synthetic source; no live OCR).')
    if row['status'] != 'Completed':
        raise SystemExit('Structured extraction remains unvalidated for this local model.')
