"""Persistent chats and constrained, evidence-backed library operations."""
import json
import re
import time
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .answers import answer, casual_intent, validate_answer

CATEGORY_NAMES = {
 'RFQ':'Request for Quotation (RFQ)', 'PO':'Purchase Order (PO)', 'PR':'Purchase Requisition (PR)',
 'Invoice':'Invoice', 'Quotation':'Quotation', 'Design Document':'Design Document',
 'BOM':'Bill of Materials (BOM)', 'Mixed document':'Mixed document', 'Unknown':'Unknown'}


def category_in(text):
    text=re.sub(r'\b(?:RFQ|PO|PR|INV|QT|QTN)[-#/]?(?=[A-Z0-9./-]*\d)[A-Z0-9][A-Z0-9./-]*\b','',text,flags=re.I)
    for pattern, name in [(r'\b(?:purchase orders?|pos?|purchasing orders?)\b','PO'),
                           (r'\b(?:rfqs?|requests? for quot(?:ation|e))\b','RFQ'),
                           (r'\b(?:prs?|purchase requisitions?|purchase requests?)\b','PR'),
                           (r'\binvoices?\b','Invoice'), (r'\b(?:quotations?|quotes?)\b','Quotation'),
                           (r'\b(?:boms?|bills? of materials?)\b','BOM'),
                           (r'\b(?:design documents?|drawings?|specifications?)\b','Design Document'),
                           (r'\bmixed\b','Mixed document'), (r'\bunknown\b','Unknown')]:
        if re.search(pattern,text,re.I):return CATEGORY_NAMES[name]


def field_value(field):
    return (field.get('normalized') or field.get('raw')) if isinstance(field,dict) else None


