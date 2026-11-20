import unittest
from unittest.mock import patch
from docagent.answers import answer, casual_intent


class CasualTests(unittest.TestCase):
    def test_greetings_work_without_documents_or_model(self):
        with patch.dict('os.environ',{},clear=True):
            for question in ['Hello!', 'Hi, how are you?', 'thanks', 'tell me a joke', 'what can you do?']:
                result=answer(None,question)
                self.assertEqual(result['mode'],'casual')
                self.assertTrue(result['message'])
                self.assertEqual(result['claims'],[])

    def test_document_questions_keep_evidence_requirements(self):
        for question in ['Hello, what are the payment terms for PO 123?', 'thanks, summarize my invoice', 'what is the total?', 'hi invoice', 'tell me about supplier Hello']:
            self.assertIsNone(casual_intent(question))

    def test_local_model_failure_has_friendly_fallback(self):
        with patch.dict('os.environ',{'DOCAGENT_AI_URL':'http://127.0.0.1:11434/v1/chat/completions','DOCAGENT_AI_MODEL':'test'}), patch('urllib.request.urlopen',side_effect=TimeoutError):
            self.assertEqual(answer(None,'hello')['message'],'Hello! How can I help you today?')
