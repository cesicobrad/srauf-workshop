/* A real local workbench. Guided buttons call the same APIs as ordinary use. */
const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const newId=()=>crypto.randomUUID().replaceAll('-','');
let state=null,controlToken='',selectedId='',activeTab='customer',deskSignature='',mailSignature='',factsDirty=false,factsVersion=0,busyChat=false;
let lastSubmission=null,pendingSubmission=null,toastTimer,startingTour=false;
let session={conversation_id:'',language:'en',messages:[],enquiry_id:'',guided:false};
try{Object.assign(session,JSON.parse(sessionStorage.getItem('srauf-connected-chat')||'{}'));}catch{}
if(!Array.isArray(session.messages))session.messages=[];
const saveSession=()=>{try{sessionStorage.setItem('srauf-connected-chat',JSON.stringify({...session,messages:session.messages.slice(-50)}));}catch{}};

function toast(text,error=false){const box=$('#toast');box.textContent=text;box.classList.toggle('error',error);box.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>box.hidden=true,6500);}
async function api(path,body,method='POST',extra={}){
  const response=await fetch(path,{method,headers:{'content-type':'application/json','x-demo-control':controlToken,...extra},...(body!==undefined?{body:JSON.stringify(body)}:{})});
  const result=await response.json();
  if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Check the entered fields and try again.');
  return result;
}
function action(fn){return async event=>{event?.preventDefault();const button=event?.currentTarget;if(button?.tagName==='BUTTON')button.disabled=true;try{await fn(event);}catch(error){toast(error.message||'The connection failed. Your saved work is still there.',true);}finally{if(button?.isConnected)button.disabled=false;}};}
function view(name){activeTab=name;$$('[data-tab]').forEach(b=>{const on=b.dataset.tab===name;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;});$$('[role=tabpanel]').forEach(p=>p.hidden=p.id!=='panel-'+name);if(name==='desk')renderDesk();}
function customerEnquiry(){return state?.enquiries.find(e=>e.id===session.enquiry_id)||null;}
function selectCustomer(id){
  const e=state?.enquiries.find(e=>e.id===id);
  session={conversation_id:e?.conversation_id||'',language:e?.language||'en',messages:e?e.transcript.map(m=>({role:m.role==='user'?'user':'assistant',text:m.text})):[],enquiry_id:e?.id||'',guided:false};
  saveSession();$('#language').value=session.language;renderChat();
  $('#lead-name').value=e?.name||'Maja Novak';$('#lead-email').value=e?.email||'maja@customer.example';$('#lead-question').value='';
  $('#handoff-card').hidden=true;$('#handoff-result').hidden=true;$('#duplicate-check').hidden=true;$('#duplicate-submit').hidden=true;
  lastSubmission=pendingSubmission=null;WorkshopFeatures.clearPhotos();$('#guide').hidden=true;
  renderMailbox();WorkshopFeatures.renderState(state);WorkshopJourney.render(state);
}
function openCustomer(id){selectCustomer(id);view('customer');WorkshopJourney.jump('#request-progress');}
function openCustomerDesk(){const e=customerEnquiry();if(!e)return;selectedId=e.id;WorkshopFeatures.showAll();deskSignature='';view('desk');}
$$('[data-tab]').forEach(b=>b.addEventListener('click',()=>view(b.dataset.tab)));
$('.tabs').addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const tabs=$$('[data-tab]');let i=tabs.findIndex(b=>b.dataset.tab===activeTab);i=event.key==='Home'?0:event.key==='End'?tabs.length-1:(i+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;view(tabs[i].dataset.tab);tabs[i].focus();});

