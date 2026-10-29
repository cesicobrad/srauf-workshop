"""Approved facts, revision-aware answers and an explicit, limited no-key mode."""
from __future__ import annotations

import re
import threading
import uuid

from chatbot.brain import GREETING, HANDOFF, Reply
from chatbot.config import Settings
from chatbot.guard import looks_like_injection
from chatbot.i18n import t
from chatbot.language import detect
from chatbot.service import ChatService

PRICE = re.compile(r"full|complete|celovit|celoten|celotni|veliki|komplet|polni|potpun|cijena|cena|price|cost|stane|košta|kosta",re.I)
BASIC = re.compile(r"\bbasic\s+(?:bike\s+)?service\b|\bosnovn\w*\s+servis\b",re.I)
FULL = re.compile(r"(?:full|complete)\s+(?:bike\s+)?service|(?:celovit\w*|celot\w*|velik\w*|komplet\w*|poln\w*|potpun\w*)\s+servis",re.I)
HOURS = re.compile(r"\b(?:hours?|open|sundays?|saturdays?|čas\w*|odpr\w*|nedelj\w*|sobot\w*|radno|otvoren\w*)\b",re.I)
CUSTOM = re.compile(r"cargo|custom|crack|carbon|frame|tovorn|počen|karbon|ogrod|predel|napuk|okvir",re.I)


class Knowledge:
    def __init__(self, store, db_path, live=False):
        self.store=store
        self.lock=threading.RLock()
        self.service=ChatService(Settings(mode="claude" if live else "demo",db_path=db_path,embeddings="off",seed_demo=False))
        self.service.db.upsert_bot(id="srauf",name="Špica",business="Šrauf Bike Repair",color="#186354")
        self.version=0
        self.sync()

    def sync(self):
        f=self.store.setting("facts")
        if self.version==f["version"]:
            return
        text=(f"Full service / celovit servis / kompletni servis: {f['full_service']} EUR, VAT included. "
              f"Basic service / osnovni servis: {f['basic_service']} EUR, VAT included.\n"
              "Parts are quoted separately. A mechanic must confirm custom repairs and booking times.")
        # Store directly: the approved owner record is authoritative, not an arbitrary uploaded page.
        self.service.db.add_document("srauf","Owner-approved price list", "current-prices.md","md",[("Service prices",text,None)])
        self.service.db.add_document("srauf","Workshop hours and repair policy","workshop-policy.md","md",[
            ("Opening hours",f"{f['hours']}\n{f['hours_sl']}\n{f['hours_hr']}",None),
            ("Custom repairs",f["notes"],None)])
        for answer in self.store.feature_snapshot()["approved_answers"]:
            self.service.db.add_document("srauf","Owner-approved answer: "+answer["question"],"approved-answer-"+answer["id"],"md",
                                         [(answer["question"],answer["answer"],None)])
        self.service.search.invalidate("srauf")
        self.version=f["version"]

    def ask(self, question, conversation_id="", lang_hint="en"):
        with self.lock:
            self.sync()
            cid=conversation_id or uuid.uuid4().hex
            f=self.store.setting("facts")
            lang=detect(question,lang_hint)
            bot=self.service.db.bot("srauf")
            source={"title":"Owner-approved price list","section":"Service prices","url":"","version":f["version"]}
            if looks_like_injection(question):
                reply=Reply(t("out_of_scope",lang,business=bot.business),"out_of_scope",lang,engine="approved-facts")
            elif (PRICE.search(question) or BASIC.search(question)) and f["conflict"]:
                lines={"en":"The current price list and an unreviewed leaflet disagree. I won't quote a price until the workshop checks it. Please leave your question for the team.",
                       "sl":"Veljavni cenik in nepregledan letak se razlikujeta. Dokler ekipa ne preveri podatkov, cene ne bom navedla. Pustite vprašanje ekipi.",
                       "hr":"Važeći cjenik i nepregledani letak se razlikuju. Neću navesti cijenu dok tim ne provjeri podatke. Ostavite upit timu."}
                reply=Reply(lines.get(lang,lines["hr"]),"unknown",lang,engine="approved-facts")
            elif CUSTOM.search(question) or HANDOFF.search(question):
                reply=Reply(t("handoff",lang,business=bot.business),"handoff",lang,engine="approved-facts")
            elif self.store.answer_for(question,lang) and not (FULL.search(question) or BASIC.search(question) or HOURS.search(question)):
                answer=self.store.answer_for(question,lang)
                reply=Reply(answer["answer"],"answered",lang,[{"title":"Owner-approved answer","section":answer["question"],"url":"","version":f["version"]}],engine="approved-facts")
            elif self.service.claude:
                result=self.service.ask(bot,cid,question,lang)
                reply=result.reply
                if reply.status=="unknown":self.store.record_gap(question,lang)
                return self._result(cid,reply,f["version"])
            elif GREETING.match(question):
                reply=Reply(t("greeting_reply",lang,business=bot.business),"chat",lang,engine="approved-facts")
            elif FULL.search(question) or BASIC.search(question):
                basic=bool(BASIC.search(question))
                amount=f["basic_service"] if basic else f["full_service"]
                en=f"The {'basic' if basic else 'full'} service is €{amount:g}, including VAT. Parts are quoted separately. A mechanic confirms the booking."
                sl=f"{'Osnovni' if basic else 'Celovit'} servis stane {amount:g} €, z DDV. Rezervni deli se obračunajo posebej. Termin potrdi mehanik."
                hr=f"{'Osnovni' if basic else 'Kompletni'} servis košta {amount:g} €, s PDV-om. Dijelovi se naplaćuju posebno. Termin potvrđuje mehaničar."
                reply=Reply({"en":en,"sl":sl,"hr":hr,"sr":hr}.get(lang,en),"answered",lang,[source],engine="approved-facts")
            elif HOURS.search(question):
                hours=f["hours_sl"] if lang=="sl" else f["hours_hr"] if lang in ("hr","sr") else f["hours"]
                reply=Reply(hours,"answered",lang,[{"title":"Owner-approved opening hours","section":"Opening hours","url":"","version":f["version"]}],engine="approved-facts")
            else:
                reply=Reply(t("unknown",lang,business=bot.business),"unknown",lang,engine="approved-facts")
            self.service.db.touch_conversation(cid,"srauf")
            self.service.db.add_message(cid,"srauf","user",question,language=lang)
            self.service.db.add_message(cid,"srauf","bot",reply.text,status=reply.status,language=lang,sources=reply.sources,engine=reply.engine)
            if reply.status=="unknown" and not (f["conflict"] and PRICE.search(question)):
                self.store.record_gap(question,lang)
            return self._result(cid,reply,f["version"])

    @staticmethod
    def _result(cid,reply,version):
        return dict(conversation_id=cid,reply=reply.text,status=reply.status,language=reply.language,
                    sources=reply.sources,offer_handoff=reply.offer_handoff,engine=reply.engine,knowledge_version=version)

    def transcript(self,cid):
        if self.service.db.conversation_bot(cid)!="srauf":
            raise ValueError("Start a conversation before sending an enquiry.")
        return [{"role":m["role"],"text":m["text"]} for m in self.service.db.messages(cid)]
