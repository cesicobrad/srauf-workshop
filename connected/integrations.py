"""Opt-in Gmail and HubSpot adapters. No provider call is made in default preview mode."""
from __future__ import annotations

import base64
import email
import hashlib
import os
import re
import time
from email.message import EmailMessage
from email.policy import default
from email.utils import parseaddr

import httpx

from .delivery import UncertainDelivery


class IntegrationError(RuntimeError):pass


def allowlisted(address,key):
    return address.strip().lower() in {s.strip().lower() for s in os.getenv(key,"").split(",") if s.strip()}


class Gmail:
    BASE="https://gmail.googleapis.com/gmail/v1/users/me"

    def __init__(self,client=None):
        self.client=client or httpx.Client(timeout=20,trust_env=False)
        self.access=os.getenv("GMAIL_ACCESS_TOKEN","")
        self.expires=0 if os.getenv("GMAIL_REFRESH_TOKEN") else float("inf")

    def headers(self):
        if self.expires<time.monotonic()+30:
            refresh=os.getenv("GMAIL_REFRESH_TOKEN","")
            client_id=os.getenv("GMAIL_CLIENT_ID","");secret=os.getenv("GMAIL_CLIENT_SECRET","")
            if not all((refresh,client_id,secret)):raise IntegrationError("Gmail OAuth client and refresh token are not configured.")
            try:
                r=self.client.post("https://oauth2.googleapis.com/token",data={"client_id":client_id,"client_secret":secret,"refresh_token":refresh,"grant_type":"refresh_token"})
            except httpx.HTTPError:raise IntegrationError("Gmail token refresh could not reach Google.")
            if not r.is_success:raise IntegrationError(f"Gmail token refresh returned HTTP {r.status_code}. Reauthorize the test account.")
            data=r.json();self.access=data.get("access_token","");self.expires=time.monotonic()+int(data.get("expires_in",3600))
        if not self.access:raise IntegrationError("Gmail OAuth is not configured.")
        return {"Authorization":"Bearer "+self.access}

    def profile(self):
        r=self.client.get(self.BASE+"/profile",headers=self.headers())
        if not r.is_success:raise IntegrationError(f"Gmail profile check returned HTTP {r.status_code}.")
        return r.json()

    def send(self,item):
        if not allowlisted(item["recipient"],"GMAIL_TO_ALLOWLIST"):
            raise ValueError("Recipient is not in GMAIL_TO_ALLOWLIST. Use a test inbox you control.")
        sender=os.getenv("GMAIL_FROM","")
        if not sender:raise ValueError("GMAIL_FROM is not configured.")
        message=EmailMessage();message['From']=sender;message['To']=item['recipient'];message['Subject']=item['subject'];message['Message-ID']=item['message_id'];message.set_content(item['body'])
        payload={"raw":base64.urlsafe_b64encode(message.as_bytes()).decode()}
        headers=self.headers()  # Fail before message submission if authorization cannot refresh.
        try:r=self.client.post(self.BASE+"/messages/send",headers=headers,json=payload)
        except httpx.HTTPError as error:raise UncertainDelivery("Gmail submission ended without a confirmed result. Check Sent using the Message-ID before retrying.") from error
        if r.status_code>=500:raise UncertainDelivery("Gmail returned a server error during submission. Check Sent before retrying.")
        if not r.is_success:raise ValueError(f"Gmail rejected the message with HTTP {r.status_code}; no successful submission was confirmed.")
        if not r.json().get("id"):raise UncertainDelivery("Gmail did not return a message ID. Check Sent before retrying.")
        return r.json()["id"]

    def messages(self):
        label=os.getenv("GMAIL_LABEL","WorkshopDemo")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}",label):raise IntegrationError("Use a simple dedicated Gmail label, such as WorkshopDemo.")
        headers=self.headers()
        # Dedicated label only. Do not scan the user's whole mailbox or mark messages read.
        r=self.client.get(self.BASE+"/messages",headers=headers,params={"q":f"label:{label} newer_than:7d -in:sent","maxResults":20})
        if not r.is_success:raise IntegrationError(f"Gmail message listing returned HTTP {r.status_code}.")
        result=[]
        for item in r.json().get("messages",[]):
            full=self.client.get(self.BASE+"/messages/"+item["id"],headers=headers,params={"format":"raw"})
            if not full.is_success:raise IntegrationError(f"Gmail message retrieval returned HTTP {full.status_code}.")
            raw=full.json().get("raw","")
            if len(raw)>12*1024*1024:continue
            try:blob=base64.urlsafe_b64decode(raw+"="*(-len(raw)%4))
            except ValueError:continue
            result.append((item["id"],email.message_from_bytes(blob,policy=default)))
        return result