function renderChat(){
  const initial='<div class="bubble assistant">Hi! Ask me about service prices or opening hours. For a custom repair, I’ll pass your question to the workshop.</div>';
  $('#chat-log').innerHTML=initial+session.messages.map(m=>`<div class="bubble ${m.role==='user'?'user':'assistant'}">${esc(m.text)}${m.sources?.length?`<span class="citation">${m.sources.map(s=>esc(s.title)+(s.version?' · v'+esc(s.version):'')).join('<br>')}</span>`:''}</div>`).join('');
  $('#chat-log').scrollTop=$('#chat-log').scrollHeight;
}
async function ask(question){
  if(busyChat||!question.trim())return;
  busyChat=true;$('#chat-form button').disabled=true;
  session.messages.push({role:'user',text:question});renderChat();
  const waiting=document.createElement('div');waiting.className='bubble assistant waiting';waiting.textContent='Checking the workshop information…';$('#chat-log').append(waiting);
  try{
    const result=await api('/api/chat',{message:question,conversation_id:session.conversation_id,lang:session.language});
    session.conversation_id=result.conversation_id;session.messages.push({role:'assistant',text:result.reply,sources:result.sources});saveSession();renderChat();
    if(result.offer_handoff){$('#lead-question').value=question;$('#handoff-card').hidden=false;$('#conversation-area').open=true;$('#handoff-card').classList.add('highlight');if(!session.guided)guide('A mechanic can help with this. Add your details and send the repair request.');}
    $('#question').value='';
  }catch(error){session.messages.pop();renderChat();throw error;}
  finally{busyChat=false;$('#chat-form button').disabled=false;}
}
$('#language').value=session.language;
$('#language').addEventListener('change',()=>{session.language=$('#language').value;saveSession();});
$('#chat-form').addEventListener('submit',action(async()=>ask($('#question').value)));
$$('[data-question]').forEach(b=>b.addEventListener('click',action(()=>ask(b.dataset.question))));

async function submitRequest(data,key){
  const result=await api('/api/enquiries',data,'POST',{'idempotency-key':key});
  selectedId=result.enquiry.id;
  session.enquiry_id=result.enquiry.id;saveSession();
  $('#handoff-result').hidden=false;
  $('#handoff-result').textContent=result.duplicate?'Already received. Your original request is saved.':'Request received. Your conversation and attachments are saved for the workshop.';
  $('#duplicate-check').hidden=false;
  $('#duplicate-submit').hidden=false;
  lastSubmission={data,key};
  $('#guide').hidden=true;
  await loadState();
  WorkshopJourney.jump('#request-progress');
  return result;
}
async function sendRequestForm(){
  if(!$('#handoff-form').reportValidity()){view('customer');$('#conversation-area').open=true;WorkshopJourney.jump('#handoff-card');return;}
  const button=$('#lead-submit');button.disabled=true;
  try{
    if(!session.conversation_id)await ask($('#lead-question').value);
    const data={conversation_id:session.conversation_id,name:$('#lead-name').value.trim(),email:$('#lead-email').value.trim(),question:$('#lead-question').value.trim(),language:session.language,...await WorkshopFeatures.leadData()};
    if(!pendingSubmission||JSON.stringify(pendingSubmission.data)!==JSON.stringify(data))pendingSubmission={data,key:newId()};
    await submitRequest(data,pendingSubmission.key);
  }finally{button.disabled=false;}
}
$('#handoff-form').addEventListener('submit',action(sendRequestForm));
$('#duplicate-submit').addEventListener('click',action(async()=>{if(lastSubmission){const r=await submitRequest(lastSubmission.data,lastSubmission.key);toast(r.duplicate?'Duplicate caught: one enquiry, one review job.':'Submitted.');}}));

