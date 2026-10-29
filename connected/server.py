"""Local workbench and a durable bridge to actual n8n workflows."""
from __future__ import annotations

import asyncio
import csv
import hmac
import io
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from chatbot.guard import RateLimiter
from .delivery import UncertainDelivery, send_smtp
from .knowledge import Knowledge
from .store import Conflict, Store
from .feature_api import PhotoInput, install_feature_routes
from .photos import normalize_photo
from .integrations import IntegrationError

ROOT=Path(__file__).resolve().parent.parent
ID=re.compile(r"^[a-f0-9]{32}$")
EMAIL=re.compile(r"^[^\s@<>\r\n]{1,64}@[^\s@<>\r\n]{1,190}\.[A-Za-z]{2,24}$")


class Chat(BaseModel):
    message: str=Field(min_length=1,max_length=600)
    conversation_id: str=""
    lang: Literal["en","sl","hr","sr"]="en"


class Lead(BaseModel):
    conversation_id: str=Field(pattern=r"^[a-f0-9]{32}$")
    name: str=Field(min_length=2,max_length=100)
    email: str=Field(max_length=254)
    question: str=Field(min_length=5,max_length=2000)
    language: Literal["en","sl","hr","sr"]="en"
    photos: list[PhotoInput]=Field(default_factory=list,max_length=3)


class Approval(BaseModel):
    version: int
    subject: str=Field(min_length=1,max_length=200)
    body: str=Field(min_length=10,max_length=5000)


class Decision(BaseModel):
    version: int


class Callback(BaseModel):
    task_id: str=Field(pattern=r"^[a-f0-9]{32}$")
    execution_id: str=Field(default="",max_length=100)
    error: str=Field(default="n8n could not complete this step.",max_length=600)


class Controls(BaseModel):
    delivery_paused: bool | None=None
    automation_paused: bool | None=None
    advance_hours: int=Field(default=0,ge=0,le=168)


class Facts(BaseModel):
    version: int
    full_service: float=Field(gt=0,le=10000)
    basic_service: float=Field(gt=0,le=10000)
    hours: str=Field(min_length=5,max_length=300)
    hours_sl: str=Field(min_length=5,max_length=300)
    hours_hr: str=Field(min_length=5,max_length=300)
    notes: str=Field(max_length=1000)
    conflict: bool=False


class Inbound(BaseModel):
    message_id: str=Field(min_length=5,max_length=150)
    body: str=Field(min_length=2,max_length=3000)


