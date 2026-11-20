import unittest
from docagent.references import find_references


class ReferenceTests(unittest.TestCase):
    def refs(self,text,category):
        return find_references([{'id':'s','text':text}],category)

    def test_prefixes_labels_and_leading_zeros(self):
        for text,category,expected in [('RFQ-000123','Request For Quotation','RFQ-000123'),('PO Number: 00012','Purchase Order','00012'),('Invoice No: 000789','Invoice','000789'),('INVABC00123','Invoice','INVABC00123')]:
            with self.subTest(text=text):
                result=self.refs(text,category)
                self.assertEqual(result[0]['normalized'],expected)
                self.assertIn(result[0]['raw'],result[0]['evidence'][0]['excerpt'])

    def test_invoice_ignores_referenced_po(self):
        result=self.refs('Invoice No: 000789\nPO12345','Invoice')
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['normalized'],'000789')

    def test_all_document_types(self):
        cases=[('RFQ No.: RFQ-2026-0142','Request For Quotation','RFQ-2026-0142'),
               ('P.O. No.: PO-2026-0088','Purchase Order','PO-2026-0088'),
               ('Purchase Request Number: PR-000321','Purchase Requisition','PR-000321'),
               ('Quotation Ref.: QTN-2026-0912','Quotation','QTN-2026-0912'),
               ('Drawing No.: ME-GA-0012\nRevision: C','Design Document','ME-GA-0012'),
               ('BOM No.: BOM-0042\nAssembly Drawing: ME-GA-0012','Bill of Materials (BOM)','BOM-0042'),
               ('Invoice No.: INV-000045','Invoice','INV-000045')]
        for text,category,expected in cases:
            with self.subTest(category=category):self.assertEqual(self.refs(text,category)[0]['normalized'],expected)

    def test_split_line_unknown_related_and_ocr(self):
        self.assertEqual(self.refs('PO No.\n000123','Purchase Order')[0]['raw'],'000123')
        self.assertEqual(self.refs('BILL OF MATERIALS\nAssembly Drawing: ME-GA-0012','Bill of Materials (BOM)'),[])
        self.assertEqual(self.refs('Invoice No: INV0001','Unknown'),[])
        self.assertEqual(self.refs('PO No: PO-00O12','Purchase Order')[0]['raw'],'PO-00O12')
        self.assertTrue(self.refs('PO No: PO-00O12','Purchase Order')[0]['flags'])

    def test_heading_precedence(self):
        from docagent.references import heading_category
        self.assertEqual(heading_category(['QUOTATION','Your RFQ: RFQ123'])[0],'Quotation')
        self.assertEqual(heading_category(['REQUEST FOR QUOTATION'])[0],'Request For Quotation')
        self.assertEqual(heading_category(['PURCHASE ORDER','QUOTATION'])[0],'Mixed document')

    def test_missing_and_conflicting(self):
        self.assertEqual(self.refs('Invoice Date: 2026-01-01','Invoice'),[])
        result=self.refs('INV0001\nINV0002','Invoice')
        self.assertEqual(len(result),2)
        self.assertTrue(all(r['flags'] for r in result))