function statusClass(status){return /failed|uncertain/.test(status)?'error':/review/.test(status)?'review':/sent|replied/.test(status)?'sent':'';}
function statusText(status){return ({queued:'Waiting for n8n',awaiting_review:'Needs your review',followup_review:'Review follow-up',delivery_queued:'Approved · queued',delivery_failed:'Delivery failed',delivery_uncertain:'Check email provider',automation_failed:'Handover failed',customer_replied:'Customer replied',followup_queued:'Follow-up queued',followup_sent:'Follow-up delivered',sent:'Reply delivered',rejected:'Rejected',quote_draft:'Quote ready to review',quote_sent:'Quote delivered',quote_question:'Question about quote',quote_accepted:'Demo quote accepted',quote_expired:'Quote expired'})[status]||status;}
function dateLabel(iso){try{return new Date(iso).toLocaleString('en-GB',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});}catch{return iso;}}
function eventHTML(event){return `<div class="audit-item"><div><p>${esc(event.note)}</p><time>${esc(dateLabel(event.created_at))}</time></div></div>`;}
function renderDesk(){
  if(!state)return;
  const all=state.enquiries;
  const visible=WorkshopFeatures.visibleEnquiries(all);
  const signature=JSON.stringify(all);
  $('#desk-stats').innerHTML=[['Enquiries',all.length],['To review',all.filter(e=>/review|quote_draft|quote_question/.test(e.status)).length],['Delivered',all.filter(e=>e.outbox.some(o=>o.state==='delivered')).length],['Needs attention',all.filter(e=>/failed|uncertain/.test(e.status)||e.overdue).length]].map(([label,n])=>`<div class="stat"><strong>${n}</strong><span>${label}</span></div>`).join('');
  if(!visible.some(e=>e.id===selectedId))selectedId=visible[0]?.id||'';
  if(signature===deskSignature&&$('#enquiry-detail').dataset.id===selectedId)return;
  deskSignature=signature;
  const previousEditor=$('#draft-body');
  const previous=previousEditor?{id:$('#enquiry-detail').dataset.id,original:$('#enquiry-detail').dataset.originalDraft,body:previousEditor.value,subject:$('#draft-subject').value}:null;
  $('#enquiry-list').innerHTML=visible.length?visible.map(e=>`<button class="enquiry-button ${e.id===selectedId?'selected':''}" data-select="${e.id}" aria-pressed="${e.id===selectedId}"><strong>${esc(e.name)}${e.overdue?' <span class="overdue-text">· Overdue</span>':''}</strong><small>${esc(e.question.slice(0,110))}</small><small>${esc(e.assignee)} · ${esc(e.next_action)}</small><span class="status ${statusClass(e.status)}">${esc(statusText(e.status))}</span></button>`).join(''):'<p class="empty">No enquiries match this view.</p>';
  $$('[data-select]').forEach(b=>b.addEventListener('click',()=>{selectedId=b.dataset.select;deskSignature='';renderDesk();}));
  const e=visible.find(x=>x.id===selectedId);if(!e){$('#enquiry-detail').innerHTML='<p class="empty">Choose another filter or send a new repair request.</p>';$('#enquiry-detail').dataset.id='';return;}
  const detail=$('#enquiry-detail');detail.dataset.id=e.id;detail.dataset.version=e.version;detail.dataset.originalDraft=e.draft_body;
  const reviewing=['awaiting_review','followup_review'].includes(e.status);
  const failure=e.tasks.filter(t=>t.state==='failed').at(-1);
  detail.innerHTML=`<div class="detail-top"><div><h3>${esc(e.name)}</h3><p>${esc(e.email)} · ${esc(e.language.toUpperCase())} · ${esc(dateLabel(e.created_at))}</p></div><span class="status ${statusClass(e.status)}">${esc(statusText(e.status))}</span></div>
    <div class="detail-question">${esc(e.question)}</div>
    <details class="transcript"><summary>Read the conversation (${e.transcript.length} messages)</summary>${e.transcript.map(m=>`<p><strong>${m.role==='user'?'Customer':'Assistant'}</strong>${esc(m.text)}</p>`).join('')}</details>
    ${reviewing?`<details class="reply-editor"><summary>${e.draft_kind==='followup'?'Review a follow-up message':'Send a message before quoting'} <span>Optional</span></summary><p class="draft-note">${state.ai_mode==='live'?'AI draft':'Draft template'} · Edit and review before sending.</p><label for="draft-subject">Subject</label><input id="draft-subject" maxlength="200" value="${esc(e.draft_subject)}"><label for="draft-body">Reply</label><textarea id="draft-body" rows="6" maxlength="5000">${esc(e.draft_body)}</textarea><div class="approval-buttons"><button class="button primary" id="approve-draft">${state.delivery_mode!=='preview'?'Approve & send via '+esc(state.delivery_mode.toUpperCase()):'Approve & deliver to preview inbox'} <span>↗</span></button><button class="button secondary" id="reject-draft">Reject draft</button></div></details>`:''}
    ${failure?`<div class="notice warning">${esc(failure.error)}<br><button class="text-button" id="retry-job">Retry the saved job</button></div>`:''}
    ${e.status==='delivery_uncertain'?'<div class="notice warning">Delivery may have reached the external mail server. Automatic retry is blocked. Check the provider’s sent log using the message ID before taking further action.</div>':''}
    ${e.status==='delivery_queued'?'<div class="notice success">Approved reply is saved in the outbox. n8n will attempt delivery.</div>':''}
    ${e.status==='queued'?'<p class="empty">The request is saved. Waiting for n8n to prepare the review.</p>':''}
    <details class="audit-disclosure"><summary>Activity & delivery details</summary><div class="audit-list">${e.events.map(eventHTML).join('')}</div></details>`;
  if(reviewing&&previous?.id===e.id&&previous.original===e.draft_body){$('#draft-body').value=previous.body;$('#draft-subject').value=previous.subject;}
  WorkshopFeatures.desk(e);
  detail.append(detail.querySelector('.audit-disclosure'));
  $('#approve-draft')?.addEventListener('click',action(async()=>{await api(`/api/enquiries/${e.id}/approve`,{version:e.version,subject:$('#draft-subject').value,body:$('#draft-body').value});toast('Exact approved reply saved to the outbox.');await loadState();}));
  $('#reject-draft')?.addEventListener('click',action(async()=>{await api(`/api/enquiries/${e.id}/reject`,{version:e.version});toast('Draft rejected. Nothing sent.');await loadState();}));
  $('#retry-job')?.addEventListener('click',action(async()=>{await api(`/api/enquiries/${e.id}/retry`,{});toast('Retry queued using the same saved message.');await loadState();}));
}
function renderMailbox(){
  const e=customerEnquiry();
  const messages=(e?.outbox||[]).filter(o=>o.state==='delivered'&&!o.kind.startsWith('quote_')).map(o=>({...o,customer_replied:!!e.replied_at})).sort((a,b)=>b.delivered_at.localeCompare(a.delivered_at));
  const signature=JSON.stringify([e?.id,messages]);if(signature===mailSignature)return;mailSignature=signature;
  $('#mailbox-panel').hidden=!messages.length;
  const messageHTML=o=>`<article class="mail-item"><div class="mail-meta"><span>To: ${esc(o.recipient)}</span><span>${esc(dateLabel(o.delivered_at))}</span></div><h4>${esc(o.subject)}</h4><p class="mail-body">${esc(o.body)}</p>${o.kind==='reply'&&!o.customer_replied&&state.delivery_mode==='preview'?`<label for="reply-${o.id}">Your reply</label><textarea class="reply-input" id="reply-${o.id}" rows="2" maxlength="3000" placeholder="Write back to the workshop…"></textarea><button class="button secondary" data-reply="${o.enquiry_id}" data-outbox="${o.id}">Send preview reply</button>`:o.customer_replied?'<p class="notice success">Your reply was received.</p>':''}</article>`;
  $('#mailbox').innerHTML=messages.length?messageHTML(messages[0])+(messages.length>1?`<details class="message-history"><summary>Earlier messages (${messages.length-1})</summary>${messages.slice(1).map(messageHTML).join('')}</details>`:''):'';
  $$('[data-reply]').forEach(b=>b.addEventListener('click',action(async()=>{await api(`/api/enquiries/${b.dataset.reply}/reply`,{message_id:newId(),body:$('#reply-'+b.dataset.outbox).value});toast('Customer reply saved. Pending follow-ups cancelled.');await loadState();})));
}
function renderFacts(){
  const f=state.facts;
  $('#shop-price').textContent='€'+f.full_service;$('#shop-basic').textContent='€'+f.basic_service;$('#shop-hours').textContent=f.hours;
  $('#knowledge-version').textContent='Version '+f.version;$('#conflict-notice').hidden=!f.conflict;$('#conflict-toggle').textContent=f.conflict?'Resolve with approved price list':'Add a conflicting leaflet';
  if(!factsDirty){$('#full-price').value=f.full_service;$('#basic-price').value=f.basic_service;$('#hours-en').value=f.hours;$('#hours-sl').value=f.hours_sl;$('#hours-hr').value=f.hours_hr;$('#policy-notes').value=f.notes;factsVersion=f.version;}
}
$('#knowledge-form').addEventListener('input',()=>factsDirty=true);
$('#knowledge-form').addEventListener('submit',action(async()=>{
  await api('/api/knowledge',{version:factsVersion,full_service:Number($('#full-price').value),basic_service:Number($('#basic-price').value),hours:$('#hours-en').value,hours_sl:$('#hours-sl').value,hours_hr:$('#hours-hr').value,notes:$('#policy-notes').value,conflict:false},'PUT');
  factsDirty=false;await loadState();toast('Approved information published. Ask again to see the new answer.');guide('The owner published a new knowledge version. Return to Customer view and ask the service price again. No restart or deployment is needed.');
}));
$('#conflict-toggle').addEventListener('click',action(async()=>{await api('/api/knowledge',{...state.facts,conflict:!state.facts.conflict},'PUT');await loadState();toast(state.facts.conflict?'Conflict added. Try asking the service price.':'Conflict resolved against the owner-approved price list.');}));

