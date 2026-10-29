"""HTTP endpoints for quotes, attachments, owner answers and the staff work queue."""
from typing import Literal

from fastapi import Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .photos import normalize_photo


class PhotoInput(BaseModel):
    filename: str=Field(min_length=1,max_length=150)
    data_base64: str=Field(min_length=1,max_length=5600000)


class WorkInput(BaseModel):
    version: int
    assignee: str
    next_action: str
    due_hours: int | None=Field(default=None,ge=1,le=720)


class QuoteLine(BaseModel):
    description: str=Field(min_length=2,max_length=180)
    quantity: str=Field(max_length=15)
    unit_price: str=Field(max_length=15)


class QuoteInput(BaseModel):
    enquiry_version: int
    items: list[QuoteLine]=Field(min_length=1,max_length=12)
    turnaround: str=Field(min_length=2,max_length=200)
    notes: str=Field(default='',max_length=1000)
    valid_days: int=Field(default=7,ge=1,le=30)


class QuoteVersion(BaseModel):version:int


class QuoteDecision(BaseModel):
    token: str=Field(min_length=10,max_length=100)
    decision: Literal['accept','question']
    note: str=Field(default='',max_length=2000)


class ApprovedAnswer(BaseModel):
    answer: str=Field(min_length=5,max_length=2000)
    version: int | None=None


def install_feature_routes(app,store,knowledge,*,transport='preview',live_crm=False):
    @app.get('/api/photos/{pid}')
    def photo(pid:str):
        p=store.photo(pid)
        return Response(p['data'],media_type=p['media_type'],headers={'Content-Disposition':'inline','X-Content-Type-Options':'nosniff'})

    @app.patch('/api/enquiries/{eid}/work')
    def work(eid:str,body:WorkInput):
        return store.set_work(eid,**body.model_dump())

    @app.post('/api/alerts/{aid}/acknowledge')
    def acknowledge(aid:int):
        store.acknowledge_alert(aid)
        return {'ok':True}

    @app.post('/api/enquiries/{eid}/quotes')
    def save_quote(eid:str,body:QuoteInput):
        return store.save_quote(eid,**body.model_dump())

    @app.post('/api/quotes/{qid}/approve')
    def send_quote(qid:str,body:QuoteVersion):
        return store.approve_quote(qid,body.version,transport)

    @app.post('/api/quotes/{qid}/decision')
    def decide(qid:str,body:QuoteDecision,idempotency_key:str=Header(default='')):
        if not 8<=len(idempotency_key)<=100:raise HTTPException(422,'A decision key is required.')
        return store.decide_quote(qid,body.token,idempotency_key,body.decision,body.note)

    @app.post('/api/knowledge/gaps/{gid}/approve')
    def answer(gid:str,body:ApprovedAnswer):
        result=store.publish_answer(gid,body.answer,body.version)
        with knowledge.lock:knowledge.sync()
        return result

    @app.post('/api/enquiries/{eid}/crm-sync')
    def queue_crm(eid:str):
        if not live_crm:raise HTTPException(409,'HubSpot is not authorized for this runtime. See the connection guide; local features work without it.')
        return {'task_id':store.queue_crm(eid)}
