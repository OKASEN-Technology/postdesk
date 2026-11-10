"""Encrypted AI credentials. The encryption key stays outside the database."""
from __future__ import annotations

import json

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from postdesk import store
from postdesk.config import settings

KEY = "encrypted_ai_credentials"
FIELDS = {"openai": "openai_api_key", "anthropic": "anthropic_api_key"}


def cipher() -> Fernet:
    if not settings.postdesk_credentials_key:
        raise ValueError("Credential storage is not configured. Set POSTDESK_CREDENTIALS_KEY on the server.")
    try:
        return Fernet(settings.postdesk_credentials_key.encode())
    except (ValueError, TypeError) as exc:
        raise ValueError("Credential storage key is invalid. Check POSTDESK_CREDENTIALS_KEY on the server.") from exc


def read(session: Session) -> dict:
    encrypted = store.get(session, KEY)
    if not encrypted:
        return {}
    try:
        value = json.loads(cipher().decrypt(encrypted.encode()))
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("Saved credentials cannot be decrypted. Restore the original credential storage key.") from exc
    if not isinstance(value, dict):
        raise ValueError("Saved credentials are invalid.")
    return {k: v for k, v in value.items() if k in FIELDS and isinstance(v, str)}


def refresh(session: Session) -> None:
    for provider, value in read(session).items():
        setattr(settings, FIELDS[provider], value)


def validate(provider: str, key: str) -> bool:
    if provider == "openai":
        url, headers = "https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"}
    elif provider == "anthropic":
        url, headers = "https://api.anthropic.com/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        raise ValueError("Unknown AI provider.")
    try:
        response = httpx.get(url, headers=headers, timeout=15, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise ValueError("Could not reach the provider. Try again later.") from exc
    if response.status_code in (401, 403):
        raise ValueError("The provider did not accept this key. Check it and try again.")
    if response.status_code != 200:
        raise ValueError("The provider could not verify this key right now. Try again later.")
    return True


def save(session: Session, provider: str, key: str) -> None:
    if provider not in FIELDS or not key or len(key) > 4096:
        raise ValueError("Enter a valid API key.")
    box = cipher()
    current = read(session)
    validate(provider, key)
    current[provider] = key
    store.put(session, KEY, box.encrypt(json.dumps(current).encode()).decode())
    refresh(session)