function renderProof(){
  $('#connections').innerHTML=state.connections.map(c=>`<article class="connection"><h4>${esc(c.name)}</h4><span class="pill">${esc(c.state)}</span><p>${esc(c.note)}</p></article>`).join('');
  $('#global-events').innerHTML=state.events.length?state.events.map(eventHTML).join(''):'<p class="muted">The audit trail appears as you use the demo.</p>';
  $('#n8n-link').href=state.n8n_url;
  const v=state.verification;
  $('#verification').innerHTML=v?`<p class="verification-number">${v.passed}<small>local acceptance checks passed</small></p><p class="muted">${esc(v.summary)}</p>${v.n8n_e2e?`<p class="notice success">${v.n8n_steps} checkpoints passed end to end through n8n.</p>`:''}<p class="micro">Run: ${esc(dateLabel(v.at))}. ${v.live_ai?'Live AI evaluated.':'Live AI not evaluated.'} ${v.n8n_e2e?'Full n8n journey verified.':'n8n end-to-end verification pending.'}</p>`:'<p class="muted">Verification is still in progress.</p>';
}
async function loadState(){
  const r=await fetch('/api/state');if(!r.ok)throw new Error('The local workbench is unavailable. Restart the launcher to reconnect.');
  state=await r.json();controlToken=state.control_token;
  $('#engine-label').textContent=state.engine.ready?'Demo ready':'Automation starting';$('#engine-dot').classList.toggle('ready',state.engine.ready);
  $('#mode-copy').textContent=state.ai_mode==='live'?'Live AI enabled · owner-approved sources · staff-reviewed draft templates':'Approved facts and labelled draft templates. No AI calls.';
  $('#chat-mode').textContent=state.ai_mode==='live'?'Answers use workshop-approved information.':'Preview answers use approved prices, hours and FAQs.';
  $('#clock-label').textContent=(state.clock_hours?`Demo clock +${state.clock_hours} hours`:'Real clock')+' · '+(state.delivery_mode!=='preview'?'allowlisted '+state.delivery_mode.toUpperCase():'preview delivery');
  $('#desk-count').textContent=state.enquiries.filter(e=>/review|failed|uncertain|quote_draft|quote_question/.test(e.status)).length;
  $('#pause-delivery').textContent=state.delivery_paused?'Restore preview delivery':'Pause preview delivery';
  $('#pause-automation').textContent=state.automation_paused?'Resume automation':'Pause automation';
  for(const id of ['#pause-delivery','#advance-clock'])$(id).disabled=state.delivery_mode==='smtp';
  renderDesk();renderMailbox();renderFacts();renderProof();WorkshopFeatures.renderState(state);WorkshopJourney.render(state);
}
$('#pause-delivery').addEventListener('click',action(async()=>{await api('/api/controls',{delivery_paused:!state.delivery_paused});await loadState();toast(state.delivery_paused?'Delivery paused for the failure scenario.':'Delivery restored. Retry the failed job from its enquiry.');}));
$('#pause-automation').addEventListener('click',action(async()=>{await api('/api/controls',{automation_paused:!state.automation_paused});await loadState();toast(state.automation_paused?'Automation paused. Enquiries remain saved.':'Automation resumed. Queued jobs will continue.');}));
$('#advance-clock').addEventListener('click',action(async()=>{const r=await api('/api/controls',{advance_hours:72});await loadState();toast(r.followups_queued?`${r.followups_queued} follow-up queued for review.`:'Clock advanced. No eligible unanswered enquiries need a follow-up.');}));

