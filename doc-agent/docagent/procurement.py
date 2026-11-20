"""Conservative procurement extraction from saved reader/OCR text, with exact evidence."""
import re

RULES_VERSION = 'procurement-2'
NUMBER = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
MONEY = re.compile(r'(?:(?:PHP|USD|EUR|GBP|JPY|US\$|\$)\s*)?'+NUMBER+r'(?:\s*(?:in\s+)?(?:PHP|USD|EUR|GBP|JPY|US Dollars?|Philippine Pesos?))?', re.I)
LABELS = {
 'supplier':r'Supplier|Vendor', 'buyer':r'Buyer', 'customer':r'Customer|Client',
 'project_name':r'Project(?: Name)?', 'project_number':r'Project (?:No\.?|Number)',
 'payment_terms':r'Payment Terms|Terms of Payment|Terms',
 'delivery_address':r'Deliver To|Delivery Address', 'shipping_address':r'Ship To|Shipping Address',
 'shipping_amount':r'Shipping Amount|Freight Amount',
 'validity':r'Price Validity|Quotation Validity|Validity',
 'referenced_quotation':r'Reference Quotation (?:No\.?|Number)|Referenced Quotation',
 'purchase_order_reference':r'PO Reference|Purchase Order Reference',
 'requester':r'Requested By|Request By', 'department':r'Department|Cost Center',
 'required_by_date':r'Date Needed|Required By', 'equipment':r'Equipment|Model',
 'technical_notes':r'Serial (?:Number|No\.?|#)|S/N',
 'subtotal':r'(?:Stated )?Sub\s*Total|Total Price exclusive of VAT|Total exclusive of VAT',
 'tax_amount':r'(?:Stated )?(?:Add\s*:\s*)?(?:\d+(?:\.\d+)?%\s*)?(?:VAT|Tax)(?: Amount)?(?: \(\d+(?:\.\d+)?%\))?',
 'discount_amount':r'Discount(?: Amount)?',
 'estimated_cost':r'Estimated Cost', 'amount_paid':r'Amount Paid','balance_due':r'Balance Due',
 'purpose':r'Purpose','approval_status':r'Approval Status',
 'delivery_requirements':r'Delivery Requirements|Delivery|Shipment Date|Lead Time|Work Duration',
 'totals':r'(?:Stated )?(?:Total Price inclusive of VAT|Total inclusive of VAT|Grand Total|Invoice Total|Order Total|Total Amount|Total Price|Total)',
}
NUMERIC_FIELDS={'totals','subtotal','tax_amount','discount_amount','shipping_amount','estimated_cost','amount_paid','balance_due'}
CURRENCY = re.compile(r'\b(PHP|USD|EUR|GBP|JPY|US Dollars?|United States Dollars?|Philippine Pesos?)\b|US\$', re.I)


def value(raw,source,excerpt,normalized=None):
    return {'raw':raw,'normalized':raw if normalized is None else normalized,
            'evidence':[{'source_id':source['id'],'excerpt':excerpt}],'flags':[]}


def split_columns(text):
    if '|' in text:
        text=text.strip()
        if text.startswith('|') and text.endswith('|'):text=text[1:-1]
        return [v.strip() for v in text.split('|')]
    if '\t' in text:return [v.strip() for v in text.split('\t')]
    return re.split(r'\s{2,}',text.strip())


