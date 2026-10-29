"""Durable enquiries, outbox and audit trail. Side effects never depend on browser state."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .features import FeatureStore


class Conflict(ValueError):
    pass


DEFAULT_FACTS = {
    "version": 1, "full_service": 79, "basic_service": 39,
    "hours": "Monday–Friday 09:00–18:00; Saturday 09:00–13:00; Sunday closed",
    "hours_sl": "Ponedeljek–petek 09:00–18:00; sobota 09:00–13:00; nedelja zaprto",
    "hours_hr": "Ponedjeljak–petak 09:00–18:00; subota 09:00–13:00; nedjeljom zatvoreno",
    "notes": "Custom cargo-bike repairs need a mechanic's inspection. No price or slot is promised before review.",
    "conflict": False,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS enquiries (
 id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, conversation_id TEXT NOT NULL,
 name TEXT NOT NULL, email TEXT NOT NULL, question TEXT NOT NULL, language TEXT NOT NULL,
 transcript TEXT NOT NULL, status TEXT NOT NULL, draft_subject TEXT NOT NULL DEFAULT '',
 draft_body TEXT NOT NULL DEFAULT '', draft_kind TEXT NOT NULL DEFAULT 'reply',
 created_at TEXT NOT NULL, sent_at TEXT, replied_at TEXT,
 followup_count INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS submissions (key TEXT PRIMARY KEY, digest TEXT NOT NULL, enquiry_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, enquiry_id TEXT NOT NULL, kind TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 lease_until TEXT, error TEXT NOT NULL DEFAULT '', execution_id TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
 id TEXT PRIMARY KEY, enquiry_id TEXT NOT NULL, kind TEXT NOT NULL,
 recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', transport TEXT NOT NULL DEFAULT 'preview',
 message_id TEXT UNIQUE NOT NULL, error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, delivered_at TEXT, UNIQUE(enquiry_id, kind)
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, enquiry_id TEXT, type TEXT NOT NULL,
 note TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbound (
 message_id TEXT PRIMARY KEY, enquiry_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class Store(FeatureStore):
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout = 10000")
        if str(path) != ":memory:":
            self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)
        for k, v in {"facts": DEFAULT_FACTS, "clock_hours": 0, "delivery_paused": False, "automation_paused": False}.items():
            self.db.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (k, json.dumps(v)))
        self.db.commit()
        self.initialize_features()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def setting(self, key):
        with self.lock:
            return json.loads(self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0])

    def _set(self, key, value):
        self.db.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(value, ensure_ascii=False), key))

    def now(self):
        return (datetime.now(timezone.utc) + timedelta(hours=self.setting("clock_hours"))).isoformat(timespec="seconds")

    def event(self, eid, kind, note):
        self.db.execute("INSERT INTO events (enquiry_id,type,note,created_at) VALUES (?,?,?,?)", (eid, kind, note, self.now()))

    def _task(self, eid, kind):
        tid = uuid.uuid4().hex
        self.db.execute("INSERT INTO tasks (id,enquiry_id,kind,created_at,updated_at) VALUES (?,?,?,?,?)", (tid,eid,kind,self.now(),self.now()))
        return tid

    def create_enquiry(self, *, key, conversation_id, name, email, question, language, transcript, photos=None):
        photos=photos or []
        payload = dict(conversation_id=conversation_id, name=name.strip(), email=email.strip().lower(),
                       question=question.strip(), language=language, transcript=transcript,
                       photos=[{"filename":p["filename"],"hash":hashlib.sha256(p["data"]).hexdigest()} for p in photos])
        # Transcript is a server-side snapshot. Later chat turns must not invalidate a retry
        # of the same customer submission, or overwrite its original handover context.
        digest = hashlib.sha256(json.dumps({k:v for k,v in payload.items() if k!="transcript"}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        fingerprint = hashlib.sha256((payload["email"] + "|" + conversation_id + "|" + payload["question"]+"|"+json.dumps(payload["photos"],sort_keys=True)).encode()).hexdigest()
        with self.transaction():
            seen = self.db.execute("SELECT * FROM submissions WHERE key=?", (key,)).fetchone()
            if seen:
                if seen["digest"] != digest:
                    raise Conflict("This submission key was already used for different details. Start a new request.")
                return self.detail(seen["enquiry_id"]), True
            seen = self.db.execute("SELECT id FROM enquiries WHERE fingerprint=?", (fingerprint,)).fetchone()
            if seen:
                self.db.execute("INSERT INTO submissions VALUES (?,?,?)", (key,digest,seen["id"]))
                return self.detail(seen["id"]), True
            eid = uuid.uuid4().hex
            self.db.execute("""INSERT INTO enquiries
                (id,fingerprint,conversation_id,name,email,question,language,transcript,status,created_at,due_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (eid,fingerprint,conversation_id,payload["name"],payload["email"],
                payload["question"],language,json.dumps(transcript,ensure_ascii=False),"queued",self.now(),
                (datetime.fromisoformat(self.now())+timedelta(hours=24)).isoformat(timespec="seconds")))
            self.add_photos(eid,photos)
            self.db.execute("INSERT INTO submissions VALUES (?,?,?)", (key,digest,eid))
            self._task(eid,"intake")
            self.event(eid,"received","Customer details and conversation saved before dispatch to n8n.")
            return self.detail(eid), False

    def detail(self, eid):
        with self.lock:
            row = self.db.execute("SELECT * FROM enquiries WHERE id=?", (eid,)).fetchone()
            if not row:
                raise KeyError(eid)
            result = dict(row)
            result["transcript"] = json.loads(result["transcript"])
            for table in ("tasks","outbox","events"):
                result[table] = [dict(r) for r in self.db.execute(f"SELECT * FROM {table} WHERE enquiry_id=? ORDER BY created_at,rowid",(eid,))]
            return self.enrich_detail(result)

    def snapshot(self):
        with self.lock:
            enquiries = [self.detail(r["id"]) for r in self.db.execute("SELECT id FROM enquiries ORDER BY created_at DESC,rowid DESC")]
            return dict(enquiries=enquiries, facts=self.setting("facts"), clock_hours=self.setting("clock_hours"),
                        now=self.now(), delivery_paused=self.setting("delivery_paused"), automation_paused=self.setting("automation_paused"),
                        events=[dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 80")],**self.feature_snapshot())

    def claim_task(self):
        with self.transaction():
            if self.setting("automation_paused"):
                return None
            # Lease uses the real clock: advancing the demo clock must not steal a running job.
            real = datetime.now(timezone.utc).isoformat(timespec="seconds")
            r = self.db.execute("SELECT * FROM tasks WHERE state='queued' OR (state='processing' AND lease_until<?) ORDER BY rowid LIMIT 1", (real,)).fetchone()
            if not r:
                return None
            until = (datetime.now(timezone.utc)+timedelta(seconds=90)).isoformat(timespec="seconds")
            self.db.execute("UPDATE tasks SET state='processing',attempts=attempts+1,lease_until=?,updated_at=? WHERE id=?", (until,self.now(),r["id"]))
            self.event(r["enquiry_id"],"dispatch",f"Sent {r['kind']} job to n8n (attempt {r['attempts']+1}).")
            return dict(self.db.execute("SELECT * FROM tasks WHERE id=?", (r["id"],)).fetchone())

    def task(self, tid):
        with self.lock:
            r = self.db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if not r:
                raise KeyError(tid)
            return dict(r)

    def draft(self, tid, execution_id, generated=None):
        with self.transaction():
            task = self.task(tid)
            if task["kind"] not in ("intake","followup"):
                raise Conflict("This job cannot create a draft.")
            e = self.detail(task["enquiry_id"])
            if task["state"] in ("completed","cancelled"):
                return {"ok": True,"duplicate":True}
            kind = "followup" if task["kind"] == "followup" else "reply"
            if kind == "followup" and e["replied_at"]:
                self.db.execute("UPDATE tasks SET state='cancelled' WHERE id=?", (tid,))
                return {"ok":True,"cancelled":True}
            text = generated["body"] if generated else draft_text(e, kind)
            subject = generated["subject"] if generated else {"sl":"Vaše povpraševanje za popravilo", "hr":"Vaš upit za popravak"}.get(e["language"],"Your repair enquiry")
            self.db.execute("UPDATE enquiries SET status=?,draft_subject=?,draft_body=?,draft_kind=?,version=version+1 WHERE id=?",
                            ("awaiting_review" if kind=="reply" else "followup_review",subject,text,kind,e["id"]))
            self.db.execute("UPDATE tasks SET state='completed',execution_id=?,updated_at=?,error='' WHERE id=?", (execution_id,self.now(),tid))
            origin = "AI draft" if generated else "template"
            self.event(e["id"],"draft_ready",f"n8n execution {execution_id} prepared a {kind} {origin}. A person must edit or approve it.")
            if generated and generated.get("reviewer_notes"):
                self.event(e["id"],"review_note",generated["reviewer_notes"][:600])
            return {"ok":True,"enquiry_id":e["id"],"status":"awaiting_review"}

    def approve(self, eid, version, subject, body, transport="preview"):
        with self.transaction():
            e = self.detail(eid)
            if e["version"] != version:
                raise Conflict("This enquiry changed. Refresh it before approving.")
            if e["status"] not in ("awaiting_review","followup_review"):
                raise Conflict("This draft is no longer awaiting approval.")
            kind = e["draft_kind"]
            if e["replied_at"] and kind == "followup":
                raise Conflict("The customer replied. This follow-up must not be sent.")
            oid = uuid.uuid4().hex
            self.db.execute("""INSERT INTO outbox
                (id,enquiry_id,kind,recipient,subject,body,transport,message_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (oid,eid,kind,e["email"],subject.strip(),body.strip(),transport,f"<{oid}@srauf-demo.local>",self.now()))
            self._task(eid,"send_"+kind)
            self.db.execute("UPDATE enquiries SET status='delivery_queued',version=version+1 WHERE id=?", (eid,))
            self.event(eid,"approved","Staff approved the exact subject and body now stored in the durable outbox.")
            return self.detail(eid)

    def reject(self, eid, version):
        with self.transaction():
            e = self.detail(eid)
            if e["version"] != version or e["status"] not in ("awaiting_review","followup_review"):
                raise Conflict("This draft changed or is no longer awaiting review.")
            self.db.execute("UPDATE enquiries SET status=?,version=version+1 WHERE id=?", ("sent" if e["draft_kind"]=="followup" else "rejected",eid))
            if e["draft_kind"] == "followup":
                self.db.execute("UPDATE enquiries SET followup_count=1 WHERE id=?", (eid,))
            self.event(eid,"rejected","Staff rejected the draft. Nothing was sent.")

    def prepare_delivery(self, tid, execution_id):
        with self.transaction():
            t = self.task(tid)
            if not t["kind"].startswith("send_"):
                raise Conflict("This job is not a delivery job.")
            kind = t["kind"][5:]
            e = self.detail(t["enquiry_id"])
            item = self.db.execute("SELECT * FROM outbox WHERE enquiry_id=? AND kind=?", (e["id"],kind)).fetchone()
            if not item:
                raise Conflict("No approved outbox item exists.")
            item = dict(item)
            if item["state"] in ("delivered","uncertain","cancelled"):
                self.db.execute("UPDATE tasks SET state=?,execution_id=? WHERE id=?", ("completed" if item["state"]=="delivered" else item["state"],execution_id,tid))
                return None
            if kind.startswith("quote_"):
                quote=self.quote(kind[6:])
                if quote["state"]=="superseded" or quote["expired"]:
                    self.db.execute("UPDATE outbox SET state='cancelled' WHERE id=?",(item["id"],))
                    self.db.execute("UPDATE tasks SET state='cancelled' WHERE id=?",(tid,))
                    self.db.execute("UPDATE quotes SET state=CASE WHEN state='superseded' THEN state ELSE 'expired' END,version=version+1 WHERE id=?",(quote["id"],))
                    self.db.execute("UPDATE enquiries SET status='quote_expired',next_action='Prepare quote',version=version+1 WHERE id=?",(e["id"],))
                    self.event(e["id"],"quote_not_sent","An expired or superseded quote was stopped before delivery.")
                    return None
            if kind=="followup" and e["replied_at"]:
                self.db.execute("UPDATE outbox SET state='cancelled' WHERE id=?", (item["id"],))
                self.db.execute("UPDATE tasks SET state='cancelled' WHERE id=?",(tid,))
                return None
            if item["state"] == "sending":
                # A process may have died after SMTP accepted the email. Never blindly repeat it.
                self.db.execute("UPDATE outbox SET state='uncertain',error='Interrupted after external send began; check the provider before retrying.' WHERE id=?",(item["id"],))
                self.db.execute("UPDATE tasks SET state='uncertain' WHERE id=?",(tid,))
                self.db.execute("UPDATE enquiries SET status='delivery_uncertain',version=version+1 WHERE id=?",(e["id"],))
                self.event(e["id"],"delivery_uncertain","An external send was interrupted. Automatic retry stopped to prevent a duplicate.")
                return None
            if self.setting("delivery_paused"):
                raise ConnectionError("Preview delivery is paused for the recovery scenario. Restore delivery, then retry.")
            self.db.execute("UPDATE tasks SET execution_id=? WHERE id=?",(execution_id,tid))
            if item["transport"] != "preview":
                self.db.execute("UPDATE outbox SET state='sending',error='' WHERE id=?",(item["id"],))
            return item

    def delivered(self, tid, oid):
        with self.transaction():
            task = self.task(tid)
            item = self.db.execute("SELECT * FROM outbox WHERE id=?",(oid,)).fetchone()
            if item["state"]=="delivered":
                return True
            enquiry=self.detail(item["enquiry_id"])
            if item["transport"]=="preview" and item["kind"]=="followup" and enquiry["replied_at"]:
                self.db.execute("UPDATE outbox SET state='cancelled' WHERE id=?",(oid,))
                self.db.execute("UPDATE tasks SET state='cancelled' WHERE id=?",(tid,))
                return False
            self.db.execute("UPDATE outbox SET state='delivered',delivered_at=?,error='' WHERE id=?",(self.now(),oid))
            self.db.execute("UPDATE tasks SET state='completed',error='',updated_at=? WHERE id=?",(self.now(),tid))
            if item["kind"].startswith("quote_"):
                self.quote_delivered(item)
            elif item["kind"]=="reply":
                self.db.execute("UPDATE enquiries SET status=CASE WHEN replied_at IS NULL THEN 'sent' ELSE 'customer_replied' END,sent_at=?,version=version+1 WHERE id=?",(self.now(),item["enquiry_id"]))
            else:
                self.db.execute("UPDATE enquiries SET status=CASE WHEN replied_at IS NULL THEN 'followup_sent' ELSE 'customer_replied' END,followup_count=1,version=version+1 WHERE id=?",(item["enquiry_id"],))
            where = "local preview inbox" if item["transport"]=="preview" else "configured SMTP server (accepted, not proof of inbox receipt)"
            self.event(task["enquiry_id"],"delivered",f"Approved {item['kind']} delivered to {where}. Message ID: {item['message_id']}")
            return True

    def failed(self, tid, error, *, uncertain=False):
        with self.transaction():
            t = self.task(tid)
            if t["state"] in ("completed","cancelled","uncertain","failed"):
                return
            state = "uncertain" if uncertain else "failed"
            self.db.execute("UPDATE tasks SET state=?,error=?,updated_at=? WHERE id=?",(state,error[:600],self.now(),tid))
            if t["kind"].startswith("send_"):
                self.db.execute("UPDATE outbox SET state=?,error=? WHERE enquiry_id=? AND kind=? AND state!='delivered'",(state,error[:600],t["enquiry_id"],t["kind"][5:]))
                status = "delivery_uncertain" if uncertain else "delivery_failed"
                if t["kind"].startswith("send_quote_"):
                    self.db.execute("UPDATE quotes SET state=?,version=version+1 WHERE id=?",("uncertain" if uncertain else "delivery_failed",t["kind"][11:]))
            elif t["kind"]=="crm_sync":
                self.db.execute("UPDATE crm_links SET state='failed',error=?,updated_at=? WHERE enquiry_id=?",(error[:600],self.now(),t["enquiry_id"]))
                self.event(t["enquiry_id"],"crm_failed",error[:600])
                return
            else:
                status = "automation_failed"
            self.db.execute("UPDATE enquiries SET status=?,version=version+1 WHERE id=?",(status,t["enquiry_id"]))
            self.event(t["enquiry_id"],"delivery_uncertain" if uncertain else "job_failed",error[:600])

    def retry(self, eid):
        with self.transaction():
            e = self.detail(eid)
            t = self.db.execute("SELECT * FROM tasks WHERE enquiry_id=? AND state='failed' ORDER BY rowid DESC LIMIT 1",(eid,)).fetchone()
            if not t:
                raise Conflict("There is no safely retryable job. Uncertain external sends require manual verification.")
            if t["kind"]=="send_followup" and e["replied_at"]:
                raise Conflict("The customer replied; no follow-up will be sent.")
            self.db.execute("UPDATE tasks SET state='queued',lease_until=NULL,error='' WHERE id=?",(t["id"],))
            if t["kind"].startswith("send_"):
                self.db.execute("UPDATE outbox SET state='queued',error='' WHERE enquiry_id=? AND kind=? AND state='failed'",(eid,t["kind"][5:]))
                if t["kind"].startswith("send_quote_"):
                    self.db.execute("UPDATE quotes SET state='sending',version=version+1 WHERE id=?",(t["kind"][11:],))
            if t["kind"]=="crm_sync":
                self.db.execute("UPDATE crm_links SET state='queued',error='' WHERE enquiry_id=?",(eid,))
            else:
                self.db.execute("UPDATE enquiries SET status=?,version=version+1 WHERE id=?",("delivery_queued" if t["kind"].startswith("send_") else "queued",eid))
            self.event(eid,"retry_requested","Staff retried the same durable job; the approved outbox item and message ID are reused.")

    def inbound(self, eid, message_id, body):
        with self.transaction():
            e = self.detail(eid)
            previous = self.db.execute("SELECT * FROM inbound WHERE message_id=?",(message_id,)).fetchone()
            if previous:
                if previous["enquiry_id"]!=eid or previous["body"]!=body:
                    raise Conflict("That inbound message ID was already used for different content.")
                return False
            self.db.execute("INSERT INTO inbound VALUES (?,?,?,?)",(message_id,eid,body,self.now()))
            self.db.execute("UPDATE enquiries SET replied_at=?,status='customer_replied',version=version+1 WHERE id=?",(self.now(),eid))
            self.db.execute("UPDATE tasks SET state='cancelled' WHERE enquiry_id=? AND kind IN ('followup','send_followup') AND state IN ('queued','processing','failed')",(eid,))
            self.db.execute("UPDATE outbox SET state='cancelled' WHERE enquiry_id=? AND kind='followup' AND state IN ('queued','failed')",(eid,))
            self.event(eid,"customer_replied",body[:500]+" · Pending follow-ups suppressed.")
            return True

    def due_followups(self):
        count=0
        with self.transaction():
            cutoff=(datetime.fromisoformat(self.now())-timedelta(days=3)).isoformat(timespec="seconds")
            rows=self.db.execute("SELECT * FROM enquiries WHERE status='sent' AND sent_at<=? AND replied_at IS NULL AND followup_count=0",(cutoff,)).fetchall()
            for e in rows:
                self._task(e["id"],"followup")
                self.db.execute("UPDATE enquiries SET status='followup_queued',version=version+1 WHERE id=?",(e["id"],))
                self.event(e["id"],"followup_due","Three demo days passed without a reply. Follow-up queued for drafting; approval is still required.")
                count+=1
        return count

    def control(self, *, delivery_paused=None, automation_paused=None, advance_hours=0):
        with self.transaction():
            if delivery_paused is not None:
                self._set("delivery_paused",delivery_paused)
                self.event(None,"scenario","Preview delivery paused." if delivery_paused else "Preview delivery restored.")
            if automation_paused is not None:
                self._set("automation_paused",automation_paused)
                self.event(None,"scenario","Automation paused; new enquiries remain saved." if automation_paused else "Automation resumed.")
            if advance_hours:
                self._set("clock_hours",self.setting("clock_hours")+advance_hours)
                self.event(None,"demo_clock",f"Demo clock advanced {advance_hours} hours. No real waiting time is being claimed.")

    def save_facts(self, version, changes):
        with self.transaction():
            f=self.setting("facts")
            if f["version"]!=version:
                raise Conflict("Someone changed the knowledge. Refresh before saving.")
            f.update(changes)
            f["version"]+=1
            self._set("facts",f)
            self.event(None,"knowledge_updated",f"Owner published knowledge version {f['version']}. New chat answers use this version immediately.")
            return f


def draft_text(e, kind):
    name=e["name"]
    if e["language"]=="sl":
        return (f"Pozdravljeni, {name}!\n\nHvala za povpraševanje. Za popravilo vašega kolesa potrebujemo pregled mehanika. "
                "Prosimo, pošljite fotografijo poškodbe ter znamko in model kolesa. Ceno in termin bomo potrdili po pregledu.\n\nLep pozdrav,\nEkipa Šrauf") if kind=="reply" else (
                f"Pozdravljeni, {name}!\n\nSe želite še dogovoriti za pregled kolesa? Če je zadeva že urejena, lahko sporočilo prezrete.\n\nLep pozdrav,\nEkipa Šrauf")
    if e["language"] in ("hr","sr"):
        return (f"Pozdrav, {name}!\n\nHvala na upitu. Za ovaj popravak potreban je pregled mehaničara. "
                "Pošaljite fotografiju oštećenja te marku i model bicikla. Cijenu i termin potvrdit ćemo nakon pregleda.\n\nLijep pozdrav,\nTim Šrauf") if kind=="reply" else (
                f"Pozdrav, {name}!\n\nŽelite li još dogovoriti pregled bicikla? Ako je stvar riješena, zanemarite ovu poruku.\n\nLijep pozdrav,\nTim Šrauf")
    return (f"Hi {name},\n\nThanks for your enquiry. A mechanic needs to inspect this repair before we can confirm a price or a slot. "
            "Could you send a photo of the damage, plus your bike's make and model? We'll review those details and get back to you.\n\nBest,\nThe Šrauf team") if kind=="reply" else (
            f"Hi {name},\n\nWould you still like us to arrange a bike inspection? If you've already sorted it out, there's nothing you need to do.\n\nBest,\nThe Šrauf team")
