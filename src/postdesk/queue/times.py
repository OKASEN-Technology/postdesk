"""
Recommended publish times, in the account's timezone (`[account].timezone`)
from the `[schedule]` section of postdesk.toml:

- a feed post (image, carousel) at `feed_slot` on the next day with fewer
  than `max_feed_per_day` feed posts, never closer than `min_lead_hours`;
- a Story 15 minutes after the feed post that shares its `source_ref`,
  else `story_frame_gap_minutes` after the previous frame of a multi-frame
  Story, else `story_slot` on the next free day (`max_story_per_day`).

A recommendation is only a suggestion: nothing publishes until a person
approves, and the approver can change the time. A rejected or failed post
frees its day.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from postdesk.config import config
from postdesk.db.models import SocialPost

STORY_AFTER_FEED = timedelta(minutes=15)
# Statuses that hold a slot; a rejected or failed post frees its day.
HOLDS_SLOT = ("draft", "approved", "publishing", "published")
# When a day already has a post at the slot but room for more, the next
# one goes this much later.
SAME_DAY_SPACING = timedelta(hours=1)


def now_utc() -> datetime:
    return datetime.now(UTC)


def local(dt: datetime) -> datetime:
    return dt.astimezone(config.account.tz)


def _when(post: SocialPost) -> datetime | None:
    return post.published_at or post.publish_at


def _taken(session: Session, *, story: bool, since: date, exclude: uuid.UUID | None
           ) -> tuple[Counter[date], set[datetime]]:
    """Per local day, how many posts of this kind hold a slot from `since`
    on, and the exact times they hold."""
    stmt = select(SocialPost).where(SocialPost.status.in_(HOLDS_SLOT), SocialPost.channel == "instagram")
    stmt = stmt.where(SocialPost.kind == "story") if story else stmt.where(SocialPost.kind != "story")
    days: Counter[date] = Counter()
    times: set[datetime] = set()
    for p in session.execute(stmt).scalars():
        w = _when(p)
        if p.id == exclude or w is None:
            continue
        day = local(w).date()
        if day >= since:
            days[day] += 1
            times.add(w)
    return days, times


def recommend_publish_at(
    session: Session,
    kind: str,
    source_ref: str | None = None,
    *,
    now: datetime | None = None,
    exclude: uuid.UUID | None = None,
) -> datetime:
    """The next free slot for a post of this kind, as an aware UTC datetime."""
    sched = config.schedule
    now = now or now_utc()
    earliest = now + sched.min_lead
    story = kind == "story"
    if story and source_ref:
        feed = session.execute(
            select(SocialPost)
            .where(SocialPost.source_ref == source_ref, SocialPost.kind != "story",
                   SocialPost.status.in_(HOLDS_SLOT))
            .order_by(SocialPost.created_at.desc())
        ).scalars()
        for p in feed:
            w = _when(p)
            if w is not None and w + STORY_AFTER_FEED >= earliest:
                return w + STORY_AFTER_FEED
        frames = [_when(p) for p in session.execute(
            select(SocialPost).where(SocialPost.source_ref == source_ref, SocialPost.kind == "story",
                                     SocialPost.status.in_(HOLDS_SLOT))
        ).scalars() if p.id != exclude and _when(p) is not None]
        if frames and max(frames) + sched.story_frame_gap >= earliest:
            return max(frames) + sched.story_frame_gap

    slot: time = sched.story_time if story else sched.feed_time
    per_day = sched.max_story_per_day if story else sched.max_feed_per_day
    tz = config.account.tz
    day = local(earliest).date()
    taken_days, taken_times = _taken(session, story=story, since=day, exclude=exclude)
    while True:
        if taken_days[day] < per_day:
            candidate = datetime.combine(day, slot, tzinfo=tz)
            # Room on this day: the slot, or the next free hour after it.
            for _ in range(per_day):
                if candidate.astimezone(UTC) not in taken_times:
                    break
                candidate += SAME_DAY_SPACING
            if candidate >= earliest and candidate.astimezone(UTC) not in taken_times:
                return candidate.astimezone(UTC)
        day += timedelta(days=1)


def suggest_times(session: Session, *, now: datetime | None = None, overwrite: bool = False) -> list[SocialPost]:
    """Give drafts a recommended publish_at: those without one, or every
    draft with `overwrite`. Feed posts are placed first, oldest first, so
    each Story can follow its feed post. Approved posts are never touched."""
    drafts = session.execute(
        select(SocialPost).where(SocialPost.status == "draft").order_by(SocialPost.created_at)
    ).scalars()
    todo = [p for p in drafts if overwrite or p.publish_at is None]
    for p in todo:
        p.publish_at = None
    session.flush()
    todo.sort(key=lambda p: p.kind == "story")
    for p in todo:
        p.publish_at = recommend_publish_at(session, p.kind, p.source_ref, now=now, exclude=p.id)
        p.updated_at = now_utc()
        session.flush()
    session.commit()
    return todo


def within_publisher_hours(now: datetime | None = None) -> bool:
    """Whether the publisher may post now (`[schedule].publisher_hours`,
    local time)."""
    start, end = config.schedule.publisher_window
    t = local(now or now_utc()).time()
    return start <= t <= end


def next_publisher_window(now: datetime | None = None) -> datetime:
    """When the publisher next opens, for the `serve` loop's log line."""
    start, _ = config.schedule.publisher_window
    here = local(now or now_utc())
    opens = datetime.combine(here.date(), start, tzinfo=config.account.tz)
    if opens <= here:
        opens += timedelta(days=1)
    return opens.astimezone(UTC)
