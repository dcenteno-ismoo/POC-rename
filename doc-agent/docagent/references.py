"""Content-based document identifiers; never invent a missing reference."""
import re
import json
import os
from pathlib import Path





def apply_references(output, sources):
    for segment in output['segments']:
        ids={ref['source_id'] for ref in segment['category_evidence']}
        scoped=[s for s in sources if s['id'] in ids]
        candidates=find_references(scoped,segment['category'])
        if len(candidates)==1:
            segment['fields']['document_number']=candidates[0]
            if segment['category']=='Invoice': segment['fields']['invoice_number']=candidates[0]
        elif len(candidates)>1:
            segment['fields']['document_number']=None
            if segment['category']=='Invoice': segment['fields']['invoice_number']=None
            output['quality_flags'].append('Multiple '+segment['category']+' reference numbers found; review document number')
    return output


def rules():
    path=Path(os.environ.get('DOCAGENT_REFERENCE_RULES',Path(__file__).resolve().parents[1]/'reference-rules.json'))
    return json.loads(path.read_text('utf-8'))


def heading_category(lines):
    matches=[]
    configured=rules()['rules']
    for index,line in enumerate(lines):
        title=' '.join(line.strip(' :\t').upper().split())
        for rule in configured:
            if title in rule['headings']:matches.append((rule['category'],index,line))
    if len({m[0] for m in matches})>1:return ('Mixed document',matches[0][1],matches[0][2])
    return matches[0] if matches else None


def find_references(sources,category):
    rule=next((r for r in rules()['rules'] if category==r['category'] or category in r['aliases']),None)
    if rule is None:return []
    labels='|'.join(re.escape(v) for v in sorted(rule['labels'],key=len,reverse=True))
    pattern=re.compile(r'^\s*(?:'+labels+r')\s*(?:number|no\.?|#|ref(?:erence)?\.?|id)\s*[:#|=\-]?\s*(.*)$',re.I)
    found=[]
    for source in sources:
        lines=source['text'].splitlines()
        for index,line in enumerate(lines):
            match=pattern.match(line)
            raw=None;excerpt=line
            if match:
                raw=match.group(1).strip(' \t:|=#')
                if not raw and index+1<len(lines):
                    following=lines[index+1].strip()
                    if not re.search(r'[:=|]',following):raw=following;excerpt=line+'\n'+lines[index+1]
            elif rule['prefixes']:
                token=re.fullmatch(r'\s*((?:'+ '|'.join(rule['prefixes'])+r')[-/]?[A-Z0-9][A-Z0-9./-]*)\s*',line,re.I)
                if token:raw=token.group(1)
            if not raw:continue
            raw=re.split(r'\s*\|\s*|\s+(?:Revision|Rev\.?|Sheet|Date)\s*[:#]',raw,flags=re.I)[0].strip()
            if len(raw)>120 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ./_-]*',raw):continue
            if not any(c.isdigit() for c in raw) and not re.search(r'[A-Za-z][./-][A-Za-z0-9]',raw):continue
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}',raw):continue
            if raw not in excerpt or len(excerpt)>800:continue
            flags=[]
            if re.search(r'(?<=\d)[OI](?=\d)|(?<=[-/])[OI](?=\d)',raw,re.I):flags.append('Possible OCR O/0 or I/1 ambiguity; original preserved')
            value={'raw':raw,'normalized':raw.strip(),'evidence':[{'source_id':source['id'],'excerpt':excerpt}],'flags':flags}
            if not any(v['normalized']==value['normalized'] for v in found):found.append(value)
    if len(found)>1:
        for value in found:value['flags'].append('Multiple candidate reference numbers; needs review')
    return found
