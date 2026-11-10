"""
The `setting` table: small values the app keeps between runs (the
Telegram chat it is paired with, the agents' pause, the scout's last run)
and the settings screen's edits to `postdesk.toml` (`OVERRIDES`, laid over
the file by `refresh_config`). Never a secret: those stay in `.env`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from postdesk import config as config_mod
from postdesk.db.models import Setting

OVERRIDES = "config_overrides"


def get(session: Session, key: str, default=None):
    row = session.get(Setting, key)
    return row.value if row is not None else default


def put(session: Session, key: str, value, *, commit: bool = True) -> None:
    row = session.get(Setting, key)
    now = datetime.now(UTC)
    if row is None:
        session.add(Setting(key=key, value=value, updated_at=now))
    else:
        row.value, row.updated_at = value, now
    if commit:
        session.commit()


def delete(session: Session, key: str, *, commit: bool = True) -> None:
    row = session.get(Setting, key)
    if row is not None:
        session.delete(row)
    if commit:
        session.commit()


def overrides(session: Session) -> dict:
    value = get(session, OVERRIDES, {})
    return value if isinstance(value, dict) else {}


def refresh_config(session: Session, *, log=None) -> bool:
    """Lay the stored overrides over postdesk.toml if they changed since the
    last time (the web process and a separate worker each call this).
    True if something was applied."""
    from postdesk import credentials

    credentials.refresh(session)
    stored = overrides(session)
    if stored == config_mod.applied_overrides():
        return False
    try:
        config_mod.apply_overrides(stored)
    except ValueError as e:
        if log:
            log(f"settings: the saved settings no longer fit postdesk.toml, ignored: {e}")
        return False
    return True


def save_overrides(session: Session, patch: dict) -> dict:
    """Merge `patch` ({section: {key: value | None}}; None drops the key and
    the file's value comes back) into the stored overrides, check them, store
    them and apply them. Raises ValueError, storing nothing, on a bad value."""
    current = overrides(session)
    for section, keys in patch.items():
        if keys is None:
            current.pop(section, None)
            continue
        if config_mod.EDITABLE.get(section, ()) is None:
            current[section] = keys
            continue
        if not isinstance(keys, dict):
            raise ValueError(f"[{section}] must be a table")
        merged = dict(current.get(section) or {})
        for k, v in keys.items():
            if v is None:
                merged.pop(k, None)
            else:
                merged[k] = v
        if merged:
            current[section] = merged
        else:
            current.pop(section, None)
    config_mod.check_overrides(current)
    put(session, OVERRIDES, current)
    config_mod.apply_overrides(current)
    return current
