"""File a single daily idea for the partner, without crossing approval gates."""
from __future__ import annotations

from datetime import UTC, datetime, time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from postdesk import control, jobs
from postdesk.board import cards
from postdesk.config import config, settings
from postdesk.db.models import Card, Job, Notice

REASON = (
    "Suggest one original social media post idea for this account and its audience. "
    "Use the account's saved voice and verified company context; choose an angle different from recent posts. "
    "Provide a concise hook, the core idea and a practical visual direction. "
    "Do not invent product capabilities, launch status, customer claims or statistics. "
    "This is a suggestion for human review, not permission to publish."
)


def ensure_due(session: Session, *, now: datetime | None = None) -> bool:
    """File today's idea after 08:00 account time; return whether the web worker should wake.

    A unique date fingerprint prevents duplicates across cron retries. If a process
    dies between filing and enqueueing, the next run repairs the missing job.
    """
    if not settings.postdesk_daily_ideas or control.stopped(session):
        return False
    local = (now or datetime.now(UTC)).astimezone(config.account.tz)
    if local.time() < time(8):
        return False
    fingerprint = f"daily-idea:{local.date().isoformat()}"
    card = cards.by_fingerprint(session, fingerprint)
    if card is None:
        recent = session.scalars(select(Card).where(Card.closed_at.is_(None))
                                 .order_by(Card.created_at.desc()).limit(12)).all()
        previous = "\n".join(f"- {c.title}: {(c.brief_md or '')[:250]}" for c in recent)
        try:
            card = cards.file_card(
                session, title="Daily post idea", created_by="daily-ideas", fingerprint=fingerprint,
                source_ref=fingerprint,
                body_md="Develop a fresh idea based on the account's saved company context. "
                        "Avoid repeating these recent posts and briefs:\n" + (previous or "None yet."),
                labels=["daily idea"], reason="daily idea suggestion",
            )
        except IntegrityError:
            session.rollback()
            card = cards.by_fingerprint(session, fingerprint)
            if card is None:
                raise
    if card.closed_at is None and card.column == "Planned":
        existing = session.scalar(select(Job.id).where(Job.card_id == card.id, Job.kind == "partner").limit(1))
        if existing is None:
            jobs.enqueue(session, "partner", card.id, {"reason": REASON})
    active = session.scalar(select(Job.id).where(Job.card_id == card.id, Job.status.in_(["queued", "running"]))
                            .limit(1))
    pending = session.scalar(select(Notice.id).where(Notice.card_id == card.id, Notice.sent_at.is_(None)).limit(1))
    return active is not None or pending is not None
