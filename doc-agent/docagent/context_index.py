"""Evidence-backed, unverified scan and saved-context fields; no model calls."""
import re
from .references import heading_category,find_references


def index_context(sources):
    from .library import FIELDS
    from .extract import find_date
    aliases={'Request For Quotation':'Request for Quotation (RFQ)','Purchase Order':'Purchase Order (PO)','Purchase Requisition':'Purchase Requisition (PR)'}
    lines=[line for s in sources for line in s['text'].splitlines()]
    heading=heading_category(lines);kind=aliases.get(heading[0],heading[0]) if heading else 'Unknown'
    evidence=[]
    if heading:
        source=next((s for s in sources if heading[2] in s['text']),None)
        if source:evidence=[{'source_id':source['id'],'excerpt':heading[2]}]
    fields=dict.fromkeys(FIELDS)
    flags=['Indexed with deterministic rules only. Structured fields are unverified and may be incomplete.']
    refs=find_references(sources,kind)
    if len(refs)==1:fields['document_number']=refs[0]
    elif len(refs)>1:flags.append('Conflicting references; review required')
    dt=find_date(lines,{})
    if dt:
        source=next((s for s in sources if dt.raw in s['text']),None)
        if source:fields['document_date']={'raw':dt.raw,'normalized':dt.value.isoformat(),'evidence':[{'source_id':source['id'],'excerpt':dt.raw}],'flags':['Ambiguous date'] if dt.ambiguous else []}
    from .procurement import enrich,material_tags
    items=enrich(sources,fields,flags) if kind!='Mixed document' else []
    if kind=='Mixed document':flags.append('Mixed document: procurement fields require per-document review')
    return {'category':kind,'category_evidence':evidence,'segments':[{'category':kind,'category_evidence':evidence,'fields':fields,'line_items':items}], 'summary':[],'tags':material_tags(sources),'quality_flags':flags}
