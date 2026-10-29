import { spawn, spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { appendFileSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const args=new Set(process.argv.slice(2));
const runtime=join(ROOT,'.runtime');
mkdirSync(runtime,{recursive:true});
const children=[];
const log=join(runtime,'startup.log');
writeFileSync(log,'Šrauf local demo startup\n');

function envFile() {
  const f=join(ROOT,'.env');
  if(!existsSync(f))return {};
  return Object.fromEntries(readFileSync(f,'utf8').split(/\r?\n/).map(s=>s.match(/^([A-Z][A-Z0-9_]*)=(.*)$/)).filter(Boolean).map(m=>[m[1],m[2].replace(/^["']|["']$/g,'')]));
}
function findN8n() {
  if(process.env.N8N_ENTRY && existsSync(process.env.N8N_ENTRY))return process.env.N8N_ENTRY;
  const local=join(ROOT,'node_modules','n8n','bin','n8n');
  if(existsSync(local))return local;
  const cache=join(process.env.LOCALAPPDATA || '', 'npm-cache','_npx');
  if(existsSync(cache))for(const folder of readdirSync(cache)) {
    const p=join(cache,folder,'node_modules','n8n','bin','n8n');
    if(existsSync(p))return p;
  }
  throw new Error('n8n is not installed locally. Install n8n@2.44.6, then set N8N_ENTRY to its bin/n8n file. No automatic downloads are made.');
}
function pythonPath() {
  if(process.env.PYTHON_BIN)return process.env.PYTHON_BIN;
  for(const p of [join(ROOT,'.venv','Scripts','python.exe'),join(ROOT,'.venv','bin','python'),resolve(ROOT,'../../demos/website-chatbot/.venv/Scripts/python.exe')])if(existsSync(p))return p;
  return process.platform==='win32'?'python':'python3';
}
function child(command,argv,env) {
  const p=spawn(command,argv,{cwd:ROOT,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
  children.push(p);
  for(const s of [p.stdout,p.stderr])s.on('data',b=>appendFileSync(log,b));
  p.on('error',error=>appendFileSync(log,error.message+'\n'));
  return p;
}
async function run(command,argv,env) {
  const p=child(command,argv,env);
  const code=await new Promise((ok,no)=>{p.on('exit',ok);p.on('error',no);});
  if(code!==0)throw new Error(`Startup step ${argv[0]} failed. See .runtime/startup.log.`);
}
async function waitFor(url,processRef,timeout=120000) {
  const end=Date.now()+timeout;
  while(Date.now()<end) {
    if(processRef.exitCode!==null)throw new Error('A demo process stopped. See .runtime/startup.log.');
    try {if((await fetch(url,{signal:AbortSignal.timeout(1500)})).ok)return;}catch{}
    await new Promise(r=>setTimeout(r,700));
  }
  throw new Error('Startup timed out. See .runtime/startup.log.');
}
function stop() {
  for(const p of [...children].reverse())if(p.exitCode===null) {
    if(process.platform==='win32')spawnSync('taskkill',['/pid',String(p.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
    else p.kill('SIGTERM');
  }
}
process.on('SIGINT',()=>{stop();process.exit(0);});
process.on('SIGTERM',()=>{stop();process.exit(0);});
process.on('exit',stop);

try {
  const local=envFile();
  const env={...process.env,...local};
  const apiPort=Number(env.WORKSHOP_PORT||5790);
  const enginePort=Number(env.WORKSHOP_N8N_PORT||5798);
  const api=`http://127.0.0.1:${apiPort}`;
  const n8n=`http://127.0.0.1:${enginePort}`;
  // Fail before mutating the private n8n database if another instance owns either port.
  for(const url of [api,n8n]) {
    let active=false;
    try{await fetch(url+'/api/health',{signal:AbortSignal.timeout(700)});active=true;}catch{}
    if(active)throw new Error(`Port is already in use: ${url}. Reuse the running demo or choose different WORKSHOP_PORT / WORKSHOP_N8N_PORT values.`);
  }
  const keyPath=join(runtime,'bridge.key');
  if(!existsSync(keyPath))writeFileSync(keyPath,randomBytes(32).toString('hex'));
  Object.assign(env,{WORKSHOP_BRIDGE_TOKEN:readFileSync(keyPath,'utf8').trim(),WORKSHOP_API_URL:api,WORKSHOP_N8N_URL:n8n,
    WORKSHOP_LIVE_AI:args.has('--live-ai')?'1':'0',WORKSHOP_LIVE_EMAIL:args.has('--live-email')?'1':'0',
    WORKSHOP_LIVE_GMAIL:args.has('--live-gmail')?'1':'0',WORKSHOP_GMAIL_INBOX:args.has('--gmail-inbox')?'1':'0',WORKSHOP_LIVE_CRM:args.has('--live-crm')?'1':'0',
    PYTHONDONTWRITEBYTECODE:'1',PYTHONUTF8:'1'});
  if(!args.has('--live-ai'))for(const key of ['ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','ANTHROPIC_BASE_URL'])delete env[key];
  if(args.has('--live-ai')&&!env.ANTHROPIC_API_KEY)throw new Error('Live AI requires ANTHROPIC_API_KEY in this project’s .env. Offline mode needs no key.');
  if(args.has('--live-email')&&(!env.SMTP_HOST||!env.SMTP_TO_ALLOWLIST))throw new Error('Live email requires SMTP settings and SMTP_TO_ALLOWLIST. Preview mode sends no external email.');
  if((args.has('--live-gmail')||args.has('--gmail-inbox'))&&!(env.GMAIL_ACCESS_TOKEN||env.GMAIL_REFRESH_TOKEN))throw new Error('Gmail runtime OAuth is not configured. See docs/CONNECTIONS.md. Default preview still works without it.');
  if(args.has('--live-crm')&&(!env.HUBSPOT_ACCESS_TOKEN||!env.HUBSPOT_EMAIL_ALLOWLIST))throw new Error('HubSpot requires runtime authorization and a test-contact allowlist. See docs/CONNECTIONS.md.');
  await run(process.execPath,[join(ROOT,'n8n','build.mjs')],env);
  console.log('Starting the local workbench…');
  const server=child(pythonPath(),['-B','-m','uvicorn','connected.server:create_app','--factory','--host','127.0.0.1','--port',String(apiPort)],env);
  await waitFor(api+'/api/health',server,20000);
  const engineEnv={...env,N8N_PORT:String(enginePort),N8N_HOST:'127.0.0.1',N8N_LISTEN_ADDRESS:'127.0.0.1',N8N_USER_FOLDER:runtime,
    WEBHOOK_URL:n8n,N8N_BLOCK_ENV_ACCESS_IN_NODE:'false',N8N_DIAGNOSTICS_ENABLED:'false',N8N_VERSION_NOTIFICATIONS_ENABLED:'false',
    N8N_PERSONALIZATION_ENABLED:'false',N8N_SECURE_COOKIE:'false',N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS:'false',
    GENERIC_TIMEZONE:'Europe/Ljubljana',N8N_ENCRYPTION_KEY:readFileSync(keyPath,'utf8').trim(),
    EXECUTIONS_DATA_SAVE_ON_SUCCESS:'all',EXECUTIONS_DATA_PRUNE:'true',EXECUTIONS_DATA_MAX_AGE:'24'};
  // The orchestration engine never needs an AI or SMTP credential.
  for(const key of Object.keys(engineEnv))if(/^(SMTP_|ANTHROPIC_|GMAIL_|HUBSPOT_)/.test(key))delete engineEnv[key];
  const entry=findN8n();
  console.log('Importing the n8n workflows into an isolated local database…');
  await run(process.execPath,[entry,'import:workflow','--separate',`--input=${join(ROOT,'n8n','workflows')}`],engineEnv);
  for(const id of ['sraufIntake2026','sraufDelivery26','sraufCrmSync2026'])await run(process.execPath,[entry,'publish:workflow',`--id=${id}`],engineEnv);
  console.log('Starting n8n and registering its webhooks…');
  const engine=child(process.execPath,[entry,'start'],engineEnv);
  await waitFor(n8n+'/healthz/readiness',engine,180000);
  // A registered protected webhook returns 401; an unregistered one returns 404.
  const deadline=Date.now()+60000;
  let registered=false;
  while(Date.now()<deadline) {
    const r=await fetch(n8n+'/webhook/workshop-intake',{method:'POST',headers:{'content-type':'application/json'},body:'{}'});
    if(r.status===401){registered=true;break;}
    await new Promise(r=>setTimeout(r,700));
  }
  if(!registered)throw new Error('n8n started but its protected webhook did not register. See .runtime/startup.log.');
  console.log(`\nReady: ${api}\nn8n: ${n8n}\nMode: ${args.has('--live-ai')?'live AI':'offline approved facts'} / ${args.has('--live-gmail')?'allowlisted Gmail':args.has('--live-email')?'allowlisted SMTP':'local preview inbox'}${args.has('--gmail-inbox')?' / dedicated Gmail label polling':''}${args.has('--live-crm')?' / HubSpot test contacts':''}\nNothing has been published. Press Ctrl+C to stop.`);
  server.on('exit',()=>{console.log('Workbench stopped.');stop();});
  engine.on('exit',()=>{console.log('n8n stopped.');stop();});
}catch(error){console.error(error.message);stop();process.exit(1);}
