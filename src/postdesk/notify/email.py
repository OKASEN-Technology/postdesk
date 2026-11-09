"""
Email, through your own SMTP server (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
`SMTP_PASSWORD`) or Resend (`RESEND_API_KEY`), from `SMTP_FROM`.
`[notify].email = { to = "you@example.com" }` turns it on.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import httpx

from postdesk.config import settings
from postdesk.notify.text import Message

RESEND_URL = "https://api.resend.com/emails"


def ready(opts: dict) -> bool:
    return bool(opts.get("to") and settings.smtp_from and (settings.smtp_host or settings.resend_api_key))


def send(msg: Message, opts: dict, *, client: httpx.Client | None = None) -> None:
    subject = msg.title[:200]
    text = msg.plain()
    to = opts["to"] if isinstance(opts["to"], list) else [opts["to"]]
    if settings.smtp_host:
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = settings.smtp_from, ", ".join(to), subject
        m.set_content(text)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password or "")
            smtp.send_message(m)
        return
    http = client or httpx.Client(timeout=20)
    resp = http.post(RESEND_URL, headers={"authorization": f"Bearer {settings.resend_api_key}"},
                     json={"from": settings.smtp_from, "to": to, "subject": subject, "text": text})
    if resp.status_code >= 300:
        raise RuntimeError(f"Resend answered {resp.status_code}: {resp.text[:200]}")