def create_app(store=None,knowledge=None,*,worker=True):
    store=store or Store(ROOT/"data"/"workshop.sqlite")
    live_ai=os.getenv("WORKSHOP_LIVE_AI")=="1"
    live_email=os.getenv("WORKSHOP_LIVE_EMAIL")=="1"
    live_gmail=os.getenv("WORKSHOP_LIVE_GMAIL")=="1"
    gmail_inbox=os.getenv("WORKSHOP_GMAIL_INBOX")=="1"
    live_crm=os.getenv("WORKSHOP_LIVE_CRM")=="1"
    transport="gmail" if live_gmail else "smtp" if live_email else "preview"
    knowledge=knowledge or Knowledge(store,ROOT/"data"/"chatbot.sqlite",live=live_ai)
    token=os.getenv("WORKSHOP_BRIDGE_TOKEN") or secrets.token_urlsafe(32)
    ui_token=secrets.token_urlsafe(24)
    n8n=os.getenv("WORKSHOP_N8N_URL","http://127.0.0.1:5798").rstrip("/")
    engine={"ready":False,"checked_at":None,"problem":"Starting n8n…"}
    limiter=RateLimiter()

    async def pump():
        checked=0
        async with httpx.AsyncClient(timeout=40,trust_env=False) as client:
            while True:
                if time.monotonic()-checked>5:
                    try:
                        r=await client.get(n8n+"/healthz/readiness",timeout=2)
                        engine.update(ready=r.is_success,problem="" if r.is_success else "n8n is not ready.")
                    except httpx.HTTPError:
                        engine.update(ready=False,problem="n8n is unavailable. New enquiries stay saved; restart the launcher to recover.")
                    engine["checked_at"]=store.now()
                    checked=time.monotonic()
                    store.due_followups()
                    store.refresh_overdue()
                if engine["ready"]:
                    task=store.claim_task()
                    if task:
                        path="workshop-crm" if task["kind"]=="crm_sync" else "workshop-send" if task["kind"].startswith("send_") else "workshop-intake"
                        try:
                            r=await client.post(n8n+"/webhook/"+path,json={"task_id":task["id"]},headers={"x-workshop-key":token})
                            r.raise_for_status()
                            current=store.task(task["id"])
                            if current["state"]=="processing":
                                store.failed(task["id"],"n8n returned without completing the job. Review its execution, then retry.")
                        except httpx.HTTPError:
                            store.failed(task["id"],"The n8n workflow did not complete. Your enquiry and approved reply are saved. Restore the connection and retry.")
                        continue
                await asyncio.sleep(.7)

    async def poll_gmail():
        from .integrations import Gmail, import_gmail
        gmail=Gmail()
        while True:
            try:
                await asyncio.to_thread(import_gmail,store,knowledge,gmail)
            except Exception:
                store.integration_result("gmail","error","Inbox check failed. Verify OAuth, the dedicated label and sender allowlist. No mailbox state was changed.")
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(app):
        task=asyncio.create_task(pump()) if worker else None
        inbox_task=asyncio.create_task(poll_gmail()) if worker and gmail_inbox else None
        yield
        if inbox_task:
            inbox_task.cancel()
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app=FastAPI(title="Šrauf · Connected workshop demo",lifespan=lifespan,docs_url=None,redoc_url=None)
    app.state.store=store
    app.state.knowledge=knowledge
    app.state.bridge_token=token

    @app.middleware("http")
    async def local_only(request,call_next):
        host=request.url.hostname
        if host not in {"127.0.0.1","localhost","::1","testserver"}:
            return JSONResponse({"detail":"This workbench is local-only."},status_code=403)
        if request.method in {"POST","PUT","PATCH"}:
            limit=18*1024*1024 if request.url.path=="/api/enquiries" else 1024*1024
            if int(request.headers.get("content-length","0"))>limit:
                return JSONResponse({"detail":"That request is too large. Use at most three photos of 4 MB each."},status_code=413)
        if request.method in {"POST","PUT","DELETE","PATCH"} and request.url.path.startswith("/api/"):
            if not hmac.compare_digest(request.headers.get("x-demo-control",""),ui_token):
                return JSONResponse({"detail":"Refresh this local demo before continuing."},status_code=403)
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["X-Frame-Options"]="DENY"
        response.headers["Cache-Control"]="no-store"
        return response

    @app.exception_handler(Conflict)
    async def conflict(request,error):
        return JSONResponse({"detail":str(error)},status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request,error):
        return JSONResponse({"detail":"That enquiry or job does not exist."},status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request,error):
        return JSONResponse({"detail":str(error)},status_code=422)

    @app.exception_handler(PermissionError)
    async def forbidden(request,error):
        return JSONResponse({"detail":str(error)},status_code=403)

    def internal(authorization: str=Header(default="")):
        if not hmac.compare_digest(authorization,"Bearer "+token):
            raise HTTPException(401,"Bridge authentication required.")

    @app.get("/")
    def index():
        return FileResponse(ROOT/"web"/"index.html")

    @app.get("/assets/{name}")
    def asset(name: str):
        if name not in {"app.js","features.js","journey.js","style.css","ux.css","icon.svg","sample-repair.png"}:
            raise HTTPException(404)
        return FileResponse(ROOT/"web"/name)

    @app.get("/api/health")
    def health():
        return {"ok":True}

    @app.get("/api/state")
    def state():
        report=ROOT/"docs"/"verification.json"
        return {**store.snapshot(),"control_token":ui_token,"engine":engine,"ai_mode":"live" if live_ai else "offline",
                "delivery_mode":transport,"n8n_url":n8n,
                "live_crm":live_crm,"gmail_inbox":gmail_inbox,"customer_records":"local",
                "verification":json.loads(report.read_text(encoding="utf-8")) if report.exists() else None,
                "connections":[
                    {"name":"n8n orchestration","state":"Connected" if engine["ready"] else "Waiting","note":"Actual local n8n webhooks. Execution IDs appear in the audit trail."},
                    {"name":"Customer records","state":"Saved locally","note":"Customer history, enquiries, photos, quotes and replies persist on this device. CSV export is available. No external CRM account is required."},
                    *([{"name":"HubSpot CRM · optional","state":next((i['state'] for i in store.feature_snapshot()['integration_status'] if i['name']=='hubspot'),'not_connected'),"note":"Explicitly enabled contact upsert through n8n. Local customer records remain available independently."}] if live_crm else []),
                    {"name":"Email delivery","state":"Gmail enabled · check status" if live_gmail else "SMTP configured · unverified" if live_email else "Local preview inbox","note":"External delivery is opt-in and limited to addresses you allowlist. No email leaves this machine in preview mode."},
                    {"name":"Gmail inbox · optional","state":next((i['state'] for i in store.feature_snapshot()['integration_status'] if i['name']=='gmail'),'not_connected') if gmail_inbox else "Not enabled","note":"Opt-in read-only polling of a dedicated label. Replies require a matching sender and message reference. Preview replies work without Gmail."},
                    {"name":"AI answers","state":"Live AI enabled" if live_ai else "Approved facts · no AI calls","note":"Offline replies and draft templates are deliberately limited. Live model accuracy has not been measured for this combined demo."}]}

    @app.post("/api/chat")
    def chat(body: Chat,request: Request):
        if body.conversation_id and not ID.fullmatch(body.conversation_id):
            raise HTTPException(422,"Invalid conversation ID.")
        if not limiter.check_all([(f"chat:{request.client.host}",30,60)]):
            raise HTTPException(429,"Please wait a minute before asking more questions.")
        return knowledge.ask(body.message.strip(),body.conversation_id,body.lang)

    @app.post("/api/enquiries")
    def handoff(body: Lead,idempotency_key: str=Header(default="")):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{8,100}",idempotency_key):
            raise HTTPException(422,"A valid submission key is required.")
        if not EMAIL.fullmatch(body.email) or not body.name.strip() or not body.question.strip():
            raise HTTPException(422,"Enter a name, a valid email and your repair question.")
        try:
            transcript=knowledge.transcript(body.conversation_id)
        except ValueError as error:
            raise HTTPException(422,str(error))
        data=body.model_dump(exclude={"photos"})
        photos=[normalize_photo(p.filename,p.data_base64) for p in body.photos]
        item,duplicate=store.create_enquiry(key=idempotency_key,transcript=transcript,photos=photos,**data)
        if live_crm and not duplicate:store.queue_crm(item['id'])
        return {"enquiry":item,"duplicate":duplicate}

    @app.post("/api/enquiries/{eid}/approve")
    def approve(eid: str,body: Approval):
        if any(c in body.subject for c in "\r\n") or not body.subject.strip() or len(body.body.strip())<10:
            raise HTTPException(422,"Enter a subject on one line and a reply of at least 10 characters.")
        return store.approve(eid,body.version,body.subject,body.body,transport)

    @app.post("/api/enquiries/{eid}/reject")
    def reject(eid: str,body: Decision):
        store.reject(eid,body.version)
        return {"ok":True}

    @app.post("/api/enquiries/{eid}/retry")
    def retry(eid: str):
        store.retry(eid)
        return {"ok":True}

    @app.post("/api/enquiries/{eid}/reply")
    def reply(eid: str,body: Inbound):
        if transport!="preview":
            raise HTTPException(409,"Use the signed inbound endpoint for real email replies; this button is a preview scenario.")
        return {"ok":True,"new":store.inbound(eid,body.message_id,body.body)}

    @app.post("/api/controls")
    def controls(body: Controls):
        if (transport!="preview" or gmail_inbox or live_crm) and (body.advance_hours or body.delivery_paused is not None):
            raise HTTPException(409,"Clock and delivery failure scenarios are available only in preview delivery mode.")
        store.control(**body.model_dump())
        due=store.due_followups()
        store.refresh_overdue()
        return {"ok":True,"followups_queued":due}

    @app.put("/api/knowledge")
    def facts(body: Facts):
        f=store.save_facts(body.version,body.model_dump(exclude={"version"}))
        with knowledge.lock:
            knowledge.sync()
        return f

    @app.get("/api/export.csv")
    def export():
        output=io.StringIO()
        w=csv.writer(output)
        w.writerow(["Enquiry","Name","Email","Language","Question","Status","Created","Sent"])
        for e in store.snapshot()["enquiries"]:
            row=[e["id"],e["name"],e["email"],e["language"],e["question"],e["status"],e["created_at"],e["sent_at"] or ""]
            w.writerow(["'"+str(c) if str(c).lstrip().startswith(("=","+","-","@")) else c for c in row])
        return Response(output.getvalue(),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=workshop-enquiries.csv"})

    @app.post("/internal/draft",dependencies=[Depends(internal)])
    def draft(body: Callback):
        if live_ai:
            from .drafting import generate_draft
            task=store.task(body.task_id)
            if task["state"] in ("completed","cancelled"):
                return {"ok":True,"duplicate":True}
            enquiry=store.detail(task["enquiry_id"])
            if task["kind"]=="followup" and enquiry["replied_at"]:
                return store.draft(body.task_id,body.execution_id)
            # Model text can only become a review draft, never a sent message.
            generated=generate_draft(enquiry,store.setting("facts"),"followup" if task["kind"]=="followup" else "reply")
            return store.draft(body.task_id,body.execution_id,generated)
        return store.draft(body.task_id,body.execution_id)

    @app.post("/internal/deliver",dependencies=[Depends(internal)])
    def deliver(body: Callback):
        try:
            item=store.prepare_delivery(body.task_id,body.execution_id)
            if not item:
                return {"ok":True,"already_handled":True}
            if item["transport"]=="smtp":
                send_smtp(item)
            elif item["transport"]=="gmail":
                from .integrations import Gmail
                Gmail().send(item)
                store.integration_result('gmail','connected','Gmail confirmed an allowlisted test message submission.')
            sent=store.delivered(body.task_id,item["id"])
            return {"ok":True,"message_id":item["message_id"],"cancelled":not sent}
        except ConnectionError as error:
            raise HTTPException(503,str(error))
        except UncertainDelivery as error:
            store.failed(body.task_id,str(error),uncertain=True)
            return {"ok":True,"manual_check_required":True}
        except (ValueError,OSError,IntegrationError) as error:
            store.failed(body.task_id,"Email was not submitted. Check the active provider's authorization and recipient allowlist before retrying.")
            return {"ok":False,"configuration_error":True}

    @app.post("/internal/failure",dependencies=[Depends(internal)])
    def failure(body: Callback):
        store.failed(body.task_id,body.error)
        return {"ok":False,"saved_for_retry":True}

    @app.post("/internal/inbound/{eid}",dependencies=[Depends(internal)])
    def inbound(eid: str,body: Inbound):
        return {"ok":True,"new":store.inbound(eid,body.message_id,body.body)}

    @app.post("/internal/crm",dependencies=[Depends(internal)])
    def crm(body:Callback):
        if not live_crm:raise HTTPException(409,"HubSpot is not enabled for this runtime.")
        task=store.task(body.task_id)
        if task['kind']!='crm_sync':raise HTTPException(409,'This is not a CRM job.')
        if task['state']=='completed':return {'ok':True,'duplicate':True}
        from .integrations import IntegrationError, sync_hubspot
        try:
            contact=sync_hubspot(store.detail(task['enquiry_id']))
        except IntegrationError as error:
            store.integration_result('hubspot','error',str(error))
            raise HTTPException(503,str(error))
        store.crm_complete(body.task_id,contact,body.execution_id)
        store.integration_result('hubspot','connected','HubSpot confirmed a contact upsert from the test allowlist.')
        return {'ok':True,'contact_id':contact}

    install_feature_routes(app,store,knowledge,transport=transport,live_crm=live_crm)

    return app