class Documents:
    """Typed operations. Models never supply executable code, SQL, or file paths."""
    def __init__(self,store): self.store=store

    def records(self):
        records=[]
        for item in self.store.search():
            row=self.store.get(item['id'])
            output=row['reviewed'] or row['machine'] or {}
            upload=next((h['at'] for h in row['history'] if h['event']=='Queued'),row['updated'])
            segments=output.get('segments',[])
            if not segments and row['sources']:
                segments=[{'category':'Unknown','category_evidence':[],'fields':{},'line_items':[]}]
            for index,segment in enumerate(segments):
                records.append({'key':row['id']+':'+str(index),'id':row['id'],'index':index,
                                'row':row,'segment':segment,'uploaded':upload})
        return records

    def get_document(self,document_id):
        if not isinstance(document_id,str) or not re.fullmatch(r'[0-9a-f]{32}',document_id):raise ValueError('Invalid document ID')
        return self.store.get(document_id)

    def resolve(self,keys):
        lookup={r['key']:r for r in self.records()}
        return [lookup[k] for k in keys if k in lookup]

    def search_documents(self,query='',filters=None):
        filters=filters or {}
        if set(filters)-{'category','review_status','date_from','date_to','supplier','buyer','company','amount_min','amount_max','currency','due_before','due_after'}:raise ValueError('Unsupported filter')
        rows=self.records()
        if filters.get('category'): rows=[r for r in rows if r['segment']['category']==filters['category']]
        if filters.get('review_status'): rows=[r for r in rows if r['row']['review_status']==filters['review_status']]
        for key in ('date_from','date_to'):
            if filters.get(key):
                date.fromisoformat(filters[key])
                rows=[r for r in rows if self.document_date(r) and (self.document_date(r)>=filters[key] if key=='date_from' else self.document_date(r)<=filters[key])]
        for role in ('supplier','buyer'):
            if filters.get(role):rows=[r for r in rows if filters[role].casefold() in str(field_value(r['segment']['fields'].get(role)) or '').casefold()]
        if filters.get('company'):
            rows=[r for r in rows if any(filters['company'].casefold() in str(field_value(r['segment']['fields'].get(k)) or '').casefold() for k in ('buyer','supplier','customer','issuing_organization','recipient_organization'))]
        for key in ('due_before','due_after'):
            if filters.get(key):
                bound=date.fromisoformat(filters[key]).isoformat();matched=[]
                for r in rows:
                    value=r['segment']['fields'].get('due_date')
                    if not value or value.get('flags'):continue
                    try:due=date.fromisoformat(field_value(value)).isoformat()
                    except (ValueError,TypeError):continue
                    if due<=bound if key=='due_before' else due>=bound:matched.append(r)
                rows=matched
        if any(k in filters for k in ('amount_min','amount_max','currency')):
            matched=[]
            for r in rows:
                value=self.get_document_amount(r)
                if value is None:continue
                if filters.get('currency') and value['currency']!=filters['currency'].upper():continue
                if 'amount_min' in filters and value['amount']<=Decimal(str(filters['amount_min'])):continue
                if 'amount_max' in filters and value['amount']>=Decimal(str(filters['amount_max'])):continue
                matched.append(r)
            rows=matched
        # Exact identifier matching is separate from text ranking; punctuation is retained.
        exact=[]
        for r in rows:
            ref=r['segment']['fields'].get('document_number')
            values=[ref.get(k) for k in ('raw','normalized')] if ref else []
            if any(v and re.search(r'(?<!\w)'+re.escape(v)+r'(?!\w)',query,re.I) for v in values):exact.append(r)
        if exact:return exact
        terms=set(re.findall(r'\w+',query.casefold()))-{'show','find','my','the','a','which','documents','document','mention','mentions','please','me'}
        if not terms:return rows
        ranked=[]
        for r in rows:
            # Include reviewed metadata and OCR for material/part queries.
            text=(r['row']['filename']+' '+json.dumps(r['segment'])+' '+r['row']['search_text']).casefold()
            score=sum(1 for term in terms if re.search(r'\b'+re.escape(term)+r'\b',text))
            if score==len(terms):ranked.append((score,r))
        return [r for _,r in ranked]

    def document_date(self,record):
        fields=record['segment']['fields']
        value=fields.get('document_date') or fields.get('invoice_date')
        if not value or value.get('flags'):return None
        try:return date.fromisoformat(field_value(value)).isoformat()
        except (ValueError,TypeError):return None

    def get_latest_documents(self,category=None,limit=5,filters=None,uploaded=False):
        if not 1<=limit<=20:raise ValueError('Limit must be 1–20')
        filters=dict(filters or {})
        if category:filters['category']=category
        rows=self.search_documents('',filters)
        excluded=0
        if not uploaded:
            dated=[r for r in rows if self.document_date(r)]
            excluded=len(rows)-len(dated);rows=dated
        rows.sort(key=lambda r:r['uploaded'] if uploaded else self.document_date(r),reverse=True)
        tied=len(rows)>limit and (rows[limit-1]['uploaded'] if uploaded else self.document_date(rows[limit-1]))==(rows[limit]['uploaded'] if uploaded else self.document_date(rows[limit]))
        # Retain ties for clarification rather than arbitrarily selecting a winner.
        if tied:
            boundary=rows[limit-1]['uploaded'] if uploaded else self.document_date(rows[limit-1])
            chosen=[r for r in rows if (r['uploaded'] if uploaded else self.document_date(r))>=boundary]
        else:chosen=rows[:limit]
        return chosen,excluded,tied

    def references(self,record,field=None):
        row=record['row']
        evidence=field.get('evidence',[]) if field else record['segment'].get('category_evidence',[])
        sources=[dict(s,filename=row['filename'],review_status=row['review_status'],processing_status=row['status']) for s in row['sources'] or []]
        if not evidence:return []
        try:return validate_answer({'claims':[{'text':'Evidence','evidence':evidence}]},sources)['claims'][0]['references']
        except (ValueError,KeyError,TypeError):return []

    def get_source_excerpt(self,document_id,source_id):
        row=self.get_document(document_id)
        if not Path(row['path']).is_file():raise ValueError('Original file unavailable')
        source=next((s for s in row['sources'] or [] if s['id']==source_id),None)
        if source is None:raise ValueError('Source unavailable')
        return source

    def label(self,r):
        number=field_value(r['segment']['fields'].get('document_number')) or r['row']['filename']
        state='Verified' if r['row']['reviewed'] and r['row']['review_status']=='Verified' else ('Needs clarification' if r['row']['review_status']=='Needs clarification' else 'Unverified')
        return str(number)+' ('+state+')'

    def get_document_amount(self,record):
        fields=record['segment']['fields'];value=fields.get('totals');currency=fields.get('currency')
        if not value or value.get('flags') or not self.references(record,value):return None
        text=field_value(value)
        # Refuse composite subtotal/tax/total strings, unknown currencies, and ambiguous separators.
        match=re.fullmatch(r'\s*(?:(USD|PHP|EUR|GBP|JPY)\s*)?(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)\s*(USD|PHP|EUR|GBP|JPY)?\s*',text or '')
        if not match:return None
        currency_value=field_value(currency) if currency else None
        currency_code=currency_value.upper().strip() if isinstance(currency_value,str) else None
        stated=[c for c in (match[1],match[3],currency_code) if c]
        if not stated or len(set(stated))!=1 or currency and (currency.get('flags') or not self.references(record,currency)):return None
        try:amount=Decimal(match[2].replace(',',''))
        except InvalidOperation:return None
        return {'amount':amount,'currency':stated[0],'references':self.references(record,value)+(self.references(record,currency) if currency else [])}

    def compare_documents(self,records,fields):
        allowed={'totals','supplier','buyer','document_date','revision','stated_status','document_number'}
        if not set(fields)<=allowed or not 2<=len(records)<=10:raise ValueError('Choose 2–10 records and supported fields')
        claims=[]
        for record in records:
            for name in fields:
                value=record['segment']['fields'].get(name)
                refs=self.references(record,value) if value else []
                display={'totals':'stated total','document_date':'document date','stated_status':'stated status'}.get(name,name)
                claims.append({'text':'For '+self.label(record)+', the '+display+' is '+(str(field_value(value)) if refs else 'not established')+'.', 'references':refs})
        amounts=[self.get_document_amount(r) for r in records]
        if len(records)==2 and all(amounts) and amounts[0]['currency']==amounts[1]['currency']:
            claims.append({'text':'First minus second stated total: '+amounts[0]['currency']+' '+str(amounts[0]['amount']-amounts[1]['amount'])+'. Calculated from the cited stated totals; no currency conversion.', 'references':amounts[0]['references']+amounts[1]['references']})
        else:claims.append({'text':'No combined total or difference calculated: amounts may be missing, ambiguous, or in different currencies.','references':[]})
        return claims


