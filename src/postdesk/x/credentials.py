"""X credentials encrypted using the existing separate server-held key."""
from __future__ import annotations

import json

from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session

from postdesk import credentials, store
from postdesk.x.client import XClient, XCredentials

KEY = "encrypted_x_credentials"
FIELDS = ("api_key", "api_secret", "access_token", "access_secret")


def read(session: Session) -> XCredentials | None:
    encrypted = store.get(session, KEY)
    if not encrypted:
        return None
    try:
        value = json.loads(credentials.cipher().decrypt(encrypted.encode()))
        return XCredentials(**{key: value[key] for key in FIELDS})
    except (InvalidToken, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Saved X credentials cannot be decrypted. Restore the original storage key.") from exc


def save(session: Session, values: dict) -> dict:
    if not all(isinstance(values.get(k), str) and 0 < len(values[k].strip()) <= 4096 for k in FIELDS):
        raise ValueError("Enter all four X API credentials.")
    box = credentials.cipher()
    clean = {k: values[k].strip() for k in FIELDS}
    account = XClient(XCredentials(**clean)).whoami()
    store.put(session, KEY, box.encrypt(json.dumps(clean).encode()).decode())
    return account
