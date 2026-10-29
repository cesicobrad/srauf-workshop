/* Actual n8n integration verification. Uses fictional data and local preview delivery only. */
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';

const ROOT=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const BASE=process.env.WORKSHOP_TEST_URL||'http://127.0.0.1:5790';
let token,checks=0;
const id=()=>randomUUID().replaceAll('-','');
const read=async()=>{const r=await fetch(BASE+'/api/state');assert.equal(r.status,200);return r.json();};
async function request(path,body,method='POST',extra={}) {
  const response=await fetch(BASE+path,{method,headers:{'content-type':'application/json','x-demo-control':token,...extra},body:JSON.stringify(body)});
  return {status:response.status,body:await response.json()};
}
function check(value,message){assert.ok(value,message);checks++;console.log('PASS '+message);}
async function waitFor(fn,message,ms=35000) {
  const end=Date.now()+ms;
  while(Date.now()<end){const value=await fn();if(value){check(true,message);return value;}await new Promise(r=>setTimeout(r,400));}
  throw new Error('Timed out: '+message);
}
const initial=await read();token=initial.control_token;
assert.equal(initial.delivery_mode,'preview','This verifier never exercises live email.');
assert.equal(initial.ai_mode,'offline','This verifier never spends AI tokens.');
await waitFor(async()=>{const s=await read();return s.engine.ready;},'Actual n8n engine is ready');
await request('/api/controls',{delivery_paused:false,automation_paused:false});

async function makeEnquiry(name,email,lang='en',photos=[]) {
  const q=lang==='sl'?'Na tovornem kolesu je počeno ogrodje. Ali ga lahko pregleda mehanik?':'My cargo-bike frame is cracked. Can a mechanic inspect it?';
  const chat=await request('/api/chat',{message:q,lang});
  check(chat.status===200&&chat.body.status==='handoff','Custom repair goes to a person without an invented quote');
  const payload={conversation_id:chat.body.conversation_id,name,email,question:q,language:lang,photos};
  const key=id();
  const result=await request('/api/enquiries',payload,'POST',{'idempotency-key':key});
  assert.equal(result.status,200);
  const eid=result.body.enquiry.id;
  const duplicate=await request('/api/enquiries',payload,'POST',{'idempotency-key':key});
  check(duplicate.body.duplicate&&duplicate.body.enquiry.id===eid,'Repeated submission keeps one enquiry');
  const e=await waitFor(async()=>{const e=(await read()).enquiries.find(e=>e.id===eid);return e?.status==='awaiting_review'&&e;},'n8n prepares a saved staff-review draft');
  check(e.tasks.some(t=>t.state==='completed'&&/^\d+$/.test(t.execution_id)),'Audit trail contains an actual n8n execution ID');
  check(e.outbox.length===0,'No message is sent before staff approval');
  check(e.transcript.some(m=>m.role==='user'&&m.text===q),'The customer conversation reaches the staff desk');
  return e;
}

const repair=await makeEnquiry('Maja Kovač','maja.kovac@customer.example','sl');
await request('/api/controls',{delivery_paused:true});
const approvedText='Pozdravljeni, Maja!\n\nProsimo, pošljite fotografijo poškodbe ter znamko in model kolesa. Mehanik bo preveril možnost popravila pred potrditvijo cene.\n\nEkipa Šrauf';
const approval={version:repair.version,subject:'Pregled vašega tovornega kolesa',body:approvedText};
const [firstApproval,secondApproval]=await Promise.all([request(`/api/enquiries/${repair.id}/approve`,approval),request(`/api/enquiries/${repair.id}/approve`,approval)]);
check([firstApproval.status,secondApproval.status].sort().join(',')==='200,409','Concurrent double approval accepts exactly one decision');
const failed=await waitFor(async()=>{const e=(await read()).enquiries.find(e=>e.id===repair.id);return e.status==='delivery_failed'&&e;},'n8n retries a paused delivery, then records a recoverable failure');
check(failed.outbox.length===1&&failed.outbox[0].body===approvedText,'Failure retains one exact approved message');
await request('/api/controls',{delivery_paused:false});
assert.equal((await request(`/api/enquiries/${repair.id}/retry`,{})).status,200);
const sent=await waitFor(async()=>{const e=(await read()).enquiries.find(e=>e.id===repair.id);return e.status==='sent'&&e;},'Restored delivery completes the same saved job');
check(sent.outbox.length===1&&sent.outbox[0].message_id===failed.outbox[0].message_id,'Recovery preserves message identity without a duplicate');
check(sent.outbox[0].body===approvedText,'The preview inbox contains the staff-edited reply');