def import_gmail(store,knowledge,gmail=None):
    gmail=gmail or Gmail()
    imported=0
    for mid,message in gmail.messages():
        with store.lock:
            if store.db.execute("SELECT 1 FROM mail_imports WHERE message_id=?",(mid,)).fetchone():continue
        name,sender=parseaddr(str(message.get("From","")))
        if not allowlisted(sender,"GMAIL_INBOUND_ALLOWLIST"):
            continue
        if message.get_content_type()=='multipart/report' or message.get('Auto-Submitted','no').lower()!='no':continue
        bodypart=message.get_body(preferencelist=('plain',)) if message.is_multipart() else message
        if bodypart is None or bodypart.get_content_type()!='text/plain':continue
        text=str(bodypart.get_content()).strip()[:2000]
        if len(text)<2:continue
        references=' '.join(str(message.get(k,"")) for k in ('In-Reply-To','References'))
        ids=re.findall(r"<[A-Za-z0-9@._-]{3,200}>",references)
        target=None
        with store.lock:
            for reference in reversed(ids):
                row=store.db.execute("SELECT e.id,e.email FROM enquiries e JOIN outbox o ON o.enquiry_id=e.id WHERE o.message_id=? AND o.state='delivered'",(reference,)).fetchone()
                if row and row['email'].lower()==sender.lower():target=row['id'];break
        if ids and not target:
            # An unverified reference must not be allowed to modify someone else's enquiry.
            continue
        if target:
            store.inbound(target,'gmail-'+mid,text)
            eid=target
        else:
            from chatbot.language import detect
            cid=hashlib.sha256(('gmail-'+mid).encode()).hexdigest()[:32]
            knowledge.service.db.touch_conversation(cid,'srauf')
            knowledge.service.db.add_message(cid,'srauf','user',text,language=detect(text,'en'))
            eid=store.create_enquiry(key='gmail-'+mid,conversation_id=cid,name=(name or sender)[:100],email=sender,
                question=text if len(text)>=5 else str(message.get('Subject','Enquiry'))[:200],language=detect(text,'en'),transcript=[{'role':'user','text':text}])[0]['id']
            if os.getenv('WORKSHOP_LIVE_CRM')=='1':store.queue_crm(eid)
        with store.transaction():
            store.db.execute("INSERT OR IGNORE INTO mail_imports VALUES (?,?,?,?)",(mid,eid,'imported',store.now()))
        imported+=1
    store.integration_result('gmail','connected',f"Dedicated-label inbox check succeeded. {imported} new allowlisted message(s) imported.")
    return imported


def sync_hubspot(enquiry,client=None):
    if not allowlisted(enquiry['email'],'HUBSPOT_EMAIL_ALLOWLIST'):
        raise IntegrationError("Contact email is not in HUBSPOT_EMAIL_ALLOWLIST. Allow only test contacts you control.")
    token=os.getenv('HUBSPOT_ACCESS_TOKEN','')
    if not token:raise IntegrationError("HubSpot runtime authorization is not configured.")
    name=enquiry['name'].split(maxsplit=1)
    properties={"email":enquiry['email'],"firstname":name[0],"lastname":name[1] if len(name)>1 else ''}
    payload={"inputs":[{"idProperty":"email","id":enquiry['email'],"properties":properties}]}
    client=client or httpx.Client(timeout=20,trust_env=False)
    try:r=client.post('https://api.hubapi.com/crm/v3/objects/contacts/batch/upsert',headers={'Authorization':'Bearer '+token},json=payload)
    except httpx.HTTPError:raise IntegrationError("HubSpot did not confirm the upsert. Retrying uses the same unique contact email.")
    if not r.is_success:raise IntegrationError(f"HubSpot returned HTTP {r.status_code}. Check the account's contact permissions.")
    data=r.json()
    if data.get('status')!='COMPLETE' or not data.get('results') or data.get('numErrors'):
        raise IntegrationError("HubSpot did not confirm one completed contact upsert.")
    result=data['results'][0]
    if not result.get('id'):raise IntegrationError("HubSpot returned no contact ID.")
    return str(result['id'])
