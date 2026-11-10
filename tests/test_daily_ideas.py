from datetime import UTC, datetime

from sqlalchemy import select

from postdesk import control
from postdesk.board import cards, daily_ideas
from postdesk.config import config, settings
from postdesk.db.models import Card, Job, Notice


def test_daily_idea_time_idempotency_and_next_day(session, monkeypatch):
    monkeypatch.setattr(settings, "postdesk_daily_ideas", True)
    monkeypatch.setattr(config.account, "timezone", "America/New_York")
    assert not daily_ideas.ensure_due(session, now=datetime(2026, 11, 10, 12, 59, tzinfo=UTC))
    assert daily_ideas.ensure_due(session, now=datetime(2026, 11, 10, 13, 0, tzinfo=UTC))
    assert daily_ideas.ensure_due(session, now=datetime(2026, 11, 10, 13, 15, tzinfo=UTC))
    assert len(session.scalars(select(Card)).all()) == 1
    assert len(session.scalars(select(Job)).all()) == 1
    assert daily_ideas.ensure_due(session, now=datetime(2026, 11, 11, 13, 0, tzinfo=UTC))
    assert len(session.scalars(select(Card)).all()) == 2


def test_daily_idea_repairs_interrupted_enqueue_and_wakes_for_notification(session, monkeypatch):
    monkeypatch.setattr(settings, "postdesk_daily_ideas", True)
    monkeypatch.setattr(config.account, "timezone", "America/New_York")
    card = cards.file_card(session, title="Daily post idea", created_by="daily-ideas",
                           fingerprint="daily-idea:2026-07-01")
    now = datetime(2026, 7, 1, 12, tzinfo=UTC)  # daylight saving time
    assert daily_ideas.ensure_due(session, now=now)
    job = session.scalar(select(Job))
    job.status = "done"
    card.column = "Brief ready"
    notice = Notice(channel="telegram", kind="brief_ready", card_id=card.id, payload={},
                    created_at=now, attempts=0)
    session.add(notice)
    session.commit()
    assert daily_ideas.ensure_due(session, now=now)
    notice.sent_at = now
    session.commit()
    assert not daily_ideas.ensure_due(session, now=now)
    assert len(session.scalars(select(Job)).all()) == 1


def test_daily_idea_respects_opt_in_and_pause(session, monkeypatch):
    now = datetime(2026, 11, 10, 18, tzinfo=UTC)
    monkeypatch.setattr(settings, "postdesk_daily_ideas", False)
    assert not daily_ideas.ensure_due(session, now=now)
    monkeypatch.setattr(settings, "postdesk_daily_ideas", True)
    control.pause(session, by="test")
    assert not daily_ideas.ensure_due(session, now=now)
    assert session.scalars(select(Card)).all() == []
