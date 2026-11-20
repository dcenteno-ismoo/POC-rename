"""Evidence-bound scan fields and Rescon-style procurement layouts; no model calls."""
import json
import tempfile
import unittest
from pathlib import Path
from docagent.context_index import index_context
from docagent.library import Store,validate
from docagent.procurement import RULES_VERSION,refresh_saved_scan


class ProcurementTests(unittest.TestCase):
    def extract(self,text,sources=None):
        sources=sources or [{'id':'page-1','text':text,'location':{'page':1}}]
        result=validate(index_context(sources),sources)
        return result,result['segments'][0]['fields'],result['segments'][0]['line_items']

    def test_rescon_items_materials_totals_terms_and_tax(self):
        result,fields,items=self.extract('QUOTATION\nQuotation No: QT-001\nSupplier: Demo Supplier\nCustomer: Demo Client\nItem | Qty. | Unit | Description | Part No. | Material | Unit Price | Amount\n1 | 2 | pcs | Pump impeller | PN-100 | SS 304 | PHP 500.00 | PHP 1,000.00\nTotal Price exclusive of VAT | PHP 1,000.00\n12% VAT | PHP 120.00\nTotal Price inclusive of VAT | PHP 1,120.00\nPayment Terms: Within 30 days\nDelivery: Within 20 weeks\nPrice Validity: 15 days')
        self.assertEqual(fields['totals']['raw'],'PHP 1,120.00')
        self.assertEqual(fields['subtotal']['raw'],'PHP 1,000.00')
        self.assertEqual(fields['tax_amount']['raw'],'PHP 120.00')
        self.assertEqual(fields['tax_rate']['raw'],'12%')
        self.assertEqual(fields['currency']['normalized'],'PHP')
        self.assertEqual(fields['payment_terms']['raw'],'Within 30 days')
        self.assertEqual(fields['delivery_requirements']['raw'],'Within 20 weeks')
        self.assertEqual(items[0]['part_number']['raw'],'PN-100')
        self.assertEqual(items[0]['material_specification']['raw'],'SS 304')
        self.assertEqual(items[0]['quantity']['raw'],'2')
        self.assertEqual(items[0]['stated_line_amount']['raw'],'PHP 1,000.00')
        self.assertTrue(any(t['text']=='Material mentioned: SS 304' for t in result['tags']))

    def test_missing_redacted_amounts_are_not_calculated(self):
        _,fields,items=self.extract('PURCHASE ORDER\nPO No: PO-002\nItem | Qty | Unit | Description | Unit Price | Amount\n1 | 2 | pc | Mechanical seal | 500.00 | [REDACTED]\nGrand Total: [REDACTED]\nTotal Price exclusive of VAT: 1000.00\nPayment Terms: Cash in advance')
        self.assertEqual(len(items),1)
        self.assertIsNone(items[0]['stated_line_amount'])
        self.assertIsNone(fields['totals'])
        self.assertIsNone(fields['currency'])
        self.assertEqual(fields['subtotal']['raw'],'1000.00')

    def test_conflicting_totals_and_currencies_abstain(self):
        result,fields,_=self.extract('INVOICE\nGrand Total: USD 10.00\nGrand Total: USD 20.00\nCurrency: USD\nCurrency: PHP')
        self.assertIsNone(fields['totals']);self.assertIsNone(fields['currency'])
        self.assertTrue(any('Conflicting totals' in f for f in result['quality_flags']))

    def test_two_line_total_and_currency_words(self):
        _,fields,_=self.extract('PURCHASE ORDER\nTotal Price:\n1234.50\nPrices in US Dollars\nShipment Date: 14 production/working weeks')
        self.assertEqual(fields['totals']['raw'],'1234.50');self.assertEqual(fields['currency']['normalized'],'USD')
        self.assertEqual(fields['delivery_requirements']['raw'],'14 production/working weeks')

    def test_materials_are_literal_mentions_not_supplier_inferences(self):
        result,fields,items=self.extract('PURCHASE REQUISITION\nMaterial SS 304. Shaft in stainless steel.\nSupplier: Demo supplier')
        self.assertEqual({t['text'] for t in result['tags']},{'Material mentioned: SS 304','Material mentioned: stainless steel'})
        self.assertFalse(items);self.assertIsNone(fields['totals'])

    def test_excel_sparse_columns_and_sheet_boundaries(self):
        sources=[{'id':'h','text':'Description | Qty | Amount','location':{'sheet':'Items','cells':['A1','B1','D1']}},
                 {'id':'r','text':'Pump assembly | 2 | USD 25.00','location':{'sheet':'Items','cells':['A2','B2','D2']}},
                 {'id':'s','text':'Not an item | 3 | USD 50.00','location':{'sheet':'Notes','cells':['A1','B1','D1']}}]
        _,fields,items=self.extract('',sources)
        self.assertEqual(len(items),1);self.assertEqual(items[0]['quantity']['raw'],'2')
        self.assertEqual(items[0]['stated_line_amount']['evidence'][0]['source_id'],'r')

    def test_flattened_or_misaligned_tables_are_not_guessed(self):
        result,_,items=self.extract('PURCHASE ORDER\nQuantity\nUnit\nDescription\n2\npc\nPump assembly\n1000')
        self.assertEqual(items,[])
        self.assertTrue(any('could not be aligned' in f for f in result['quality_flags']))
        _,_,items=self.extract('PURCHASE ORDER\nQty | Description | Amount\n2 | Pump assembly')
        self.assertEqual(items,[])

    def test_multi_document_bundle_does_not_assign_totals_to_wrong_record(self):
        result,fields,items=self.extract('PURCHASE ORDER\nGrand Total: PHP 100\nINVOICE\nGrand Total: PHP 200')
        self.assertEqual(result['category'],'Mixed document');self.assertIsNone(fields['totals']);self.assertEqual(items,[])

    def test_cached_upgrade_preserves_review_and_original_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(root);path=Path(root)/'original.txt';content='PURCHASE ORDER\nPO No: PO-001\nTotal Price inclusive of VAT: PHP 112.00';path.write_text(content)
            identity=store.ingest(path,'order.txt');source={'id':'s1','document_id':identity,'text':content,'location':{'page':1}}
            output=validate(index_context([source]),[source]);reviewed=json.dumps(output)
            with store.db() as db:db.execute("UPDATE documents SET status='Completed',sources=?,machine=?,reviewed=?,review_status='Verified',model='deterministic-source-rules',prompt_version='source-scan-1' WHERE id=?",(json.dumps([source]),reviewed,reviewed,identity))
            original=Path(store.get(identity)['path']);before=original.read_bytes()
            self.assertTrue(refresh_saved_scan(store,identity));self.assertFalse(refresh_saved_scan(store,identity))
            row=store.get(identity);self.assertEqual(row['prompt_version'],RULES_VERSION)
            self.assertEqual(json.dumps(row['reviewed']),reviewed);self.assertEqual(row['review_status'],'Verified')
            self.assertEqual(original.read_bytes(),before)
            with store.db() as db:self.assertEqual(db.execute("SELECT count(*) FROM history WHERE event='Scan metadata refreshed'").fetchone()[0],1)

    def test_inline_commercial_notes_keep_stated_values_separate(self):
        _,fields,_=self.extract('SALES INVOICE\nSubtotal: PHP 55,000.00. Tax (12%): PHP 6,600.00. Total: PHP 61,600.00.\nAmount paid: PHP 0.00. Balance due: PHP 61,600.00.')
        self.assertEqual(fields['totals']['raw'],'PHP 61,600.00')
        self.assertEqual(fields['subtotal']['raw'],'PHP 55,000.00')
        self.assertEqual(fields['tax_amount']['raw'],'PHP 6,600.00')
        self.assertEqual(fields['tax_rate']['raw'],'12%')
        self.assertEqual(fields['amount_paid']['raw'],'PHP 0.00')
        self.assertEqual(fields['balance_due']['raw'],'PHP 61,600.00')

    def test_unlabelled_legacy_items_are_flagged_and_keep_stated_prices(self):
        result,fields,items=self.extract('PURCHASE ORDER\nITEMS / TECHNICAL CONTENT\n1 | PUMP-001 | Centrifugal pump | 2 EA | 25,000.00 | 50,000.00\n2 | SEAL-010 | Mechanical seal | 4 SET | 1,250.00 | 5,000.00\nNOTES / COMMERCIAL DETAILS\nStated order total: PHP 61,600.00.')
        self.assertEqual(len(items),2)
        self.assertEqual(items[0]['quantity']['raw'],'2')
        self.assertEqual(items[1]['unit_price']['raw'],'1,250.00')
        self.assertTrue(items[0]['description']['flags'])
        self.assertEqual(fields['totals']['raw'],'PHP 61,600.00')
        _,_,items=self.extract('BILL OF MATERIALS\nITEMS / TECHNICAL CONTENT\n1 | PL-0001 | Base plate | ASTM A36 | 1 | EA')
        self.assertEqual(items[0]['material_specification']['raw'],'ASTM A36')
        _,_,items=self.extract('PURCHASE REQUISITION\nITEMS / TECHNICAL CONTENT\n1 | PUMP-001 | Centrifugal pump | 2 | EA')
        self.assertEqual(items[0]['unit']['raw'],'EA')

    def test_unknown_unlabelled_layout_without_item_section_is_not_assigned(self):
        _,_,items=self.extract('PURCHASE ORDER\n1 | PN-001 | Pump | 2 EA | 100 | 200')
        self.assertEqual(items,[])

    def test_currency_words_normalized_without_altering_stated_amount(self):
        _,fields,_=self.extract('PURCHASE ORDER\nTotal Price: 1,200.00 in US Dollars')
        self.assertEqual(fields['totals']['raw'],'1,200.00 in US Dollars')
        self.assertEqual(fields['totals']['normalized'],'USD 1,200.00')
        self.assertEqual(fields['currency']['normalized'],'USD')

    def test_serial_reference_kept_as_literal_item_remark(self):
        _,_,items=self.extract('PURCHASE ORDER\nItem | Qty | Unit | Description\n1 | 1 | pc | Pump Model 3196 Serial # SN-1234')
        self.assertEqual(items[0]['remarks']['raw'],'Serial # SN-1234')
