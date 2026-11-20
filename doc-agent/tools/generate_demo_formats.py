"""Create PNG and XLSX companions to the synthetic PDF demo set.

Run after generate_demo_training.py. Uses openpyxl with explicit user authorization.
"""
import json
import re
from datetime import datetime
from pathlib import Path

import pymupdf as fitz
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from PIL import Image, ImageDraw, ImageFont

from generate_demo_training import DOCS, OUT, PREVIEW


def worksheet(wb, item, title):
    ws=wb.create_sheet(title)
    ws.sheet_view.showGridLines=False
    ws.append(['SYNTHETIC DEMO - NOT FOR COMMERCIAL OR ENGINEERING USE'])
    ws.merge_cells('A1:H1')
    ws.append([item[2]]);ws.merge_cells('A2:H2')
    ws.append([item[3]]);ws.merge_cells('A3:H3')
    ws.append([])
    for label,value in item[5]:
        if 'date' in label.casefold():
            try:value=datetime.strptime(value,'%B %d, %Y')
            except ValueError:pass
        ws.append([label,value])
        row=ws.max_row
        ws.merge_cells(start_row=row,start_column=2,end_row=row,end_column=8)
        if isinstance(value,datetime):ws.cell(row,2).number_format='yyyy-mm-dd'
        else:ws.cell(row,2).number_format='@'
    ws.append([])
    ws.append(['ITEMS / TECHNICAL CONTENT']);ws.merge_cells(start_row=ws.max_row,start_column=1,end_row=ws.max_row,end_column=8)
    for line in item[6]:
        parts=[p.strip() for p in line.split('|')]
        values=[]
        for index,part in enumerate(parts):
            # Reference/part identifiers stay text; pure quantities/prices may be numeric.
            values.append(float(part.replace(',','')) if index>=3 and re.fullmatch(r'\d[\d,]*(?:\.\d+)?',part) else part)
        ws.append(values)
        if len(values)==1:ws.merge_cells(start_row=ws.max_row,start_column=1,end_row=ws.max_row,end_column=8)
    ws.append([])
    ws.append(['NOTES / COMMERCIAL DETAILS']);ws.merge_cells(start_row=ws.max_row,start_column=1,end_row=ws.max_row,end_column=8)
    for line in item[7]:
        ws.append([line]);ws.merge_cells(start_row=ws.max_row,start_column=1,end_row=ws.max_row,end_column=8)
    ws.append([])
    ws.append(['All values and transactions are synthetic. No macros or external links.'])
    ws.merge_cells(start_row=ws.max_row,start_column=1,end_row=ws.max_row,end_column=8)
    for row in ws:
        for cell in row:
            cell.font=Font(name='Calibri',size=11,color='1F2937')
            cell.alignment=Alignment(vertical='top',wrap_text=True)
        ws.row_dimensions[row[0].row].height=32
    for row in (1,2,3):
        ws.cell(row,1).font=Font(name='Calibri',size=11 if row==1 else 17,bold=True,color='FFFFFF')
        for cell in ws[row]:cell.fill=PatternFill('solid',fgColor='203E5A')
    for row in ws:
        if row[0].value in ('ITEMS / TECHNICAL CONTENT','NOTES / COMMERCIAL DETAILS'):
            for cell in row:cell.fill=PatternFill('solid',fgColor='DBEAFE')
            row[0].font=Font(bold=True,color='203E5A')
    for i,width in enumerate([25,22,40,26,17,19,18,18],1):ws.column_dimensions[get_column_letter(i)].width=width
    ws.freeze_panes='C5'
    ws.print_options.horizontalCentered=True
    ws.sheet_properties.pageSetUpPr.fitToPage=True
    ws.page_setup.orientation='landscape';ws.page_setup.paperSize=ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth=1;ws.page_setup.fitToHeight=1
    ws.print_area=ws.dimensions
    return ws