function guide(text){$('#guide').textContent=text;$('#guide').hidden=false;}
async function beginTour(){
  if(startingTour)return;
  if(state.delivery_mode!=='preview'||state.gmail_inbox||state.live_crm)throw new Error('Guided examples are available in local preview mode. Use the ordinary forms for live accounts.');
  startingTour=true;
  try{
    selectCustomer('');session.guided=true;session.language='en';$('#language').value='en';saveSession();view('customer');
    $('#lead-name').value='Alex Demo';$('#lead-email').value='alex@customer.example';
    await ask('My cargo-bike frame is cracked. Can a mechanic inspect it?');
    await WorkshopFeatures.attachExample();WorkshopJourney.render(state);WorkshopJourney.jump('#handoff-card');
  }finally{startingTour=false;}
}
$('#start-tour').addEventListener('click',action(beginTour));
$$('[data-scenario]').forEach(button=>button.addEventListener('click',action(async()=>{
  $$('.scenario').forEach(b=>b.classList.toggle('active',b===button));
  if(button.dataset.scenario==='ask'){view('customer');session.language='sl';$('#language').value='sl';await ask('Koliko stane celovit servis?');guide('That answer used the owner-approved price list. Next try a custom repair that needs a mechanic.');}
  if(button.dataset.scenario==='repair'){view('customer');const question=session.language==='sl'?'Na tovornem kolesu je počeno ogrodje. Ali ga lahko pregleda mehanik?':session.language==='hr'?'Okvir teretnog bicikla je napukao. Može li ga pregledati mehaničar?':'My cargo-bike frame is cracked. Can a mechanic inspect it?';await ask(question);$('#lead-question').value=question;$('#handoff-card').scrollIntoView({behavior:'smooth',block:'center'});}
  if(button.dataset.scenario==='recovery'){view('proof');$('#recovery-tools').open=true;if(state.delivery_mode!=='preview'||state.gmail_inbox||state.live_crm){toast('The failure scenario is available only with local preview connections.',true);return;}await api('/api/controls',{delivery_paused:true});await loadState();guide('Preview delivery is paused. Approve a message at the workshop, then restore delivery and retry the saved job.');WorkshopJourney.jump('#recovery-tools');}
  if(button.dataset.scenario==='knowledge'){view('learn');$('.owner-settings').open=true;guide('Update the full-service price, publish it, then ask again in Customer view.');$('.owner-section').scrollIntoView({behavior:'smooth',block:'start'});$('#full-price').focus({preventScroll:true});}
})));

WorkshopFeatures.init({api,action,esc,newId,view,toast,guide,dateLabel,loadState,state:()=>state,customer:customerEnquiry,showCustomer:openCustomer,beginTour,refreshDesk:()=>{deskSignature='';renderDesk();}});
WorkshopJourney.init({action,esc,dateLabel,view,selectCustomer,begin:beginTour,send:sendRequestForm,customer:customerEnquiry,guided:()=>session.guided,openDesk:openCustomerDesk,prefillQuote:()=>WorkshopFeatures.prefillQuote(customerEnquiry())});
renderChat();
loadState().catch(e=>toast(e.message,true));
setInterval(()=>{if(document.visibilityState==='visible')loadState().catch(()=>{});},1800);
