"""Create synthetic demo PDFs without changing the lookup sheet or Training data."""
import json
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'demo-training-samples'
PREVIEW = ROOT / 'tmp' / 'pdfs' / 'demo-training'

DOCS = [
 ('01-rfq.pdf','RFQ','RESCON ENGINEERING','REQUEST FOR QUOTATION','RFQ-2026-0142',[
  ('RFQ No.','RFQ-2026-0142'),('Document Date','November 18, 2026'),('To','ABC Industrial Supply'),('Project','Demo Pump Station / DEMO-001'),
  ('Submission Deadline','November 25, 2026'),('Delivery Requirements','Deliver to Demo Site A within 30 days of order')],
  ['1 | PUMP-001 | Centrifugal pump, 5 kW | 2 | EA','2 | SEAL-010 | Mechanical seal kit | 4 | SET'],
  ['Please quote the requested items and quantities. No offered prices are stated.','Buyer / Issuer: RESCON ENGINEERING. Invited supplier: ABC Industrial Supply.']),
 ('02-purchase-order.pdf','PO','RESCON ENGINEERING','PURCHASE ORDER','PO-2026-0088',[
  ('PO No.','PO-2026-0088'),('Document Date','November 18, 2026'),('Supplier','ABC Industrial Supply'),('Quotation Ref.','QTN-2026-0912'),('PR Ref.','PR-000321'),
  ('Ship To','Demo Warehouse Services, Demo Site A'),('Payment Terms','Net 30 days'),('Delivery Date','December 18, 2026'),('Currency','PHP')],
  ['1 | PUMP-001 | Centrifugal pump, 5 kW | 2 EA | 25,000.00 | 50,000.00','2 | SEAL-010 | Mechanical seal kit | 4 SET | 1,250.00 | 5,000.00'],
  ['Stated subtotal: PHP 55,000.00. Stated tax (12%): PHP 6,600.00.','Stated order total: PHP 61,600.00. Ship To is a delivery party, not the buyer.']),
 ('03-purchase-requisition.pdf','PR','RESCON ENGINEERING','PURCHASE REQUISITION','PR-000321',[
  ('PR No.','PR-000321'),('Document Date','November 18, 2026'),('Department','Maintenance'),('Requested By','Juan Dela Cruz'),
  ('Purpose','Replace demo circulation equipment'),('Required By','December 18, 2026'),('Approval Status','Pending approval'),('Estimated Cost','PHP 55,000.00')],
  ['1 | PUMP-001 | Centrifugal pump, 5 kW | 2 | EA','2 | SEAL-010 | Mechanical seal kit | 4 | SET'],
  ['This is an internal request. No supplier has been identified or awarded.','Requester and department are not company names.']),
 ('04-design-document.pdf','DESIGN_DOCUMENT','ABC DESIGN SERVICES','GENERAL ARRANGEMENT','ME-GA-0012',[
  ('Drawing No.','ME-GA-0012'),('Document Date','November 18, 2026'),('Client','RESCON ENGINEERING'),('Project','Demo Pump Station / DEMO-001'),
  ('Revision','C'),('Revision Date','November 17, 2026'),('Sheet','1 of 1'),('Discipline','Mechanical'),('Drawn By','Demo Designer'),('Checked By','Demo Checker'),('Stated Status','For review')],
  ['Equipment: Demo pump skid, assembly SKID-001','Technical note: Provide 600 mm maintenance clearance.','Technical note: All stated dimensions are in millimetres unless noted.'],
  ['Schematic for demonstration only; not an engineering drawing for construction.','Revision C is separate from the drawing number. Approval is not asserted.']),
 ('05-bill-of-materials.pdf','BOM','RESCON ENGINEERING','BILL OF MATERIALS','BOM-0042',[
  ('BOM No.','BOM-0042'),('Document Date','November 18, 2026'),('Assembly','Demo pump skid / SKID-001'),('Assembly Drawing','ME-GA-0012'),('Revision','A')],
  ['1 | PL-0001 | Base plate | ASTM A36 | 1 | EA','2 | BOLT-0016 | Hex bolt M16 x 50 | ISO 4017, grade 8.8 | 8 | EA','3 | PIPE-0050 | DN50 pipe | ASTM A106 Grade B | 3 | M'],
  ['Assembly drawing ME-GA-0012 is a related reference, not the BOM identifier.','Material specifications and quantities are document-stated demo values.']),
 ('06-quotation.pdf','QUOTATION','ABC INDUSTRIAL SUPPLY','QUOTATION','QTN-2026-0912',[
  ('Quotation No.','QTN-2026-0912'),('Document Date','November 18, 2026'),('Customer','RESCON ENGINEERING'),('Your RFQ','RFQ-2026-0142'),
  ('Validity','Until December 2, 2026'),('Lead Time','30 calendar days from confirmed order'),('Payment Terms','Net 30 days'),('Currency','PHP')],
  ['1 | PUMP-001 | Centrifugal pump, 5 kW | 2 EA | 25,000.00 | 50,000.00','2 | SEAL-010 | Mechanical seal kit | 4 SET | 1,250.00 | 5,000.00'],
  ['Stated subtotal: PHP 55,000.00. Stated tax: PHP 6,600.00. Total: PHP 61,600.00.','Scope: Supply only. Exclusions: Installation and commissioning.','This supplier offer references an RFQ; it is not itself an RFQ.']),
 ('07-invoice.pdf','Invoice','ABC INDUSTRIAL SUPPLY','SALES INVOICE','INV-000045',[
  ('Invoice No.','INV-000045'),('Invoice Date','November 18, 2026'),('Due Date','December 18, 2026'),('Bill To','RESCON ENGINEERING'),
  ('Billing Address','100 Demo Avenue, Demo City'),('PO Reference','PO-2026-0088'),('Payment Terms','Net 30 days'),('Currency','PHP')],
  ['1 | PUMP-001 | Centrifugal pump, 5 kW | 2 EA | 25,000.00 | 50,000.00','2 | SEAL-010 | Mechanical seal kit | 4 SET | 1,250.00 | 5,000.00'],
  ['Subtotal: PHP 55,000.00. Tax (12%): PHP 6,600.00. Total: PHP 61,600.00.','Amount paid: PHP 0.00. Balance due: PHP 61,600.00.','Payment instructions: Contact the demo accounts team. No real bank details.']),
 ('09-unknown.pdf','Unknown','DEMO OPERATIONS TEAM','WORKING NOTES',None,[('Note Date','November 18, 2026'),('Topic','Pump station discussion')],
  ['Discuss access requirements at the next meeting.','Confirm whether the demo site is available in December.'],
  ['No document type, primary reference or company roles can be established.','Expected classification: Unknown. Missing identifiers must remain null.'])
]


