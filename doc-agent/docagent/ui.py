"""Shared server-rendered chrome for the existing plain-HTML workspaces."""
from html import escape
from pathlib import Path
import re

STATIC=Path(__file__).parent/'static'
PAGES={
 'renamer':('index.html','Renamer','Prepare consistent filenames while preserving original documents.'),
 'sources':('sources.html','Sources','Manage authorized folders and run read-only document scans.'),
 'documents':('documents.html','Documents','Upload, search and review documents available to the Assistant.'),
 'library':('library.html','Upload & Review','Upload documents, monitor processing, and review extracted information.'),
 'training':('training.html','AI Training','Review examples and manage the documents saved to Assistant context.'),
 'assistant':('assistant-chat.html','AI Assistant','Ask questions, follow up, and inspect supporting document references.')}
NAV=[('documents','/documents'),('sources','/sources'),('assistant','/assistant'),('renamer','/')]


def render_page(page):
    if page in ('library','training'):page='documents'
    filename,title,description=PAGES[page]
    links=''.join('<a href="'+url+'"'+(' aria-current="page"' if key==page else '')+'>'+escape(PAGES[key][1])+'</a>' for key,url in NAV)
    shell='<a class="rich-skip" href="#rich-content">Skip to workspace</a><header class="rich-header"><div class="rich-header-inner"><div class="rich-brand"><a href="/documents">Project <strong>RICH</strong></a><span>Ismoo Technologies Inc.</span><span class="rich-beta">Beta</span></div><nav class="rich-nav" aria-label="Main navigation">'+links+'</nav></div></header><div class="rich-content" id="rich-content"><section class="rich-page-heading"><h1>'+escape(title)+'</h1><p>'+escape(description)+'</p></section>'
    html=(STATIC/filename).read_text('utf-8-sig')
    html=re.sub(r'<title>.*?</title>','<title>'+escape(title)+' · Project RICH</title>',html,count=1,flags=re.S)
    html=html.replace('<rich-shell></rich-shell>',shell)
    html=html.replace('<script','</div><script',1)
    if '<body>' in html:html=html.replace('<body>','<body class="rich-app rich-'+page+'">',1)
    else:html=html.replace('<a class="rich-skip"','<body class="rich-app rich-'+page+'"><a class="rich-skip"',1).replace('</html>','</body></html>')
    html=html.replace('<rich-assets>','<link rel="stylesheet" href="/assets/app.css">')
    from .demo import demo_mode
    if demo_mode() and page in ('sources','documents'):
        script="<script>document.querySelector('#unlock').closest('section').hidden=true;"
        if page=='sources':script+="token='demo';load().catch(e=>message(e.message,true));"
        script+='</script>'
        html=html.replace('</body>',script+'</body>')
    if demo_mode() and page=='library':
        html=html.replace('</body>',"<script>document.querySelector('#token').hidden=true;</script></body>")
    return html
