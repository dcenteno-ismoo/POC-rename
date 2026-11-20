import copy
import io
import os
from contextlib import closing
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docagent.library import Store,FIELDS
from docagent.conversations import Chats,Documents


class ConversationTests(unittest.TestCase):
    def test_mentions_group_counts_all_passages_and_show_top_three(self):
        for index,count in enumerate([1,5,3,2]):
            identity=self.record('PO-'+str(index),'2026-11-18')
            row=self.store.get(identity);source=row['sources'][0]
            source['text']='stainless steel\n'*count
            other=dict(source,id=source['id']+':second',text='stainless steel')
            with self.store.db() as db:db.execute('UPDATE documents SET sources=? WHERE id=?',(json.dumps([source,other]),identity))
        chat=self.chats.create();result=self.send(chat,'Which documents mention stainless steel?')
        self.assertEqual(len(result['claims']),3)
        self.assertEqual([m['count'] for m in result['matches']],[6,4,3])
        self.assertEqual(len({m['document_id'] for m in result['matches']}),3)
        self.assertIn('appears 6 times',result['claims'][0]['text'])
    def test_recall_and_expanding_followup_keep_previous_answer_context(self):
        self.record('PO-001','2026-11-18')
        chat=self.chats.create();self.send(chat,'Find PO-001')
        recall=self.send(chat,'What did I ask?')
        self.assertIn('Find PO-001',recall['message'])
        with patch('docagent.conversations.answer',return_value={'claims':[],'message':'Fixture explanation'}) as model:
            self.send(chat,'Tell me more')
        history=model.call_args.kwargs['recent']
        self.assertTrue(any(m['role']=='assistant' for m in history))
        self.assertTrue(any('PO-001' in m['content'] for m in history))
    def test_company_lists_counts_and_review_queries_are_deterministic(self):
        self.record('PO-001','2026-11-18',supplier='Caltex')
        self.record('PO-002','2026-11-17',supplier='Other Supply')
        chat=self.chats.create()
        with patch('docagent.conversations.answer',side_effect=AssertionError('No LLM lists/arithmetic')):
            response=self.send(chat,'What purchase orders do we have for Caltex?')
            self.assertEqual(len(response['claims']),1)
            response=self.send(chat,'How many purchase orders do we have for Caltex?')
            self.assertIn('1 matching indexed',response['message'])
            response=self.send(chat,'Which documents require review?')
            self.assertEqual(len(response['claims']),2)

    def test_explicit_relationship_does_not_invent_missing_link(self):
        self.record('PO-2026-078','2026-11-18')
        chat=self.chats.create();response=self.send(chat,'What documents reference PO-2026-078?')
        self.assertEqual(response['claims'],[])
        self.assertIn('could not establish',response['message'])
    def test_mentions_search_never_requires_model(self):
        identity=self.record('PO-001','2026-11-18')
        row=self.store.get(identity);source=row['sources'][0];source['text']+='\nMaterial: stainless steel'
        with self.store.db() as db:db.execute('UPDATE documents SET sources=? WHERE id=?',(json.dumps([source]),identity))
        chat=self.chats.create()
        with patch('docagent.conversations.answer',side_effect=AssertionError('No model needed')):
            response=self.send(chat,'Which documents mention stainless steel?')
            self.assertEqual(len(response['claims']),1)
            self.assertTrue(response['claims'][0]['references'])
            response=self.send(chat,'Which documents mention titanium?')
            self.assertIn('no saved text',response['message'])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name);self.chats=Chats(self.store);self.docs=self.chats.docs

    def record(self,number,day,amount='100.25',currency='PHP',supplier='Example Supply'):
        text=f'PURCHASE ORDER\nPO No: {number}\nDate: {day}\nTotal: {amount}\nCurrency: {currency}\nSupplier: {supplier}'
        path=Path(self.tmp.name)/'upload';path.write_text(text)
        identity=self.store.ingest(path,number+'.txt');job=self.store.claim()
        source={'id':identity+':0','document_id':identity,'version':job['version'],'location':{'page':1},'text':text}
        def value(raw):return {'raw':raw,'normalized':raw,'evidence':[{'source_id':source['id'],'excerpt':raw}],'flags':[]}
        fields=dict.fromkeys(FIELDS)
        for key,raw in [('document_number',number),('document_date',day),('totals',amount),('currency',currency),('supplier',supplier)]:
            fields[key]=value(raw) if raw else None
        ref=[{'source_id':source['id'],'excerpt':'PURCHASE ORDER'}]
        output={'category':'Purchase Order (PO)','category_evidence':ref,'segments':[{'category':'Purchase Order (PO)','category_evidence':ref,'fields':fields,'line_items':[]}],'summary':[],'tags':[],'quality_flags':[]}
        self.store.stage(job,'Completed',sources=json.dumps([source]),machine=json.dumps(output),search_text=text,finished=1)
        return identity

    def send(self,chat,text):
        result=self.chats.send(chat['id'],text,str(len(self.chats.get(chat['id'])['turns'])+1))
        return result['turns'][-1]['response']

    def test_persistence_isolation_and_followups(self):
        identity=self.record('PO-0001','2026-11-18')
        chat=self.chats.create()
        first=self.send(chat,'Show my latest PO.')
        self.assertIn('PO-0001',first['claims'][0]['text'])
        second=self.send(chat,'How much is it?')
        self.assertIn('PHP 100.25',second['claims'][0]['text'])
        third=self.send(chat,'Who is the supplier?')
        self.assertIn('Example Supply',third['claims'][0]['text'])
        self.assertEqual(len(Chats(Store(self.tmp.name)).get(chat['id'])['turns']),3)
        other=self.chats.create()
        self.assertIn('No matching',self.send(other,'How much is it?')['message'])
        self.chats.delete(chat['id'])
        self.assertEqual(self.store.get(identity)['id'],identity)

    def test_latest_dates_uploads_missing_ties_and_duplicates(self):
        a=self.record('PO-001','2026-11-18');b=self.record('PO-002','2026-11-01');self.record('PO-003','')
        rows,excluded,tied=self.docs.get_latest_documents('Purchase Order (PO)',1)
        self.assertEqual(rows[0]['id'],a);self.assertEqual(excluded,1)
        uploaded,_,_=self.docs.get_latest_documents('Purchase Order (PO)',1,uploaded=True)
        self.assertNotEqual(uploaded[0]['id'],a)
        self.record('PO-004','2026-11-18')
        self.assertTrue(self.docs.get_latest_documents('Purchase Order (PO)',1)[2])
        source=Path(self.store.get(b)['path']);p=Path(self.tmp.name)/'duplicate';p.write_bytes(source.read_bytes())
        self.assertEqual(self.store.ingest(p,'copy.txt'),b)
        self.assertEqual(len(self.docs.records()),4)

    def test_ordinal_ambiguity_comparison_and_review(self):
        a=self.record('PO-001','2026-11-18','100.25');self.record('PO-002','2026-11-17','20.10')
        chat=self.chats.create();self.send(chat,'Show my two latest POs')
        result=self.send(chat,'How much is it?');self.assertIn('Which',result['message'])
        result=self.send(chat,'What about the second one?');self.assertIn('PO-002',result['claims'][0]['text'])
        self.send(chat,'Show my latest PO')
        result=self.send(chat,'Compare it with the previous PO')
        self.assertTrue(any('80.15' in c['text'] for c in result['claims']))
        row=self.store.get(a);corrected=copy.deepcopy(row['machine']);corrected['segments'][0]['fields']['totals']['normalized']='101.25'
        self.store.review(a,corrected,'Verified','Reviewer')
        self.send(chat,'Find PO-001');result=self.send(chat,'How much is it?')
        self.assertIn('101.25',result['claims'][0]['text']);self.assertIn('Verified',result['claims'][0]['text'])

    def test_currency_missing_amounts_and_deleted_sources(self):
        a=self.record('PO-001','2026-11-18','100.25','PHP');self.record('PO-002','2026-11-17','20.10','USD')
        claims=self.docs.compare_documents(self.docs.records(),['totals'])
        self.assertIn('No combined',claims[-1]['text'])
        self.record('PO-003','2026-11-16','')
        missing=next(r for r in self.docs.records() if r['segment']['fields']['totals'] is None)
        self.assertIsNone(self.docs.get_document_amount(missing))
        chat=self.chats.create();self.send(chat,'Find PO-001')
        self.store.clear()
        old=self.chats.get(chat['id'])['turns'][0]['response']['claims'][0]['references'][0]
        self.assertTrue(old['unavailable']);self.assertIsNone(old['url'])
        self.assertIn('unavailable',self.send(chat,'Open that document')['message'])

    def test_idempotency_and_model_failure_fallback(self):
        self.record('PO-0001','2026-11-18')
        chat=self.chats.create()
        self.chats.send(chat['id'],'Find PO-0001','request-1');self.chats.send(chat['id'],'Find PO-0001','request-1')
        self.assertEqual(len(self.chats.get(chat['id'])['turns']),1)
        with patch('docagent.conversations.answer',side_effect=RuntimeError('model failed')):
            result=self.send(chat,'Explain payment requirements')
        self.assertIn('unavailable',result['message'])

    def test_sum_currency_groups_and_duplicate_references(self):
        self.record('PO-001','2026-11-18','100.25','PHP');self.record('PO-002','2026-11-17','20.10','PHP');self.record('PO-003','2026-11-16','5.00','USD')
        chat=self.chats.create();result=self.send(chat,'Sum all PO amounts')
        self.assertTrue(any('PHP total: 120.35' in c['text'] for c in result['claims']))
        self.assertTrue(any('USD total: 5.00' in c['text'] for c in result['claims']))
        self.record('PO-001','2026-11-15','99.00','PHP')
        self.assertIn('share a reference',self.send(chat,'Sum all PO amounts')['message'])

    def test_exact_identifier_and_retry_after_failure(self):
        self.record('PO-0001','2026-11-18');self.record('PO-00012','2026-11-17')
        self.assertEqual(len(self.docs.search_documents('Find PO-0001')),1)
        chat=self.chats.create()
        with patch.object(self.chats,'respond',side_effect=RuntimeError('fixture interrupted')):
            with self.assertRaises(RuntimeError):self.chats.send(chat['id'],'Find PO-0001','retry-1')
        self.assertEqual(self.chats.get(chat['id'])['turns'][0]['status'],'error')
        self.chats.send(chat['id'],'Find PO-0001','retry-1')
        self.assertEqual(len(self.chats.get(chat['id'])['turns']),1)
        self.assertEqual(self.chats.get(chat['id'])['turns'][0]['status'],'completed')

    def test_scoped_generation_and_bounded_history(self):
        identity=self.record('PO-001','2026-11-18')
        chat=self.chats.create();self.send(chat,'Find PO-001')
        with patch('docagent.conversations.answer',return_value={'claims':[],'message':'fixture model reply'}) as model:
            self.send(chat,'Explain the payment terms for PO-001')
        self.assertEqual(model.call_args.kwargs['document_ids'],[identity])
        self.assertLessEqual(len(model.call_args.kwargs['recent']),6)
        self.assertTrue(any(m['role']=='assistant' for m in model.call_args.kwargs['recent']))

    def test_mixed_segments_are_separate_records(self):
        identity=self.record('PO-001','2026-11-18')
        row=self.store.get(identity);output=copy.deepcopy(row['machine']);output['category']='Mixed document'
        output['segments'].append(copy.deepcopy(output['segments'][0]))
        with self.store.db() as db:db.execute('UPDATE documents SET machine=? WHERE id=?',(json.dumps(output),identity))
        self.assertEqual(len(self.docs.records()),2)
        chat=self.chats.create();self.assertIn('Several records',self.send(chat,'How much is PO-001?')['message'])

    def test_malformed_model_citations_fall_back_to_evidence(self):
        self.record('PO-0001','2026-11-18')
        chat=self.chats.create()
        body={'choices':[{'message':{'content':json.dumps({'claims':[{'text':'Invented answer','evidence':[{'source_id':'fake','excerpt':'Invented'}]}]})}}]}
        with patch.dict(os.environ,{'DOCAGENT_AI_URL':'http://127.0.0.1:11434/v1/chat/completions','DOCAGENT_AI_MODEL':'fixture'}),patch('urllib.request.urlopen',side_effect=lambda *a,**kw:closing(io.BytesIO(json.dumps(body).encode()))):
            response=self.send(chat,'Explain PO-0001')
        self.assertIn('could not produce validated',response['message'])
        self.assertTrue(response['claims'])
        self.assertFalse(any(c['text']=='Invented answer' for c in response['claims']))

    def test_latest_specific_reference_excludes_old_versions(self):
        self.record('PO-0001','2026-11-18');self.record('PO-0001','2026-11-17')
        chat=self.chats.create();response=self.send(chat,'Find the latest version of PO-0001')
        self.assertEqual(len(response['claims']),1)
        self.assertIn('2026-11-18',response['claims'][0]['text'])

    def test_advanced_supplier_date_amount_filters(self):
        self.record('PO-0001','2026-11-18','100.25',supplier='Example Supply')
        self.record('PO-0002','2026-11-17','20.10',supplier='Other Company')
        chat=self.chats.create()
        response=self.send(chat,'Show POs from Example Supply over PHP 50 after 2026-11-01')
        self.assertEqual(len(response['claims']),1)
        self.assertIn('PO-0001',response['claims'][0]['text'])
        self.assertIn('100.25',response['claims'][0]['text'])
        self.assertIn('currency',self.send(chat,'Show POs over 50')['message'].lower())

    def test_due_date_filter_and_cross_document_relationship(self):
        self.record('PO-0001','2026-11-18')
        invoice=self.record('INV-0001','2026-11-17')
        row=self.store.get(invoice);output=copy.deepcopy(row['machine'])
        # Fixture source supports all values and records an explicit cross-reference.
        source=row['sources'][0];source['text']+='\nDue date: 2026-12-01\nPO reference: PO-0001'
        def value(raw):return {'raw':raw,'normalized':raw,'evidence':[{'source_id':source['id'],'excerpt':raw}],'flags':[]}
        output['category']='Invoice';output['segments'][0]['category']='Invoice'
        output['segments'][0]['fields']['due_date']=value('2026-12-01')
        output['segments'][0]['fields']['purchase_order_reference']=value('PO-0001')
        with self.store.db() as db:db.execute('UPDATE documents SET machine=?,sources=? WHERE id=?',(json.dumps(output),json.dumps([source]),invoice))
        chat=self.chats.create();response=self.send(chat,'Show invoices due before 2026-12-02')
        self.assertEqual(len(response['claims']),1)
        self.assertIn('INV-0001',response['claims'][0]['text'])
        response=self.send(chat,'Find invoices referencing PO-0001')
        self.assertIn('INV-0001',response['claims'][0]['text'])


if __name__=='__main__':unittest.main()
