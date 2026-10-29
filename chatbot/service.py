"""The chat pipeline, independent of HTTP: validate, detect language, search, answer, check, log."""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from .brain import BrainError, ClaudeBrain, DemoBrain, Reply
from .config import ROOT, Settings
from .db import DB, Bot
from .guard import looks_like_injection, new_canary, strip_control
from .i18n import t
from .ingest import Parsed, chunk_sections, fetch_url, parse_upload
from .language import confident, detect
from .search import Embedder, SearchService

log = logging.getLogger("chatbot")

DEMO_BOT = dict(id="srauf", name="Špica", business="Šrauf Bike Repair", contact_email="servis@srauf.example",
                allowed_origins="", color="#1f6f5c", suggestions={
                    "en": ["How much is a basic service?", "Are you open on Saturday?", "Do you repair e-bikes?"],
                    "sl": ["Koliko stane osnovni servis?", "Ali imate odprto ob sobotah?", "Ali popravljate e-kolesa?"],
                    "hr": ["Koliko košta osnovni servis?", "Radite li subotom?", "Popravljate li e-bicikle?"],
                    "sr": ["Колико кошта основни сервис?", "Да ли радите суботом?", "Да ли поправљате е-бицикле?"]})
DEMO_DOCS = ROOT / "demo" / "docs"


def month_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


@dataclass
class ChatResult:
    reply: Reply
    conversation_id: str
    flagged: bool = False


class ChatService:
    def __init__(self, settings: Settings, db: DB | None = None, embedder: Embedder | None = None,
                 claude: ClaudeBrain | None = None):
        self.settings = settings
        self.db = db or DB(settings.db_path)
        if embedder is None and settings.embeddings == "local":
            embedder = Embedder(settings.embedding_model)
        self.search = SearchService(self.db, embedder)
        self.demo = DemoBrain()
        self.claude = claude
        if self.claude is None and settings.use_claude:
            self.claude = ClaudeBrain(settings.model, settings.effort)
        self.canary = new_canary()

    @property
    def mode(self) -> str:
        return "claude" if self.claude else "demo"

    # ---- documents -----------------------------------------------------------------------------
    def store(self, bot_id: str, parsed: Parsed, source: str) -> dict:
        chunks = chunk_sections(parsed.sections)
        if not chunks:
            from .ingest import IngestError
            raise IngestError("No text found in this document.")
        embeddings = self.search.embed_chunks([(parsed.title, c.heading, c.text) for c in chunks])
        doc_id = self.db.add_document(bot_id, parsed.title, source, parsed.kind,
                                      [(c.heading, c.text, e) for c, e in zip(chunks, embeddings)])
        self.search.invalidate(bot_id)
        return {"id": doc_id, "title": parsed.title, "chunks": len(chunks)}

    def add_upload(self, bot_id: str, filename: str, data: bytes) -> dict:
        return self.store(bot_id, parse_upload(filename, data), Path(filename).name)

    def add_url(self, bot_id: str, url: str) -> dict:
        return self.store(bot_id, fetch_url(url), url)

    def delete_document(self, bot_id: str, doc_id: int) -> bool:
        ok = self.db.delete_document(bot_id, doc_id)
        self.search.invalidate(bot_id)
        return ok

    def seed_demo(self) -> None:
        """Create the demo bike shop and load its documents the first time the server starts."""
        if self.db.bot(DEMO_BOT["id"]):
            return
        self.db.upsert_bot(monthly_cap_usd=self.settings.default_monthly_cap_usd, **DEMO_BOT)
        for path in sorted(DEMO_DOCS.iterdir()):
            if path.suffix.lower() in {".md", ".pdf", ".txt"}:
                info = self.add_upload(DEMO_BOT["id"], path.name, path.read_bytes())
                log.info("demo document %s: %d chunks", path.name, info["chunks"])

    # ---- spend ---------------------------------------------------------------------------------
    def budget_left(self, bot: Bot) -> float:
        return bot.monthly_cap_usd - self.db.month_spend(bot.id, month_now())

    # ---- chat ----------------------------------------------------------------------------------
    def ask(self, bot: Bot, conv_id: str, question: str, lang_hint: str = "", page: str = "") -> ChatResult:
        question = strip_control(question)
        # A short follow-up ("A nedjeljom?") has no clear language: keep the conversation's language.
        if not confident(question):
            lang_hint = self.db.conversation_language(conv_id) or lang_hint
        lang = detect(question, lang_hint)
        if len(question) > self.settings.max_question_chars:
            reply = Reply(t("too_long", lang, n=self.settings.max_question_chars), "limited", lang)
            return ChatResult(reply, conv_id)
        flagged = looks_like_injection(question)
        self.db.touch_conversation(conv_id, bot.id, page)
        history = self.db.history(conv_id, limit=6)
        self.db.add_message(conv_id, bot.id, "user", question, language=lang, flagged=int(flagged))

        # Follow-up questions ("and on Saturday?") search better with the previous question attached.
        last_user = next((m["text"] for m in reversed(history) if m["role"] == "user"), "")
        query = f"{last_user}\n{question}" if last_user and len(question) < 60 else question
        hits = self.search.search(bot.id, query, k=8)  # demo mode quotes the best one
        context = (self.search.context(bot.id, query, k=8, full_chars=self.settings.full_context_chars)
                   if self.claude else hits)

        reply = None
        if self.claude and self.budget_left(bot) > 0:
            try:
                reply = self.claude.answer(bot, question, history, context, lang, self.canary)
            except BrainError as e:
                log.warning("Claude failed, answering from documents: %s", e)
                reply = self.demo.answer(bot, question, hits, lang, note=t("error_note", lang))
        elif self.claude:
            reply = self.demo.answer(bot, question, hits, lang, note=t("budget_note", lang))
        else:
            reply = self.demo.answer(bot, question, hits, lang)

        self.db.add_message(conv_id, bot.id, "bot", reply.text, status=reply.status, sources=reply.sources,
                            language=reply.language, engine=reply.engine, input_tokens=reply.input_tokens,
                            output_tokens=reply.output_tokens, cost_usd=reply.cost_usd,
                            latency_ms=reply.latency_ms, flagged=int(flagged))
        return ChatResult(reply, conv_id, flagged)

    # ---- leads ---------------------------------------------------------------------------------
    def add_lead(self, bot: Bot, conv_id: str, name: str, email: str, question: str) -> int:
        lead_id = self.db.add_lead(bot.id, conv_id, name, email, question)
        log.info("lead #%d for %s: %s <%s>", lead_id, bot.id, name, email)
        if self.settings.smtp_host and bot.contact_email:
            try:
                self.send_lead_email(bot, name, email, question, conv_id)
                self.db.set_lead_delivery(lead_id, "emailed")
            except Exception as e:  # never lose the lead because e-mail failed; it's in the database
                log.warning("lead e-mail failed: %s", e)
                self.db.set_lead_delivery(lead_id, "email_failed")
        return lead_id

    def send_lead_email(self, bot: Bot, name: str, email: str, question: str, conv_id: str) -> None:
        s = self.settings
        msg = EmailMessage()
        msg["Subject"] = f"[{bot.name}] {name} needs a reply"
        msg["From"] = s.smtp_from or s.smtp_user
        msg["To"] = bot.contact_email
        msg["Reply-To"] = email
        transcript = "\n".join(f"{m['role']}: {m['text']}" for m in self.db.messages(conv_id))
        msg.set_content(f"{name} <{email}> asked the website assistant:\n\n{question}\n\n"
                        f"Conversation:\n{transcript}\n")
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
