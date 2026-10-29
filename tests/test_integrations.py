import base64
import json
import os
import unittest
from email.message import EmailMessage
from unittest.mock import patch

import httpx

from connected.delivery import UncertainDelivery
from connected.integrations import Gmail, IntegrationError, import_gmail, sync_hubspot
from connected.knowledge import Knowledge
from connected.store import Store


class ProviderContractTests(unittest.TestCase):
    def setUp(self):
        self.env={'GMAIL_ACCESS_TOKEN':'test-token','GMAIL_FROM':'owner@test.example','GMAIL_TO_ALLOWLIST':'customer@test.example',
                  'GMAIL_INBOUND_ALLOWLIST':'customer@test.example','HUBSPOT_ACCESS_TOKEN':'test-hubspot','HUBSPOT_EMAIL_ALLOWLIST':'customer@test.example'}
        self.item={'recipient':'customer@test.example','subject':'Reviewed subject','body':'Exact approved body.','message_id':'<stable-id@srauf-demo.local>'}

    def client(self,handler):return httpx.Client(transport=httpx.MockTransport(handler))

    def test_gmail_sends_the_exact_message_with_stable_id(self):
        calls=[]
        def handler(request):
            calls.append(request)
            raw=json.loads(request.content)['raw'];data=base64.urlsafe_b64decode(raw)
            self.assertIn(b'Message-ID: <stable-id@srauf-demo.local>',data)
            self.assertIn(b'Exact approved body.',data)
            return httpx.Response(200,json={'id':'gmail-123'})
        with patch.dict(os.environ,self.env,clear=True):self.assertEqual(Gmail(self.client(handler)).send(self.item),'gmail-123')
        self.assertEqual(len(calls),1)

    def test_gmail_blocks_a_recipient_outside_the_test_allowlist(self):
        def handler(request):self.fail('No provider call should occur')
        with patch.dict(os.environ,self.env,clear=True),self.assertRaises(ValueError):
            Gmail(self.client(handler)).send({**self.item,'recipient':'outside@test.example'})

    def test_gmail_server_error_during_send_is_uncertain(self):
        with patch.dict(os.environ,self.env,clear=True),self.assertRaises(UncertainDelivery):
            Gmail(self.client(lambda r:httpx.Response(503))).send(self.item)

    def test_gmail_network_timeout_during_send_is_uncertain(self):
        def handler(request):raise httpx.ReadTimeout('test timeout',request=request)
        with patch.dict(os.environ,self.env,clear=True),self.assertRaises(UncertainDelivery):Gmail(self.client(handler)).send(self.item)

    def test_gmail_explicit_rejection_is_safely_retryable(self):
        with patch.dict(os.environ,self.env,clear=True),self.assertRaises(ValueError):
            Gmail(self.client(lambda r:httpx.Response(403))).send(self.item)

    def test_gmail_token_refresh_failure_does_not_submit_email(self):
        calls=[]
        def handler(request):calls.append(str(request.url));return httpx.Response(400,json={'error':'invalid_grant'})
        env={**self.env,'GMAIL_REFRESH_TOKEN':'refresh','GMAIL_CLIENT_ID':'client','GMAIL_CLIENT_SECRET':'secret'}
        with patch.dict(os.environ,env,clear=True),self.assertRaises(IntegrationError):Gmail(self.client(handler)).send(self.item)
        self.assertEqual(calls,['https://oauth2.googleapis.com/token'])

    def test_gmail_reads_only_the_dedicated_label(self):
        calls=[]
        def handler(request):calls.append(request);return httpx.Response(200,json={})
        with patch.dict(os.environ,self.env,clear=True):self.assertEqual(Gmail(self.client(handler)).messages(),[])
        self.assertIn('label:WorkshopDemo',calls[0].url.params['q'])
        self.assertEqual(calls[0].method,'GET')

    def test_hubspot_uses_email_as_the_unique_upsert_identity(self):
        calls=[]
        def handler(request):
            calls.append(request);payload=json.loads(request.content)
            self.assertEqual(payload['inputs'][0]['idProperty'],'email')
            self.assertEqual(payload['inputs'][0]['id'],'customer@test.example')
            return httpx.Response(200,json={'status':'COMPLETE','results':[{'id':'contact-42'}]})
        with patch.dict(os.environ,self.env,clear=True):self.assertEqual(sync_hubspot({'name':'Maja Demo','email':'customer@test.example'},self.client(handler)),'contact-42')
        self.assertEqual(len(calls),1)

    def test_hubspot_partial_response_is_not_reported_as_connected(self):
        with patch.dict(os.environ,self.env,clear=True),self.assertRaises(IntegrationError):
            sync_hubspot({'name':'Maja Demo','email':'customer@test.example'},self.client(lambda r:httpx.Response(207,json={'status':'COMPLETE','numErrors':1,'results':[]})))

    def test_hubspot_blocks_contacts_outside_the_test_allowlist(self):
        def handler(request):self.fail('No provider call should occur')
        with patch.dict(os.environ,self.env,clear=True),self.assertRaises(IntegrationError):
            sync_hubspot({'name':'Outside','email':'outside@test.example'},self.client(handler))

    def test_matched_gmail_reply_suppresses_followups_and_replay_is_safe(self):
        s=Store();k=Knowledge(s,':memory:')
        e,_=s.create_enquiry(key='inbox-test',conversation_id='a'*32,name='Maja Demo',email='customer@test.example',question='A repair enquiry',language='en',transcript=[])
        t=s.claim_task();s.draft(t['id'],'test');e=s.detail(e['id']);s.approve(e['id'],e['version'],'Subject','Approved reply body.');t=s.claim_task();out=s.prepare_delivery(t['id'],'test');s.delivered(t['id'],out['id'])
        message=EmailMessage();message['From']='Maja <customer@test.example>';message['In-Reply-To']=out['message_id'];message.set_content('Thanks, I will send a photo.')
        class FakeGmail:
            def messages(self):return [('message-one',message)]
        with patch.dict(os.environ,self.env,clear=True):
            self.assertEqual(import_gmail(s,k,FakeGmail()),1)
            self.assertEqual(import_gmail(s,k,FakeGmail()),0)
        self.assertEqual(s.detail(e['id'])['status'],'customer_replied')
        s.control(advance_hours=73);self.assertEqual(s.due_followups(),0)
        s.db.close();k.service.db.conn.close()


if __name__=='__main__':unittest.main()
