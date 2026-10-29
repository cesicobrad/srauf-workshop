"""One SQLite file: bots, documents and their chunks (with embeddings), conversations, messages and leads.

A single connection behind a lock is plenty for a small-business widget and keeps hosting to one file.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,             -- the assistant's name shown in the widget
    business TEXT NOT NULL,         -- the business it speaks for
    contact_email TEXT NOT NULL DEFAULT '',
    allowed_origins TEXT NOT NULL DEFAULT '',   -- comma-separated; empty = any site
    monthly_cap_usd REAL NOT NULL DEFAULT 5.0,
    color TEXT NOT NULL DEFAULT '#1f6f5c',
    suggestions TEXT NOT NULL DEFAULT '{}',     -- {"en": ["question", ...], "sl": [...]}: starter chips
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    source TEXT NOT NULL,           -- file name or URL
    kind TEXT NOT NULL,             -- md | txt | pdf | url
    chars INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    bot_id TEXT NOT NULL,
    heading TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB                  -- float32, normalised; NULL when embeddings are off
);
CREATE INDEX IF NOT EXISTS chunks_bot ON chunks(bot_id);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    last_at TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT '',
    page TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS conv_bot ON conversations(bot_id, last_at);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    role TEXT NOT NULL,             -- user | bot
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '',-- bot only: answered | unknown | out_of_scope | handoff | blocked | limited
    sources TEXT NOT NULL DEFAULT '[]',
    language TEXT NOT NULL DEFAULT '',
    engine TEXT NOT NULL DEFAULT '',-- model id, or "demo"
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    flagged INTEGER NOT NULL DEFAULT 0,  -- the injection heuristic fired on the question
    resolved INTEGER NOT NULL DEFAULT 0, -- admin ticked an unanswered question off
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS msg_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS msg_bot ON messages(bot_id, created_at);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    bot_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    question TEXT NOT NULL DEFAULT '',
    delivery TEXT NOT NULL DEFAULT 'logged',  -- logged | emailed | email_failed
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Bot:
    id: str
    name: str
    business: str
    contact_email: str
    allowed_origins: list[str]
    monthly_cap_usd: float
    color: str
    suggestions: dict

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "business": self.business, "color": self.color,
                "suggestions": self.suggestions}


@dataclass
class Chunk:
    id: int
    doc_id: int
    doc_title: str
    source: str
    kind: str
    heading: str
    text: str
    embedding: bytes | None

    @property
    def cite(self) -> str:
        return f"{self.doc_title} › {self.heading}" if self.heading and self.heading != self.doc_title else self.doc_title

    @property
    def url(self) -> str:
        return self.source if self.kind == "url" else ""


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.executescript(SCHEMA)

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # ---- bots ----------------------------------------------------------------------------------
    def upsert_bot(self, id: str, name: str, business: str, contact_email: str = "",
                   allowed_origins: str = "", monthly_cap_usd: float = 5.0, color: str = "#1f6f5c",
                   suggestions: dict | None = None) -> None:
        self.x("""INSERT INTO bots (id, name, business, contact_email, allowed_origins, monthly_cap_usd, color,
                  suggestions, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                  ON CONFLICT(id) DO UPDATE SET name=excluded.name, business=excluded.business,
                  contact_email=excluded.contact_email, allowed_origins=excluded.allowed_origins,
                  monthly_cap_usd=excluded.monthly_cap_usd, color=excluded.color, suggestions=excluded.suggestions""",
               (id, name, business, contact_email, allowed_origins, monthly_cap_usd, color,
                json.dumps(suggestions or {}, ensure_ascii=False), now_iso()))

    def bot(self, bot_id: str) -> Bot | None:
        rows = self.q("SELECT * FROM bots WHERE id = ?", (bot_id,))
        if not rows:
            return None
        r = rows[0]
        origins = [o.strip().rstrip("/") for o in r["allowed_origins"].split(",") if o.strip()]
        return Bot(r["id"], r["name"], r["business"], r["contact_email"], origins, r["monthly_cap_usd"], r["color"],
                   json.loads(r["suggestions"] or "{}"))

    def bots(self) -> list[Bot]:
        return [self.bot(r["id"]) for r in self.q("SELECT id FROM bots ORDER BY created_at")]

    def set_cap(self, bot_id: str, cap: float) -> None:
        self.x("UPDATE bots SET monthly_cap_usd = ? WHERE id = ?", (cap, bot_id))

    # ---- documents -----------------------------------------------------------------------------
    def add_document(self, bot_id: str, title: str, source: str, kind: str,
                     chunks: list[tuple[str, str, bytes | None]]) -> int:
        """Replace any document with the same source, then store the new one with its chunks."""
        with self.lock:
            self.conn.execute("DELETE FROM documents WHERE bot_id = ? AND source = ?", (bot_id, source))
            chars = sum(len(t) for _, t, _ in chunks)
            cur = self.conn.execute(
                "INSERT INTO documents (bot_id, title, source, kind, chars, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (bot_id, title, source, kind, chars, now_iso()))
            doc_id = cur.lastrowid
            self.conn.executemany(
                "INSERT INTO chunks (doc_id, bot_id, heading, text, embedding) VALUES (?, ?, ?, ?, ?)",
                [(doc_id, bot_id, h, t, e) for h, t, e in chunks])
            self.conn.commit()
            return doc_id

    def documents(self, bot_id: str) -> list[dict]:
        rows = self.q("""SELECT d.*, COUNT(c.id) AS n_chunks FROM documents d LEFT JOIN chunks c ON c.doc_id = d.id
                         WHERE d.bot_id = ? GROUP BY d.id ORDER BY d.title""", (bot_id,))
        return [dict(r) for r in rows]

    def delete_document(self, bot_id: str, doc_id: int) -> bool:
        with self.lock:
            cur = self.conn.execute("DELETE FROM documents WHERE id = ? AND bot_id = ?", (doc_id, bot_id))
            self.conn.commit()
            return cur.rowcount > 0

    def chunks(self, bot_id: str) -> list[Chunk]:
        rows = self.q("""SELECT c.id, c.doc_id, d.title, d.source, d.kind, c.heading, c.text, c.embedding
                         FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE c.bot_id = ? ORDER BY c.id""",
                      (bot_id,))
        return [Chunk(*tuple(r)) for r in rows]

    # ---- conversations -------------------------------------------------------------------------
    def touch_conversation(self, conv_id: str, bot_id: str, page: str = "") -> None:
        now = now_iso()
        self.x("""INSERT INTO conversations (id, bot_id, started_at, last_at, page) VALUES (?, ?, ?, ?, ?)
                  ON CONFLICT(id) DO UPDATE SET last_at = excluded.last_at""", (conv_id, bot_id, now, now, page[:300]))

    def conversation_language(self, conv_id: str) -> str:
        rows = self.q("SELECT language FROM conversations WHERE id = ?", (conv_id,))
        return rows[0]["language"] if rows else ""

    def conversation_bot(self, conv_id: str) -> str | None:
        rows = self.q("SELECT bot_id FROM conversations WHERE id = ?", (conv_id,))
        return rows[0]["bot_id"] if rows else None

    def add_message(self, conv_id: str, bot_id: str, role: str, text: str, **fields) -> int:
        cols = ["conversation_id", "bot_id", "role", "text", "created_at"]
        vals: list = [conv_id, bot_id, role, text, now_iso()]
        for k, v in fields.items():
            cols.append(k)
            vals.append(json.dumps(v, ensure_ascii=False) if k == "sources" else v)
        msg_id = self.x(f"INSERT INTO messages ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(vals))
        if role == "bot" and fields.get("language"):
            self.x("UPDATE conversations SET language = ? WHERE id = ?", (fields["language"], conv_id))
        return msg_id

    def history(self, conv_id: str, limit: int = 6) -> list[dict]:
        rows = self.q("""SELECT role, text FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?""",
                      (conv_id, limit))
        return [dict(r) for r in reversed(rows)]

    def conversations(self, bot_id: str, limit: int = 100) -> list[dict]:
        rows = self.q("""SELECT c.*, COUNT(m.id) AS n_messages,
                           SUM(CASE WHEN m.role='bot' AND m.status IN ('unknown','out_of_scope','handoff') THEN 1 ELSE 0 END) AS n_unanswered,
                           SUM(m.cost_usd) AS cost_usd,
                           (SELECT text FROM messages WHERE conversation_id = c.id AND role='user' ORDER BY id LIMIT 1) AS first_question,
                           EXISTS(SELECT 1 FROM leads l WHERE l.conversation_id = c.id) AS has_lead
                         FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id
                         WHERE c.bot_id = ? GROUP BY c.id ORDER BY c.last_at DESC LIMIT ?""", (bot_id, limit))
        return [dict(r) for r in rows]

    def messages(self, conv_id: str) -> list[dict]:
        rows = self.q("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conv_id,))
        out = []
        for r in rows:
            d = dict(r)
            d["sources"] = json.loads(d["sources"] or "[]")
            out.append(d)
        return out

    def unanswered(self, bot_id: str, include_resolved: bool = False) -> list[dict]:
        """Bot replies that didn't answer, paired with the question that preceded them."""
        rows = self.q(f"""SELECT b.id, b.conversation_id, b.status, b.language, b.created_at, b.resolved, b.flagged,
                            (SELECT u.text FROM messages u WHERE u.conversation_id = b.conversation_id AND u.role = 'user'
                             AND u.id < b.id ORDER BY u.id DESC LIMIT 1) AS question
                          FROM messages b WHERE b.bot_id = ? AND b.role = 'bot'
                          AND b.status IN ('unknown', 'out_of_scope', 'handoff')
                          {'' if include_resolved else 'AND b.resolved = 0'}
                          ORDER BY b.id DESC LIMIT 200""", (bot_id,))
        return [dict(r) for r in rows]

    def resolve(self, bot_id: str, msg_id: int) -> None:
        self.x("UPDATE messages SET resolved = 1 WHERE id = ? AND bot_id = ?", (msg_id, bot_id))

    # ---- spend ---------------------------------------------------------------------------------
    def month_spend(self, bot_id: str, month: str) -> float:
        """`month` is "YYYY-MM" (UTC)."""
        rows = self.q("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM messages WHERE bot_id = ? AND created_at LIKE ?",
                      (bot_id, f"{month}%"))
        return float(rows[0]["s"])

    def month_stats(self, bot_id: str, month: str) -> dict:
        r = self.q("""SELECT COUNT(DISTINCT conversation_id) AS conversations,
                             SUM(role='user') AS questions,
                             SUM(role='bot' AND status='answered') AS answered,
                             SUM(role='bot' AND status IN ('unknown','out_of_scope','handoff')) AS unanswered,
                             COALESCE(SUM(cost_usd),0) AS cost_usd,
                             COALESCE(SUM(input_tokens),0) AS input_tokens, COALESCE(SUM(output_tokens),0) AS output_tokens,
                             AVG(CASE WHEN role='bot' AND latency_ms > 0 THEN latency_ms END) AS avg_latency_ms
                      FROM messages WHERE bot_id = ? AND created_at LIKE ?""", (bot_id, f"{month}%"))[0]
        leads = self.q("SELECT COUNT(*) AS n FROM leads WHERE bot_id = ? AND created_at LIKE ?", (bot_id, f"{month}%"))[0]
        d = {k: (r[k] or 0) for k in r.keys()}
        d["leads"] = leads["n"]
        return d

    # ---- leads ---------------------------------------------------------------------------------
    def add_lead(self, bot_id: str, conv_id: str, name: str, email: str, question: str) -> int:
        return self.x("INSERT INTO leads (bot_id, conversation_id, name, email, question, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                      (bot_id, conv_id, name, email, question, now_iso()))

    def set_lead_delivery(self, lead_id: int, delivery: str) -> None:
        self.x("UPDATE leads SET delivery = ? WHERE id = ?", (delivery, lead_id))

    def leads(self, bot_id: str) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM leads WHERE bot_id = ? ORDER BY id DESC LIMIT 200", (bot_id,))]

    def recent_leads_from(self, conv_id: str) -> int:
        return self.q("SELECT COUNT(*) AS n FROM leads WHERE conversation_id = ?", (conv_id,))[0]["n"]
