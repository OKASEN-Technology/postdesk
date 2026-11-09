"""
Telling the operator what happened. One outbox, many channels:

- `emit` writes a `notice` row per channel in the same transaction as the
  move it reports (board/cards.py, transitions.py, control.py), so a move
  never waits on a network and nothing is lost to a crash between the two.
- Each channel sends its own rows afterwards: Telegram from its bot loop
  (telegram/bot.py), the one-way notifiers from the worker's housekeeping
  (`dispatch_one_way`).

Channels:

- **telegram**, once a chat is paired (§ the daily interface). Not a
  notifier that links to a web page: the message carries the brief, the
  slides and the buttons.
- **slack** (an incoming webhook), **webhook** (JSON to a URL) and
  **email** (SMTP or Resend), each turned on by its table in `[notify]`
  and its secret in `.env`. One-way: they carry the same events as text,
  with signed links that expire into the web UI for the two gate actions
  (notify/links.py).

`[notify]` in postdesk.toml:

    [notify]
    slack = {}                                   # SLACK_WEBHOOK_URL in .env
    webhook = { events = ["live", "on_hold"] }   # NOTIFY_WEBHOOK_URL in .env
    email = { to = "you@example.com" }           # SMTP_* or RESEND_API_KEY in .env

`events` limits a channel to some kinds; without it a channel gets them all.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from postdesk.config import config, settings
from postdesk.db.models import Card, Notice

KINDS = ("filed", "brief_ready", "partner_reply", "awaiting_approval", "scheduled", "live", "on_hold", "measured",
         "spend_ceiling")
ONE_WAY = ("slack", "webhook", "email")
MAX_ATTEMPTS = 5
# A notice that could not go out for this long is dropped, not sent late
# (a bot that was down for a week should not replay the week).
STALE = timedelta(days=2)


class NotifyError(RuntimeError):
    pass


def now_utc() -> datetime:
    return datetime.now(UTC)


def one_way(name: str):
    """The sender module for a one-way channel."""
    from postdesk.notify import email, slack, webhook

    return {"slack": slack, "webhook": webhook, "email": email}[name]


def configured() -> dict[str, dict]:
    """The one-way channels turned on in `[notify]` whose secret is set."""
    out = {}
    for name, opts in (config.notify or {}).items():
        opts = dict(opts or {})
        if name not in ONE_WAY or opts.get("enabled", True) is False:
            continue
        if one_way(name).ready(opts):
            out[name] = opts
    return out


def channels(session: Session, kind: str) -> list[str]:
    from postdesk.telegram import pairing

    out = []
    if settings.telegram_bot_token and pairing.linked(session) is not None:
        out.append("telegram")
    for name, opts in configured().items():
        events = opts.get("events")
        if not events or kind in events:
            out.append(name)
    return out


def emit(session: Session, kind: str, card: Card | None = None, **payload) -> list[Notice]:
    """A notice of `kind` on every channel that wants it. Not committed: it
    lands with the caller's transaction."""
    if kind not in KINDS:
        raise ValueError(f"no notice kind {kind!r}")
    rows = []
    for ch in channels(session, kind):
        n = Notice(channel=ch, kind=kind, card_id=card.id if card is not None else None, payload=payload,
                   created_at=now_utc(), attempts=0)
        session.add(n)
        rows.append(n)
    return rows


def pending(session: Session, channel: str, *, limit: int = 20) -> list[Notice]:
    return list(session.scalars(select(Notice).where(Notice.channel == channel, Notice.sent_at.is_(None))
                                .order_by(Notice.created_at, Notice.id).limit(limit)))


def dispatch(session: Session, channel: str, send: Callable[[Notice], None], *, log=print,
             now: datetime | None = None) -> int:
    """Send `channel`'s waiting notices, oldest first, with `send`. A notice
    that fails is tried again on the next pass, `MAX_ATTEMPTS` times."""
    sent = 0
    for n in pending(session, channel):
        now = now or now_utc()
        if now - n.created_at > STALE:
            n.sent_at, n.error = now, "dropped: too old to send"
            session.commit()
            continue
        try:
            send(n)
        except Exception as e:  # one bad notice must not block the rest
            session.rollback()
            n.attempts += 1
            n.error = f"{type(e).__name__}: {e}"[:2000]
            if n.attempts >= MAX_ATTEMPTS:
                n.sent_at = now_utc()
            log(f"notify {channel}: {n.kind}: {n.error}")
            session.commit()
            continue
        n.sent_at, n.error = now_utc(), None
        session.commit()
        sent += 1
    return sent


def dispatch_one_way(session: Session, *, client: httpx.Client | None = None, log=print) -> int:
    """Send what the one-way channels have waiting (the worker's housekeeping)."""
    from postdesk.notify import text

    total = 0
    for name, opts in configured().items():
        mod = one_way(name)
        total += dispatch(session, name, lambda n, mod=mod, opts=opts: mod.send(text.render(session, n), opts,
                                                                                   client=client), log=log)
    # Notices for a channel that has since been turned off are dropped.
    for n in session.scalars(select(Notice).where(Notice.channel.in_(ONE_WAY), Notice.sent_at.is_(None),
                                                  Notice.channel.not_in(list(configured())))):
        n.sent_at, n.error = now_utc(), "dropped: the channel is off"
    session.commit()
    return total


def test(name: str, *, client: httpx.Client | None = None) -> None:
    """Send a test message on a one-way channel (the settings screen's button)."""
    from postdesk.notify.text import Message

    opts = configured().get(name)
    if opts is None:
        raise NotifyError(f"{name} is not set up: it needs a [notify].{name} table and its secret in .env")
    one_way(name).send(Message(kind="test", title="postdesk test",
                               text="A test from postdesk. Notices will arrive here.", links={}), opts, client=client)