def preview_sheet(ws, path):
    """Data-based visual QA preview; Excel styling is also checked in the workbook."""
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',15)
    image=Image.new('RGB',(1250,60+ws.max_row*38),'white');draw=ImageDraw.Draw(image)
    for row in ws:
        y=20+(row[0].row-1)*38
        values=[str(c.value.strftime('%Y-%m-%d') if isinstance(c.value,datetime) else c.value) for c in row if c.value is not None]
        text=' | '.join(values)
        fill='#203e5a' if row[0].row<=3 else ('#dbeafe' if text in ('ITEMS / TECHNICAL CONTENT','NOTES / COMMERCIAL DETAILS') else 'white')
        draw.rectangle((10,y-3,1240,y+32),fill=fill)
        assert draw.textlength(text,font=font)<1210,(ws.title,text)
        draw.text((20,y),text,font=font,fill='white' if row[0].row<=3 else '#1f2937')
    image.save(path)


def main():
    OUT.mkdir(parents=True,exist_ok=True);PREVIEW.mkdir(parents=True,exist_ok=True)
    key=json.loads((OUT/'answer-key.json').read_text('utf-8'))
    for pdf in sorted(OUT.glob('*.pdf')):
        images=[]
        with fitz.open(pdf) as doc:
            for index,page in enumerate(doc):
                name=pdf.stem+(f'-page-{index+1}' if len(doc)>1 else '')+'.png'
                page.get_pixmap(dpi=144,alpha=False).save(OUT/name)
                images.append(name)
                assert Image.open(OUT/name).width>=1100
        entries=[DOCS[0],DOCS[5]] if pdf.stem.startswith('08-') else [next(d for d in DOCS if d[0]==pdf.name)]
        wb=Workbook();wb.remove(wb.active)
        for index,item in enumerate(entries):worksheet(wb,item,('RFQ' if index==0 else 'Quotation') if len(entries)>1 else 'Document')
        target=OUT/(pdf.stem+'.xlsx');wb.save(target);wb.close()
        check=load_workbook(target,data_only=True)
        assert len(check.worksheets)==len(entries)
        for index,(sheet,item) in enumerate(zip(check.worksheets,entries)):
            text='\n'.join(str(c.value) for row in sheet for c in row if c.value is not None)
            assert 'SYNTHETIC DEMO' in text
            if item[4]:assert item[4] in text
            assert not any(c.data_type=='f' for row in sheet for c in row)
            preview_sheet(sheet,PREVIEW/(target.stem+'-sheet-'+str(index+1)+'.png'))
        check.close()
        expected=dict(key[pdf.name]);expected['format']='xlsx';key[target.name]=expected
        for index,name in enumerate(images):
            expected=dict(key[pdf.name]);expected['format']='png'
            if len(entries)>1:
                expected={'document_type':entries[index][1],'primary_reference':entries[index][4],'demo_only':True,'format':'png','mixed_pdf_page':index+1}
            key[name]=expected
    (OUT/'answer-key.json').write_text(json.dumps(key,indent=2),encoding='utf-8')
    (OUT/'README.md').write_text('# Demo Training Samples\n\nSynthetic demo set: 9 PDFs, 10 PNG images, and 9 XLSX workbooks. Types: RFQ, PO, PR, Design Document, BOM, Quotation, Invoice, Mixed and Unknown.\n\nThe mixed PDF has two pages, the mixed workbook has two sheets, and its PNGs are separate page images. Select one format per sample unless demonstrating duplicate/format handling. Upload only document files, not this README or answer-key.json.\n\nThe PNGs are clean rendered pages for OCR demos, not camera photos. XLSX files preserve reference/part identifiers as text and dates as date cells; no macros or external links. Stated figures are sample source data, not calculations.\n\nThese examples are not approved training records and may require correction in the current classifier/model. Company approval in the legacy Training screen requires matching lookup entries; your lookup sheet has not been changed.\n\nExpected types/references are in answer-key.json.\n',encoding='utf-8')
    print('Created and validated 10 PNG images and 9 Excel workbooks in',OUT)


if __name__=='__main__':main()
