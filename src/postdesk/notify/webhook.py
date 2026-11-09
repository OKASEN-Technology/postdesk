"""
JSON to a URL of yours (`NOTIFY_WEBHOOK_URL`), for anything that is not
Slack or email. `[notify].webhook = {}` turns it on. The body:

    {"event": "brief_ready", "title": "...", "text": "...", "card_id": "...",
     "links": {"Open the card": "...", "Approve": "..."}, "sent_at": "..."}

With `POSTDESK_SECRET_KEY` set, `X-Postdesk-Signature: sha256=<hex>` is
the HMAC-SHA256 of the body with that key, so the receiver can check it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

import httpx

from postdesk.config import settings
from postdesk.notify.text import Message


def ready(opts: dict) -> bool:
    return bool(settings.notify_webhook_url)


def body(msg: Message) -> bytes:
    return json.dumps({"event": msg.kind, "title": msg.title, "text": msg.text, "card_id": msg.card_id,
                       "links": msg.links, "sent_at": datetime.now(UTC).isoformat()}).encode()


def send(msg: Message, opts: dict, *, client: httpx.Client | None = None) -> None:
    data = body(msg)
    headers = {"content-type": "application/json"}
    if settings.postdesk_secret_key:
        sig = hmac.new(settings.postdesk_secret_key.encode(), data, hashlib.sha256).hexdigest()
        headers["x-postdesk-signature"] = f"sha256={sig}"
    http = client or httpx.Client(timeout=20)
    resp = http.post(settings.notify_webhook_url, content=data, headers=headers)
    if resp.status_code >= 300:
        raise RuntimeError(f"the webhook answered {resp.status_code}: {resp.text[:200]}")
