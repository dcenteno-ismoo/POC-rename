"""Fixture-based tests: no live OCR or AI service required."""
import copy
import tempfile
import unittest
from pathlib import Path

from docagent.library import FIELDS, Store, process, validate


def output(sources, filename=''):
    ref = {'source_id':sources[0]['id'], 'excerpt':'PO 000123'}
    fields = dict.fromkeys(FIELDS)
    fields['document_number'] = {'raw':'000123','normalized':'000123','evidence':[ref],'flags':[]}
    return {'category':'Purchase Order (PO)','category_evidence':[ref],
            'segments':[{'category':'Purchase Order (PO)','category_evidence':[ref],'fields':fields,'line_items':[]}],
            'summary':[{'text':'Purchase order 000123','evidence':[ref]}], 'tags':[], 'quality_flags':[]}


class LibraryTests(unittest.TestCase):
    def test_clear_removes_documents_and_history(self):
        identity=self.complete()
        path=Path(self.store.get(identity)['path'])
        self.assertEqual(self.store.clear(),1)
        self.assertFalse(path.exists())
        self.assertEqual(self.store.search(),[])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM history').fetchone()[0],0)

    def test_clear_refuses_active_processing(self):
        identity=self.ingest()
        self.store.claim()
        with self.assertRaises(ValueError):self.store.clear()
        self.assertTrue(Path(self.store.get(identity)['path']).exists())

    def test_invoice_fields_and_legacy_compatibility(self):
        from docagent.library import INVOICE_FIELDS
        sources=[{'id':'s','text':'Invoice 000123; Due Date: 2026-12-01'}]
        result=output(sources)
        result['category']='Invoice'
        result['segments'][0]['category']='Invoice'
        for ref in [result['category_evidence'][0]]:
            ref['excerpt']='Invoice 000123'
        result['segments'][0]['fields']['due_date']={'raw':'2026-12-01','normalized':'2026-12-01','evidence':[{'source_id':'s','excerpt':'Due Date: 2026-12-01'}],'flags':[]}
        validate(result,sources)
        self.assertIsNone(result['segments'][0]['fields']['tax_amount'])
        legacy=output([{'id':'s','text':'PO 000123'}])
        for name in INVOICE_FIELDS: legacy['segments'][0]['fields'].pop(name)
        validate(legacy,[{'id':'s','text':'PO 000123'}])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def ingest(self, text='PO 000123', name='order.txt'):
        path = Path(self.temp.name)/'upload.tmp'
        path.write_text(text)
        return self.store.ingest(path, name)

    def reader(self, job, progress):
        progress(1,1)
        return [{'id':job['version']+':0', 'document_id':job['id'], 'version':job['version'],
                 'location':{'precision':'Text order only'}, 'text':'PO 000123'}]

    def complete(self):
        identity = self.ingest()
        process(self.store, self.store.claim(), self.reader, output)
        return identity

    def test_progress_and_persistence(self):
        identity = self.complete()
        row = Store(self.temp.name).get(identity)
        self.assertEqual(row['status'], 'Completed')
        events = [h['event'] for h in row['history']]
        for event in ['Queued','Reading / OCR','Classifying','Extracting information','Indexing','Completed']:
            self.assertIn(event, events)
        self.assertEqual(row['review_status'],'Pending')
        self.assertEqual(list(self.store.export()),[])

    def test_failure_independence_retry_and_cache(self):
        first = self.ingest()
        second = self.ingest('other file','other.txt')
        def broken(*args): raise RuntimeError('fixture failure')
        process(self.store,self.store.claim(),self.reader,broken)
        process(self.store,self.store.claim(),self.reader,output)
        self.assertEqual(self.store.get(first)['status'],'Failed')
        self.assertEqual(self.store.get(second)['status'],'Completed')
        self.store.retry(first)
        def no_ocr(*args): raise AssertionError('OCR must be cached')
        process(self.store,self.store.claim(),no_ocr,output)
        self.assertEqual(self.store.get(first)['attempt'],2)
        self.assertEqual(self.ingest(),first)
        self.assertEqual(len(self.store.search()),2)

    def test_sources_and_missing_values(self):
        sources=[{'id':'s','text':'PO 000123'}]
        result=output(sources)
        self.assertIsNone(result['segments'][0]['fields']['approval_status'])
        validate(result,sources)
        result['summary'][0]['evidence'][0]={'source_id':'invented','excerpt':'PO 000123'}
        with self.assertRaises(ValueError): validate(result,sources)
        result=output(sources)
        result['segments'][0]['fields']['document_number']['raw']='999'
        with self.assertRaises(ValueError): validate(result,sources)

    def test_review_export_search_and_reprocessing(self):
        identity=self.complete()
        row=self.store.get(identity)
        corrected=copy.deepcopy(row['machine'])
        corrected['quality_flags']=['Ambiguous date: reviewer clarification required']
        self.store.review(identity,corrected,'Needs clarification','Reviewer')
        self.assertEqual(list(self.store.export()),[])
        self.store.review(identity,corrected,'Verified','Reviewer')
        self.assertEqual(len(list(self.store.export())),1)
        self.store.retry(identity)
        process(self.store,self.store.claim(),self.reader,output)
        self.assertEqual(self.store.get(identity)['reviewed'],corrected)
        results=self.store.search('000123',category='Purchase Order (PO)', review_status='Verified')
        self.assertEqual(results[0]['source']['document_id'],identity)
        self.assertEqual(self.store.get(identity)['machine']['segments'][0]['fields']['document_number']['normalized'],'000123')

    def test_interruption_and_stale_attempt(self):
        identity=self.ingest()
        old=self.store.claim()
        self.store.recover()
        self.assertEqual(self.store.get(identity)['status'],'Failed')
        self.store.retry(identity)
        current=self.store.claim()
        with self.assertRaises(RuntimeError): self.store.stage(old,'Completed')
        self.assertEqual(self.store.get(identity)['attempt'],current['attempt'])


if __name__=='__main__': unittest.main()
