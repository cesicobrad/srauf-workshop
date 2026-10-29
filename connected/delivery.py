"""Preview delivery is atomic. Ambiguous SMTP sends are never automatically repeated."""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


class UncertainDelivery(Exception):
    pass


def send_smtp(item):
    allowed={a.strip().lower() for a in os.getenv("SMTP_TO_ALLOWLIST","").split(",") if a.strip()}
    if item["recipient"].lower() not in allowed:
        raise ValueError("Recipient is not in SMTP_TO_ALLOWLIST. Add only an address you control for live testing.")
    host=os.getenv("SMTP_HOST","")
    if not host:
        raise ValueError("SMTP_HOST is not configured.")
    msg=EmailMessage()
    msg["From"]=os.getenv("SMTP_FROM","") or os.getenv("SMTP_USER","")
    if not msg["From"]:
        raise ValueError("SMTP_FROM must be configured.")
    msg["To"]=item["recipient"]
    msg["Subject"]=item["subject"]
    msg["Message-ID"]=item["message_id"]
    msg.set_content(item["body"])
    # Connection/auth failures happen before submission and are safe to retry.
    smtp=smtplib.SMTP(host,int(os.getenv("SMTP_PORT","587")),timeout=15)
    try:
        smtp.starttls(context=ssl.create_default_context())
        if os.getenv("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"],os.getenv("SMTP_PASSWORD",""))
        try:
            smtp.send_message(msg)
        except (smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused,smtplib.SMTPDataError):
            raise
        except (OSError,smtplib.SMTPException) as error:
            raise UncertainDelivery("The connection ended during submission. Check the mail provider before retrying.") from error
    finally:
        # A failed QUIT after DATA was accepted must not turn a successful submission
        # into a retryable failure and produce a duplicate message.
        try:
            smtp.quit()
        except (OSError,smtplib.SMTPException):
            smtp.close()
