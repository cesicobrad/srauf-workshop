import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Workflow } from './lib.mjs';

const out=join(dirname(fileURLToPath(import.meta.url)),'workflows');
mkdirSync(out,{recursive:true});
for (const [kind,id,path,step,endpoint] of [
  ['intake','sraufIntake2026','workshop-intake','Prepare a staff review','draft'],
  ['delivery','sraufDelivery26','workshop-send','Deliver the approved outbox item','deliver'],
  ['crm','sraufCrmSync2026','workshop-crm','Upsert the authorized HubSpot contact','crm'],
]) {
  const w=new Workflow({id,name:kind==='intake'?'Šrauf · Enquiry → staff review':kind==='crm'?'Šrauf · Saved enquiry → HubSpot contact':'Šrauf · Approval → delivery → recovery'});
  w.note(kind==='intake'
    ? '## A saved enquiry becomes a staff draft\nThe workbench persists the enquiry and its transcript before calling this workflow. The callback is idempotent. In offline mode the draft is a labelled template, not generated AI text.'
    : kind==='crm' ? '## Save an authorized test contact\nThe backend checks the test-contact allowlist and upserts by email. A provider-confirmed contact ID is saved with this execution. Credentials stay in the backend; this workflow receives only a durable task ID.'
    : '## Deliver only what a person approved\nThe durable outbox owns the exact approved message. Preview delivery is atomic. Ambiguous external mail sends stop for a manual provider check rather than being blindly repeated.',[0,-1.3],{width:740,height:170,color:4});
  w.webhook('Saved job received',[0,0],{path});
  w.if('Authenticated bridge?',[1,0],"={{ $json.headers['x-workshop-key'] === $env.WORKSHOP_BRIDGE_TOKEN }}");
  w.respond('Reject unauthenticated request',[2,1],{json:'={{ JSON.stringify({ok:false,error:"Bridge authentication required"}) }}',status:401});
  const payload="={{ JSON.stringify({task_id:$('Saved job received').first().json.body.task_id,execution_id:String($execution.id)}) }}";
  w.http(step,[2,0],{method:'POST',url:'={{ $env.WORKSHOP_API_URL }}/internal/'+endpoint,
    headers:{Authorization:"={{ 'Bearer ' + $env.WORKSHOP_BRIDGE_TOKEN }}"},jsonBody:payload,errorOutput:true,timeout:20000});
  w.respond('Return recorded result',[3,0],{json:'={{ JSON.stringify($json) }}'});
  w.http('Record the recoverable failure',[3,1],{method:'POST',url:'={{ $env.WORKSHOP_API_URL }}/internal/failure',
    headers:{Authorization:"={{ 'Bearer ' + $env.WORKSHOP_BRIDGE_TOKEN }}"},
    jsonBody:"={{ JSON.stringify({task_id:$('Saved job received').first().json.body.task_id,execution_id:String($execution.id),error:'The '+"+JSON.stringify(kind)+"+' step failed after retries. The original job and approved message are saved. Restore the service and retry from the workshop desk.'}) }}",
    errorOutput:true,timeout:5000});
  w.respond('Return retryable failure',[4,1],{json:'={{ JSON.stringify({ok:false,saved_for_retry:true}) }}',status:503});
  w.respond('Return bridge unavailable',[4,2],{json:'={{ JSON.stringify({ok:false,error:"Bridge unavailable; the durable job remains saved."}) }}',status:503});
  w.chain('Saved job received','Authenticated bridge?',step,'Return recorded result');
  w.link('Authenticated bridge?','Reject unauthenticated request',1);
  w.link(step,'Record the recoverable failure',1);
  w.link('Record the recoverable failure','Return retryable failure');
  w.link('Record the recoverable failure','Return bridge unavailable',1);
  writeFileSync(join(out,kind+'.json'),JSON.stringify(w.toJSON(),null,2)+'\n');
}
console.log('Built three importable n8n workflows.');
