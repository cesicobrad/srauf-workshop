"""HTTP API, the widget script, the demo page and the admin page. Run: uvicorn chatbot.main:app"""
from __future__ import annotations

import logging
import os
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ROOT, Settings
from .db import Bot
from .guard import RateLimiter, strip_control
from .i18n import t
from .ingest import IngestError
from .language import detect, normalise
from .service import ChatService, month_now

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("chatbot")

STATIC = ROOT / "static"
DEMO = ROOT / "demo"
PUBLIC_PREFIXES = ("/api/chat", "/api/lead", "/api/bots/", "/widget.js")
EMAIL = re.compile(r"^[^@\s<>\"']{1,64}@[^@\s<>\"']{1,190}\.[a-zA-Z]{2,24}$")
CONV_ID = re.compile(r"^[a-f0-9]{32}$")


class ChatIn(BaseModel):
    bot: str = Field(max_length=64)
    message: str = Field(max_length=4000)
    conversation_id: str = Field(default="", max_length=64)
    lang: str = Field(default="", max_length=20)
    page: str = Field(default="", max_length=500)


class LeadIn(BaseModel):
    bot: str = Field(max_length=64)
    conversation_id: str = Field(default="", max_length=64)
    name: str = Field(max_length=200)
    email: str = Field(max_length=254)
    question: str = Field(default="", max_length=2000)


class CapIn(BaseModel):
    monthly_cap_usd: float = Field(ge=0, le=10000)


class UrlIn(BaseModel):
    url: str = Field(max_length=2000)


