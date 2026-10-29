import base64
import concurrent.futures
import io
import unittest

from PIL import Image

from connected.features import quote_items
from connected.knowledge import Knowledge
from connected.photos import normalize_photo
from connected.store import Conflict, Store


def photo_data():
    image=Image.new('RGB',(30,20),'green');out=io.BytesIO();image.save(out,format='PNG')
    return base64.b64encode(out.getvalue()).decode()


class AddedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.s=Store();self.k=Knowledge(self.s,':memory:')

    def tearDown(self):
        self.s.db.close();self.k.service.db.conn.close()

    def enquiry(self,photos=None):
        e,_=self.s.create_enquiry(key='feature-one',conversation_id='a'*32,name='Maja Demo',email='maja@example.com',
                                 question='My cargo-bike frame needs inspection.',language='en',transcript=[],photos=photos)
        t=self.s.claim_task();self.s.draft(t['id'],'unit-intake')
        return self.s.detail(e['id'])

    def quote(self,transport='preview'):
        e=self.enquiry()
        q=self.s.save_quote(e['id'],e['version'],[{'description':'Labour','quantity':'1.5','unit_price':'40.00'},{'description':'Replacement part','quantity':'1','unit_price':'25.00'}],
                            '2–3 working days after inspection','No appointment is reserved.',7)
        return e,q

    def sent_quote(self):
        e,q=self.quote();self.s.approve_quote(q['id'],q['version']);task=self.s.claim_task()
        item=self.s.prepare_delivery(task['id'],'unit-quote-send');self.s.delivered(task['id'],item['id'])
        return self.s.detail(e['id']),self.s.quote(q['id'])

    def test_photo_is_present_in_handover_without_blob_in_snapshot(self):
        p=normalize_photo('frame.png',photo_data());e=self.enquiry([p])
        self.assertEqual(len(e['photos']),1)
        self.assertNotIn('data',e['photos'][0])
        self.assertGreater(len(self.s.photo(e['photos'][0]['id'])['data']),0)

    def test_photo_retry_does_not_duplicate_attachments(self):
        p=normalize_photo('frame.png',photo_data());e=self.enquiry([p])
        repeated,duplicate=self.s.create_enquiry(key='feature-one',conversation_id='a'*32,name='Maja Demo',email='maja@example.com',
                                 question='My cargo-bike frame needs inspection.',language='en',transcript=[],photos=[p])
        self.assertTrue(duplicate);self.assertEqual(len(repeated['photos']),1)

    def test_quote_totals_are_calculated_from_staff_entries(self):
        e,q=self.quote()
        self.assertEqual(q['total_cents'],8500)
        self.assertEqual(q['state'],'draft')
        self.assertEqual(self.s.detail(e['id'])['outbox'],[])

    def test_fractional_quantities_round_half_up_in_cents(self):
        lines,total=quote_items([{'description':'Small item','quantity':'0.125','unit_price':'0.20'}])
        self.assertEqual(total,3)
        self.assertEqual(lines[0]['amount_cents'],3)

    def test_nonfinite_negative_and_excess_precision_prices_are_rejected(self):
        for value in ('NaN','Infinity','-1','1.001'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                quote_items([{'description':'Part','quantity':'1','unit_price':value}])

    def test_customer_cannot_accept_undelivered_quote(self):
        e,q=self.quote()
        with self.assertRaises(Conflict):self.s.decide_quote(q['id'],q['decision_token'],'decision-one','accept')

    def test_wrong_quote_token_cannot_record_a_decision(self):
        e,q=self.sent_quote()
        with self.assertRaises(PermissionError):self.s.decide_quote(q['id'],'wrong-token','decision-one','accept')

    def test_double_approval_only_queues_one_quote_delivery(self):
        e,q=self.quote()
        def approve(_):
            try:self.s.approve_quote(q['id'],q['version']);return True
            except Conflict:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(approve,range(2)))
        self.assertEqual(sum(results),1)
        self.assertEqual(len(self.s.detail(e['id'])['outbox']),1)

    def test_customer_question_is_visible_to_staff_and_can_be_followed_by_acceptance(self):
        e,q=self.sent_quote()
        self.s.decide_quote(q['id'],q['decision_token'],'question-one','question','Are the parts included?')
        item=self.s.detail(e['id'])
        self.assertEqual(item['status'],'quote_question')
        self.assertEqual(item['quotes'][0]['customer_note'],'Are the parts included?')
        self.s.decide_quote(q['id'],q['decision_token'],'accept-one','accept')
        item=self.s.detail(e['id'])
        self.assertEqual(item['status'],'quote_accepted')
        self.assertEqual(item['next_action'],'Schedule repair')

    def test_repeated_customer_decision_is_idempotent(self):
        e,q=self.sent_quote()
        self.s.decide_quote(q['id'],q['decision_token'],'accept-one','accept')
        self.s.decide_quote(q['id'],q['decision_token'],'accept-one','accept')
        events=[a for a in self.s.detail(e['id'])['events'] if a['type']=='quote_accepted']
        self.assertEqual(len(events),1)

    def test_old_revision_cannot_be_accepted(self):
        e,q=self.sent_quote();e=self.s.detail(e['id'])
        self.s.save_quote(e['id'],e['version'],[{'description':'Updated repair','quantity':'1','unit_price':'100.00'}],'4 days','',7)
        with self.assertRaises(Conflict):self.s.decide_quote(q['id'],q['decision_token'],'accept-old','accept')

    def test_accepted_quote_cannot_be_silently_replaced(self):
        e,q=self.sent_quote();self.s.decide_quote(q['id'],q['decision_token'],'accept-one','accept');e=self.s.detail(e['id'])
        with self.assertRaises(Conflict):self.s.save_quote(e['id'],e['version'],[{'description':'Different repair','quantity':'1','unit_price':'100.00'}],'4 days','',7)

    def test_expired_quote_cannot_be_accepted(self):
        e,q=self.sent_quote();self.s.control(advance_hours=169)
        with self.assertRaises(Conflict):self.s.decide_quote(q['id'],q['decision_token'],'expired-one','accept')

    def test_expired_queued_quote_is_not_delivered(self):
        e,q=self.quote();self.s.approve_quote(q['id'],q['version']);self.s.control(advance_hours=169)
        t=self.s.claim_task()
        self.assertIsNone(self.s.prepare_delivery(t['id'],'unit-expired'))
        self.assertEqual(self.s.detail(e['id'])['status'],'quote_expired')

    def test_failed_quote_recovery_preserves_exact_message(self):
        e,q=self.quote();self.s.approve_quote(q['id'],q['version']);t=self.s.claim_task();self.s.control(delivery_paused=True)
        with self.assertRaises(ConnectionError):self.s.prepare_delivery(t['id'],'unit-paused')
        self.s.failed(t['id'],'Preview paused')
        old=self.s.detail(e['id'])['outbox'][0]
        self.assertEqual(self.s.quote(q['id'])['state'],'delivery_failed')
        self.s.control(delivery_paused=False);self.s.retry(e['id']);t=self.s.claim_task()
        out=self.s.prepare_delivery(t['id'],'unit-retry');self.s.delivered(t['id'],out['id'])
        self.assertEqual(old['message_id'],out['message_id'])
        self.assertEqual(old['body'],out['body'])
        self.assertEqual(self.s.quote(q['id'])['state'],'sent')

    def test_assignment_uses_version_and_preserves_the_quote(self):
        e,q=self.sent_quote();old=e['version']
        updated=self.s.set_work(e['id'],old,'Nika','Schedule repair',4)
        self.assertEqual(updated['assignee'],'Nika')
        self.assertEqual(updated['quotes'][0]['id'],q['id'])
        with self.assertRaises(Conflict):self.s.set_work(e['id'],old,'Luka','Prepare quote',4)

    def test_overdue_alert_is_saved_once_and_acknowledgment_does_not_clear_overdue(self):
        e=self.enquiry();self.s.set_work(e['id'],e['version'],'Nika','Request photos',4);self.s.control(advance_hours=5)
        self.assertEqual(self.s.refresh_overdue(),1)
        self.assertEqual(self.s.refresh_overdue(),0)
        alert=self.s.feature_snapshot()['alerts'][0];self.s.acknowledge_alert(alert['id'])
        self.assertEqual(self.s.feature_snapshot()['alerts'],[])
        self.assertEqual(self.s.refresh_overdue(),0)
        self.assertTrue(self.s.detail(e['id'])['overdue'])

    def test_completed_work_resolves_alert_and_due_time(self):
        e=self.enquiry();self.s.control(advance_hours=25);self.s.refresh_overdue();e=self.s.detail(e['id'])
        e=self.s.set_work(e['id'],e['version'],'Luka','Completed')
        self.assertFalse(e['overdue']);self.assertIsNone(e['due_at'])
        self.assertEqual(self.s.feature_snapshot()['alerts'],[])

    def test_unknown_questions_are_grouped_without_automatic_learning(self):
        question='Can I store my bike indoors while it waits for inspection?'
        first=self.k.ask(question)
        second=self.k.ask(question.upper())
        self.assertEqual(first['status'],'unknown');self.assertEqual(second['status'],'unknown')
        gaps=self.s.feature_snapshot()['knowledge_gaps']
        self.assertEqual(len(gaps),1);self.assertEqual(gaps[0]['occurrences'],2)
        self.assertEqual(self.s.feature_snapshot()['approved_answers'],[])

    def test_owner_approved_answer_is_immediately_used_and_cited(self):
        question='Can I store my bike indoors while it waits for inspection?'
        first=self.k.ask(question);gap=self.s.feature_snapshot()['knowledge_gaps'][0]
        self.s.publish_answer(gap['id'],'Yes. Bikes awaiting inspection are stored in the locked workshop.')
        after=self.k.ask(question,first['conversation_id'])
        self.assertEqual(after['status'],'answered')
        self.assertIn('locked workshop',after['reply'])
        self.assertTrue(after['sources'])
        self.assertEqual(after['knowledge_version'],2)

    def test_stale_answer_update_cannot_replace_a_new_owner_answer(self):
        self.k.ask('Do you have secure storage?');gap=self.s.feature_snapshot()['knowledge_gaps'][0]
        answer=self.s.publish_answer(gap['id'],'Yes, there is secure storage.')
        self.s.publish_answer(gap['id'],'Yes, the storage area is locked.',answer['version'])
        with self.assertRaises(Conflict):self.s.publish_answer(gap['id'],'A stale replacement answer.',answer['version'])

    def test_crm_failure_does_not_change_customer_delivery_stage(self):
        e,q=self.sent_quote();self.s.queue_crm(e['id']);task=self.s.claim_task();self.s.failed(task['id'],'Provider unavailable')
        item=self.s.detail(e['id'])
        self.assertEqual(item['status'],'quote_sent');self.assertEqual(item['crm']['state'],'failed')


