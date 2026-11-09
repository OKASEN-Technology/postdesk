"""
Signed links that expire, for the two gate actions in a one-way notice
(Approve on a brief, Schedule on a made post). The link opens a page in
the web UI's origin (`/act/<token>`, api/routers/act.py) that says what it
will do and asks for one more tap; the move itself is a POST, so a mail
client or a chat app fetching the link to preview it changes nothing.

A token names one card and one action, is signed with the app's secret key
(`POSTDESK_SECRET_KEY`, or the one kept in the database) and expires after
`TTL`. Whoever holds the link acts as the admin for that one move, which is
why it is only ever sent to the channels the admin configured.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from sqlalchemy.orm import Session

from postdesk.config import settings

ACTIONS = ("approve", "schedule")
TTL_S = 48 * 3600
ACTOR = "link"  # who a move from a signed link is logged as (a person: board/cards.py HUMANS)


class LinkError(ValueError):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sig(session: Session, body: str) -> str:
    from postdesk.api.auth import secret_key

    return _b64(hmac.new(secret_key(session), f"act-link:{body}".encode(), hashlib.sha256).digest())


def token(session: Session, card_id: uuid.UUID, action: str, *, now: float | None = None, ttl: int = TTL_S) -> str:
    if action not in ACTIONS:
        raise LinkError(f"no link action {action!r}")
    body = _b64(json.dumps({"c": str(card_id), "a": action, "e": int((now or time.time()) + ttl)},
                           separators=(",", ":")).encode())
    return f"{body}.{_sig(session, body)}"


def url(session: Session, card_id: uuid.UUID, action: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/act/{token(session, card_id, action)}"


def verify(session: Session, tok: str, *, now: float | None = None) -> tuple[uuid.UUID, str]:
    """(card id, action) from a valid token; LinkError otherwise."""
    body, _, sig = (tok or "").partition(".")
    if not body or not sig or not hmac.compare_digest(_sig(session, body), sig):
        raise LinkError("this link is not valid")
    try:
        d = json.loads(_unb64(body))
        card_id, action, exp = uuid.UUID(d["c"]), d["a"], int(d["e"])
    except (ValueError, KeyError, TypeError) as e:
        raise LinkError("this link is not valid") from e
    if exp < (now or time.time()):
        raise LinkError("this link has expired; open the card in the web UI instead")
    if action not in ACTIONS:
        raise LinkError("this link is not valid")
    return card_id, action