def create_app(settings: Settings | None = None, service: ChatService | None = None,
               limiter: RateLimiter | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    limiter = limiter or RateLimiter()
    state: dict = {"service": service}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["service"] is None:
            state["service"] = ChatService(settings)
        svc: ChatService = state["service"]
        if settings.seed_demo:
            svc.seed_demo()
        log.info("mode: %s (model %s), embeddings: %s", svc.mode, settings.model if svc.claude else "-",
                 "on" if svc.search.embedder else "off")
        yield

    app = FastAPI(title="Website chatbot", lifespan=lifespan, docs_url=None, redoc_url=None)

    def svc() -> ChatService:
        return state["service"]

    # ---- CORS only for the public endpoints the widget calls from other sites -------------------
    @app.middleware("http")
    async def cors(request: Request, call_next):
        public = request.url.path.startswith(PUBLIC_PREFIXES)
        if request.method == "OPTIONS" and public:
            response = JSONResponse({}, status_code=204)
        else:
            response = await call_next(request)
        if public:
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Max-Age"] = "600"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith("/admin"):
            response.headers["X-Frame-Options"] = "DENY"
        return response

    def client_ip(request: Request) -> str:
        if os.getenv("TRUST_PROXY") and request.headers.get("x-forwarded-for"):
            return request.headers["x-forwarded-for"].split(",")[0].strip()
        return request.client.host if request.client else "?"

    def get_bot(bot_id: str, request: Request) -> Bot:
        bot = svc().db.bot(bot_id)
        if not bot:
            raise HTTPException(404, "Unknown bot")
        origin = (request.headers.get("origin") or "").rstrip("/")
        if bot.allowed_origins and origin and origin not in bot.allowed_origins:
            raise HTTPException(403, "This site may not use this bot")
        return bot

    # ---- public API ----------------------------------------------------------------------------

    @app.get("/api/health")
    def health():
        return {"ok": True, "mode": svc().mode}

    @app.get("/api/bots/{bot_id}")
    def bot_config(bot_id: str, request: Request):
        return {**get_bot(bot_id, request).public(), "mode": svc().mode}

    @app.post("/api/chat")
    def chat(body: ChatIn, request: Request):
        bot = get_bot(body.bot, request)
        message = strip_control(body.message)
        if not message:
            raise HTTPException(400, "Empty message")
        ip = client_ip(request)
        allowed = limiter.check_all([
            (f"ip:{ip}:m", settings.per_ip_per_minute, 60),
            (f"ip:{ip}:d", settings.per_ip_per_day, 86400),
            (f"bot:{bot.id}:m", settings.per_bot_per_minute, 60),
        ])
        lang = detect(message, normalise(body.lang))
        if not allowed:
            return JSONResponse({"conversation_id": body.conversation_id, "reply": t("rate_limited", lang),
                                 "status": "limited", "language": lang, "sources": [], "offer_handoff": False},
                                status_code=429)
        conv = body.conversation_id if CONV_ID.match(body.conversation_id or "") else ""
        if conv and svc().db.conversation_bot(conv) not in (None, bot.id):
            conv = ""
        conv = conv or uuid.uuid4().hex
        result = svc().ask(bot, conv, message, normalise(body.lang), body.page)
        r = result.reply
        return {"conversation_id": conv, "reply": r.text, "status": r.status, "language": r.language,
                "sources": r.sources, "offer_handoff": r.offer_handoff, "engine": r.engine}

    @app.post("/api/lead")
    def lead(body: LeadIn, request: Request):
        bot = get_bot(body.bot, request)
        name = " ".join(strip_control(body.name).split())[:100]
        email = body.email.strip()
        if not name or not EMAIL.match(email):
            raise HTTPException(422, "Please give a name and a valid e-mail address.")
        ip = client_ip(request)
        if not limiter.check_all([(f"lead:{ip}", 5, 3600)]):
            raise HTTPException(429, "Too many requests")
        conv = body.conversation_id if CONV_ID.match(body.conversation_id or "") else ""
        if conv and svc().db.conversation_bot(conv) != bot.id:
            conv = ""
        if conv and svc().db.recent_leads_from(conv) >= 3:
            raise HTTPException(429, "Too many requests")
        lead_id = svc().add_lead(bot, conv, name, email, strip_control(body.question)[:1000])
        return {"ok": True, "id": lead_id}

    # ---- admin ---------------------------------------------------------------------------------
    basic = HTTPBasic(auto_error=False)

    def admin(request: Request, creds: HTTPBasicCredentials | None = Depends(basic)) -> None:
        if settings.admin_password:
            if creds and secrets.compare_digest(creds.password.encode(), settings.admin_password.encode()):
                return
            raise HTTPException(401, "Admin password required", headers={"WWW-Authenticate": 'Basic realm="admin"'})
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise HTTPException(403, "Set ADMIN_PASSWORD to use the admin page from another machine.")

    def admin_bot(bot_id: str) -> Bot:
        bot = svc().db.bot(bot_id)
        if not bot:
            raise HTTPException(404, "Unknown bot")
        return bot

    @app.get("/admin", include_in_schema=False)
    def admin_page(_: None = Depends(admin)):
        return FileResponse(STATIC / "admin.html")

    @app.get("/api/admin/bots")
    def admin_bots(_: None = Depends(admin)):
        return [b.public() for b in svc().db.bots()]

    @app.get("/api/admin/{bot_id}/overview")
    def overview(bot_id: str, request: Request, _: None = Depends(admin)):
        bot = admin_bot(bot_id)
        month = month_now()
        stats = svc().db.month_stats(bot.id, month)
        base = settings.public_url or str(request.base_url).rstrip("/")
        return {"bot": bot.public(), "month": month, "stats": stats, "cap_usd": bot.monthly_cap_usd,
                "spent_usd": stats["cost_usd"], "mode": svc().mode, "model": settings.model if svc().claude else None,
                "embeddings": bool(svc().search.embedder), "allowed_origins": bot.allowed_origins,
                "embed": f'<script src="{base}/widget.js" data-bot="{bot.id}" defer></script>'}

    @app.post("/api/admin/{bot_id}/cap")
    def set_cap(bot_id: str, body: CapIn, _: None = Depends(admin)):
        admin_bot(bot_id)
        svc().db.set_cap(bot_id, round(body.monthly_cap_usd, 2))
        return {"ok": True}

    @app.get("/api/admin/{bot_id}/documents")
    def documents(bot_id: str, _: None = Depends(admin)):
        admin_bot(bot_id)
        return svc().db.documents(bot_id)

    @app.post("/api/admin/{bot_id}/documents")
    async def upload(bot_id: str, file: UploadFile = File(...), _: None = Depends(admin)):
        admin_bot(bot_id)
        data = await file.read(5 * 1024 * 1024 + 1)
        try:
            return svc().add_upload(bot_id, file.filename or "document.txt", data)
        except IngestError as e:
            raise HTTPException(422, str(e))

    @app.post("/api/admin/{bot_id}/documents/url")
    def add_url(bot_id: str, body: UrlIn, _: None = Depends(admin)):
        admin_bot(bot_id)
        try:
            return svc().add_url(bot_id, body.url.strip())
        except IngestError as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/admin/{bot_id}/documents/{doc_id}")
    def delete_doc(bot_id: str, doc_id: int, _: None = Depends(admin)):
        if not svc().delete_document(bot_id, doc_id):
            raise HTTPException(404, "No such document")
        return {"ok": True}

    @app.get("/api/admin/{bot_id}/conversations")
    def conversations(bot_id: str, _: None = Depends(admin)):
        admin_bot(bot_id)
        return svc().db.conversations(bot_id)

    @app.get("/api/admin/{bot_id}/conversations/{conv_id}")
    def conversation(bot_id: str, conv_id: str, _: None = Depends(admin)):
        if svc().db.conversation_bot(conv_id) != bot_id:
            raise HTTPException(404, "No such conversation")
        return svc().db.messages(conv_id)

    @app.get("/api/admin/{bot_id}/unanswered")
    def unanswered(bot_id: str, _: None = Depends(admin)):
        admin_bot(bot_id)
        return svc().db.unanswered(bot_id)

    @app.post("/api/admin/{bot_id}/unanswered/{msg_id}/resolve")
    def resolve(bot_id: str, msg_id: int, _: None = Depends(admin)):
        svc().db.resolve(bot_id, msg_id)
        return {"ok": True}

    @app.get("/api/admin/{bot_id}/leads")
    def leads(bot_id: str, _: None = Depends(admin)):
        admin_bot(bot_id)
        return svc().db.leads(bot_id)

    # ---- static files --------------------------------------------------------------------------
    @app.get("/widget.js", include_in_schema=False)
    def widget():
        return FileResponse(STATIC / "widget.js", media_type="text/javascript",
                            headers={"Cache-Control": "public, max-age=300"})

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/demo/")

    app.mount("/demo", StaticFiles(directory=DEMO, html=True), name="demo")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
