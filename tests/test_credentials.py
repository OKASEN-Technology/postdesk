"""Credentials must be verified, encrypted, and never overwrite on failure."""
import pytest
from cryptography.fernet import Fernet

from postdesk import credentials, store
from postdesk.config import settings


def test_credentials_encrypted_and_refreshed(session, monkeypatch):
    monkeypatch.setattr(settings, "postdesk_credentials_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(credentials, "validate", lambda provider, key: True)
    credentials.save(session, "openai", "sk-private-example")
    stored = store.get(session, credentials.KEY)
    assert "sk-private-example" not in stored
    settings.openai_api_key = None
    store.refresh_config(session)
    assert settings.openai_api_key == "sk-private-example"


def test_rejected_key_does_not_replace_saved_key(session, monkeypatch):
    monkeypatch.setattr(settings, "postdesk_credentials_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(credentials, "validate", lambda provider, key: True)
    credentials.save(session, "openai", "original")
    def reject(provider, key):
        raise ValueError("rejected")
    monkeypatch.setattr(credentials, "validate", reject)
    with pytest.raises(ValueError, match="rejected"):
        credentials.save(session, "openai", "bad")
    assert credentials.read(session) == {"openai": "original"}


def test_missing_or_wrong_encryption_key_fails_closed(session, monkeypatch):
    monkeypatch.setattr(settings, "postdesk_credentials_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(credentials, "validate", lambda provider, key: True)
    credentials.save(session, "openai", "original")
    monkeypatch.setattr(settings, "postdesk_credentials_key", Fernet.generate_key().decode())
    with pytest.raises(ValueError, match="cannot be decrypted"):
        credentials.read(session)
    monkeypatch.setattr(settings, "postdesk_credentials_key", None)
    with pytest.raises(ValueError, match="not configured"):
        credentials.save(session, "openai", "other")
