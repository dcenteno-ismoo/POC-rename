"""Run in the app venv to include FastAPI-backed Training tests."""
import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from docagent import training
except ModuleNotFoundError:
    training=None


@unittest.skipIf(training is None,'Training API dependencies unavailable in this Python environment')
class TrainingArchiveTests(unittest.TestCase):
    def test_training_context_indexes_without_model(self):
        from docagent.library import Store,process
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder);path=Path(folder)/'sample.txt';path.write_text('PURCHASE ORDER\nPO No: PO-0001\nDate: November 18, 2026\nTotal: PHP 125.50\nSupplier: Example Supply')
            store.ingest(path,'sample.txt');job=store.claim()
            sources=[{'id':'s','document_id':job['id'],'version':job['version'],'method':'training_cached_text','location':{'precision':'text'},'text':Path(job['path']).read_text()}]
            with store.db() as db:db.execute('UPDATE documents SET sources=? WHERE id=?',(json.dumps(sources),job['id']))
            job=store.get(job['id'])
            process(store,job,lambda *a:None,lambda *a:(_ for _ in ()).throw(AssertionError('No model needed')))
            row=store.get(job['id'])
            self.assertEqual(row['status'],'Completed')
            self.assertEqual(row['model'],'deterministic-context-rules')
            self.assertEqual(row['machine']['segments'][0]['fields']['document_number']['raw'],'PO-0001')
            self.assertEqual(row['review_status'],'Pending')
    def test_reupload_restores_hidden_example_and_missing_source(self):
        from starlette.datastructures import UploadFile
        from docagent.record import sha256_file
        with tempfile.TemporaryDirectory() as folder,patch.object(training.config,'WORK_DIR',Path(folder)):
            sources=Path(folder)/'training-files';sources.mkdir()
            path=sources/'demo.txt';data=b'Synthetic reupload PO-0001';path.write_bytes(data)
            with training.connection() as db:
                db.execute('INSERT INTO examples (id,fingerprint,source,text,labels,approved,archived) VALUES (?,?,?,?,?,?,?)',('demo',sha256_file(path),'sample.txt',data.decode(),json.dumps({'reference_number':'PO-0001'}),1,1))
            for missing in (False,True):
                if missing:path.unlink()
                with patch.object(training,'acquire',side_effect=AssertionError('No OCR needed for duplicate')):
                    result=training.ingest(UploadFile(filename='sample.txt',file=io.BytesIO(data)))
                self.assertTrue(result['restored_to_view'])
                row=training.examples()[0]
                self.assertEqual(row['id'],'demo');self.assertTrue(row['approved'])
                self.assertEqual(row['labels']['reference_number'],'PO-0001')
                self.assertEqual(path.read_bytes(),data)
                training.hide_example('demo')
    def test_hide_keeps_retrievable_context_and_deduplicates(self):
        from docagent.library import Store
        from docagent import library_api
        from docagent.answers import retrieve
        with tempfile.TemporaryDirectory() as folder,patch.object(training.config,'WORK_DIR',Path(folder)):
            store=Store(Path(folder)/'library')
            with patch.object(library_api,'store',store):
                sources=Path(folder)/'training-files';sources.mkdir()
                source=sources/'demo.txt';source.write_text('Synthetic pump reference PO-000123')
                from docagent.record import sha256_file
                with training.connection() as db:db.execute('INSERT INTO examples (id,fingerprint,source,text,labels) VALUES (?,?,?,?,?)',('demo',sha256_file(source),'demo.txt',source.read_text(),'{}'))
                first=training.save_context('demo');second=training.save_context('demo')
                self.assertEqual(first['document_id'],second['document_id'])
                job=store.claim()
                record={'id':'source','document_id':job['id'],'version':job['version'],'location':{'precision':'synthetic text'},'text':source.read_text()}
                store.stage(job,'Failed',sources=json.dumps([record]),error='Fixture structured extraction failed')
                training.hide_training()
                self.assertEqual(training.examples(),[])
                hidden=training.examples(include_archived=True)[0]
                self.assertTrue(hidden['context']['ready'])
                self.assertTrue(source.exists())
                self.assertEqual(len(retrieve(store,'pump')),1)

    def test_approve_then_hide_without_deleting_and_can_restore(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(training.config,'WORK_DIR',Path(folder)):
            with training.connection() as db:
                db.execute('INSERT INTO examples (id,fingerprint,source,text,labels) VALUES (?,?,?,?,?)',('demo','hash','example.txt','Synthetic training source',json.dumps({'company':'Example Supply'})))
            training.approve('demo',training.Labels(company='Example Supply',reference_number='PO-0001',category='Purchase Order',date='2026-11-18'))
            self.assertEqual(len(training.examples()),1)
            training.hide_example('demo')
            self.assertEqual(training.examples(),[])
            archived=training.examples(include_archived=True)
            self.assertEqual(len(archived),1)
            self.assertTrue(archived[0]['approved']);self.assertTrue(archived[0]['archived'])
            self.assertEqual(archived[0]['labels']['reference_number'],'PO-0001')
            training.restore_example('demo')
            self.assertEqual(len(training.examples()),1)
            self.assertTrue(training.examples()[0]['approved'])