def page(doc, item, page_number=1):
    filename, kind, issuer, title, reference, fields, items, notes = item
    p = doc.new_page(width=595, height=842)
    p.draw_rect(fitz.Rect(0,0,595,10),color=None,fill=(.12,.28,.45))
    def text(y,value,size=10,color=(.12,.17,.23),bold=False):
        result=p.insert_textbox(fitz.Rect(42,y,553,y+48),value,fontname='hebo' if bold else 'helv',fontsize=size,color=color)
        assert result>=0,(filename,value)
    text(25,'SYNTHETIC DEMO - NOT FOR COMMERCIAL OR ENGINEERING USE',9,(.65,.25,.12),True)
    text(58,issuer,17,bold=True)
    text(92,title,20,bold=True)
    y=135
    for label,value in fields:
        text(y,label+': '+value,10)
        y+=23
    y+=10
    text(y,'ITEMS / TECHNICAL CONTENT',10,bold=True); y+=25
    for line in items:
        text(y,line,9); y+=31
    y+=12
    text(y,'NOTES / COMMERCIAL DETAILS',10,bold=True);y+=25
    for line in notes:
        text(y,line,9); y+=31
    assert y<780,(filename,y)
    p.draw_line((42,792),(553,792),color=(.75,.8,.85))
    text(803,'Project RICH demo training | All organizations and transactions are synthetic | Page '+str(page_number),8)


def main():
    OUT.mkdir(parents=True,exist_ok=True); PREVIEW.mkdir(parents=True,exist_ok=True)
    key={}
    for item in DOCS:
        doc=fitz.open();page(doc,item);doc.save(OUT/item[0]);doc.close()
        key[item[0]]={'document_type':item[1],'primary_reference':item[4],'issuer_candidate':item[2] if item[1]!='Unknown' else None,'demo_only':True}
    doc=fitz.open();page(doc,DOCS[0],1);page(doc,DOCS[5],2);doc.save(OUT/'08-mixed-rfq-and-quotation.pdf');doc.close()
    key['08-mixed-rfq-and-quotation.pdf']={'document_type':'Mixed document','segments':[{'page':1,'type':'RFQ','reference':'RFQ-2026-0142'},{'page':2,'type':'QUOTATION','reference':'QTN-2026-0912'}],'demo_only':True}
    (OUT/'answer-key.json').write_text(json.dumps(key,indent=2),encoding='utf-8')
    (OUT/'README.md').write_text('# Demo Training Samples\n\nNine synthetic PDFs covering RFQ, PO, PR, Design Document, BOM, Quotation, Invoice, Mixed and Unknown.\n\nUpload the PDF files to AI Training or the Document Library. Do not upload this README or the answer key as training documents. Mixed contains two separate pages. These are demo inputs, not approved training examples or real transactions. The current rule/model implementation may require review or corrections for some categories. Company approval in the legacy Training screen requires matching entries in your lookup sheet; this generator does not modify that sheet.\n\nSee answer-key.json for expected types and references. Regenerate with `.venv\\Scripts\\python tools/generate_demo_training.py`.\n',encoding='utf-8')
    thumbs=[]
    for path in sorted(OUT.glob('*.pdf')):
        doc=fitz.open(path)
        for i,p in enumerate(doc):
            text=p.get_text();assert 'SYNTHETIC DEMO' in text
            pix=p.get_pixmap(matrix=fitz.Matrix(.65,.65),alpha=False)
            image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
            image.save(PREVIEW/(path.stem+'-'+str(i+1)+'.png'))
            thumb=Image.new('RGB',(410,580),'white');thumb.paste(image,(10,20));ImageDraw.Draw(thumb).text((10,565),path.name+' p'+str(i+1),fill='black');thumbs.append(thumb)
        if path.name in key and key[path.name].get('primary_reference'):
            assert key[path.name]['primary_reference'] in ''.join(p.get_text() for p in doc)
        doc.close()
    sheet=Image.new('RGB',(410*5,580*2),'#dce3eb')
    for i,thumb in enumerate(thumbs):sheet.paste(thumb,((i%5)*410,(i//5)*580))
    sheet.save(PREVIEW/'contact-sheet.png')
    print('Created and text-validated 9 PDFs (10 pages):',OUT)


if __name__=='__main__':main()
