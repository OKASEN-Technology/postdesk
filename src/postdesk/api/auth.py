"""
One admin login. The password is `POSTDESK_ADMIN_PASSWORD` in `.env`, or,
when that is unset, the one set on the first visit to the web UI (stored as
a salted hash in the `setting` table). A signed cookie keeps the session;
scripts and the CLI on another machine use `Authorization: Bearer
<POSTDESK_API_TOKEN>` instead. `/media/<id>` is public (Instagram fetches
from it); everything else under `/api` needs one of the two.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from postdesk.config import settings
from postdesk.db.models import Setting
from postdesk.db.session import get_session

COOKIE = "postdesk_session"
SESSION_DAYS = 30
PBKDF2_ROUNDS = 200_000
PASSWORD_KEY = "admin_password"
SECRET_KEY = "secret_key"


def _setting(session: Session, key: str):
    row = session.get(Setting, key)
    return row.value if row else None


def _set_setting(session: Session, key: str, value) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value, updated_at=datetime.now(UTC)))
    else:
        row.value, row.updated_at = value, datetime.now(UTC)
    session.commit()


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def check_hash(password: str, stored: str) -> bool:
    try:
        salt_b64, _ = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, base64.b64decode(salt_b64)), stored)


def password_configured(session: Session) -> bool:
    return bool(settings.postdesk_admin_password) or _setting(session, PASSWORD_KEY) is not None


def set_password(session: Session, password: str) -> None:
    if len(password) < 8:
        raise ValueError("the password needs at least 8 characters")
    _set_setting(session, PASSWORD_KEY, hash_password(password))


def verify_password(session: Session, password: str) -> bool:
    if settings.postdesk_admin_password:
        return hmac.compare_digest(password, settings.postdesk_admin_password)
    stored = _setting(session, PASSWORD_KEY)
    return isinstance(stored, str) and check_hash(password, stored)


def secret_key(session: Session) -> bytes:
    """`POSTDESK_SECRET_KEY`, else one generated once and kept in the
    database so sessions survive a restart."""
    if settings.postdesk_secret_key:
        return settings.postdesk_secret_key.encode()
    key = _setting(session, SECRET_KEY)
    if not key:
        key = secrets.token_urlsafe(32)
        _set_setting(session, SECRET_KEY, key)
    return str(key).encode()


def _sign(key: bytes, payload: str) -> str:
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def issue_cookie(session: Session, response: Response, *, secure: bool) -> None:
    expires = int(time.time()) + SESSION_DAYS * 86400
    payload = str(expires)
    value = f"{payload}.{_sign(secret_key(session), payload)}"
    response.set_cookie(COOKIE, value, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=secure, path="/")


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def cookie_valid(session: Session, value: str | None) -> bool:
    if not value or "." not in value:
        return False
    payload, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(_sign(secret_key(session), payload), sig):
        return False
    try:
        return int(payload) > time.time()
    except ValueError:
        return False


def bearer_valid(header: str | None) -> bool:
    if not header or not settings.postdesk_api_token:
        return False
    scheme, _, token = header.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(token.strip(), settings.postdesk_api_token)


def is_authenticated(request: Request, session: Session) -> bool:
    return bearer_valid(request.headers.get("authorization")) or cookie_valid(session, request.cookies.get(COOKIE))


def require_auth(request: Request, session: Session = Depends(get_session)) -> str:
    """Who is acting: "admin" for the cookie, "api" for the bearer token."""
    if bearer_valid(request.headers.get("authorization")):
        return "api"
    if cookie_valid(session, request.cookies.get(COOKIE)):
        return "admin"
    raise HTTPException(status_code=401, detail="sign in first")
