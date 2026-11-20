const fs=require('fs'),vm=require('vm'),assert=require('assert');
function element(tag='div',text){return {tag,textContent:text,children:[],hidden:false,checked:false,disabled:false,append(...nodes){this.children.push(...nodes)},replaceChildren(...nodes){this.children=nodes},setAttribute(){},setCustomValidity(value){this.validityMessage=value}}}
const html=fs.readFileSync('docagent/static/documents.html','utf8');
const nodes=new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>['#'+m[1],element()]));nodes.get('#workspace').hidden=true;
const handlers=new Map(),calls=[],storage=new Map();let timer;
const sources=[{id:'s1',text:'PO-001 Supplier: Acme',location:{page:1}}];
const documents=[{id:'doc1',filename:'PO.pdf',attempt:1,status:'Completed',source:sources[0],review_status:'Pending'},{id:'doc2',filename:'Queued.pdf',attempt:0,status:'Queued',source:null,review_status:'Pending'}];
const references=[{id:'example1',source:'Older.pdf',labels:{},context:{ready:true,status:'Saved',document_id:'doc1'}}];
const context={console,JSON,Map,Set,Promise,performance:{now:()=>1000},location:{hash:'#reference-activity'},localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},setInterval:fn=>{timer=fn},
 document:{querySelector:s=>{assert(nodes.has(s),'Unexpected selector '+s);return nodes.get(s)},addEventListener:(name,fn)=>handlers.set(name,fn),activeElement:null},
 add:(tag,text,parent,cls)=>{const e=element(tag,text);e.className=cls;if(parent)parent.append(e);return e},
 message:()=>{},inspect:async id=>calls.push({inspect:id}),search:async()=>{},types:['Purchase Order (PO)','Invoice','Unknown'],
 DocProgress:{duration:()=>'0s',processing:()=>{},meter:()=>({update(){}}),upload:async(path,file)=>{calls.push({path,file});return{id:'uploaded'}}},
 api:async(path,options={})=>{calls.push({path,options});if(path==='/api/library'&&!options.method)return documents;if(path==='/api/training'||path.startsWith('/api/training?'))return references;return{hidden:1}},
};
vm.createContext(context);vm.runInContext(fs.readFileSync('docagent/static/document-workspace.js','utf8'),context);
const descendants=e=>[e,...e.children.flatMap(descendants)];const button=(box,label)=>{const e=descendants(box).find(e=>e.tag==='button'&&e.textContent===label);assert(e,'Missing button '+label);return e};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 assert(nodes.get('#document-activity').open);assert(nodes.get('#reference-activity').open);
 handlers.get('documents-ready')();await flush();await flush();
 assert.equal(nodes.get('#processing-activity').children.length,2);
 await nodes.get('#clear-activity').onclick();assert.equal(nodes.get('#processing-activity').children.length,1);assert.equal(documents.length,2);assert(storage.size);
 nodes.get('#show-hidden-activity').checked=true;await nodes.get('#show-hidden-activity').onchange();assert.equal(nodes.get('#processing-activity').children.length,2);
 await button(nodes.get('#processing-activity'),'Restore activity').onclick();assert.equal(JSON.parse(storage.get('rich-hidden-document-activity')).length,0);
 await nodes.get('#hide-references').onclick();assert(calls.some(c=>c.path==='/api/training/hide'));assert(!calls.some(c=>c.options?.method==='DELETE'));
 await nodes.get('#refresh-references').onclick();assert(calls.some(c=>c.path==='/api/training/example1/refresh'));
 await button(nodes.get('#reference-rows'),'Inspect document').onclick();assert(calls.some(c=>c.inspect==='doc1'));
 const uploadInput=nodes.get('#upload-files');uploadInput.files=[{name:'New.pdf'}];await uploadInput.onchange({target:uploadInput});assert(calls.some(c=>c.file?.name==='New.pdf'&&c.path==='/api/library'));assert(!uploadInput.disabled);
 const output={category:'Purchase Order (PO)',segments:[{category:'Purchase Order (PO)',fields:{document_number:{raw:'PO-001',normalized:'PO-001',evidence:[{source_id:'s1',excerpt:'PO-001'}],flags:[]}},line_items:[]}],summary:[],tags:[],quality_flags:[]};
 const box=element();context.documentReview({id:'doc1',extraction:output,sources,review_status:'Pending'},box);
 const form=descendants(box).find(e=>e.tag==='form');const inputs=descendants(form).filter(e=>e.tag==='input');
 inputs[0].value='Invented';inputs[0].oninput();assert(inputs[0].validityMessage);
 inputs[0].value='Acme';inputs[0].oninput();assert.equal(inputs[0].validityMessage,'');inputs[1].value='ACME';inputs[1].oninput();inputs[2].value='Demo reviewer';
 const status=descendants(form).filter(e=>e.tag==='select').at(-1);status.value='Verified';await form.onsubmit({preventDefault(){}});
 const review=calls.find(c=>c.path==='/api/library/doc1/review');assert(review);const payload=JSON.parse(review.options.body);assert.equal(payload.actor,'Demo reviewer');assert.equal(payload.status,'Verified');assert.equal(payload.output.segments[0].fields.document_number.evidence[0].source_id,'s1');assert.equal(payload.output.segments[0].fields.document_number.normalized,'ACME');assert.equal(output.segments[0].fields.document_number.raw,'PO-001');
 console.log('PASS: unified upload, activity hide/restore without deletion, retained reference controls, inspector navigation, evidence-backed corrections and review submission.');
})().catch(e=>{console.error(e);process.exitCode=1});
