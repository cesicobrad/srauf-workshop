/* Workshop additions use the same saved API state as the original connected journey. */
window.WorkshopFeatures=(()=>{
  let ctx,chosenFiles=[],filter='all',team='all',selectedGap='',gapDrafts={},quoteDrafts={},workDrafts={},quoteSignature='',inboxSignature='',pendingDecisions={};
  const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
  const money=cents=>'€'+(cents/100).toFixed(2);
  const escape=v=>ctx.esc(v);
  const itemsTable=q=>`<table class="quote-table"><thead><tr><th>Work / parts</th><th>Qty</th><th>Unit</th><th>Amount</th></tr></thead><tbody>${q.items.map(i=>`<tr><td>${escape(i.description)}</td><td>${escape(i.quantity)}</td><td>€${escape(i.unit_price)}</td><td>${money(i.amount_cents)}</td></tr>`).join('')}</tbody><tfoot><tr><th colspan="3">Total entered by staff</th><td>${money(q.total_cents)}</td></tr></tfoot></table>`;
  async function attachExample(){
    const response=await fetch('/assets/sample-repair.png');if(!response.ok)throw new Error('The example sketch could not be loaded.');
    chosenFiles=[new File([await response.blob()],'sample-frame-sketch.png',{type:'image/png'})];$('#repair-photos').value='';
    $('#upload-preview').innerHTML='<span class="pill">Example frame sketch attached</span>';
  }
  function clearPhotos(){chosenFiles=[];$('#repair-photos').value='';$('#upload-preview').replaceChildren();}
  function showAll(){filter=team='all';$('#work-filter').value='all';$('#team-filter').value='all';}
  function prefillQuote(e){
    if(!e)return;
    quoteDrafts[e.id]={items:[{description:'Inspection and labour',quantity:'1.5',unit_price:'40'},{description:'Replacement part',quantity:'1',unit_price:'25'}],turnaround:'2–3 working days after inspection',notes:'Example amounts for this demonstration.',valid_days:7};
    renderQuoteEditor(e);ctx.toast('Example items filled. Edit them, then save the quote for review.');
  }

  function init(context){
    ctx=context;
    $('#lead-submit').insertAdjacentHTML('beforebegin',`<label for="repair-photos">Photos for the mechanic <span class="optional">optional</span></label><input id="repair-photos" type="file" accept="image/jpeg,image/png,image/webp" multiple aria-describedby="photo-help"><p id="photo-help" class="micro">Up to 3 photos · 4 MB each · JPEG, PNG or WebP.</p><button id="sample-photo" class="text-button" type="button">Attach an example frame sketch</button><div id="upload-preview" class="upload-preview"></div>`);
    $('#sample-photo').addEventListener('click',ctx.action(async()=>{await attachExample();ctx.toast('Example sketch attached.');}));
    $('#repair-photos').addEventListener('change',ctx.action(async()=>{
      chosenFiles=[...$('#repair-photos').files];
      if(chosenFiles.length>3||chosenFiles.some(f=>f.size>4*1024*1024)){chosenFiles=[];$('#repair-photos').value='';throw new Error('Choose at most three photos, each no larger than 4 MB.');}
      $('#upload-preview').replaceChildren();
      for(const file of chosenFiles){const span=document.createElement('span');span.className='pill';span.textContent=file.name;$('#upload-preview').append(span);}
    }));
    $('#request-progress').insertAdjacentHTML('afterend','<section class="quote-receipts customer-mailbox" hidden><div class="section-title"><div><p class="eyebrow">YOUR REPAIR QUOTE</p><h3>Review the workshop’s offer.</h3></div></div><div id="customer-quotes"></div></section>');
    $('#tab-proof').insertAdjacentHTML('beforebegin','<button id="tab-learn" role="tab" aria-selected="false" aria-controls="panel-learn" data-tab="learn">Improve answers <span class="count" id="gap-count">0</span></button>');
    $('#tab-learn').addEventListener('click',()=>ctx.view('learn'));
    $('#panel-proof').insertAdjacentHTML('beforebegin',`<div id="panel-learn" role="tabpanel" aria-labelledby="tab-learn" class="panel" hidden><div class="desk-intro"><div><p class="eyebrow">OWNER-APPROVED IMPROVEMENTS</p><h3>Turn a missing answer into useful knowledge.</h3><p>Review unanswered questions, write an approved answer and test it. Customer messages never teach the assistant automatically.</p></div></div><div class="learning-layout"><aside id="gap-list" class="enquiry-list"></aside><section id="gap-editor" class="enquiry-detail"></section></div><section class="proof-card"><h3>Approved answers</h3><div id="approved-list"></div><p class="micro">Offline mode matches the approved question exactly, ignoring case and final punctuation. Live AI also retrieves these approved answers as sources. Critical repair assessments still go to a mechanic.</p></section></div>`);
    $('.desk-intro').insertAdjacentHTML('afterend',`<div class="queue-filters"><label for="work-filter">Show<select id="work-filter"><option value="all">All enquiries</option><option value="action">Needs action</option><option value="overdue">Overdue</option></select></label><label for="team-filter">Assigned to<select id="team-filter"><option value="all">Everyone</option><option>Unassigned</option><option>Nika</option><option>Luka</option><option>Maja</option></select></label></div>`);
    $('#work-filter').addEventListener('change',()=>{filter=$('#work-filter').value;ctx.refreshDesk();});
    $('#team-filter').addEventListener('change',()=>{team=$('#team-filter').value;ctx.refreshDesk();});
    $('#desk-stats').insertAdjacentHTML('afterend','<div id="overdue-alerts" class="overdue-alerts" aria-live="polite"></div>');
    document.body.insertAdjacentHTML('beforeend','<dialog id="photo-viewer" class="photo-viewer" aria-label="Repair photo"><form method="dialog"><button class="button secondary">Close photo ×</button></form><img id="full-photo" alt=""><p class="micro" id="photo-caption"></p></dialog>');
    $('#photo-viewer').addEventListener('click',e=>{if(e.target===$('#photo-viewer'))$('#photo-viewer').close();});
    $('.scenario-bar').insertAdjacentHTML('beforeend','<button class="scenario" data-extra-scenario="quote"><span>05</span><b>From photo to quote</b><small>Staff price it. Customer decides.</small></button><button class="scenario" data-extra-scenario="learn"><span>06</span><b>Fill a knowledge gap</b><small>Approve an answer. Test it.</small></button>');
    $$('[data-extra-scenario]').forEach(b=>b.addEventListener('click',ctx.action(async()=>{
      $$('.scenario').forEach(s=>s.classList.toggle('active',s===b));
      if(b.dataset.extraScenario==='quote'){await ctx.beginTour();}
      else{const question='Is secure indoor storage available while my bike is awaiting inspection?';await ctx.api('/api/chat',{message:question,lang:'en'});await ctx.loadState();selectedGap=ctx.state().knowledge_gaps.find(g=>g.question===question)?.id||'';inboxSignature='';renderKnowledge(ctx.state());ctx.view('learn');ctx.guide('This question went through the chat. If it is unanswered, review it here, write the workshop-approved answer, then test it.');}
    })));
  }

  async function leadData(){
    const photos=[];
    for(const f of chosenFiles){const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('Could not read the selected photo.'));reader.readAsDataURL(f);});photos.push({filename:f.name,data_base64:data});}
    return {photos};
  }
  function visibleEnquiries(all){return all.filter(e=>(team==='all'||e.assignee===team)&&(filter==='all'||filter==='overdue'&&e.overdue||filter==='action'&&(e.overdue||/review|failed|uncertain|question/.test(e.status)||!['Wait for customer','Completed'].includes(e.next_action))));}

  function desk(e){
    $('#enquiry-detail .detail-top').insertAdjacentHTML('afterend','<button class="text-button" id="view-this-customer">View this customer’s side →</button>');
    $('#view-this-customer').addEventListener('click',()=>ctx.showCustomer(e.id));
    const photos=e.photos||[];
    $('#enquiry-detail .detail-question').insertAdjacentHTML('afterend',`<div class="photo-handover"><p class="detail-label">ATTACHMENTS FOR THE MECHANIC · ${photos.length}</p>${photos.length?`<div class="photo-grid">${photos.map(p=>`<button class="photo-thumb" data-photo="${p.id}" data-caption="${escape(p.filename)}" aria-label="Open repair photo: ${escape(p.filename)}"><img src="/api/photos/${p.id}" alt="${escape(p.filename)}"><span>${escape(p.filename)}</span></button>`).join('')}</div>`:'<p class="micro">No photos attached. Use “Request photos” as the next action if you need a closer look.</p>'}</div>`);
    $$('[data-photo]').forEach(b=>b.addEventListener('click',()=>{$('#full-photo').src='/api/photos/'+b.dataset.photo;$('#full-photo').alt=b.dataset.caption;$('#photo-caption').textContent=b.dataset.caption;$('#photo-viewer').showModal();}));
    const work=workDrafts[e.id]||{assignee:e.assignee,next_action:e.next_action,due_hours:''};
    $('#enquiry-detail .detail-question').insertAdjacentHTML('beforebegin',`<form class="work-form" id="work-form"><div><label for="assignee">Assigned to</label><select id="assignee">${ctx.state().staff.map(s=>`<option ${s===work.assignee?'selected':''}>${escape(s)}</option>`).join('')}</select></div><div><label for="next-action">Next action</label><select id="next-action">${ctx.state().next_actions.map(s=>`<option ${s===work.next_action?'selected':''}>${escape(s)}</option>`).join('')}</select></div><div><label for="due-hours">Due</label><select id="due-hours"><option value="">Keep current due time</option>${[4,24,48,72,168].map(h=>`<option value="${h}" ${String(h)===work.due_hours?'selected':''}>In ${h} hours</option>`).join('')}</select></div><button class="button secondary" type="submit">Save assignment</button><p class="micro work-due">${e.overdue?'<strong class="overdue-text">Overdue · </strong>':''}${e.due_at?'Due '+escape(ctx.dateLabel(e.due_at)):'No due time'} · ${escape(e.next_action)}</p></form>`);
    $('#work-form').addEventListener('change',()=>workDrafts[e.id]={assignee:$('#assignee').value,next_action:$('#next-action').value,due_hours:$('#due-hours').value});
    $('#work-form').addEventListener('submit',ctx.action(async()=>{await ctx.api(`/api/enquiries/${e.id}/work`,{version:e.version,assignee:$('#assignee').value,next_action:$('#next-action').value,due_hours:$('#due-hours').value?Number($('#due-hours').value):null},'PATCH');delete workDrafts[e.id];ctx.toast('Assignment and due time saved.');await ctx.loadState();}));
    const history=ctx.state().enquiries.filter(item=>item.email.toLowerCase()===e.email.toLowerCase());
    const accepted=history.reduce((n,item)=>n+(item.quotes||[]).filter(q=>q.state==='accepted').length,0);
    const deliveries=history.reduce((n,item)=>n+item.outbox.filter(o=>o.state==='delivered').length,0);
    $('#enquiry-detail').insertAdjacentHTML('beforeend',`<section class="quote-builder" id="quote-builder"><div class="section-title"><div><p class="eyebrow">STAFF SET THE PRICE</p><h3>${e.quotes?.[0]?.state==='accepted'?'Accepted repair quote':'Prepare a repair quote'}</h3></div><span class="pill">Review before sending</span></div><p class="micro">Enter the final prices for labour and parts.</p><div id="quote-editor"></div><div id="staff-quote"></div></section><details class="crm-detail"><summary>Customer record <span>${history.length} saved ${history.length===1?'enquiry':'enquiries'}</span></summary><p class="micro">${escape(e.name)} · ${escape(e.email)}</p><div class="customer-record-stats"><div><strong>${history.length}</strong><span>Saved enquiries</span></div><div><strong>${accepted}</strong><span>Accepted quotes</span></div><div><strong>${deliveries}</strong><span>Delivered messages</span></div></div><a class="button secondary" href="/api/export.csv">Export enquiry list ↗</a></details>`);
    if(ctx.state().live_crm){
      const crmLabel=({not_synced:'Not synced',queued:'Waiting to sync',synced:'Synced',failed:'Needs attention'})[e.crm?.state]||'Not synced';
      $('.crm-detail').insertAdjacentHTML('beforeend',`<details class="optional-crm"><summary>Optional HubSpot connection</summary><p class="micro">${escape(crmLabel)}${e.crm?.contact_id?' · Contact '+escape(e.crm.contact_id):''}${e.crm?.error?' · '+escape(e.crm.error):''}</p><button id="crm-sync" class="button secondary">Sync allowlisted test contact</button></details>`);
      $('#crm-sync').addEventListener('click',ctx.action(async()=>{await ctx.api(`/api/enquiries/${e.id}/crm-sync`,{});ctx.toast('HubSpot job queued through n8n.');await ctx.loadState();}));
    }
    renderQuoteEditor(e);
  }

  function draftFor(e){
    if(quoteDrafts[e.id])return quoteDrafts[e.id];
    const old=e.quotes?.[0];
    return quoteDrafts[e.id]={items:old?old.items.map(({description,quantity,unit_price})=>({description,quantity,unit_price})):[{description:'Labour',quantity:'1',unit_price:''},{description:'Replacement parts',quantity:'1',unit_price:''}],turnaround:old?.turnaround||'',notes:old?.notes||'',valid_days:7};
  }
  function previewTotal(items){
    try{return items.reduce((sum,i)=>{if(!/^\d+(\.\d{1,3})?$/.test(i.quantity)||!/^\d+(\.\d{1,2})?$/.test(i.unit_price))return sum;const q=BigInt(i.quantity.split('.')[0]+(i.quantity.split('.')[1]||'').padEnd(3,'0'));const p=BigInt(i.unit_price.split('.')[0]+(i.unit_price.split('.')[1]||'').padEnd(2,'0'));return sum+Number((q*p+500n)/1000n);},0);}catch{return 0;}
  }
  function renderQuoteEditor(e){
    const current=e.quotes?.[0],draft=draftFor(e);
    const blocked=current&&['accepted','sending','uncertain'].includes(current.state)||['queued','followup_queued','delivery_queued','delivery_uncertain'].includes(e.status);
    $('#quote-editor').innerHTML=blocked?`<p class="notice ${current?.state==='accepted'?'success':'warning'}">${current?.state==='accepted'?'Quote accepted. Arrange the next step with the customer.':'Finish or resolve the current delivery before replacing its quote.'}</p>`:`<form id="quote-form"><div id="quote-lines">${draft.items.map((i,index)=>`<div class="quote-line" data-line="${index}"><div><label for="quote-description-${index}">Work / part</label><input id="quote-description-${index}" data-item="description" value="${escape(i.description)}" maxlength="180" required></div><div><label for="quote-quantity-${index}">Qty</label><input id="quote-quantity-${index}" data-item="quantity" type="number" min="0.001" max="1000" step="0.001" value="${escape(i.quantity)}" required></div><div><label for="quote-price-${index}">Unit (€)</label><input id="quote-price-${index}" data-item="unit_price" type="number" min="0" max="10000" step="0.01" value="${escape(i.unit_price)}" placeholder="0.00" required></div><button type="button" class="remove-line" data-remove="${index}" aria-label="Remove quote line ${index+1}" ${draft.items.length===1?'disabled':''}>×</button></div>`).join('')}</div><button type="button" id="add-quote-line" class="text-button" ${draft.items.length>=12?'disabled':''}>+ Add a line</button><div class="quote-preview-total">Draft total <strong id="quote-total">${money(previewTotal(draft.items))}</strong></div><label for="quote-turnaround">Estimated turnaround</label><input id="quote-turnaround" value="${escape(draft.turnaround)}" maxlength="200" placeholder="e.g. 2–3 working days after inspection" required><label for="quote-notes">What should the customer know?</label><textarea id="quote-notes" rows="2" maxlength="1000">${escape(draft.notes)}</textarea><label for="quote-validity">Valid for</label><select id="quote-validity">${[3,7,14,30].map(d=>`<option value="${d}" ${d===Number(draft.valid_days)?'selected':''}>${d} days</option>`).join('')}</select><button type="submit" class="button primary">Save quote for review <span>→</span></button></form>`;
    $('#quote-form')?.addEventListener('input',()=>{draft.items=$$('.quote-line').map(row=>({description:row.querySelector('[data-item=description]').value,quantity:row.querySelector('[data-item=quantity]').value,unit_price:row.querySelector('[data-item=unit_price]').value}));draft.turnaround=$('#quote-turnaround').value;draft.notes=$('#quote-notes').value;draft.valid_days=Number($('#quote-validity').value);$('#quote-total').textContent=money(previewTotal(draft.items));});
    $('#quote-form')?.addEventListener('change',()=>draft.valid_days=Number($('#quote-validity').value));
    $('#add-quote-line')?.addEventListener('click',()=>{draft.items.push({description:'',quantity:'1',unit_price:''});renderQuoteEditor(e);});
    $$('[data-remove]').forEach(b=>b.addEventListener('click',()=>{draft.items.splice(Number(b.dataset.remove),1);renderQuoteEditor(e);}));
    $('#quote-form')?.addEventListener('submit',ctx.action(async()=>{await ctx.api(`/api/enquiries/${e.id}/quotes`,{enquiry_version:e.version,...draft});delete quoteDrafts[e.id];ctx.toast('Quote saved. Review its exact total before approving delivery.');await ctx.loadState();}));
    $('#staff-quote').innerHTML=current?`<div class="saved-quote"><div class="section-title"><h4>Saved revision ${current.revision}</h4><span class="status ${current.state==='accepted'?'sent':'review'}">${escape(current.expired?'expired':current.state)}</span></div>${itemsTable(current)}<p class="micro">Turnaround: ${escape(current.turnaround)}<br>Valid until ${escape(ctx.dateLabel(current.valid_until))}</p>${current.notes?`<p class="micro">${escape(current.notes)}</p>`:''}${current.customer_note?`<div class="notice warning">Customer question: ${escape(current.customer_note)}</div>`:''}${current.state==='draft'&&!current.expired?`<button id="approve-quote" class="button primary">Approve & ${ctx.state().delivery_mode==='preview'?'deliver demo quote':'send test quote'} <span>↗</span></button>`:''}</div>`:'';
    $('#approve-quote')?.addEventListener('click',ctx.action(async()=>{await ctx.api(`/api/quotes/${current.id}/approve`,{version:current.version});ctx.toast('Reviewed quote queued for delivery through n8n.');await ctx.loadState();}));
  }

  function renderCustomerQuotes(state){
    const customer=ctx.customer();
    const quotes=(customer?.quotes||[]).filter(q=>q.sent_at).sort((a,b)=>b.revision-a.revision);
    const signature=JSON.stringify([customer?.id,quotes]);if(signature===quoteSignature)return;quoteSignature=signature;
    $('.quote-receipts').hidden=!quotes.length;
    const label=q=>q.expired?'Expired':({sent:'Ready to review',questions:'Question sent',accepted:'Accepted',superseded:'Replaced'})[q.state]||q.state;
    function card(q,current){
      const canDecide=current&&['sent','questions'].includes(q.state)&&!q.expired;
      return `<article class="customer-quote"><div class="section-title"><div><p class="eyebrow">${escape(customer.name)} · VERSION ${q.revision}</p><h4>${money(q.total_cents)}</h4></div><span class="status ${q.state==='accepted'?'sent':'review'}">${escape(label(q))}</span></div>${itemsTable(q)}<p>Estimated turnaround: ${escape(q.turnaround)}</p>${q.notes?`<p class="muted">${escape(q.notes)}</p>`:''}<p class="micro">Valid until ${escape(ctx.dateLabel(q.valid_until))}.</p>${canDecide?`<div class="quote-decisions"><button class="button primary" data-accept="${q.id}">Accept demo quote <span>✓</span></button><details class="quote-question"><summary>Ask a question first</summary><label for="quote-question-${q.id}">Your question</label><textarea id="quote-question-${q.id}" rows="2" maxlength="2000" placeholder="What would you like the workshop to clarify?"></textarea><button class="button secondary" data-quote-question="${q.id}">Ask the workshop</button></details></div>`:q.state==='accepted'?'<div class="notice success">Quote accepted. The workshop has your decision.</div>':q.state==='superseded'?'<p class="notice">The workshop is preparing an updated quote.</p>':''}</article>`;
    }
    $('#customer-quotes').innerHTML=quotes.length?card(quotes[0],true)+(quotes.length>1?`<details class="quote-history"><summary>Earlier quote versions (${quotes.length-1})</summary>${quotes.slice(1).map(q=>card(q,false)).join('')}</details>`:''):'';
    async function decision(qid,choice,note=''){
      const q=quotes.find(q=>q.id===qid);
      const payload={token:q.decision_token,decision:choice,note};
      const key=qid+choice+note;
      pendingDecisions[key]??=ctx.newId();
      await ctx.api(`/api/quotes/${qid}/decision`,payload,'POST',{'idempotency-key':pendingDecisions[key]});
      ctx.toast(choice==='accept'?'Quote accepted. The workshop has your decision.':'Your question was sent to the workshop.');await ctx.loadState();
    }
    $$('[data-accept]').forEach(b=>b.addEventListener('click',ctx.action(()=>decision(b.dataset.accept,'accept'))));
    $$('[data-quote-question]').forEach(b=>b.addEventListener('click',ctx.action(async()=>{const note=$('#quote-question-'+b.dataset.quoteQuestion).value.trim();if(note.length<2)throw new Error('Write a question for the workshop first.');await decision(b.dataset.quoteQuestion,'question',note);})));
  }

  function renderKnowledge(state){
    const signature=JSON.stringify([state.knowledge_gaps,state.approved_answers,selectedGap]);
    $('#gap-count').textContent=state.knowledge_gaps.filter(g=>g.state==='open').length;
    if(signature===inboxSignature)return;inboxSignature=signature;
    if(!state.knowledge_gaps.some(g=>g.id===selectedGap))selectedGap=state.knowledge_gaps[0]?.id||'';
    $('#gap-list').innerHTML=state.knowledge_gaps.length?state.knowledge_gaps.map(g=>`<button class="enquiry-button ${g.id===selectedGap?'selected':''}" data-gap="${g.id}"><strong>${escape(g.question)}</strong><small>${escape(g.language.toUpperCase())} · asked ${g.occurrences} time(s)</small><span class="status ${g.state==='resolved'?'sent':'review'}">${escape(g.state)}</span></button>`).join(''):'<p class="empty">No unanswered questions yet. Use “Fill a knowledge gap” to send a genuine example question through the assistant.</p>';
    $$('[data-gap]').forEach(b=>b.addEventListener('click',()=>{selectedGap=b.dataset.gap;inboxSignature='';renderKnowledge(ctx.state());}));
    const gap=state.knowledge_gaps.find(g=>g.id===selectedGap);
    const answer=state.approved_answers.find(a=>a.id===gap?.answer_id);
    $('#gap-editor').innerHTML=gap?`<p class="eyebrow">REVIEW BEFORE PUBLISHING · ${escape(gap.language.toUpperCase())}</p><h3>${escape(gap.question)}</h3><p class="muted">The assistant could not answer this question. Write only information the workshop has approved.</p><form id="approved-answer-form"><label for="approved-answer">Approved answer</label><textarea id="approved-answer" rows="5" minlength="5" maxlength="2000" required>${escape(gapDrafts[gap.id]??answer?.answer??'')}</textarea><button class="button primary" type="submit">Publish approved answer <span>↑</span></button></form><button class="button secondary" id="test-approved-answer">Test this question now</button><div id="answer-test-result" class="notice" hidden aria-live="polite"></div>`:'<p class="empty">Select a question to review its answer.</p>';
    $('#approved-answer')?.addEventListener('input',()=>gapDrafts[gap.id]=$('#approved-answer').value);
    $('#approved-answer-form')?.addEventListener('submit',ctx.action(async()=>{await ctx.api(`/api/knowledge/gaps/${gap.id}/approve`,{answer:$('#approved-answer').value,version:answer?.version??null});delete gapDrafts[gap.id];inboxSignature='';await ctx.loadState();ctx.toast('Owner-approved answer published. Test it using the same chat API.');}));
    $('#test-approved-answer')?.addEventListener('click',ctx.action(async()=>{const result=await ctx.api('/api/chat',{message:gap.question,lang:gap.language});const box=$('#answer-test-result');box.hidden=false;box.className='notice '+(result.status==='answered'?'success':'warning');box.textContent=result.reply+(result.sources.length?' · Source: '+result.sources.map(s=>s.title).join(', '):'');}));
    $('#approved-list').innerHTML=state.approved_answers.length?state.approved_answers.map(a=>`<details class="approved-answer"><summary>${escape(a.question)} <span class="pill">${escape(a.language.toUpperCase())} · v${a.version}</span></summary><p>${escape(a.answer)}</p></details>`).join(''):'<p class="muted">No answers have been approved yet.</p>';
  }

  function renderState(state){
    renderCustomerQuotes(state);renderKnowledge(state);
    $('#overdue-alerts').innerHTML=state.alerts.map(a=>`<div class="overdue-notice"><div><strong>${escape(a.name)} · overdue</strong><p>${escape(a.assignee)} · ${escape(a.next_action)} · due ${escape(ctx.dateLabel(a.due_at))}</p></div><button class="button secondary" data-ack="${a.id}">Acknowledge</button></div>`).join('');
    $$('[data-ack]').forEach(b=>b.addEventListener('click',ctx.action(async()=>{await ctx.api(`/api/alerts/${b.dataset.ack}/acknowledge`,{});await ctx.loadState();})));
    const live=state.delivery_mode!=='preview'||state.gmail_inbox||state.live_crm;
    $('#advance-clock').disabled=live;$('#pause-delivery').disabled=live;
  }

  return {init,leadData,visibleEnquiries,desk,renderState,attachExample,clearPhotos,showAll,prefillQuote};
})();
