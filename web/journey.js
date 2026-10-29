/* Navigation follows saved server state. It never fabricates a submitted or delivered step. */
window.WorkshopJourney=(()=>{
  let ctx,signature='',choiceSignature='',previousCustomer=null;
  const $=s=>document.querySelector(s);
  function jump(selector){const el=$(selector);if(!el)return;el.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});}
  function init(context){
    ctx=context;
    $('#demo-customer').addEventListener('change',()=>{ctx.selectCustomer($('#demo-customer').value);ctx.view('customer');jump('#panel-customer');});
    $('.customer-side').prepend($('#handoff-card'));
    const owner=document.createElement('details');owner.className='owner-settings';owner.innerHTML='<summary>Prices, hours & workshop policy</summary>';
    owner.append($('.owner-section'));$('#panel-learn').append(owner);
    const recovery=document.createElement('details');recovery.className='demo-options';recovery.id='recovery-tools';recovery.innerHTML='<summary>Recovery & demo controls</summary>';
    recovery.append($('.recovery-section'));$('#panel-proof').append(recovery);
    $('#guide').hidden=true;
  }
  function stage(e){
    if(!e)return ctx.guided()?{step:0,title:'Send your repair request.',copy:'The example details and sketch are ready. You can edit them before sending.',action:'send',label:'Send example request'}:{step:0,title:'Try one complete repair journey.',copy:'Send an example request, review it at the workshop and decide on the quote. Your choices run through the real workflow.',action:'start',label:'Start guided demo'};
    if(/failed|uncertain/.test(e.status))return {step:1,title:'The request is safe. Delivery needs attention.',copy:'Open the saved request to review the problem and recovery options.',action:'desk',label:'Review saved request'};
    const q=e.quotes?.[0];
    if(q?.state==='accepted')return {step:4,title:'The full handover is complete.',copy:'The customer accepted the quote. Scheduling is now the workshop’s next action.',action:'proof',label:'See what was verified'};
    if(q?.state==='sending'||e.status==='delivery_queued')return {step:2,title:'The reviewed quote is being delivered.',copy:'The exact approved message is saved. This page updates when delivery is confirmed.',action:'desk',label:'View workshop progress'};
    if(q?.state==='draft'&&!q.expired)return {step:2,title:'Review the saved quote, then approve it.',copy:'Check the items, total and turnaround in the workshop view before choosing Approve & deliver.',action:'review',label:'Review saved quote'};
    if(q&&['sent','questions'].includes(q.state)&&!q.expired)return {step:3,title:q.state==='questions'?'The customer has a question.':'The customer can now decide.',copy:q.state==='questions'?'Review the question at the workshop, or return to the customer quote.':'The quote was delivered. Return to the customer side to ask a question or accept it.',action:q.state==='questions'?'desk':'quote',label:q.state==='questions'?'Review customer question':'Open customer quote'};
    if(e.status==='queued')return {step:1,title:'Request received. Preparing the workshop review.',copy:'The conversation and attachments are saved. The workflow is preparing a staff draft.',action:'desk',label:'View workshop desk'};
    return {step:1,title:'Review the request and prepare a quote.',copy:'Read the conversation and photos, assign the work, then enter the repair items. Example prices are available for this guided preview.',action:'prepare',label:'Prepare example quote'};
  }
  function customerStatus(e){
    if(e.quotes?.[0]?.state==='accepted')return ['Quote accepted','The workshop’s next step is to arrange the repair.'];
    if(e.quotes?.[0]?.state==='questions')return ['Your question is with the workshop','The team can clarify the quote or send an updated version.'];
    if(e.quotes?.[0]?.state==='sent')return ['Your quote is ready','Review the total below, then accept it or ask the workshop a question.'];
    if(e.status==='quote_expired')return ['An updated quote is needed','The previous quote expired. The workshop can prepare a new version.'];
    if(e.status==='customer_replied')return ['Your reply was received','The workshop has your message and will review the next step.'];
    if(/sent|followup_review/.test(e.status))return ['The workshop has replied','Read the latest message below.'];
    return ['Request received','Your details are saved. The workshop is reviewing your repair request.'];
  }
  function render(state){
    if(!ctx)return;
    const e=ctx.customer(),step=stage(e),escape=ctx.esc;
    const choices=JSON.stringify(state.enquiries.map(e=>[e.id,e.name,e.created_at]));
    if(choices!==choiceSignature){choiceSignature=choices;$('#demo-customer').innerHTML='<option value="">New customer conversation</option>'+state.enquiries.map(e=>`<option value="${e.id}">${escape(e.name)} · ${escape(ctx.dateLabel(e.created_at))}</option>`).join('');}
    $('#demo-customer').value=e?.id||'';
    if(previousCustomer!==(e?.id||'')){previousCustomer=e?.id||'';$('#conversation-area').open=!e;}
    const key=JSON.stringify([e?.id,e?.status,e?.version,step,ctx.guided(),state.delivery_mode,state.gmail_inbox,state.live_crm]);
    if(key===signature)return;signature=key;
    const preview=state.delivery_mode==='preview'&&!state.gmail_inbox&&!state.live_crm;
    const steps=['Send request','Workshop review','Prepare quote','Customer decision'];
    $('#journey').innerHTML=`<ol class="journey-steps" aria-label="Repair journey">${steps.map((text,i)=>`<li class="${i<step.step?'done':i===step.step?'current':''}" ${i===step.step?'aria-current="step"':''}><span>${i<step.step?'✓':i+1}</span>${text}</li>`).join('')}</ol><div class="journey-content"><div><p class="eyebrow">${step.step===4?'DEMO COMPLETE':`STEP ${Math.min(step.step+1,4)} OF 4`}</p><h3>${escape(step.title)}</h3><p>${escape(step.copy)}</p></div><div class="journey-actions"><button class="button primary" id="journey-action" ${!preview&&['start','send','prepare'].includes(step.action)?'disabled':''}>${escape(step.label)} <span aria-hidden="true">→</span></button>${e?'<button class="text-button" id="restart-demo">Start another example</button>':''}</div></div>${!preview?'<p class="micro">Guided examples use local preview mode. Live account modes use the ordinary request and review forms.</p>':''}`;
    $('#journey-action').addEventListener('click',ctx.action(async()=>{
      if(step.action==='start')await ctx.begin();
      if(step.action==='send')await ctx.send();
      if(['desk','prepare','review'].includes(step.action)){
        ctx.openDesk();
        if(step.action==='prepare'){ctx.prefillQuote();jump('#quote-editor');}
        else jump(step.action==='review'?'#staff-quote':'#enquiry-detail');
      }
      if(step.action==='quote'){ctx.view('customer');jump('.quote-receipts');}
      if(step.action==='proof'){ctx.view('proof');jump('#panel-proof');}
    }));
    $('#restart-demo')?.addEventListener('click',ctx.action(()=>ctx.begin()));
    const progress=$('#request-progress');progress.hidden=!e;
    if(e){const [title,copy]=customerStatus(e);progress.innerHTML=`<div><p class="eyebrow">YOUR REPAIR REQUEST · ${escape(e.name)}</p><h3>${escape(title)}</h3><p>${escape(copy)}</p></div><button class="button secondary" id="customer-to-workshop">Demo: review as workshop <span>→</span></button>`;
      $('#customer-to-workshop').addEventListener('click',()=>{ctx.openDesk();jump('#enquiry-detail');});}
  }
  return {init,render,jump};
})();
