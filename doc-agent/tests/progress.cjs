const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
function element() { return {children:[],append(...items){this.children.push(...items)},setAttribute(){},removeAttribute(name){delete this[name]},style:{}}; }
const context={window:{},document:{createElement:element},performance:{now:()=>1000},Number,Math,FormData:class{append(){}},XMLHttpRequest:class{constructor(){this.upload={};context.xhr=this}open(){}send(){}}};
vm.runInNewContext(fs.readFileSync('docagent/static/progress.js','utf8'),context);
const progress=context.window.DocProgress;
assert.equal(progress.duration(NaN),'Estimating…');
assert.equal(progress.duration(65),'1m 5s');
const parent=element();
progress.processing(parent,{status:'Extracting information',done:2,total:4,stage_elapsed:10});
assert.equal(parent.children[0].children[1].value,50);
assert(parent.children[0].children[2].textContent.includes('10s'));
const pending=element();progress.processing(pending,{status:'Reading / OCR'});
assert.equal(pending.children[0].children[1].value,undefined);
const completed=element();progress.processing(completed,{status:'Completed'});
assert.equal(completed.children[0].children[1].value,100);
(async()=>{
 const uploads=element();const promise=progress.upload('/test',{name:'sample.txt'},uploads);
 context.xhr.upload.onprogress({lengthComputable:true,loaded:50,total:100});
 assert.equal(uploads.children[0].children[1].value,50);
 context.xhr.upload.onload();
 assert.equal(uploads.children[0].children[1].value,undefined);
 context.xhr.status=200;context.xhr.responseText='{}';context.xhr.onload();await promise;
 assert.equal(uploads.children[0].children[1].value,100);
 console.log('Progress tests passed');
})().catch(e=>{console.error(e);process.exitCode=1});
