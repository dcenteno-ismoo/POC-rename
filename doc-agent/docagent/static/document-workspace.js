/* Documents is the single upload, review and saved-context workspace. */
(function(){
 const pick=s=>document.querySelector(s);
 let activity=[],loading=false,uploading=false,ready=false;
 let hidden=new Set();try{hidden=new Set(JSON.parse(localStorage.getItem('rich-hidden-document-activity')||'[]'))}catch{}
 const activityKey=r=>r.id+':'+r.attempt;
 function saveHidden(){try{localStorage.setItem('rich-hidden-document-activity',JSON.stringify([...hidden]))}catch{message('Activity hidden for this visit; browser storage is unavailable.')}}
 const finished=r=>['Completed','Failed'].includes(r.status);
 function action(parent,label,callback){const b=add('button',label,parent);b.type='button';b.onclick=async()=>{b.disabled=true;try{await callback()}catch(e){message(e.message,true)}finally{b.disabled=false}};return b}
 function input(parent,label,value,type='text'){const wrap=add('label',label,parent);const e=add('input',undefined,wrap);e.type=type;e.value=value??'';return e}
 async function updateActivity(){
  activity=await api('/api/library');const box=pick('#processing-activity');box.replaceChildren();
  const rows=activity.filter(r=>!finished(r)||pick('#show-hidden-activity').checked||!hidden.has(activityKey(r)));
  if(!rows.length)add('p','No visible activity. Saved documents remain in the list below.',box,'muted');
  for(const row of rows){const card=add('article',undefined,box);add('strong',row.filename,card);DocProgress.processing(card,row);
   add('p',(row.source?'Assistant context: available from saved text':'Assistant context: '+(row.status==='Failed'?'not available; retry processing':'waiting for document text'))+' | Review: '+row.review_status,card,'muted');
   if(row.error)add('p','Processing issue: '+row.error,card,'error');
   action(card,'Inspect document',()=>inspect(row.id));
   if(finished(row)){action(card,row.status==='Failed'?'Retry processing':'Reprocess',async()=>{await api('/api/library/'+row.id+'/retry',{method:'POST'});await updateActivity();await search()});
    if(!hidden.has(activityKey(row)))action(card,'Hide activity',async()=>{hidden.add(activityKey(row));saveHidden();await updateActivity()});
    else action(card,'Restore activity',async()=>{hidden.delete(activityKey(row));saveHidden();await updateActivity()});
   }
  }
 }
 async function upload(event){
  const files=Array.from(event.target.files);if(!files.length)return;
  uploading=true;pick('#document-activity').open=true;pick('#upload-files').disabled=pick('#upload-folder').disabled=true;
  let accepted=0;const errors=[];
  try{for(const file of files){try{await DocProgress.upload('/api/library',file,pick('#upload-progress'));accepted++}catch(e){errors.push(file.name+': '+e.message)}}
   await search();await updateActivity();message(accepted+' documents accepted.'+(errors.length?' '+errors.join('; '):''),!!errors.length);
  }catch(e){message(e.message,true)}finally{event.target.value='';uploading=false;pick('#upload-files').disabled=pick('#upload-folder').disabled=false}
 }
 pick('#upload-files').onchange=pick('#upload-folder').onchange=upload;
 pick('#show-hidden-activity').onchange=()=>updateActivity().catch(e=>message(e.message,true));
 pick('#clear-activity').onclick=async()=>{for(const r of activity)if(finished(r))hidden.add(activityKey(r));saveHidden();pick('#upload-progress').replaceChildren();await updateActivity().catch(e=>message(e.message,true))};

 // Preserve existing reference examples and their approval/refresh controls.
 async function references(){
  const rows=await api('/api/training?include_archived='+pick('#show-hidden-references').checked);const box=pick('#reference-rows');box.replaceChildren();
  if(!rows.length)add('p','No visible earlier reference uploads. Show hidden references to find saved entries.',box,'muted');
  for(const row of rows){const card=add('article',undefined,box);add('strong',row.source,card);const ctx=row.context||{};
   const meter=DocProgress.meter(card,'Assistant context');meter.update(ctx.status||'Not saved',ctx.ready?100:ctx.error?0:null);
   if(ctx.error)add('p',ctx.error,card,'error');
   if(ctx.document_id)action(card,'Inspect document',()=>inspect(ctx.document_id));
   if(!ctx.ready)action(card,'Save / retry Assistant context',async()=>{await api('/api/training/'+row.id+'/context',{method:'POST'});await references();await search()});
   action(card,row.archived?'Restore activity':'Hide activity (keep context)',async()=>{await api('/api/training/'+row.id+(row.archived?'/restore':'/hide'),{method:'POST'});await references()});
   action(card,'Refresh saved reference',async()=>{await api('/api/training/'+row.id+'/refresh',{method:'POST'});await references()});
   if(row.refresh_error)add('p',row.refresh_error,card,'error');
   const details=add('details',undefined,card);add('summary',row.approved?'Approved example labels':'Optional example labels',details);
   const form=add('form',undefined,details);form.className='filters';
   for(const key of ['company','reference_number','category','date']){
    const label=add('label',key.replaceAll('_',' '),form);const field=add(key==='category'?'select':'input',undefined,label);field.name=key;field.required=key!=='reference_number';
    if(key==='date')field.type='date';
    if(key==='category')for(const name of ['','Request For Quotation','Purchase Order','Purchase Requisition','Invoice','Quotation','Design Document','Bill of Materials (BOM)','Mixed document','Unknown']){const option=add('option',name||'Choose type',field);option.value=name}
    field.value=row.labels[key]||'';
   }
   const submit=add('button','Save approved labels',form);form.onsubmit=async e=>{e.preventDefault();submit.disabled=true;try{await api('/api/training/'+row.id+'/approve',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(form)))});await api('/api/training/'+row.id+'/context',{method:'POST'});await references();await search();message('Example labels saved; document context retained.')}catch(error){message(error.message,true)}finally{submit.disabled=false}};
  }
 }
 pick('#show-hidden-references').onchange=()=>references().catch(e=>message(e.message,true));
 pick('#hide-references').onclick=async()=>{try{const result=await api('/api/training/hide',{method:'POST'});pick('#reference-status').textContent=result.hidden+' entries hidden. Saved context retained.';await references()}catch(e){message(e.message,true)}};
 pick('#refresh-references').onclick=async()=>{const b=pick('#refresh-references');b.disabled=true;let done=0;try{const rows=await api('/api/training');const errors=[];const progress=DocProgress.meter(pick('#reference-status'),'Refreshing saved references');const started=performance.now();for(const row of rows){progress.update('Refreshing '+row.source+' ('+done+'/'+rows.length+')',rows.length?done/rows.length*100:0);try{await api('/api/training/'+row.id+'/refresh',{method:'POST'})}catch(e){errors.push(row.source+': '+e.message)}done++;progress.update(done+'/'+rows.length+' checked | '+Math.floor(done/rows.length*100)+'% | Estimated time left: '+DocProgress.duration((performance.now()-started)/1000/done*(rows.length-done)),done/rows.length*100)}await references();add('p',done+' references checked.'+(errors.length?' '+errors.join('; '):''),pick('#reference-status'))}catch(e){message(e.message,true)}finally{b.disabled=false}};
 document.addEventListener('documents-ready',()=>{ready=true;if(!loading){loading=true;Promise.all([updateActivity(),references()]).catch(e=>message(e.message,true)).finally(()=>loading=false)}});
 if(!pick('#workspace').hidden){ready=true;updateActivity().catch(e=>message(e.message,true));references().catch(e=>message(e.message,true))}
 if(location.hash==='#reference-activity'){pick('#document-activity').open=true;pick('#reference-activity').open=true}
 setInterval(async()=>{if(!ready||loading||uploading||pick('#workspace').hidden||document.activeElement?.closest('#reference-rows form'))return;
  loading=true;try{const wasPending=activity.some(r=>!finished(r));await updateActivity();if(pick('#reference-activity').open)await references();if(wasPending)await search()}catch(e){message(e.message,true)}finally{loading=false}
 },4000);
})();

