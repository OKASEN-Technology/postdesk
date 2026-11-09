"""
The bot's loop, a thread inside `postdesk serve` while `TELEGRAM_BOT_TOKEN`
is set:

- **Updates.** Long polling by default: the bot asks Telegram for new
  messages, so no public URL is needed (a laptop works). A webhook instead
  when `PUBLIC_BASE_URL` is a public https address (`[telegram].mode =
  "auto"`), or when `mode = "webhook"`: Telegram then posts each update to
  `/api/telegram/webhook`, checked against a secret derived from the bot
  token. Every update goes through commands.handle, which checks the chat
  before anything else.
- **Notices.** What the board left in the outbox for Telegram (notify/):
  the brief with its buttons, the slides with Schedule, a hold with its
  reason, the link once live.
- **The digest**, once a day at `[telegram].digest_at` in the account's
  timezone.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from postdesk import notify, store
from postdesk.config import config, settings
from postdesk.db.models import Card, Notice
from postdesk.db.session import SessionLocal
from postdesk.telegram import commands, pairing, render
from postdesk.telegram.api import Bot, TelegramError

OFFSET = "telegram_offset"
DIGEST = "telegram_digest_day"
POLL_TIMEOUT_S = 20
WEBHOOK_PATH = "/api/telegram/webhook"


def webhook_secret() -> str:
    """What Telegram sends back in X-Telegram-Bot-Api-Secret-Token."""
    return hashlib.sha256(f"postdesk-webhook:{settings.telegram_bot_token}".encode()).hexdigest()


def public_https() -> bool:
    u = urlparse(settings.public_base_url or "")
    host = (u.hostname or "").lower()
    return u.scheme == "https" and host not in ("localhost", "127.0.0.1", "::1") and not host.endswith(".localhost")


def mode() -> str:
    m = config.telegram.mode
    if m == "auto":
        return "webhook" if public_https() else "polling"
    return m


def webhook_url() -> str:
    return settings.public_base_url.rstrip("/") + WEBHOOK_PATH


# ---------------------------------------------------------------- notices


def notice_outs(session: Session, n: Notice) -> tuple[list[render.Out], Card | None]:
    """What to send for a notice, and the card it is about."""
    from postdesk.control import Spend, ceiling_message

    p = n.payload or {}
    if n.kind == "spend_ceiling":
        text = ceiling_message(Spend(float(p.get("month_usd", 0)), float(p.get("ceiling_usd", 0))))
        return render.messages([f"<b>The agents have stopped</b>\n{render.esc(text)}"]), None
    card = session.get(Card, n.card_id) if n.card_id else None
    if card is None:
        return [], None
    open_ = card.closed_at is None
    if n.kind == "filed":
        src = (card.plan or {}).get("source")
        return render.messages([f"New post to plan: <b>{render.esc(card.title)}</b>"
                                + (f" (from {render.esc(src)})" if src else "")
                                + ". The partner is writing the brief."]), card
    if n.kind == "brief_ready":
        return render.brief(session, card, reply=p.get("reply"),
                            buttons=open_ and card.column == "Brief ready"), card
    if n.kind == "partner_reply":
        return render.partner_reply(card, p.get("reply"), p.get("problems")), card
    if n.kind == "awaiting_approval":
        return render.drafts(session, card, buttons=open_ and card.column == "Awaiting approval"), card
    if n.kind == "scheduled":
        if str(p.get("actor") or "").startswith("telegram:"):
            return [], card  # the chat that scheduled it was told already
        return render.messages([render.scheduled_text(session, card)]), card
    if n.kind == "live":
        return render.live(session, card), card
    if n.kind == "on_hold":
        return render.on_hold(session, card, p.get("reason"), buttons=open_ and card.column == "On hold"), card
    if n.kind == "measured":
        from postdesk.board import posts
        from postdesk.notify.text import measured_line

        live = [x for x in posts.for_card(session, card.id) if x.status == "published"]
        return render.messages([f"<b>A week on: {render.esc(card.title)}</b>\n{render.esc(measured_line(live))}"]), \
            card
    return [], card


def send_notices(session: Session, bot: Bot, *, log=print) -> int:
    linked = pairing.linked(session)
    if linked is None:
        return 0
    chat_id = str(linked["chat_id"])

    def send(n: Notice) -> None:
        outs, card = notice_outs(session, n)
        if outs:
            commands.send(session, bot, chat_id, outs, card=card)

    return notify.dispatch(session, "telegram", send, log=log)


# ---------------------------------------------------------------- the digest


def digest_due(session: Session, now: datetime) -> bool:
    local = now.astimezone(config.account.tz)
    return local.time() >= config.telegram.digest_time and store.get(session, DIGEST) != local.date().isoformat()


def send_digest(session: Session, bot: Bot, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    linked = pairing.linked(session)
    if linked is None or not digest_due(session, now):
        return False
    bot.send_message(str(linked["chat_id"]), commands.digest_text(session, now=now))
    store.put(session, DIGEST, now.astimezone(config.account.tz).date().isoformat())
    return True


# ---------------------------------------------------------------- updates


def poll_once(session: Session, bot: Bot, *, timeout: int = POLL_TIMEOUT_S, deps: commands.Deps | None = None,
              log=print) -> int:
    """One long poll: wait for updates, handle each, remember how far we got."""
    offset = store.get(session, OFFSET)
    updates = bot.get_updates(int(offset) if offset else None, timeout=timeout)
    for u in updates:
        try:
            commands.handle(session, bot, u, deps or commands.Deps(log=log))
        except Exception as e:  # one bad update must not stop the bot, nor come back forever
            session.rollback()
            log(f"telegram: update {u.get('update_id')}: {type(e).__name__}: {e}")
        store.put(session, OFFSET, int(u["update_id"]) + 1)
    return len(updates)


def tick(session: Session, bot: Bot, *, log=print, now: datetime | None = None) -> None:
    """Everything but reading updates: settings, notices, the digest."""
    store.refresh_config(session, log=log)
    send_notices(session, bot, log=log)
    try:
        send_digest(session, bot, now=now)
    except TelegramError as e:
        log(f"telegram: digest: {e}")


def start(bot: Bot, *, log=print) -> str:
    """Say who the bot is and set up how updates arrive. Returns the mode."""
    m = mode()
    try:
        me = bot.get_me()
        log(f"telegram: @{me.get('username')} ({m})")
    except TelegramError as e:
        log(f"telegram: {e}")
    if m == "webhook":
        bot.set_webhook(webhook_url(), webhook_secret())
    else:
        bot.delete_webhook()  # getUpdates refuses to run while a webhook is set
    return m


def loop(stop: threading.Event, *, bot: Bot | None = None, log=print) -> None:
    if not settings.telegram_bot_token:
        return
    bot = bot or Bot()
    m = None
    while not stop.is_set() and m is None:
        try:
            m = start(bot, log=log)
        except TelegramError as e:
            log(f"telegram: could not start ({e}); trying again in a minute")
            stop.wait(60)
    while not stop.is_set():
        try:
            with SessionLocal() as session:
                if m == "polling":
                    poll_once(session, bot, log=log)
                tick(session, bot, log=log)
        except TelegramError as e:
            log(f"telegram: {e}")
            stop.wait(e.retry_after or 10)
        except Exception as e:
            log(f"telegram: {type(e).__name__}: {e}")
            stop.wait(10)
        if m == "webhook":
            stop.wait(3)