class Chats:
    def __init__(self,store):
        self.store=store;self.docs=Documents(store)
        with store.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY,title TEXT,created REAL,updated REAL,state TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS chat_turns (id TEXT PRIMARY KEY,chat_id TEXT NOT NULL,request_id TEXT NOT NULL,question TEXT NOT NULL,response TEXT,status TEXT NOT NULL,created REAL,UNIQUE(chat_id,request_id));
            CREATE INDEX IF NOT EXISTS chat_turn_order ON chat_turns(chat_id,created);''')

    def recover(self):
        with self.store.db() as db:db.execute("UPDATE chat_turns SET status='error' WHERE status='pending'")

    def create(self):
        identity=uuid.uuid4().hex;now=time.time()
        with self.store.db() as db:db.execute('INSERT INTO chats VALUES (?,?,?,?,?)',(identity,'New conversation',now,now,'[]'))
        return self.get(identity)

    def list(self):
        with self.store.db() as db:return [dict(r) for r in db.execute('SELECT id,title,created,updated FROM chats ORDER BY updated DESC')]

    def get(self,identity):
        with self.store.db() as db:
            row=db.execute('SELECT * FROM chats WHERE id=?',(identity,)).fetchone()
            if row is None:raise KeyError(identity)
            result=dict(row);result['state']=json.loads(row['state'])
            turns=[dict(t) for t in db.execute('SELECT * FROM chat_turns WHERE chat_id=? ORDER BY created,id',(identity,))]
        for turn in turns:
            turn['response']=json.loads(turn['response']) if turn['response'] else None
            # Revalidate availability on reopen. Never keep a clickable deleted file.
            for claim in (turn['response'] or {}).get('claims',[]):
                for ref in claim.get('references',[]):
                    try:
                        source=self.docs.get_source_excerpt(ref['document_id'],ref['source_id'])
                        if source['version']!=ref['version']:raise ValueError('Version changed')
                    except (KeyError,ValueError):ref['unavailable']=True;ref['url']=None
        result['turns']=turns
        return result

    def grounded(self,question,state,recent):
        from .answers import retrieve
        ids=list(dict.fromkeys(r['id'] for r in self.docs.resolve(state))) if state else None
        history=[]
        for turn in recent[-3:]:
            history.append({'role':'user','content':turn['question'][:350]})
            response=turn.get('response') or {}
            text=' '.join([response.get('message','')]+[c['text'] for c in response.get('claims',[])])
            if text:history.append({'role':'assistant','content':text[:650]})
        try:result=answer(self.store,question,recent=history,document_ids=ids)
        except RuntimeError as exc:
            sources=retrieve(self.store,question,ids)
            result={'claims':[],'message':'The local model is unavailable or could not produce validated citations. '+str(exc)+'. Here are matching source excerpts; no answer has been inferred.'}
            for s in sources[:3]:
                excerpt=s['text'][:600]
                result['claims']+=validate_answer({'claims':[{'text':'Source excerpt','evidence':[{'source_id':s['id'],'excerpt':excerpt}]}]},[s])['claims']
        mentioned={ref['document_id'] for claim in result.get('claims',[]) for ref in claim.get('references',[])}
        keys=[r['key'] for r in self.docs.records() if r['id'] in mentioned]
        return result,keys or state

    def delete(self,identity):
        self.get(identity)
        with self.store.db() as db:
            db.execute('DELETE FROM chat_turns WHERE chat_id=?',(identity,))
            db.execute('DELETE FROM chats WHERE id=?',(identity,))

    def send(self,identity,question,request_id):
        question=question.strip()
        if not question or len(question)>2000 or not re.fullmatch(r'[A-Za-z0-9-]{1,80}',request_id):raise ValueError('Invalid question or request ID')
        chat=self.get(identity)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT * FROM chat_turns WHERE chat_id=? AND request_id=?',(identity,request_id)).fetchone()
            if existing and existing['question']!=question:raise ValueError('Request ID already used for another question')
            if existing and existing['status']=='completed':return self.get(identity)
            if existing and existing['status']=='pending':raise ValueError('This message is already processing')
            if db.execute("SELECT 1 FROM chat_turns WHERE chat_id=? AND status='pending'",(identity,)).fetchone():raise ValueError('Wait for the previous message')
            if existing:db.execute("UPDATE chat_turns SET status='pending' WHERE id=?",(existing['id'],))
            else:db.execute('INSERT INTO chat_turns VALUES (?,?,?,?,?,?,?)',(uuid.uuid4().hex,identity,request_id,question,None,'pending',time.time()))
        try:
            result,state=self.respond(question,chat['state'],chat['turns'][-6:])
            result['document_keys']=state
            with self.store.db() as db:
                db.execute("UPDATE chat_turns SET response=?,status='completed' WHERE chat_id=? AND request_id=?",(json.dumps(result),identity,request_id))
                db.execute('UPDATE chats SET state=?,title=CASE WHEN title=? THEN ? ELSE title END,updated=? WHERE id=?',(json.dumps(state[:20]),'New conversation',question[:65],time.time(),identity))
        except Exception:
            with self.store.db() as db:db.execute("UPDATE chat_turns SET status='error' WHERE chat_id=? AND request_id=?",(identity,request_id))
            raise
        return self.get(identity)

    def respond(self,question,state,recent):
        docs=self.docs;low=question.casefold();category=category_in(question)
        response={'claims':[]};selected=[];note=''
        if re.fullmatch(r'(?:what did i (?:ask|say)|what was my last question)[?.! ]*',low):
            return {'claims':[],'message':('Your previous message was: '+recent[-1]['question'] if recent else 'This is the first message in this conversation.'),'scope':'Conversation history, not document evidence.'},state
        if re.fullmatch(r'(?:what did you (?:say|answer)|repeat your last (?:answer|response))[?.! ]*',low):
            previous=next((t.get('response') for t in reversed(recent) if t.get('response')),None)
            return (dict(previous) if previous else {'claims':[],'message':'There is no earlier answer in this conversation.'}),state
        filters={}
        if category:filters['category']=category
        company_for=re.search(r'\bfor\s+([A-Z][\w &.-]+)\s*[?.]?$',question)
        if category and company_for:filters['company']=company_for[1].strip(' .?')
        company=re.search(r'\b(?:from|supplier|vendor)\s+([\w &.\x27-]+?)(?=\s+(?:over|under|before|after|dated|due|with|and|by)\b|[?]|$)',question,re.I)
        if company and not re.fullmatch(r'\d{4}-\d{2}-\d{2}',company[1].strip()):filters['supplier']=company[1].strip(' .')
        for word,key in [('over','amount_min'),('above','amount_min'),('under','amount_max'),('below','amount_max')]:
            match=re.search(r'\b'+word+r'\s+(?:(PHP|USD|EUR|GBP|JPY)\s*)?(\d[\d,]*(?:\.\d{1,2})?)',question,re.I)
            if match:
                if not match[1]:return {'claims':[],'message':'Which currency should I use for the amount filter? For example, “over PHP 5000”.'},state
                filters[key]=str(Decimal(match[2].replace(',','')));filters['currency']=match[1].upper()
        for word,key in [('before','date_to'),('after','date_from'),('since','date_from')]:
            match=re.search(r'\b'+word+r'\s+(\d{4}-\d{2}-\d{2})',question,re.I)
            if match:filters[('due_before' if key=='date_to' else 'due_after') if 'due' in low else key]=match[1]
        if re.search(r'\bverified\b',low) and not re.search(r'\bunverified\b',low):filters['review_status']='Verified'
        advanced=len(filters)>(1 if category else 0)
        if casual_intent(question):return answer(self.store,question),state
        if re.search(r'\bhow many\b|\bcount\b',low):
            if not category or re.search(r'\b(mention|contain|related|reference)\b',low):
                return {'claims':[],'message':'For an exact count, specify a document type and optional company/date filters. For content matches, ask “Which documents mention …?” first.'},state
            records=docs.search_documents('',filters)
            return {'claims':[{'text':docs.label(r),'references':docs.references(r,r['segment']['fields'].get('document_number'))} for r in records],
                    'message':'I found '+str(len(records))+' matching indexed business records. This is a deterministic count of records with established metadata; mixed documents can contain multiple records.'},[r['key'] for r in records[:20]]
        relation=re.search(r'\b(?:documents|files)\s+(?:reference|refer to)\s+([\w./-]+)',question,re.I)
        if relation:
            target=relation[1].rstrip('?.');claims=[];keys=[]
            for r in docs.records():
                for name in ('referenced_identifiers','purchase_order_reference','rfq_reference','referenced_quotation'):
                    value=r['segment']['fields'].get(name)
                    if value and re.search(r'(?<!\w)'+re.escape(target)+r'(?!\w)',str(field_value(value)),re.I):
                        refs=docs.references(r,value)
                        if refs:claims.append({'text':docs.label(r)+' explicitly references '+target+'.','references':refs});keys.append(r['key']);break
            return {'claims':claims,'message':('These relationships are explicitly recorded in extracted reference fields.' if claims else 'I could not establish an explicit document relationship from indexed reference fields. Try a text search for the reference; a textual mention alone does not prove a business relationship.')},keys
        if re.search(r'\b(?:documents|files)\s+(?:require|need)\s+review\b',low):
            rows=[r for r in docs.records() if r['row']['review_status']!='Verified']
            claims=[{'text':docs.label(r)+' requires review.','references':docs.references(r,r['segment']['fields'].get('document_number'))} for r in rows]
            return {'claims':claims,'message':str(len(rows))+' indexed records require review. This count includes separate records in mixed documents.'},[r['key'] for r in rows[:20]]
        mention=re.search(r'\b(?:which|what|find|show|list)\s+(?:documents|files)\s+(?:mention|contain|include)\s+(.+)',question,re.I)
        if mention:
            phrase=mention[1].strip(' ?.!');claims=[];keys=[];seen=set();matches=[]
            pattern=re.compile(r'(?<!\w)'+r'\s+'.join(re.escape(t) for t in phrase.split())+r'(?!\w)',re.I)
            for r in docs.records():
                if r['id'] in seen:continue
                seen.add(r['id']);count=0;refs=[];locations=[]
                for source in r['row']['sources'] or []:
                    hits=list(pattern.finditer(source['text'])) if phrase else []
                    if hits:
                        count+=len(hits);locations.append({'source_id':source['id'],'location':source['location'],'count':len(hits)})
                        start=max(0,hits[0].start()-120)
                        excerpt=source['text'][start:start+650]
                        canonical=dict(source,filename=r['row']['filename'],review_status=r['row']['review_status'],processing_status=r['row']['status'])
                        refs+=validate_answer({'claims':[{'text':'Matching passage','evidence':[{'source_id':source['id'],'excerpt':excerpt}]}]},[canonical])['claims'][0]['references']
                if count:matches.append({'record':r,'count':count,'references':refs,'locations':locations})
            matches.sort(key=lambda item:(-item['count'],item['record']['row']['filename'].casefold()))
            for item in matches[:3]:
                r=item['record'];keys.append(r['key'])
                claims.append({'text':'“'+phrase+'” appears '+str(item['count'])+' '+('time' if item['count']==1 else 'times')+' in '+r['row']['filename']+'.','references':item['references'][:3]})
            return {'claims':claims,'message':('Found in '+str(len(matches))+' documents. Showing the top '+str(min(3,len(matches)))+' by occurrence count.' if claims else 'I found no saved text containing that exact phrase. Unreadable pages may contain additional matches.'),
                    'matches':[{'document_id':i['record']['id'],'count':i['count'],'locations':i['locations']} for i in matches[:3]],'scope':'Counts cover exact phrase matches within saved indexed passages. Matches spanning passage boundaries or unreadable pages may be missed.'},keys
        if re.search(r'\b(list|show|what|which)\b',low) and re.search(r'\b(available|uploaded|saved|your|my)\b',low) and re.search(r'\b(documents|files)\b',low) and not category and not re.search(r'\b(mention|contain|before|after|latest)\b',low):
            rows=docs.records()[:20]
            if not rows:return {'claims':[],'message':'No saved document text is available yet. Upload files in Documents or scan a connected source folder first.'},state
            response={'claims':[],'message':'Here are the saved documents I can reference. Structured details may still be processing.'}
            for index,r in enumerate(rows):
                sources=r['row']['sources'] or []
                refs=[]
                if sources:
                    s=dict(sources[0],filename=r['row']['filename'],review_status=r['row']['review_status'],processing_status=r['row']['status'])
                    excerpt=s['text'][:400]
                    refs=validate_answer({'claims':[{'text':'Source','evidence':[{'source_id':s['id'],'excerpt':excerpt}]}]},[s])['claims'][0]['references']
                response['claims'].append({'text':str(index+1)+'. '+r['row']['filename'],'references':refs})
            return response,[r['key'] for r in rows]
        explicit=[]
        for r in docs.records():
            value=r['segment']['fields'].get('document_number')
            if value and any(v and re.search(r'(?<!\w)'+re.escape(v)+r'(?!\w)',question,re.I) for v in (value['raw'],value['normalized'])):explicit.append(r)
        latest=bool(re.search(r'\b(latest|newest|most recent(?:ly)?|previous)\b',low))
        compare='compare' in low or 'difference' in low
        amount=bool(re.search(r'\b(amounts?|totals?|costs?|how much|prices?)\b',low))
        supplier=bool(re.search(r'\b(supplier|vendor)\b',low))
        aggregate=amount and bool(re.search(r'\b(all|sum|combined)\b',low)) and not latest
        follow=bool(re.search(r'\b(it|that|those|them|second|first|third|previous|these|this|same|above)\b',low))
        if state and re.search(r'^(?:and |also |what about |how about |can you (?:explain|summarize|expand)|tell me more)',low):follow=True
        if state and not category and (amount or supplier or re.search(r'\b(buyer|customer|revision|status|date)\b',low)):
            follow=True
        if explicit and category and all(r['segment']['category']!=category for r in explicit):
            wanted=[field_value(r['segment']['fields'].get('document_number')) for r in explicit]
            related=[]
            for r in docs.search_documents('',filters):
                fields=r['segment']['fields']
                values=[str(field_value(fields.get(k)) or '') for k in ('referenced_identifiers','purchase_order_reference','rfq_reference','referenced_quotation')]
                if any(number and re.search(r'(?<!\w)'+re.escape(number)+r'(?!\w)',value,re.I) for number in wanted for value in values):related.append(r)
            if not related:return {'claims':[],'message':'I could not establish that relationship from the extracted document references. It may still appear in the original text.'},state
            selected=related;note='These records explicitly reference the requested document; the relationship is not inferred from filenames.'
        elif explicit:
            selected=explicit
            if latest:
                uploaded='upload' in low
                dated=selected if uploaded else [r for r in selected if docs.document_date(r)]
                excluded=len(selected)-len(dated)
                dated.sort(key=lambda r:r['uploaded'] if uploaded else docs.document_date(r),reverse=True)
                if dated:
                    newest=dated[0]['uploaded'] if uploaded else docs.document_date(dated[0])
                    selected=[r for r in dated if (r['uploaded'] if uploaded else docs.document_date(r))==newest]
                else:selected=[]
                note=('Latest means upload time.' if uploaded else 'Latest means document date; '+str(excluded)+' undated or ambiguous-date records excluded.')
                if len(selected)>1:note+=' Multiple equally dated versions match; choose a record. Revision precedence is not inferred.'
        elif aggregate:selected=docs.search_documents('',filters)
        elif latest:
            if compare and state:
                prior=docs.resolve(state[:1]);category=category or (prior[0]['segment']['category'] if prior else None)
            number=re.search(r'\b(\d{1,2}|five|two|three|ten)\b',low)
            count={'five':5,'two':2,'three':3,'ten':10}.get(number[1],int(number[1]) if number and number[1].isdigit() else 1) if number else 1
            count=min(20,max(1,count))
            if compare:count=max(2,count)
            selected,excluded,tied=docs.get_latest_documents(category,count,filters,uploaded='upload' in low)
            note=('Latest means upload time.' if 'upload' in low else 'Latest means document date; '+str(excluded)+' undated or ambiguous-date records excluded.')
            if tied:note+=' Dates tie; no revision or approval precedence is inferred.'
            if compare and state:
                prior=docs.resolve(state[:1])
                if prior:
                    current_date=docs.document_date(prior[0])
                    older=[r for r in docs.search_documents('',{'category':category} if category else {}) if docs.document_date(r) and current_date and docs.document_date(r)<current_date]
                    older.sort(key=lambda r:docs.document_date(r),reverse=True)
                    if older:
                        closest=docs.document_date(older[0]);older=[r for r in older if docs.document_date(r)==closest]
                    if len(older)>1:return {'claims':[],'message':'Several previous records have the same document date. Choose a reference before comparing.'},[prior[0]['key']]+[r['key'] for r in older[:19]]
                    selected=prior+older[:1]
        elif advanced:
            selected=docs.search_documents('',filters)
            note='Applied structured filters. Records with missing or ambiguous required fields are excluded; dates use YYYY-MM-DD and bounds are inclusive.'
            if not selected:return {'claims':[],'message':'I couldn’t find records with established values matching those filters. Missing or ambiguous values are excluded.'},state
        elif follow:
            selected=docs.resolve(state)
            if state and not selected:return {'claims':[],'message':'The previously discussed documents are unavailable. Please choose another Library document.'},[]
            ordinal=next((i for word,i in [('first',0),('second',1),('third',2)] if word in low),None)
            if ordinal is not None:
                if ordinal>=len(selected):return {'claims':[],'message':'There is no such result in the previous list. Which document do you mean?'},state
                selected=[selected[ordinal]]
        if not selected and (latest or follow):return {'claims':[],'message':'No matching dated records found. '+note},state
        if not selected:
            query=re.sub(r'(?i)^(?:find|show|which documents mention)\s+','',question).strip(' ?.');selected=docs.search_documents(query,{'category':category} if category else {})[:10]
        if not selected:
            if category:
                scoped=[r['key'] for r in docs.search_documents('',filters)]
                if not scoped:return {'claims':[],'message':'No indexed records with established metadata match that document type or company.'},state
                return self.grounded(question,scoped,recent)
            return self.grounded(question,state,recent)
        keys=[r['key'] for r in selected]
        listing=(bool(re.search(r'\b(show|find|list|which)\b',low)) or bool(category and re.search(r'\bdo we have\b',low))) and (advanced or bool(category))
        if compare and len(selected)>10:return {'claims':[],'message':'Choose two document references or specific results to compare.'},keys[:20]
        if aggregate:
            seen=set();groups={};missing=0
            for r in selected:
                ref=field_value(r['segment']['fields'].get('document_number'))
                if ref and (r['segment']['category'],ref) in seen:
                    return {'claims':[],'message':'Multiple records share a reference (possibly revisions). Choose which versions to include before calculating a total.'},keys[:20]
                if ref:seen.add((r['segment']['category'],ref))
                value=docs.get_document_amount(r)
                if value is None:missing+=1;continue
                group=groups.setdefault(value['currency'],{'total':Decimal('0'),'refs':[],'records':[]})
                group['total']+=value['amount'];group['refs']+=value['references'];group['records'].append(docs.label(r))
            response['message']=str(missing)+' records excluded because amount or currency is missing, ambiguous, or unsupported. Currency groups are separate; unknown amounts were not treated as zero.'
            for currency,group in groups.items():response['claims'].append({'text':'Calculated '+currency+' total: '+str(group['total'])+' from '+', '.join(group['records']), 'references':group['refs']})
            return response,keys[:20]
        fact_query=amount or supplier or bool(re.search(r'\b(buyer|customer|revision|status|date)\b',low))
        if not fact_query and not compare and not latest and not listing and not re.search(r'\b(show|find|list|open|first|second|third)\b',low):
            if len(selected)>1 and follow:return {'claims':[],'message':'Which document do you mean? Give its reference or say the first or second one.'},keys
            return self.grounded(question,keys,recent)
        if (amount or supplier or re.search(r'\b(buyer|customer|revision|status|date)\b',low)) and len(selected)>1 and not compare and not listing and not re.search(r'\b(each|all|those|them)\b',low):
            response['message']='Several records match. Which one do you mean? Say “the first one” or give a reference. '+note
        elif compare and len(selected)<2:response['message']='Please select two records to compare.'
        elif compare:response['claims']=docs.compare_documents(selected,['document_date','supplier','totals','revision','stated_status'])
        elif amount:
            for r in selected:
                value=docs.get_document_amount(r)
                response['claims'].append({'text':('The stated total for '+docs.label(r)+' is '+value['currency']+' '+str(value['amount'])+'.' if value else 'I couldn’t establish an unambiguous total and currency for '+docs.label(r)+'.'),'references':value['references'] if value else docs.references(r,r['segment']['fields'].get('totals'))})
        elif (supplier or re.search(r'\b(buyer|customer|revision|status|date)\b',low)) and not latest and not listing:
            name='supplier' if supplier else next(k for word,k in [('buyer','buyer'),('customer','customer'),('revision','revision'),('status','stated_status'),('date','document_date')] if word in low)
            for r in selected:
                field=r['segment']['fields'].get(name);refs=docs.references(r,field) if field else []
                display={'document_date':'document date','stated_status':'stated status'}.get(name,name)
                response['claims'].append({'text':('The '+display+' for '+docs.label(r)+' is '+str(field_value(field))+'.' if refs else 'I couldn’t establish the '+display+' for '+docs.label(r)+'.')+(' Please review: '+', '.join(field.get('flags',[])) if field and field.get('flags') else ''),'references':refs})
        else:
            grouped=[];seen_documents=set()
            for record in selected:
                if record['id'] not in seen_documents:grouped.append(record);seen_documents.add(record['id'])
            total_documents=len(grouped);selected=grouped[:3];keys=[r['key'] for r in selected]
            response['message']='Showing '+str(len(selected))+' of '+str(total_documents)+' matching documents (one entry per file).'
            for i,r in enumerate(selected):
                number=r['segment']['fields'].get('document_number');dt=r['segment']['fields'].get('document_date')
                refs=docs.references(r)+(docs.references(r,number) if number else [])+(docs.references(r,dt) if dt else [])
                extra=''
                for key in ('supplier','buyer','due_date','totals','currency','referenced_identifiers','purchase_order_reference','rfq_reference','referenced_quotation'):
                    value=r['segment']['fields'].get(key)
                    include=key in filters or key=='due_date' and ('due_before' in filters or 'due_after' in filters) or key in ('totals','currency') and any(k in filters for k in ('amount_min','amount_max','currency')) or key in ('referenced_identifiers','purchase_order_reference','rfq_reference','referenced_quotation') and explicit
                    if value and include:
                        refs+=docs.references(r,value)
                        extra+=' '+key.replace('_',' ').capitalize()+': '+str(field_value(value))+'.'
                response['claims'].append({'text':str(i+1)+'. '+docs.label(r)+' is a '+r['segment']['category'].lower()+(' dated '+docs.document_date(r)+'.' if docs.document_date(r) else '; its document date is not established.')+extra,'references':refs})
        if response.get('message','').startswith('Several'):
            for i,r in enumerate(selected):response['claims'].append({'text':str(i+1)+'. '+docs.label(r),'references':docs.references(r,r['segment']['fields'].get('document_number'))})
        response['scope']=note or 'Processed Library records only. Verification status is shown per record; no revision or approval precedence is inferred.'
        return response,keys