function documentReview(row,box){
 const section=add('details',undefined,box);add('summary','Review and correct information',section);
 const output=row.extraction;
 if(!output){add('p','Extracted details are not available yet. Review will be available when extraction succeeds.',section,'muted');return}
 const draft=JSON.parse(JSON.stringify(output));const form=add('form',undefined,section);
 add('p','Check the original and saved evidence before verifying. Corrections keep the existing evidence and are recorded in review history.',form,'muted');
 for(const[index,segment]of draft.segments.entries()){
  const group=add('fieldset',undefined,form);add('legend','Part '+(index+1)+' | '+segment.category,group);
  const typeLabel=add('label','Document type',group);const type=add('select',undefined,typeLabel);
  for(const name of types){const option=add('option',name,type);option.value=name}type.value=segment.category;
  type.onchange=()=>{segment.category=type.value;if(draft.segments.length===1)draft.category=type.value};
  function editFields(fields,parent){for(const[key,value]of Object.entries(fields||{}))if(value&&typeof value==='object'&&'raw' in value){
   const label=add('label',key.replaceAll('_',' '),parent);const field=add('input',undefined,label);field.value=value.raw;field.required=true;
   const normalLabel=add('label','Standardized value',parent);const normalized=add('input',undefined,normalLabel);normalized.value=value.normalized??'';
   normalized.oninput=()=>{value.normalized=normalized.value||null};
   field.oninput=()=>{const source=row.sources.find(s=>s.text.includes(field.value));field.setCustomValidity(field.value&&source?'':'Use wording present in the saved document text.');if(field.value&&source){value.raw=field.value;const start=source.text.indexOf(field.value);value.evidence=[{source_id:source.id,excerpt:source.text.slice(start,start+Math.max(650,field.value.length))}]}};
  }}
  editFields(segment.fields,group);for(const[index,item]of(segment.line_items||[]).entries()){const itemGroup=add('fieldset',undefined,group);add('legend','Line item '+(index+1),itemGroup);editFields(item,itemGroup)}
 }
 const actorLabel=add('label','Reviewer name',form);const actor=add('input',undefined,actorLabel);actor.required=true;
 const statusLabel=add('label','Review status',form);const state=add('select',undefined,statusLabel);for(const name of ['Pending','Verified','Needs clarification'])add('option',name,state);state.value=row.review_status;
 const save=add('button','Save review',form);form.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{
  await api('/api/library/'+row.id+'/review',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({output:draft,status:state.value,actor:actor.value})});await inspect(row.id);await search();message('Review saved.');
 }catch(e){message(e.message,true)}finally{save.disabled=false}};
}