class PhotoValidationTests(unittest.TestCase):
    def test_valid_photo_is_reencoded_and_path_is_not_used_as_filename(self):
        p=normalize_photo('../../private/frame.png',photo_data())
        self.assertEqual(p['filename'],'frame.jpg');self.assertEqual(p['media_type'],'image/jpeg')
        with Image.open(io.BytesIO(p['data'])) as image:self.assertEqual(image.format,'JPEG')

    def test_svg_disguised_as_photo_is_rejected(self):
        with self.assertRaises(ValueError):normalize_photo('photo.jpg',base64.b64encode(b'<svg onload="alert(1)"></svg>').decode())

    def test_invalid_base64_is_rejected(self):
        with self.assertRaises(ValueError):normalize_photo('photo.jpg','not:base64')

    def test_oversize_upload_is_rejected_before_decoding_image(self):
        with self.assertRaises(ValueError):normalize_photo('photo.jpg',base64.b64encode(b'x'*(4*1024*1024+1)).decode())

    def test_orientation_is_applied_and_metadata_is_removed(self):
        image=Image.new('RGB',(30,20),'blue');exif=Image.Exif();exif[274]=6;exif[315]='Private camera owner'
        out=io.BytesIO();image.save(out,format='JPEG',exif=exif)
        p=normalize_photo('photo.jpg',base64.b64encode(out.getvalue()).decode())
        self.assertEqual((p['width'],p['height']),(20,30))
        with Image.open(io.BytesIO(p['data'])) as clean:self.assertEqual(dict(clean.getexif()),{})


if __name__=='__main__':unittest.main()
