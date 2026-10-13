"""Optional SMTP copy of an explicitly submitted lead; no guessed recipients."""
import asyncio
import os
import re
import smtplib
import ssl
from email.message import EmailMessage


def email_configured():
    recipient = os.getenv('LEAD_EMAIL_TO', '').strip()
    sender = os.getenv('LEAD_EMAIL_FROM', '').strip()
    valid = lambda value: bool(re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+', value))
    return bool(valid(recipient) and valid(sender) and os.getenv('LEAD_SMTP_HOST', '').strip())


async def send_lead_email(text):
    if not email_configured():
        raise RuntimeError('lead_email_not_configured')
    def send():
        message = EmailMessage()
        message['From'] = os.environ['LEAD_EMAIL_FROM'].strip()
        message['To'] = os.environ['LEAD_EMAIL_TO'].strip()
        message['Subject'] = 'SKILLER — новая заявка'
        message.set_content(text)
        host = os.environ['LEAD_SMTP_HOST'].strip()
        port = int(os.getenv('LEAD_SMTP_PORT', '465'))
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=15)
        with server:
            if port != 465:
                server.starttls(context=ssl.create_default_context())
            login = os.getenv('LEAD_SMTP_USER', '')
            if login:
                server.login(login, os.environ.get('LEAD_SMTP_PASSWORD', ''))
            server.send_message(message)
    await asyncio.to_thread(send)
