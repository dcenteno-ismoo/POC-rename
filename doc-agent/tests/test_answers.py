import unittest
from docagent.answers import answer, validate_answer
from test_library import LibraryTests


class AnswerTests(LibraryTests):
    def test_natural_answer_links(self):
        identity=self.complete()
        def generate(question,sources):
            return {'claims':[{'text':'The purchase order number is 000123.', 'evidence':[{'source_id':sources[0]['id'],'excerpt':'PO 000123'}]}]}
        result=answer(self.store,'What is PO 000123?',generate)
        self.assertEqual(result['claims'][0]['references'][0]['url'],'/api/library/'+identity+'/original')

    def test_no_matching_evidence(self):
        self.complete()
        self.assertEqual(answer(self.store,'nonexistent equipment')['claims'],[])

    def test_cached_text_available_while_extraction_is_running(self):
        from docagent.answers import retrieve
        import json
        self.ingest()
        job=self.store.claim()
        self.store.stage(job,'Extracting information',sources=json.dumps(self.reader(job,lambda *a:None)))
        self.assertTrue(retrieve(self.store,'PO 000123'))

    def test_assistant_works_when_extraction_failed(self):
        identity=self.ingest()
        job=self.store.claim()
        import json
        sources=self.reader(job,lambda *args:None)
        self.store.stage(job,'Failed',sources=json.dumps(sources),error='fixture extraction failure')
        def generate(question,sources):
            return {'claims':[{'text':'PO 000123', 'evidence':[{'source_id':sources[0]['id'],'excerpt':'PO 000123'}]}]}
        result=answer(self.store,'PO 000123',generate)
        self.assertEqual(result['claims'][0]['references'][0]['processing_status'],'Failed')

    def test_fabricated_citation_rejected(self):
        self.complete()
        def generate(question,sources):
            return {'claims':[{'text':'Unsupported', 'evidence':[{'source_id':'fake','excerpt':'PO 000123'}]}]}
        with self.assertRaises(RuntimeError): answer(self.store,'PO 000123',generate)


if __name__=='__main__': unittest.main()