def labeled_fields(sources,fields,flags):
    candidates={key:[] for key in LABELS};currencies=[]
    for source in sources:
        lines=[part for original in source['text'].splitlines() for part in re.split(r'(?<=\.)\s+(?=[A-Z])',original)]
        for i,line in enumerate(lines):
            for key,label in LABELS.items():
                # Explicit labels only; no guessing the role of an unlabelled address block.
                m=re.fullmatch(r'\s*('+label+r')\s*(?::|\||\t|\s{2,})\s*(.*?)\s*',line,re.I)
                if not m and key in NUMERIC_FIELDS:
                    m=re.fullmatch(r'\s*('+label+r')\s+('+MONEY.pattern+r')\s*',line,re.I)
                if not m:continue
                raw=m[2].strip(' |');excerpt=line
                if key in NUMERIC_FIELDS and raw.endswith('.') and MONEY.fullmatch(raw[:-1]):raw=raw[:-1]
                if not raw and i+1<len(lines):
                    following=lines[i+1].strip()
                    if key in NUMERIC_FIELDS and MONEY.fullmatch(following) and line+'\n'+lines[i+1] in source['text']:raw=following;excerpt=line+'\n'+lines[i+1]
                if not raw or len(raw)>800:continue
                if key in NUMERIC_FIELDS and not MONEY.fullmatch(raw):continue
                rank=2 if re.search(r'inclusive|grand|invoice total|order total|total amount',m[1],re.I) else 1
                normalized=raw
                if key in NUMERIC_FIELDS:
                    number=re.search(NUMBER,raw)[0]
                    currency=CURRENCY.search(raw)
                    code=None
                    if currency:
                        word=currency[0].casefold()
                        code='USD' if word in ('us$','us dollar','us dollars','united states dollar','united states dollars') else 'PHP' if word.startswith('philippine peso') else currency[0].upper()
                    normalized=(code+' ' if code else '')+number
                candidates[key].append((rank,value(raw,source,excerpt,normalized)))
            # A currency word must actually occur; a bare dollar sign is ambiguous.
            for m in CURRENCY.finditer(line):
                raw=m[0];low=raw.casefold()
                code='USD' if low in ('us$','us dollar','us dollars','united states dollar','united states dollars') else 'PHP' if low.startswith('philippine peso') else raw.upper()
                currencies.append(value(raw,source,line,code))
            rate=re.fullmatch(r'\s*(?:Add\s*:\s*)?(\d+(?:\.\d+)?%)\s*VAT(?:\s*[:|]\s*.*)?\s*',line,re.I)
            if not rate:rate=re.fullmatch(r'\s*(?:Stated )?(?:Tax|VAT)(?: Rate)?\s*[:(]\s*(\d+(?:\.\d+)?%)\)?(?:\s*[:|].*)?\s*',line,re.I)
            if rate:candidates.setdefault('tax_rate',[]).append((1,value(rate[1],source,line)))
    for key,items in candidates.items():
        if not items:continue
        priority=max(rank for rank,_ in items);items=[v for rank,v in items if rank==priority]
        if len({v['raw'].casefold() for v in items})==1:fields[key]=items[0]
        else:fields[key]=None;flags.append('Conflicting '+key.replace('_',' ')+' values; review required')
    explicit=[]
    for source in sources:
        for line in source['text'].splitlines():
            match=re.fullmatch(r'\s*Currency\s*[:|]\s*(.+?)\s*',line,re.I)
            if match:
                explicit.extend(v for v in currencies if v['evidence'][0]['source_id']==source['id'] and v['raw']==match[1])
    chosen=explicit or currencies
    if len({v['normalized'] for v in chosen})==1:fields['currency']=chosen[0]
    elif chosen:fields['currency']=None;flags.append('Multiple currencies stated; document currency requires review')
    # A stated subtotal is not automatically a final amount including VAT.
    if fields.get('totals') and fields.get('tax_amount') and not any(rank==2 for rank,_ in candidates['totals']):
        flags.append('Total tax basis is not explicit; do not assume VAT is included')


HEADERS={
 'item':'item_number','item no':'item_number','item number':'item_number','no':'item_number',
 'part no':'part_number','part number':'part_number','pn':'part_number','p/n':'part_number',
 'description':'description','item description':'description','product':'description',
 'material':'material_specification','material specification':'material_specification','specification':'material_specification',
 'qty':'quantity','quantity':'quantity','unit':'unit','uom':'unit',
 'unit price':'unit_price','price':'unit_price','amount':'stated_line_amount','line amount':'stated_line_amount','total price':'stated_line_amount',
 'remarks':'remarks','serial number':'remarks','serial no':'remarks',
}
MATERIAL=re.compile(r'\b(?:stainless steel(?:\s*(?:304|316L?))?|SS\s*[- ]?\s*(?:304|316L?)|carbon steel|cast iron|titanium|alumin(?:i)?um|nickel alloy|alloy steel|PTFE|Teflon|Viton|Buna-N|Nitrile|ASTM\s+[A-Z]\d+(?:\s+Grade\s+[A-Z0-9]+)?|ISO\s+\d+(?:,?\s+grade\s+[0-9.]+)?)\b',re.I)


def table_rows(sources):
    for source in sources:
        loc=source.get('location',{})
        if loc.get('sheet') and loc.get('cells') and '|' in source['text']:
            parts=split_columns(source['text'])
            addresses=[re.fullmatch(r'([A-Z]+)(\d+)',c) for c in loc['cells']]
            if len(parts)==len(addresses) and all(addresses) and len({a[2] for a in addresses})==1:
                yield source,source['text'],parts,[a[1] for a in addresses]
                continue
        for line in source['text'].splitlines():yield source,line,split_columns(line),None


