"""Slack, through an incoming webhook (`SLACK_WEBHOOK_URL`). `[notify].slack = {}` turns it on."""

from __future__ import annotations

import httpx

from postdesk.config import settings
from postdesk.notify.text import Message


def ready(opts: dict) -> bool:
    return bool(settings.slack_webhook_url)


def payload(msg: Message) -> dict:
    lines = [f"*{msg.title}*"]
    if msg.text:
        lines.append(msg.text[:2900])
    if msg.links:
        lines.append("  ·  ".join(f"<{u}|{label}>" for label, u in msg.links.items()))
    return {"text": "\n".join(lines)}


def send(msg: Message, opts: dict, *, client: httpx.Client | None = None) -> None:
    http = client or httpx.Client(timeout=20)
    resp = http.post(settings.slack_webhook_url, json=payload(msg))
    if resp.status_code >= 300:
        raise RuntimeError(f"Slack answered {resp.status_code}: {resp.text[:200]}")
