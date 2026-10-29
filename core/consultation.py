"""Explicit booking destination; no client data is appended to the URL."""
import os
from urllib.parse import urlsplit

DEFAULT_CONSULTATION_URL = 'https://cal.com/%D0%B8%D0%B2%D0%B0%D0%BD-%D0%B2%D0%B0%D1%81%D0%B8%D0%BB%D1%8E%D0%BA-dwlef3/15min'


def consultation_url():
    value=os.getenv('CONSULTATION_URL',DEFAULT_CONSULTATION_URL).strip()
    parsed=urlsplit(value)
    return value if parsed.scheme=='https' and parsed.hostname=='cal.com' and not parsed.username and not parsed.password else ''