def line_items(sources,flags):
    from .library import ITEM_FIELDS
    items=[];header=None;scope=None;item_section=False
    for source,line,parts,positions in table_rows(sources):
        loc=source.get('location',{});current=(loc.get('sheet'),loc.get('page'),loc.get('image'))
        if current!=scope:header=None;scope=current;item_section=False
        if line.strip().upper()=='ITEMS / TECHNICAL CONTENT':item_section=True;continue
        if line.strip().upper()=='NOTES / COMMERCIAL DETAILS':item_section=False;header=None;continue
        keys=[HEADERS.get(re.sub(r'[.#:]','',p).strip().casefold()) for p in parts]
        if 'description' in keys and any(k in keys for k in ('quantity','part_number','material_specification')):
            named=[k for k in keys if k]
            if len(named)==len(set(named)):
                header=(keys,positions)
            else:header=None
            continue
        inferred=False
        if not header and item_section and len(parts) in (5,6) and re.fullmatch(r'\d+',parts[0]) and re.fullmatch(r'(?=[A-Z0-9./-]*[A-Z])(?=[A-Z0-9./-]*\d)[A-Z0-9]+[-/][A-Z0-9./-]+',parts[1],re.I):
            # Typed, unlabelled legacy exports: expose the layout inference for review.
            units=r'(?:EA|PCS?|PIECES?|SETS?|LOTS?|M|METERS?|KG|L)'
            names=None
            combined=re.fullmatch('('+NUMBER+r')\s+('+units+')',parts[3],re.I)
            if len(parts)==5 and re.fullmatch(NUMBER,parts[3]) and re.fullmatch(units,parts[4],re.I):
                names=['item_number','part_number','description','quantity','unit']
            elif len(parts)==6 and combined:
                parts=parts[:3]+[combined[1],combined[2]]+parts[4:]
                names=['item_number','part_number','description','quantity','unit','unit_price','stated_line_amount']
            elif len(parts)==6 and re.fullmatch(NUMBER,parts[4]) and re.fullmatch(units,parts[5],re.I):
                names=['item_number','part_number','description','material_specification','quantity','unit']
            if names:header=(names,None);positions=None;inferred=True
        if header:
            names,columns=header
            if positions and columns:
                cells=dict(zip(positions,parts));mapped={key:cells.get(col,'') for key,col in zip(names,columns) if key}
            elif len(parts)==len(names):mapped={key:part for key,part in zip(names,parts) if key}
            else:
                header=None;continue
            description=mapped.get('description','')
            if not description or re.match(r'^(?:sub\s*total|grand total|total|vat|tax)\b',description,re.I):header=None;continue
            if mapped.get('quantity') and not re.fullmatch(NUMBER,mapped['quantity']):header=None;continue
            # Require a visible quantity or part ID, not just prose following a header.
            if not (mapped.get('quantity') or mapped.get('part_number')):header=None;continue
            item=dict.fromkeys(ITEM_FIELDS)
            for key,raw in mapped.items():
                if not raw:continue
                if key in ('unit_price','stated_line_amount') and not MONEY.fullmatch(raw):continue
                if re.fullmatch(r'[Xx*\u2588\u25a0-]{3,}|\[.*?\]',raw):continue
                item[key]=value(raw,source,line)
            if not item.get('material_specification'):
                materials=list(MATERIAL.finditer(description))
                if len({m[0].casefold() for m in materials})==1:item['material_specification']=value(materials[0][0],source,line)
            if not item.get('part_number'):
                part=re.search(r'\b(?:PN|P/N|Part No\.?|Part Number)\s*[:#]?\s*([A-Z0-9][A-Z0-9./-]*)',description,re.I)
                if part:item['part_number']=value(part[1],source,line)
            if inferred:
                for field in item.values():
                    if field:field['flags'].append('Unlabelled item columns inferred from layout; review required')
                header=None
                if 'Unlabelled item columns require review' not in flags:flags.append('Unlabelled item columns require review')
            if not item.get('remarks'):
                serial=re.search(r'\b(?:Serial\s*(?:Number|No\.?|#)|S/N)\s*[:#]?\s*([A-Z0-9][A-Z0-9./-]*)',description,re.I)
                if serial:item['remarks']=value(serial[0],source,line)
            items.append(item)
    if not items and any(re.search(r'\b(?:Qty|Quantity|Unit Price)\b',s['text'],re.I) for s in sources):
        flags.append('Item table could not be aligned reliably; inspect saved text and original before assigning quantities or prices')
    return items


def enrich(sources,fields,flags):
    labeled_fields(sources,fields,flags)
    return line_items(sources,flags)


def material_tags(sources):
    tags=[];seen=set()
    for source in sources:
        for line in source['text'].splitlines():
            for match in MATERIAL.finditer(line):
                if match[0].casefold() in seen:continue
                seen.add(match[0].casefold())
                tags.append({'text':'Material mentioned: '+match[0], 'evidence':[{'source_id':source['id'],'excerpt':line}]})
                if len(tags)>=50:return tags
    return tags


def refresh_saved_scan(store,identity):
    """Refresh derived machine fields from existing text; preserve human reviews."""
    import json
    from .context_index import index_context
    from .library import validate
    row=store.get(identity)
    if row['model']!='deterministic-source-rules' or row['status']!='Completed' or not row['sources'] or row['prompt_version']==RULES_VERSION:return False
    output=validate(index_context(row['sources']),row['sources'])
    with store.db() as db:
        result=db.execute("UPDATE documents SET machine=?,prompt_version=? WHERE id=? AND status='Completed' AND model='deterministic-source-rules' AND sources=? AND prompt_version IS ?",
                          (json.dumps(output),RULES_VERSION,identity,json.dumps(row['sources']),row['prompt_version']))
        if result.rowcount:store.event(db,identity,row['attempt'],'Scan metadata refreshed',payload={'previous_machine':row['machine'],'rules_version':RULES_VERSION})
    return bool(result.rowcount)
