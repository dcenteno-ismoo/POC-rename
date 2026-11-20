from html.parser import HTMLParser
import unittest
from docagent.ui import PAGES,NAV,render_page


class PageParser(HTMLParser):
    def __init__(self):super().__init__();self.ids=[];self.nav=False;self.links=[];self.bodies=[];self.headings=0
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if 'id' in values:self.ids.append(values['id'])
        if tag=='body':self.bodies.append(values.get('class',''))
        if tag=='nav' and values.get('aria-label')=='Main navigation':self.nav=True
        if tag=='a' and self.nav:self.links.append(values)
        if tag=='h1':self.headings+=1
    def handle_endtag(self,tag):
        if tag=='nav':self.nav=False


class SharedUITests(unittest.TestCase):
    def test_identical_navigation_one_current_tab_and_heading(self):
        for key in PAGES:
            with self.subTest(page=key):
                html=render_page(key);parser=PageParser();parser.feed(html)
                self.assertEqual([a['href'] for a in parser.links],[url for _,url in NAV])
                self.assertEqual(sum(a.get('aria-current')=='page' for a in parser.links),1)
                self.assertEqual(parser.headings,1)
                self.assertEqual(len(parser.bodies),1)
                self.assertIn('rich-app',parser.bodies[0])
                self.assertEqual(len(parser.ids),len(set(parser.ids)))
                self.assertIn('/assets/app.css',html)
                self.assertNotIn('<rich-shell>',html)

    def test_workflow_controls_preserved(self):
        expected={'renamer':['pick','drop','go'], 'sources':['source-form','scan-all','scan-result'],
                  'documents':['search','results','detail','upload-files','upload-folder','clear-activity','reference-rows'],'training':['reference-rows','hide-references'],
                  'library':['upload-files','results','detail'],'assistant':['compose','question','messages','reference-panel']}
        for key,ids in expected.items():
            parser=PageParser();parser.feed(render_page(key))
            for identity in ids:self.assertIn(identity,parser.ids,(key,identity))

    def test_legacy_workspaces_use_documents_and_new_navigation(self):
        for page in ('library','training'):
            self.assertEqual(render_page(page),render_page('documents'))
        html=render_page('documents');parser=PageParser();parser.feed(html)
        self.assertEqual([a['href'] for a in parser.links],['/documents','/sources','/assistant','/'])
        self.assertNotIn('Upload &amp; Review',html)
        self.assertNotIn('AI Training',html)

    def test_old_urls_redirect_and_preserve_document_selection(self):
        from docagent.library_api import page
        from docagent.training import training_page
        from urllib.parse import parse_qs,urlparse
        response=page('file & other')
        self.assertEqual(response.status_code,307)
        self.assertEqual(urlparse(response.headers['location']).path,'/documents')
        self.assertEqual(parse_qs(urlparse(response.headers['location']).query),{'document':['file & other']})
        self.assertEqual(training_page().headers['location'],'/documents#reference-activity')
