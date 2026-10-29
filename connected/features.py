"""Workshop features layered onto the original durable store without replacing existing records."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import unicodedata
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


STAFF=("Unassigned","Nika","Luka","Maja")
ACTIONS=("Review request","Request photos","Prepare quote","Wait for customer","Schedule repair","Completed")

FEATURE_SCHEMA="""
CREATE TABLE IF NOT EXISTS photos (
 id TEXT PRIMARY KEY, enquiry_id TEXT NOT NULL, filename TEXT NOT NULL,
 media_type TEXT NOT NULL, data BLOB NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
 id TEXT PRIMARY KEY, enquiry_id TEXT NOT NULL, revision INTEGER NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL DEFAULT 'draft', items TEXT NOT NULL,
 total_cents INTEGER NOT NULL, turnaround TEXT NOT NULL, notes TEXT NOT NULL,
 valid_until TEXT NOT NULL, decision_token TEXT NOT NULL, created_at TEXT NOT NULL,
 sent_at TEXT, decided_at TEXT, customer_note TEXT NOT NULL DEFAULT '', UNIQUE(enquiry_id,revision)
);
CREATE TABLE IF NOT EXISTS quote_decisions (
 key TEXT PRIMARY KEY, quote_id TEXT NOT NULL, action TEXT NOT NULL, note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approved_answers (
 id TEXT PRIMARY KEY, question TEXT NOT NULL, normalized TEXT NOT NULL, language TEXT NOT NULL,
 answer TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
 UNIQUE(normalized,language)
);
CREATE TABLE IF NOT EXISTS knowledge_gaps (
 id TEXT PRIMARY KEY, question TEXT NOT NULL, normalized TEXT NOT NULL, language TEXT NOT NULL,
 occurrences INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL DEFAULT 'open',
 answer_id TEXT, last_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_alerts (
 id INTEGER PRIMARY KEY, enquiry_id TEXT NOT NULL, due_at TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, UNIQUE(enquiry_id,due_at)
);
CREATE TABLE IF NOT EXISTS integrations (
 name TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'not_connected',
 note TEXT NOT NULL DEFAULT '', last_checked TEXT
);
CREATE TABLE IF NOT EXISTS crm_links (
 enquiry_id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'not_synced', contact_id TEXT,
 updated_at TEXT, error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mail_imports (
 message_id TEXT PRIMARY KEY, enquiry_id TEXT, state TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


def normalize_question(question):
    return " ".join(unicodedata.normalize("NFKC",question).casefold().split()).rstrip(" ?.!؟")


def quote_items(items):
    if not 1<=len(items)<=12:
        raise ValueError("Add between one and twelve quote lines.")
    result=[]
    total=0
    for item in items:
        description=str(item.get("description","")).strip()
        if not 2<=len(description)<=180:
            raise ValueError("Every quote line needs a description of 2–180 characters.")
        try:
            quantity=Decimal(str(item.get("quantity","")))
            price=Decimal(str(item.get("unit_price","")))
        except InvalidOperation:
            raise ValueError("Enter valid quantities and unit prices.")
        if not quantity.is_finite() or not price.is_finite() or not 0<quantity<=1000 or not 0<=price<=10000:
            raise ValueError("Quantities must be positive and prices must be between €0 and €10,000.")
        if quantity.as_tuple().exponent < -3 or price.as_tuple().exponent < -2:
            raise ValueError("Use at most three decimal places for quantity and two for price.")
        amount=int((quantity*price*100).quantize(Decimal("1"),rounding=ROUND_HALF_UP))
        total+=amount
        result.append({"description":description,"quantity":format(quantity,"f"),"unit_price":format(price,".2f"),"amount_cents":amount})
    if not 0<total<=10000000:
        raise ValueError("The demo quote total must be greater than zero and at most €100,000.")
    return result,total


class FeatureStore:
    def initialize_features(self):
        self.db.executescript(FEATURE_SCHEMA)
        columns={r["name"] for r in self.db.execute("PRAGMA table_info(enquiries)")}
        for name,definition in {"assignee":"TEXT NOT NULL DEFAULT 'Unassigned'","next_action":"TEXT NOT NULL DEFAULT 'Review request'","due_at":"TEXT"}.items():
            if name not in columns:
                self.db.execute(f"ALTER TABLE enquiries ADD COLUMN {name} {definition}")
        for name in ("gmail","hubspot"):
            self.db.execute("INSERT OR IGNORE INTO integrations(name) VALUES (?)",(name,))
        self.db.commit()

    def enrich_detail(self,result):
        eid=result["id"]
        result["photos"]=[dict(r) for r in self.db.execute("SELECT id,filename,media_type,width,height,created_at FROM photos WHERE enquiry_id=? ORDER BY rowid",(eid,))]
        result["quotes"]=[self.quote(r["id"]) for r in self.db.execute("SELECT id FROM quotes WHERE enquiry_id=? ORDER BY revision DESC",(eid,))]
        row=self.db.execute("SELECT * FROM crm_links WHERE enquiry_id=?",(eid,)).fetchone()
        result["crm"]=dict(row) if row else {"state":"not_synced","error":""}
        result["overdue"]=bool(result.get("due_at") and result["due_at"]<self.now() and result["next_action"]!="Completed")
        return result

    def feature_snapshot(self):
        with self.lock:
            return {"staff":STAFF,"next_actions":ACTIONS,
                    "knowledge_gaps":[dict(r) for r in self.db.execute("SELECT * FROM knowledge_gaps ORDER BY (state='open') DESC,occurrences DESC,last_at DESC LIMIT 150")],
                    "approved_answers":[dict(r) for r in self.db.execute("SELECT * FROM approved_answers ORDER BY created_at DESC")],
                    "alerts":[dict(r) for r in self.db.execute("SELECT a.*,e.name,e.assignee,e.next_action FROM work_alerts a JOIN enquiries e ON e.id=a.enquiry_id WHERE a.state='open' ORDER BY a.due_at")],
                    "integration_status":[dict(r) for r in self.db.execute("SELECT * FROM integrations ORDER BY name")]}

    def add_photos(self,eid,photos):
        for photo in photos:
            self.db.execute("INSERT INTO photos VALUES (?,?,?,?,?,?,?,?)",(uuid.uuid4().hex,eid,photo["filename"],photo["media_type"],photo["data"],photo["width"],photo["height"],self.now()))
        if photos:
            self.event(eid,"photos_received",f"{len(photos)} image attachment(s) saved for staff. Images are not sent to AI.")

    def photo(self,pid):
        with self.lock:
            row=self.db.execute("SELECT * FROM photos WHERE id=?",(pid,)).fetchone()
            if not row:raise KeyError(pid)
            return dict(row)

    def set_work(self,eid,version,assignee,next_action,due_hours=None):
        from .store import Conflict
        if assignee not in STAFF or next_action not in ACTIONS:
            raise ValueError("Choose a workshop team member and a listed next action.")
        with self.transaction():
            e=self.detail(eid)
            if e["version"]!=version:raise Conflict("This enquiry changed. Refresh before assigning it.")
            if due_hours is not None and not 1<=due_hours<=720:raise ValueError("Choose a due time between 1 and 720 hours.")
            due=None if next_action=="Completed" else (datetime.fromisoformat(self.now())+timedelta(hours=due_hours)).isoformat(timespec="seconds") if due_hours else e["due_at"]
            self.db.execute("UPDATE enquiries SET assignee=?,next_action=?,due_at=?,version=version+1 WHERE id=?",(assignee,next_action,due,eid))
            self.db.execute("UPDATE work_alerts SET state='resolved' WHERE enquiry_id=? AND (due_at!=? OR ?='Completed')",(eid,due or "",next_action))
            self.event(eid,"assigned",f"Assigned to {assignee}. Next action: {next_action}."+(f" Due {due}." if due else ""))
            return self.detail(eid)

    def refresh_overdue(self):
        with self.transaction():
            rows=self.db.execute("SELECT * FROM enquiries WHERE due_at<? AND next_action!='Completed'",(self.now(),)).fetchall()
            count=0
            for e in rows:
                result=self.db.execute("INSERT OR IGNORE INTO work_alerts(enquiry_id,due_at,created_at) VALUES (?,?,?)",(e["id"],e["due_at"],self.now()))
                if result.rowcount:
                    self.event(e["id"],"overdue",f"Overdue: {e['next_action']} · {e['assignee']}.")
                    count+=1
            return count

    def acknowledge_alert(self,aid):
        with self.transaction():
            row=self.db.execute("SELECT * FROM work_alerts WHERE id=?",(aid,)).fetchone()
            if not row:raise KeyError(aid)
            self.db.execute("UPDATE work_alerts SET state='acknowledged' WHERE id=?",(aid,))
            self.event(row["enquiry_id"],"alert_acknowledged","Staff acknowledged the overdue alert. The due date and next action are unchanged.")

    def record_gap(self,question,language):
        normalized=normalize_question(question)
        gid=hashlib.sha256((language+"|"+normalized).encode()).hexdigest()[:32]
        with self.transaction():
            self.db.execute("""INSERT INTO knowledge_gaps(id,question,normalized,language,last_at) VALUES (?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET occurrences=occurrences+1,last_at=excluded.last_at""",(gid,question,normalized,language,self.now()))
        return gid

    def answer_for(self,question,language):
        with self.lock:
            row=self.db.execute("SELECT * FROM approved_answers WHERE normalized=? AND language=?",(normalize_question(question),language)).fetchone()
            return dict(row) if row else None

    def publish_answer(self,gid,answer,expected_version=None):
        from .store import Conflict
        if not 5<=len(answer.strip())<=2000:raise ValueError("Write an approved answer of 5–2,000 characters.")
        with self.transaction():
            gap=self.db.execute("SELECT * FROM knowledge_gaps WHERE id=?",(gid,)).fetchone()
            if not gap:raise KeyError(gid)
            old=self.db.execute("SELECT * FROM approved_answers WHERE normalized=? AND language=?",(gap["normalized"],gap["language"])).fetchone()
            if old and expected_version!=old["version"]:raise Conflict("That approved answer changed. Refresh it before updating.")
            aid=old["id"] if old else uuid.uuid4().hex
            self.db.execute("""INSERT INTO approved_answers(id,question,normalized,language,answer,created_at) VALUES (?,?,?,?,?,?)
                ON CONFLICT(normalized,language) DO UPDATE SET answer=excluded.answer,version=approved_answers.version+1""",(aid,gap["question"],gap["normalized"],gap["language"],answer.strip(),self.now()))
            self.db.execute("UPDATE knowledge_gaps SET state='resolved',answer_id=? WHERE id=?",(aid,gid))
            facts=self.setting("facts");facts["version"]+=1;self._set("facts",facts)
            self.event(None,"answer_approved",f"Owner approved an answer to: {gap['question'][:160]}. Knowledge version {facts['version']}.")
            return dict(self.db.execute("SELECT * FROM approved_answers WHERE id=?",(aid,)).fetchone())

    def quote(self,qid):
        with self.lock:
            row=self.db.execute("SELECT * FROM quotes WHERE id=?",(qid,)).fetchone()
            if not row:raise KeyError(qid)
            result=dict(row);result["items"]=json.loads(result["items"])
            result["expired"]=result["valid_until"]<self.now() and result["state"] not in ("accepted","superseded")
            return result

    def save_quote(self,eid,enquiry_version,items,turnaround,notes,valid_days=7):
        from .store import Conflict
        lines,total=quote_items(items)
        if not 2<=len(turnaround.strip())<=200 or len(notes)>1000:raise ValueError("Add a turnaround estimate and keep notes under 1,000 characters.")
        if not 1<=valid_days<=30:raise ValueError("Quote validity must be 1–30 days.")
        with self.transaction():
            e=self.detail(eid)
            if e["version"]!=enquiry_version:raise Conflict("The enquiry changed. Refresh before saving the quote.")
            if e["status"] in ("queued","followup_queued","delivery_queued","delivery_uncertain"):
                raise Conflict("Finish or resolve the running handover or delivery before replacing it with a quote.")
            old=self.db.execute("SELECT * FROM quotes WHERE enquiry_id=? ORDER BY revision DESC LIMIT 1",(eid,)).fetchone()
            if old and old["state"] in ("accepted","sending","uncertain"):
                raise Conflict("An accepted quote or an unresolved delivery cannot be replaced.")
            revision=old["revision"]+1 if old else 1
            if old:
                self.db.execute("UPDATE quotes SET state='superseded',version=version+1 WHERE id=?",(old["id"],))
                self.db.execute("UPDATE outbox SET state='cancelled' WHERE enquiry_id=? AND kind=? AND state IN ('queued','failed')",(eid,"quote_"+old["id"]))
                self.db.execute("UPDATE tasks SET state='cancelled' WHERE enquiry_id=? AND kind=? AND state IN ('queued','failed')",(eid,"send_quote_"+old["id"]))
            qid=uuid.uuid4().hex
            valid=(datetime.fromisoformat(self.now())+timedelta(days=valid_days)).isoformat(timespec="seconds")
            self.db.execute("INSERT INTO quotes(id,enquiry_id,revision,items,total_cents,turnaround,notes,valid_until,decision_token,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (qid,eid,revision,json.dumps(lines,ensure_ascii=False),total,turnaround.strip(),notes.strip(),valid,secrets.token_urlsafe(24),self.now()))
            self.db.execute("UPDATE enquiries SET status='quote_draft',next_action='Prepare quote',version=version+1 WHERE id=?",(eid,))
            self.db.execute("UPDATE tasks SET state='cancelled' WHERE enquiry_id=? AND kind='intake' AND state IN ('queued','processing','failed')",(eid,))
            self._cancel_followups(eid)
            self.event(eid,"quote_draft",f"Staff prepared quote revision {revision}: €{total/100:.2f}. Prices were entered by a person, not AI.")
            return self.quote(qid)

    def approve_quote(self,qid,version,transport="preview"):
        from .store import Conflict
        with self.transaction():
            q=self.quote(qid);e=self.detail(q["enquiry_id"])
            if q["version"]!=version or q["state"]!="draft":raise Conflict("This quote changed or was already approved.")
            if q["expired"]:raise Conflict("This quote expired. Prepare a new revision before sending.")
            oid=uuid.uuid4().hex
            body=render_quote(q,e["name"])
            self.db.execute("INSERT INTO outbox(id,enquiry_id,kind,recipient,subject,body,transport,message_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (oid,e["id"],"quote_"+qid,e["email"],f"Šrauf demo quote · revision {q['revision']}",body,transport,f"<{oid}@srauf-demo.local>",self.now()))
            self._task(e["id"],"send_quote_"+qid)
            self.db.execute("UPDATE quotes SET state='sending',version=version+1 WHERE id=?",(qid,))
            self.db.execute("UPDATE enquiries SET status='delivery_queued',next_action='Wait for customer',version=version+1 WHERE id=?",(e["id"],))
            self.event(e["id"],"quote_approved",f"Staff approved quote revision {q['revision']}. The exact reviewed quote is queued for delivery.")
            return self.quote(qid)

    def quote_delivered(self,item):
        qid=item["kind"][6:]
        self.db.execute("UPDATE quotes SET state='sent',sent_at=?,version=version+1 WHERE id=? AND state NOT IN ('accepted','superseded')",(self.now(),qid))
        self.db.execute("UPDATE enquiries SET status='quote_sent',next_action='Wait for customer',version=version+1 WHERE id=?",(item["enquiry_id"],))

    def decide_quote(self,qid,token,key,decision,note=""):
        from .store import Conflict
        if decision not in ("accept","question"):raise ValueError("Choose accept or question.")
        if decision=="question" and not 2<=len(note.strip())<=2000:raise ValueError("Write your question first.")
        with self.transaction():
            q=self.quote(qid)
            if not hmac.compare_digest(q["decision_token"],token):raise PermissionError("Invalid quote link.")
            old=self.db.execute("SELECT * FROM quote_decisions WHERE key=?",(key,)).fetchone()
            if old:
                if (old["quote_id"],old["action"],old["note"])!=(qid,decision,note.strip()):raise Conflict("This decision key was already used for a different response.")
                return q
            if q["state"] not in ("sent","questions"):raise Conflict("Only the current delivered quote can receive a decision.")
            if q["expired"]:raise Conflict("This quote expired. Ask the workshop for an updated quote.")
            if decision=="accept" and self.db.execute("SELECT 1 FROM quotes WHERE enquiry_id=? AND revision>?",(q["enquiry_id"],q["revision"])).fetchone():
                raise Conflict("A newer quote exists. Review that version instead.")
            self.db.execute("INSERT INTO quote_decisions VALUES (?,?,?,?)",(key,qid,decision,note.strip()))
            state="accepted" if decision=="accept" else "questions"
            self.db.execute("UPDATE quotes SET state=?,decided_at=?,customer_note=?,version=version+1 WHERE id=?",(state,self.now(),note.strip(),qid))
            self.db.execute("UPDATE enquiries SET status=?,next_action=?,replied_at=?,version=version+1 WHERE id=?",
                ("quote_accepted" if decision=="accept" else "quote_question","Schedule repair" if decision=="accept" else "Review request",self.now(),q["enquiry_id"]))
            self._cancel_followups(q["enquiry_id"])
            self.event(q["enquiry_id"],"quote_accepted" if decision=="accept" else "quote_question",
                f"Customer {'accepted the demo quote' if decision=='accept' else 'asked about the quote'} · revision {q['revision']}. "+note.strip())
            return self.quote(qid)

    def _cancel_followups(self,eid):
        self.db.execute("UPDATE tasks SET state='cancelled' WHERE enquiry_id=? AND kind IN ('followup','send_followup') AND state IN ('queued','processing','failed')",(eid,))
        self.db.execute("UPDATE outbox SET state='cancelled' WHERE enquiry_id=? AND kind='followup' AND state IN ('queued','failed')",(eid,))

    def integration_result(self,name,state,note):
        with self.transaction():
            self.db.execute("UPDATE integrations SET state=?,note=?,last_checked=? WHERE name=?",(state,note[:500],self.now(),name))

    def queue_crm(self,eid):
        with self.transaction():
            self.detail(eid)
            running=self.db.execute("SELECT * FROM tasks WHERE enquiry_id=? AND kind='crm_sync' AND state IN ('queued','processing')",(eid,)).fetchone()
            if running:return running["id"]
            self.db.execute("INSERT INTO crm_links(enquiry_id,state) VALUES (?,'queued') ON CONFLICT(enquiry_id) DO UPDATE SET state='queued',error=''",(eid,))
            tid=self._task(eid,"crm_sync")
            self.event(eid,"crm_queued","Contact upsert queued for HubSpot via n8n.")
            return tid

    def crm_complete(self,tid,contact_id,execution_id):
        with self.transaction():
            task=self.task(tid)
            if task["state"]=="completed":return
            self.db.execute("UPDATE crm_links SET state='synced',contact_id=?,updated_at=?,error='' WHERE enquiry_id=?",(str(contact_id),self.now(),task["enquiry_id"]))
            self.db.execute("UPDATE tasks SET state='completed',execution_id=?,error='',updated_at=? WHERE id=?",(execution_id,self.now(),tid))
            self.event(task["enquiry_id"],"crm_synced",f"HubSpot confirmed contact {contact_id}; n8n execution {execution_id}.")


def render_quote(quote,name):
    lines=[f"Hi {name},","","This is a fictional workshop demonstration, not an invoice, contract, payment request or confirmed booking.","",
           f"Demo quote · revision {quote['revision']}"]
    lines += [f"{x['description']}: {x['quantity']} × €{x['unit_price']} = €{x['amount_cents']/100:.2f}" for x in quote["items"]]
    lines += [f"Total: €{quote['total_cents']/100:.2f}","Amounts are the final amounts entered by staff; no tax calculation is inferred.",
              f"Estimated turnaround: {quote['turnaround']}",f"Valid until: {quote['valid_until']}",quote["notes"],"",
              "Review this demo quote in the local customer view. Acceptance does not take payment or reserve an appointment.","","The Šrauf team"]
    return "\n".join(lines)
