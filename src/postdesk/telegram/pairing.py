"""
Which Telegram chat is the operator's. The bot starts unpaired:

1. Someone sends `/start` to the bot. It answers with a six-character code
   (good for `CODE_TTL`), and remembers which chat asked.
2. The admin pastes the code into Settings -> Telegram in the web UI, or
   runs `postdesk telegram link <code>`. That chat is now the paired one.

Only the admin can finish step 2, so a stranger who finds the bot and
sends /start gets a code that is no use to them. From then on every other
chat is told the bot is private, and only the paired chat's actor
(`telegram:<chat id>`) passes the board's gates. One chat in v1; a group
works the same way.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from postdesk import store

LINKED = "telegram_chat"
PENDING = "telegram_pairing"
CODE_TTL = timedelta(minutes=30)
MAX_PENDING = 10
# No 0/O, 1/I/L: a code is read off one screen and typed into another.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class PairingError(ValueError):
    pass


def linked(session: Session) -> dict | None:
    """{chat_id, title, linked_at, linked_by} of the paired chat, or None."""
    value = store.get(session, LINKED)
    return value if isinstance(value, dict) and value.get("chat_id") else None


def _pending(session: Session, now: datetime) -> dict[str, dict]:
    value = store.get(session, PENDING) or {}
    return {code: v for code, v in value.items()
            if isinstance(v, dict) and datetime.fromisoformat(v["expires"]) > now}


def new_code(session: Session, chat_id: str, title: str = "", *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    pending = _pending(session, now)
    for code, v in pending.items():  # the same chat asking again gets the same code
        if v["chat_id"] == str(chat_id):
            return code
    code = "".join(secrets.choice(ALPHABET) for _ in range(6))
    pending[code] = {"chat_id": str(chat_id), "title": title, "expires": (now + CODE_TTL).isoformat()}
    oldest = sorted(pending, key=lambda c: pending[c]["expires"])[:-MAX_PENDING] if len(pending) > MAX_PENDING else []
    for c in oldest:
        pending.pop(c)
    store.put(session, PENDING, pending)
    return code


def link(session: Session, code: str, *, by: str, now: datetime | None = None) -> dict:
    """Pair the chat that was given `code`. Replaces a chat paired before."""
    now = now or datetime.now(UTC)
    code = (code or "").strip().upper().replace(" ", "")
    pending = _pending(session, now)
    hit = pending.get(code)
    if hit is None:
        raise PairingError("that code is not one the bot gave out in the last 30 minutes; send /start to the bot "
                           "for a new one")
    value = {"chat_id": hit["chat_id"], "title": hit.get("title") or "", "linked_at": now.isoformat(),
             "linked_by": by}
    store.put(session, LINKED, value, commit=False)
    store.delete(session, PENDING, commit=False)
    session.commit()
    return value


def unlink(session: Session) -> bool:
    if linked(session) is None:
        return False
    store.delete(session, LINKED)
    return True


def chat_title(chat: dict) -> str:
    if chat.get("title"):
        return chat["title"]
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
    return name or (f"@{chat['username']}" if chat.get("username") else str(chat.get("id")))
