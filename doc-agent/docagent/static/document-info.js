/* Human-readable document information; technical extraction data remains server-side. */
function documentStatus(row){if(row.review_status==='Verified')return 'Verified';if(row.review_status==='Needs clarification')return 'Needs clarification';if(row.processing_status==='Completed')return 'Ready for review';if(row.processing_status==='Failed')return 'Processing issue';return row.processing_status==='Queued'?'Waiting to process':'Processing';}
function sourceLocation(location){if(location?.page)return 'Page '+location.page;if(location?.sheet)return 'Sheet: '+location.sheet+(location.cells?.length?' · '+location.cells.join(', '):'');return location?.image?'Image: '+location.image:'Source text';}

// Reconstruct only rows whose saved values align with their recorded cell addresses.
function spreadsheetRows(sources){
 const sheets=new Map();
 for(const source of sources||[]){
  const loc=source.location||{};if(!loc.sheet)continue;
  if(!sheets.has(loc.sheet))sheets.set(loc.sheet,[]);
  const addresses=(loc.cells||[]).map(c=>/^([A-Z]+)([1-9][0-9]*)$/.exec(c));
  const values=source.text.split(/\s*\|\s*/);
  const aligned=addresses.length>0&&addresses.every(Boolean)&&values.length===addresses.length&&new Set(addresses.map(a=>a[2])).size===1;
  sheets.get(loc.sheet).push({text:source.text,values,addresses:aligned?addresses:null});
 }
 return sheets;
}
function spreadsheetPreview(parent,sources){
 const sheets=spreadsheetRows(sources);if(!sheets.size)return false;
 add('h3','Spreadsheet preview',parent);add('p','Saved worksheet values. Open the original for formatting, formulas and the complete workbook.',parent,'muted');
 for(const[name,rows]of sheets){
  add('h3',name,parent);const wrap=add('div',undefined,parent,'spreadsheet-preview');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label',name+' worksheet preview');
  const table=add('table',undefined,wrap);const columns=[...new Set(rows.flatMap(r=>(r.addresses||[]).map(a=>a[1])))];
  const columnNumber=s=>[...s].reduce((n,c)=>n*26+c.charCodeAt(0)-64,0);
  columns.sort((a,b)=>columnNumber(a)-columnNumber(b));
  const visible=columns.slice(0,50);const head=add('tr',undefined,add('thead',undefined,table));add('th','Row',head);for(const c of visible)add('th',c,head);
  const body=add('tbody',undefined,table);
  for(const row of rows.slice(0,1000)){
   const tr=add('tr',undefined,body);
   if(row.addresses){add('th',row.addresses[0][2],tr);const cells=new Map(row.addresses.map((a,i)=>[a[1],row.values[i]]));for(const c of visible)add('td',cells.get(c)||'',tr)}
   else{add('th','',tr);const td=add('td',undefined,tr);td.colSpan=Math.max(1,visible.length);add('pre',row.text,td)}
  }
  if(rows.length>1000||columns.length>50)add('p','Preview limited to 1,000 saved rows and 50 populated columns. Open the original for the full sheet.',parent,'muted');
 }
 return true;
}
inspect=async function(id){const row=await api('/api/documents/'+id);const box=$('#detail');box.replaceChildren();add('h2',row.filename,box);add('span',documentStatus(row),box,'badge');const dl=add('dl',undefined,box);for(const[label,value]of [['Type',row.category],['Reference',row.reference_numbers.join(', ')||'Not found'],['Companies',row.companies.join(', ')||'Not found'],['Document date',row.dates.join(', ')||'Not found']]){add('dt',label,dl);add('dd',value,dl)}if(row.processing_status==='Completed'&&row.review_status==='Pending')add('p','The document is ready to use. Its extracted information has not yet been reviewed.',box,'muted');if(row.error)add('p','Some information could not be extracted. You can still inspect the available text and original file.',box,'error');add('h3','Original document',box);for(const origin of row.origins)add('p',(origin.source_path||origin.original_filename),box,'path');if(row.original_url){fileLink(box,row.original_url,'Open original document');if(row.filename.toLowerCase().endsWith('.pdf')){const frame=add('iframe',undefined,box);frame.src=row.original_url;frame.title='Original document';frame.sandbox=''}}else add('p','The original file is unavailable. Saved text and details are shown below.',box,'error');
const materials=(row.extraction?.tags||[]).filter(tag=>tag.text.startsWith('Material mentioned: '));if(materials.length){add('h3','Materials mentioned',box);for(const tag of materials)add('span',tag.text.slice('Material mentioned: '.length),box,'badge')}
for(const [index,segment]of(row.extraction?.segments||[]).entries()){add('h3',(row.extraction.segments.length>1?'Part '+(index+1)+' · ':'')+'Document details',box);const fields=add('dl',undefined,box);for(const[key,value]of Object.entries(segment.fields||{})){if(!value||['document_number','invoice_number','document_date'].includes(key))continue;add('dt',key.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase()),fields);add('dd',String(value.normalized||value.raw),fields)}for(const item of segment.line_items||[]){const card=add('article',undefined,box);for(const[key,value]of Object.entries(item))if(value)add('p',key.replaceAll('_',' ')+': '+(value.normalized||value.raw),card)}}
if(row.filename.toLowerCase().endsWith('.xlsx'))spreadsheetPreview(box,row.sources);
const text=add('details',undefined,box);add('summary','Read document text',text);for(const source of row.sources){add('h3',sourceLocation(source.location),text);if(row.original_url)fileLink(text,row.original_url,'Open this location',source.location.page);add('pre',source.text,text)}if(typeof documentReview==='function')documentReview(row,box);box.scrollIntoView({behavior:'smooth',block:'start'})};