const current=(await read()).facts;
const price=current.full_service===89?99:89;
try {
  check((await request('/api/knowledge',{...current,full_service:price,conflict:false},'PUT')).status===200,'Owner can publish a price change without deployment');
  const answer=await request('/api/chat',{message:'Koliko stane celovit servis?',lang:'sl'});
  check(answer.body.reply.includes(String(price))&&answer.body.language==='sl','The next Slovenian answer uses the new approved price');
  const facts=(await read()).facts;
  await request('/api/knowledge',{...facts,conflict:true},'PUT');
  const conflict=await request('/api/chat',{message:'How much is a full service?',lang:'en'});
  check(conflict.body.status==='unknown'&&conflict.body.offer_handoff,'Conflicting knowledge escalates instead of guessing a price');
}finally {
  const latest=(await read()).facts;
  await request('/api/knowledge',{...current,version:latest.version},'PUT');
}

// A delivered reply gets a follow-up draft after three simulated days. A customer reply cancels it.
await request('/api/controls',{advance_hours:72});
const followup=await waitFor(async()=>{const e=(await read()).enquiries.find(e=>e.id===repair.id);return e.status==='followup_review'&&e;},'A due follow-up goes through n8n and still awaits approval');
const incoming={message_id:id(),body:'Hvala! Fotografijo pošljem jutri.'};
check((await request(`/api/enquiries/${repair.id}/reply`,incoming)).status===200,'A preview customer reply is recorded');
const stale=await request(`/api/enquiries/${repair.id}/approve`,{version:followup.version,subject:followup.draft_subject,body:followup.draft_body});
check(stale.status===409,'A customer reply blocks stale follow-up approval');
const final=(await read()).enquiries.find(e=>e.id===repair.id);
check(final.status==='customer_replied'&&final.outbox.length===1,'No follow-up is sent after the customer replies');

const unauthorized=await fetch(BASE+'/internal/draft',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({task_id:id()})});
check(unauthorized.status===401,'The internal bridge rejects unauthenticated calls');
const missingControl=await fetch(BASE+'/api/controls',{method:'POST',headers:{'content-type':'application/json'},body:'{"delivery_paused":true}'});
check(missingControl.status===403,'Owner controls reject requests without the local control token');

