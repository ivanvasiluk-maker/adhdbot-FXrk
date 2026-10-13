"""Optional HTTPS/SMTP copy of an explicitly submitted lead; no guessed recipients."""
import asyncio
import aiohttp
import os
import re
import smtplib
import ssl
from email.message import EmailMessage


def email_configured():
    recipient = os.getenv('LEAD_EMAIL_TO', '').strip()
    sender = os.getenv('LEAD_EMAIL_FROM', '').strip()
    valid = lambda value: bool(re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+', value))
    provider = os.getenv('LEAD_EMAIL_PROVIDER','resend').strip().lower()
    transport = bool(os.getenv('RESEND_API_KEY','').strip()) if provider == 'resend' else bool(os.getenv('LEAD_SMTP_HOST','').strip()) if provider == 'smtp' else False
    return bool(valid(recipient) and valid(sender) and transport)


async def send_lead_email(text, *, idempotency_key=None):
    if not email_configured():
        raise RuntimeError('lead_email_not_configured')
    if os.getenv('LEAD_EMAIL_PROVIDER','resend').strip().lower() == 'resend':
        headers = {'Authorization':'Bearer '+os.environ['RESEND_API_KEY'].strip()}
        if idempotency_key:
            headers['Idempotency-Key']='skiller-lead-'+str(idempotency_key)
        payload = {'from':os.environ['LEAD_EMAIL_FROM'].strip(), 'to':[os.environ['LEAD_EMAIL_TO'].strip()],
                   'subject':'SKILLER — новая заявка','text':text}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
            async with client.post('https://api.resend.com/emails',json=payload,headers=headers,allow_redirects=False) as response:
                response.raise_for_status()
                accepted = await response.json()
                if not isinstance(accepted,dict) or not accepted.get('id'):
                    raise RuntimeError('email_acceptance_unconfirmed')
        return
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
