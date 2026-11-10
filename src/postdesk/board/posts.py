"""
A card's posts in the queue: which drafts belong to it, the hand-made ones
that belong to none yet, linking them, and the Schedule gate's approval of
them at a time. `parse_when` reads the time a person types ("tomorrow
18:00", "fri 7:30", an ISO time) in the account's timezone.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from postdesk import queue
from postdesk.config import config
from postdesk.db.models import Card, SocialPost
from postdesk.queue.times import STORY_AFTER_FEED, now_utc

MIN_AHEAD = timedelta(minutes=5)
MAX_AHEAD = timedelta(days=60)
# How far back a hand-made draft with no card is still filed on the board.
FILE_WINDOW = timedelta(days=7)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ACTIVE = ("draft", "approved", "publishing", "published", "failed")


def feed_first(posts: list[SocialPost]) -> list[SocialPost]:
    return sorted(posts, key=lambda p: (p.kind == "story", p.created_at))


def for_card(session: Session, card_id: uuid.UUID) -> list[SocialPost]:
    return feed_first(list(session.scalars(select(SocialPost).where(SocialPost.card_id == card_id))))


def drafts(session: Session, card_id: uuid.UUID) -> list[SocialPost]:
    return [p for p in for_card(session, card_id) if p.status == "draft"]


def unfiled(session: Session, *, now: datetime | None = None) -> list[dict]:
    """Recent drafts with no card yet, grouped the way they are filed: one
    group per source (a carousel and its Story), or per post with none."""
    now = now or now_utc()
    rows = session.scalars(
        select(SocialPost)
        .where(SocialPost.status == "draft", SocialPost.channel == "instagram",
               SocialPost.card_id.is_(None), SocialPost.created_at >= now - FILE_WINDOW)
        .order_by(SocialPost.created_at)
    ).all()
    groups: dict[str, list[SocialPost]] = defaultdict(list)
    for p in rows:
        groups[p.source_ref or f"post:{p.id}"].append(p)
    return [{"source_ref": ref, "title": feed_first(ps)[0].title, "posts": feed_first(ps)}
            for ref, ps in groups.items()]


def link(session: Session, card: Card, post_ids: list[uuid.UUID]) -> list[SocialPost]:
    posts = []
    for pid in post_ids:
        post = session.get(SocialPost, pid)
        if post is None:
            raise LookupError(f"post {pid} not found")
        if post.card_id not in (None, card.id):
            raise queue.QueueError(f"post {pid} already belongs to card {post.card_id}")
        posts.append(post)
    for p in posts:
        p.card_id = card.id
        p.updated_at = now_utc()
    session.commit()
    return feed_first(posts)


def replace_drafts(session: Session, card: Card, keep: list[SocialPost]) -> int:
    """Reject the card's older drafts (and failed posts) once a new set is
    made, so the editor and the queue see only the current one."""
    keep_ids = {p.id for p in keep}
    n = 0
    for p in for_card(session, card.id):
        if p.id not in keep_ids and p.status in ("draft", "failed"):
            p.status, p.updated_at = "rejected", now_utc()
            p.error = (f"replaced by a new draft ({p.error})" if p.error else "replaced by a new draft")
            n += 1
    session.commit()
    return n


def parse_when(text: str, *, now: datetime | None = None) -> datetime | None:
    """A time a person typed: empty -> None (the recommended time); an ISO
    date-time (the account's timezone unless it has one); or `[today |
    tomorrow | mon…sun] [HH:MM]` / `HH:MM`, in the account's timezone, the
    feed slot if no time is given. Raises QueueError on anything else, or
    on a time in the past or more than 60 days out."""
    now = now or now_utc()
    tz = config.account.tz
    text = (text or "").strip().lower()
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text.upper())
    except ValueError:
        when = None
    if when is not None:
        when = when if when.tzinfo else when.replace(tzinfo=tz)
    else:
        m = re.fullmatch(r"(?:(today|tomorrow|[a-z]{3})[a-z]*)?\s*(?:at\s*)?(?:(\d{1,2}):?(\d{2})?\s*(am|pm)?)?", text)
        if not m or not (m.group(1) or m.group(2)):
            raise queue.QueueError(f"can't read {text!r} as a time: try 'tomorrow 18:00', 'fri 07:30' or "
                                   "2026-10-20T18:00")
        day_word, hh, mm, ampm = m.groups()
        local_now = now.astimezone(tz)
        if hh is None:
            at = config.schedule.feed_time
        else:
            h = int(hh) % 12 + (12 if ampm == "pm" else 0) if ampm else int(hh)
            if not (0 <= h <= 23 and 0 <= int(mm or 0) <= 59):
                raise queue.QueueError(f"{text!r} isn't a time of day")
            at = time(h, int(mm or 0))
        day = local_now.date()
        if day_word == "tomorrow":
            day += timedelta(days=1)
        elif day_word and day_word != "today":
            if day_word not in DAYS:
                raise queue.QueueError(f"can't read {day_word!r} as a day")
            day += timedelta(days=(DAYS.index(day_word) - day.weekday()) % 7)
        when = datetime.combine(day, at, tzinfo=tz)
        if not day_word and when < now + MIN_AHEAD:
            when += timedelta(days=1)  # a bare time already past today means tomorrow
        elif day_word in DAYS and when < now + MIN_AHEAD:
            when += timedelta(days=7)
    if when < now + MIN_AHEAD:
        raise queue.QueueError(f"{when:%Y-%m-%d %H:%M} has passed (or is under 5 minutes away)")
    if when > now + MAX_AHEAD:
        raise queue.QueueError("that's more than 60 days out; schedule it nearer the time")
    return when


def recommended_time(session: Session, card: Card, *, now: datetime | None = None) -> datetime | None:
    """When the card's feed post (else its first Story frame) would go out
    if scheduled without a time."""
    now = now or now_utc()
    for p in drafts(session, card.id):
        if p.publish_at and p.publish_at >= now + config.schedule.min_lead:
            return p.publish_at
        return queue.recommend_publish_at(session, p.kind, p.source_ref, now=now, exclude=p.id)
    return None


def schedule(session: Session, card: Card, *, when: datetime | None, approved_by: str,
             now: datetime | None = None) -> list[SocialPost]:
    """Approve the card's drafts: the feed post at `when` (else its
    recommended time, refreshed if that has passed), its Story 15 minutes
    after it, the frames of a Story-only post a frame gap apart, in order.
    Approved posts are left as they are, so a re-run is harmless."""
    now = now or now_utc()
    posts = for_card(session, card.id)
    todo = [p for p in posts if p.status == "draft"]
    if not todo:
        if any(p.status in ("approved", "publishing", "published") for p in posts):
            return posts
        raise queue.QueueError("no draft is linked to this card")
    feed_at: datetime | None = None
    story_at: datetime | None = None
    for p in todo:
        if p.kind == "story" and feed_at is not None:
            at = feed_at + STORY_AFTER_FEED
        elif p.kind == "story" and story_at is not None:
            at = story_at + config.schedule.story_frame_gap
        elif when is not None:
            at = when
        elif p.publish_at and p.publish_at >= now + config.schedule.min_lead:
            at = p.publish_at
        else:
            at = queue.recommend_publish_at(session, p.kind, p.source_ref, now=now, exclude=p.id)
        queue.approve(session, p.id, publish_at=at, approved_by=approved_by)
        if p.kind != "story":
            feed_at = at
        else:
            story_at = at
    return for_card(session, card.id)