// Photos, ownership, exact staff pricing and customer decisions use the same durable pipeline.
const photo=readFileSync(join(ROOT,'web','sample-repair.png')).toString('base64');
const quoteLead=await makeEnquiry('Nina Example','nina@customer.example','en',[{filename:'example-frame.png',data_base64:photo}]);
check(quoteLead.photos.length===1,'A repair photo survives the chat-to-staff handover');
const image=await fetch(BASE+'/api/photos/'+quoteLead.photos[0].id);
check(image.ok&&image.headers.get('content-type')==='image/jpeg','Staff can view the sanitized image attachment');
const assigned=await request(`/api/enquiries/${quoteLead.id}/work`,{version:quoteLead.version,assignee:'Nika',next_action:'Prepare quote',due_hours:4},'PATCH');
check(assigned.status===200&&assigned.body.assignee==='Nika','An enquiry has a named owner, next action and due time');
await request('/api/controls',{advance_hours:5});
await waitFor(async()=>{const s=await read();return s.alerts.find(a=>a.enquiry_id===quoteLead.id);},'A missed due time produces a saved staff alert');
const getQuoteLead=async()=>(await read()).enquiries.find(e=>e.id===quoteLead.id);
const currentQuoteLead=await getQuoteLead();
const quotePayload={enquiry_version:currentQuoteLead.version,items:[{description:'Inspection and labour',quantity:'1.5',unit_price:'40'},{description:'Replacement part',quantity:'1',unit_price:'25'}],turnaround:'2–3 working days after inspection',notes:'Fictional demonstration amounts.',valid_days:7};
const saved=await request(`/api/enquiries/${quoteLead.id}/quotes`,quotePayload);
check(saved.status===200&&saved.body.total_cents===8500,'Staff-entered labour and parts produce an exact €85.00 quote');
check((await getQuoteLead()).outbox.length===0,'Saving a quote draft does not send it');
const q1=saved.body;
const quoteApprovals=await Promise.all([request(`/api/quotes/${q1.id}/approve`,{version:q1.version}),request(`/api/quotes/${q1.id}/approve`,{version:q1.version})]);
check(quoteApprovals.map(r=>r.status).sort().join(',')==='200,409','Double quote approval queues one reviewed message');
await waitFor(async()=>{const e=await getQuoteLead();return e.quotes[0].state==='sent'&&e;},'The approved quote is delivered through actual n8n');
const asked=await request(`/api/quotes/${q1.id}/decision`,{token:q1.decision_token,decision:'question',note:'Does this include the replacement part?'},'POST',{'idempotency-key':id()});
check(asked.status===200&&(await getQuoteLead()).status==='quote_question','A customer question comes back to the staff desk');
const revision=await request(`/api/enquiries/${quoteLead.id}/quotes`,{...quotePayload,enquiry_version:(await getQuoteLead()).version,notes:'Includes the listed replacement part.'});
assert.equal(revision.status,200);const q2=revision.body;
const staleQuote=await request(`/api/quotes/${q1.id}/decision`,{token:q1.decision_token,decision:'accept'},'POST',{'idempotency-key':id()});
check(staleQuote.status===409,'An older quote cannot be accepted after staff issue a revision');
await request(`/api/quotes/${q2.id}/approve`,{version:q2.version});
const quoted=await waitFor(async()=>{const e=await getQuoteLead();return e.quotes[0].state==='sent'&&e;},'A revised quote is reviewed and delivered through n8n');
const choice={token:q2.decision_token,decision:'accept'},decisionKey=id();
const accepted=await request(`/api/quotes/${q2.id}/decision`,choice,'POST',{'idempotency-key':decisionKey});
const repeated=await request(`/api/quotes/${q2.id}/decision`,choice,'POST',{'idempotency-key':decisionKey});
check(accepted.status===200&&repeated.status===200&&(await getQuoteLead()).next_action==='Schedule repair','Quote acceptance is replay-safe and creates a scheduling next step');
const toComplete=await getQuoteLead();
await request(`/api/enquiries/${quoteLead.id}/work`,{version:toComplete.version,assignee:'Nika',next_action:'Completed'},'PATCH');
check(!(await read()).alerts.some(a=>a.enquiry_id===quoteLead.id),'Completing the work resolves its overdue alert');

const question=`Can I leave a helmet with my bicycle for demo check ${id().slice(0,8)}?`;
const unknown=await request('/api/chat',{message:question,lang:'en'});
check(unknown.body.status==='unknown','A genuine unsupported question is escalated without inventing an answer');
const gap=(await read()).knowledge_gaps.find(g=>g.question===question);
check(Boolean(gap),'The unanswered question appears in the owner inbox');
const approvedAnswer='For this fictional demo, a labelled helmet may be stored with the bicycle during its inspection.';
const publication=await request(`/api/knowledge/gaps/${gap.id}/approve`,{answer:approvedAnswer});
check(publication.status===200,'An owner can approve the missing answer');
const retest=await request('/api/chat',{message:question,lang:'en'});
check(retest.body.status==='answered'&&retest.body.reply===approvedAnswer&&retest.body.sources.length>0,'The same chat API now answers from the approved source');
const reportPath=join(ROOT,'docs','verification.json');
const report=JSON.parse(readFileSync(reportPath,'utf8'));
Object.assign(report,{n8n_e2e:true,n8n_steps:checks,n8n_at:new Date().toISOString(),n8n_execution_ids:[...final.tasks,...quoted.tasks].filter(t=>t.execution_id).map(t=>t.execution_id)});
writeFileSync(reportPath,JSON.stringify(report,null,2)+'\n');
console.log(`\n${checks} end-to-end checkpoints passed through actual n8n. No AI calls or external email.`);
