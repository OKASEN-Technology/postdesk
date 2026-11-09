"""
Whether the agents may run now. Two things stop them:

- **paused**: `/pause` in Telegram (or the settings screen) stops the scout
  and every agent until `/resume`. The publisher keeps posting what is
  already approved: a person approved it.
- **the spend ceiling**: once this month's agent runs add up to
  `[llm].monthly_usd`, no agent starts. Their jobs wait in the queue,
  nothing is put On hold, and Telegram, the notifiers and the web UI say
  so once. Raising the ceiling, or the month turning, lets them go on.

`stopped` is what the job runner asks before it takes a job; `llm`
checks the ceiling again before each call, so a job already running stops
at its next call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from postdesk import llm, store
from postdesk.config import config

PAUSED = "agents_paused"
CEILING_NOTICE = "spend_ceiling_notice"


@dataclass
class Spend:
    month_usd: float
    ceiling_usd: float

    @property
    def over(self) -> bool:
        return self.month_usd >= self.ceiling_usd

    def line(self) -> str:
        return f"${self.month_usd:.2f} of ${self.ceiling_usd:.2f} this month"


def spend(session: Session, now: datetime | None = None) -> Spend:
    return Spend(llm.month_spend(session, now), float(config.llm.monthly_usd))


def paused(session: Session) -> dict | None:
    """{by, at} while paused, else None."""
    value = store.get(session, PAUSED)
    return value if isinstance(value, dict) else None


def pause(session: Session, *, by: str) -> dict:
    value = paused(session)
    if value is None:
        value = {"by": by, "at": datetime.now(UTC).isoformat()}
        store.put(session, PAUSED, value)
    return value


def resume(session: Session, *, by: str) -> bool:
    """True if it was paused."""
    if paused(session) is None:
        return False
    store.delete(session, PAUSED)
    return True


def ceiling_message(s: Spend) -> str:
    return (f"The agents have stopped: this month's runs cost ${s.month_usd:.2f}, which reaches the "
            f"${s.ceiling_usd:.2f} ceiling ([llm].monthly_usd). Nothing new is written until you raise the ceiling "
            "in Settings, or the month turns. What is already scheduled still posts.")


def stopped(session: Session, now: datetime | None = None) -> str | None:
    """Why no agent may start now, or None."""
    p = paused(session)
    if p is not None:
        return f"paused by {p.get('by')} (send /resume to start them again)"
    s = spend(session, now)
    if s.over:
        ceiling_reached(session, s, now=now)
        return ceiling_message(s)
    return None


def ceiling_reached(session: Session, s: Spend | None = None, *, now: datetime | None = None) -> None:
    """Say once, on every channel, that the ceiling stopped the agents (once
    a month for each ceiling: raising it and reaching it again says so again)."""
    from postdesk import notify

    now = now or datetime.now(UTC)
    s = s or spend(session, now)
    marker = f"{now:%Y-%m} at {s.ceiling_usd:.2f}"
    if store.get(session, CEILING_NOTICE) == marker:
        return
    notify.emit(session, "spend_ceiling", month_usd=round(s.month_usd, 2), ceiling_usd=s.ceiling_usd)
    store.put(session, CEILING_NOTICE, marker)
