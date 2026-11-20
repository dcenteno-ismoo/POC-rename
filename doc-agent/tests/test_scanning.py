import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from docagent.library import Store
from docagent.sources import Sources,SourcePolicy
from docagent.scanning import Scans

try:
    import pymupdf
    from docagent.acquire import acquire
except ModuleNotFoundError:
    acquire=None


@unittest.skipIf(acquire is None,'Run native extraction tests in the app venv')
class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.folder=self.base/'source';self.folder.mkdir()
        self.store=Store(self.base/'library')
        self.sources=Sources(self.store,SourcePolicy([str(self.folder)]))
        self.source=self.sources.save({'name':'Synthetic docs','source_type':'local','path':str(self.folder)})
        self.engine=Scans(self.sources);self.owner='test-worker';self.assertTrue(self.engine.lease(self.owner))

    def text(self,name='order.txt',number='PO-0001'):
        path=self.folder/name;path.write_text('PURCHASE ORDER\nPO No: '+number+'\nDate: November 18, 2026\nSupplier: Example Supply\nTotal: PHP 100.25')
        return path

    def run_scan(self,full=False):
        job=self.engine.queue(self.source['id'],full);self.engine.run(job['id'],self.owner)
        return self.engine.get(job['id'])

    def test_first_repeat_modified_full_and_original_integrity(self):
        path=self.text();before=path.read_bytes();mtime=path.stat().st_mtime_ns
        first=self.run_scan();self.assertEqual(first['status'],'Completed');self.assertEqual(first['counts']['successful'],1)
        self.assertEqual(path.read_bytes(),before);self.assertEqual(path.stat().st_mtime_ns,mtime)
        with patch.object(self.engine,'snapshot',side_effect=AssertionError('Unchanged file must not be reread')):
            repeat=self.run_scan()
        self.assertEqual(repeat['files'][0]['change_kind'],'unchanged');self.assertEqual(repeat['counts']['skipped'],1)
        path.write_text(path.read_text().replace('PO-0001','PO-0002'))
        changed=self.run_scan();self.assertEqual(changed['files'][0]['change_kind'],'modified')
        self.assertEqual(len(self.store.search()),2)
        full=self.run_scan(True);self.assertEqual(full['counts']['successful'],1);self.assertEqual(len(self.store.search()),2)

    def test_scan_items_and_incremental_cached_metadata_upgrade(self):
        import json
        from docagent.procurement import RULES_VERSION
        path=self.folder/'materials.txt'
        path.write_text('PURCHASE ORDER\nPO No: PO-0042\nItem | Qty | Unit | Description | Material | Unit Price | Amount\n1 | 2 | pc | Pump impeller | SS 304 | PHP 500 | PHP 1000\nGrand Total: PHP 1120\n12% VAT: PHP 120')
        before=path.read_bytes();first=self.run_scan();identity=first['files'][0]['document_id']
        row=self.store.get(identity);self.assertEqual(row['machine']['segments'][0]['line_items'][0]['material_specification']['raw'],'SS 304')
        self.assertEqual(row['machine']['segments'][0]['fields']['totals']['raw'],'PHP 1120')
        with self.store.db() as db:db.execute("UPDATE documents SET prompt_version='source-scan-1' WHERE id=?",(identity,))
        with patch.object(self.engine,'snapshot',side_effect=AssertionError('Metadata upgrade uses cached text')):
            second=self.run_scan()
        self.assertEqual(second['counts']['skipped'],1)
        self.assertEqual(self.store.get(identity)['prompt_version'],RULES_VERSION)
        self.assertEqual(path.read_bytes(),before)

    def test_unsupported_corrupted_pdf_and_independent_success(self):
        self.text();(self.folder/'ignored.exe').write_bytes(b'unsupported')
        (self.folder/'broken.pdf').write_bytes(b'%PDF-1.7\ncorrupted')
        result=self.run_scan();self.assertEqual(result['counts']['successful'],1)
        self.assertEqual(result['counts']['failed'],1);self.assertEqual(result['counts']['skipped'],1)
        self.assertEqual(result['counts']['discovered'],3)
        broken=next(f for f in result['files'] if f['path']=='broken.pdf');self.assertTrue(broken['error'])
        retry=self.run_scan();self.assertEqual(retry['counts']['failed'],1)
        self.assertFalse(any(r['status']=='Scan indexing' for r in self.store.search()))

    def test_inaccessible_file_does_not_stop_other_files(self):
        self.text('good.txt');bad=self.text('denied.txt','PO-0002')
        original=self.engine.snapshot
        def snapshot(path,*args):
            if path==bad:raise PermissionError('Read access denied')
            return original(path,*args)
        with patch.object(self.engine,'snapshot',side_effect=snapshot):result=self.run_scan()
        self.assertEqual(result['counts']['failed'],1);self.assertEqual(result['counts']['successful'],1)

    def test_duplicate_prevention_and_concurrent_queue_lease(self):
        first=self.text();(self.folder/'duplicate.txt').write_bytes(first.read_bytes())
        queued=self.engine.queue(self.source['id']);again=self.engine.queue(self.source['id'])
        self.assertEqual(queued['id'],again['id']);self.assertFalse(self.engine.lease('other-worker'))
        self.engine.run(queued['id'],self.owner)
        results=self.engine.get(queued['id']);self.assertEqual(results['counts']['successful'],2)
        self.assertEqual(len(self.store.search()),1)

    def test_resume_interrupted_and_cancel(self):
        path=self.text();job=self.engine.queue(self.source['id'])
        with self.store.db() as db:db.execute("UPDATE source_scan_jobs SET status='Running' WHERE id=?",(job['id'],))
        self.engine.result(job,path.name,'new','Pending')
        restored=Scans(Sources(Store(self.base/'library'),SourcePolicy([str(self.folder)])))
        restored.run(job['id'],self.owner)
        self.assertEqual(restored.get(job['id'])['counts']['successful'],1)
        self.assertEqual(len(self.store.search()),1)
        next_job=restored.queue(self.source['id']);restored.cancel(next_job['id'])
        self.assertEqual(restored.get(next_job['id'])['status'],'Cancelled')

    def test_nested_folder_and_path_escape(self):
        nested=self.folder/'sub';nested.mkdir();self.text('sub/order.txt')
        outside=self.base/'outside';outside.mkdir();(outside/'secret.txt').write_text('Do not read')
        link=self.folder/'escape'
        try:link.symlink_to(outside,target_is_directory=True)
        except OSError:pass
        result=self.run_scan();self.assertEqual(result['counts']['successful'],1)
        self.assertFalse(any('secret.txt' in f['path'] for f in result['files']))
        with self.assertRaises(ValueError):self.engine.safe_path(outside,self.folder)

    def test_native_pdf_xlsx_docx(self):
        doc=pymupdf.open();page=doc.new_page();page.insert_text((40,40),'INVOICE\nInvoice No: INV-0001\nDate: November 18, 2026');doc.save(self.folder/'invoice.pdf');doc.close()
        from openpyxl import Workbook
        wb=Workbook();wb.active.append(['PURCHASE ORDER']);wb.active.append(['PO No','PO-0002']);wb.save(self.folder/'order.xlsx');wb.close()
        from docx import Document
        doc=Document();doc.add_paragraph('QUOTATION');doc.add_paragraph('Quotation No: QT-0003');doc.save(self.folder/'quote.docx')
        result=self.run_scan();self.assertEqual(result['counts']['successful'],3)
        for file in result['files']:
            document=self.store.get(file['document_id']);self.assertTrue(document['sources']);self.assertEqual(document['review_status'],'Pending')

    def test_mid_scan_cancellation_and_stale_worker_fencing(self):
        self.text('a.txt');self.text('b.txt','PO-0002')
        job=self.engine.queue(self.source['id']);original=self.engine.file
        def file_then_cancel(*args):
            original(*args);self.engine.cancel(job['id'])
        with patch.object(self.engine,'file',side_effect=file_then_cancel):self.engine.run(job['id'],self.owner)
        result=self.engine.get(job['id']);self.assertEqual(result['status'],'Cancelled')
        self.assertLessEqual(result['counts']['successful'],1)
        with self.store.db() as db:db.execute('UPDATE source_scan_lease SET owner=?',('new-worker',))
        from docagent.scanning import LeaseLost
        with self.assertRaises(LeaseLost):self.engine.result(job,'bad.txt','new','Indexed')
