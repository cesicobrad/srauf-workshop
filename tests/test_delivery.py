import smtplib
import unittest
from unittest.mock import MagicMock, patch

from connected.delivery import UncertainDelivery, send_smtp


class SMTPBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.item={"recipient":"test@owned.example","subject":"Approved reply","body":"Reviewed body.","message_id":"<stable-id@srauf-demo.local>"}
        self.env={"SMTP_HOST":"smtp.example","SMTP_FROM":"workshop@example.com","SMTP_TO_ALLOWLIST":"test@owned.example"}

    def test_disallowed_recipient_never_opens_connection(self):
        with patch.dict('os.environ',self.env,clear=True),patch('connected.delivery.smtplib.SMTP') as constructor:
            self.item["recipient"]="outside@example.com"
            with self.assertRaises(ValueError):send_smtp(self.item)
            constructor.assert_not_called()

    def test_connection_loss_during_submission_is_uncertain(self):
        smtp=MagicMock()
        smtp.send_message.side_effect=smtplib.SMTPServerDisconnected("Lost response after DATA")
        with patch.dict('os.environ',self.env,clear=True),patch('connected.delivery.smtplib.SMTP',return_value=smtp):
            with self.assertRaises(UncertainDelivery):send_smtp(self.item)

    def test_explicit_recipient_rejection_is_not_marked_ambiguous(self):
        smtp=MagicMock()
        smtp.send_message.side_effect=smtplib.SMTPRecipientsRefused({"test@owned.example":(550,b"Rejected")})
        with patch.dict('os.environ',self.env,clear=True),patch('connected.delivery.smtplib.SMTP',return_value=smtp):
            with self.assertRaises(smtplib.SMTPRecipientsRefused):send_smtp(self.item)

    def test_lost_quit_response_does_not_retry_an_accepted_message(self):
        smtp=MagicMock()
        smtp.quit.side_effect=smtplib.SMTPServerDisconnected("Lost QUIT")
        with patch.dict('os.environ',self.env,clear=True),patch('connected.delivery.smtplib.SMTP',return_value=smtp):
            send_smtp(self.item)
            self.assertEqual(smtp.send_message.call_count,1)
            smtp.close.assert_called_once()

    def test_smtp_uses_exact_approved_message_and_stable_identity(self):
        smtp=MagicMock()
        with patch.dict('os.environ',self.env,clear=True),patch('connected.delivery.smtplib.SMTP',return_value=smtp):
            send_smtp(self.item)
            message=smtp.send_message.call_args.args[0]
            self.assertEqual(message['Message-ID'],self.item['message_id'])
            self.assertEqual(message['Subject'],self.item['subject'])
            self.assertEqual(message.get_content().strip(),self.item['body'])


if __name__=='__main__':unittest.main()
