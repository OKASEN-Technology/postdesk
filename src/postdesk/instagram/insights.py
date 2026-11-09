"""
How posts did. Once a day (`postdesk cron`, or the loop in `serve`):
account numbers for yesterday into `metric`, and the latest per-post
insights onto every post published in the last 30 days. A `_collected`
marker row makes a second run in the same day a no-op; a failing run writes
no marker and is retried next time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from postdesk.config import settings
from postdesk.db.models import Metric, SocialPost
from postdesk.instagram import graph as ig

DONE = "_collected"
SOURCE = "instagram"
POST_WINDOW = timedelta(days=30)
FEED_METRICS = "reach,saved,shares,likes,comments,total_interactions,views,profile_visits"
STORY_METRICS = "reach,replies,shares,total_interactions,views"
ACCOUNT_METRICS = ("reach", "profile_views", "accounts_engaged")


@dataclass
class Result:
    collected: bool = False
    rows: int = 0
    problems: list[str] = field(default_factory=list)
    error: str | None = None


def store(session: Session, day: date, rows: dict[tuple[str, str], float], *, now: datetime) -> None:
    """Upsert {(metric, dimension): value} for one day."""
    for (metric, dimension), value in rows.items():
        stmt = insert(Metric).values(day=day, source=SOURCE, metric=metric, dimension=dimension[:500], value=value,
                                     recorded_at=now)
        session.execute(stmt.on_conflict_do_update(constraint="uq_metric",
                                                   set_={"value": stmt.excluded.value, "recorded_at": now}))


def done(session: Session, day: date) -> bool:
    n = session.scalar(select(func.count()).select_from(Metric).where(Metric.day == day, Metric.source == SOURCE,
                                                                       Metric.metric == DONE))
    return (n or 0) > 0


def _bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tzinfo=UTC)
    return start, start + timedelta(days=1)


def post_insights(graph: ig.Graph, post: SocialPost, token: str) -> dict:
    metrics = STORY_METRICS if post.kind == "story" else FEED_METRICS
    try:
        data = graph.get(f"{post.ig_media_id}/insights", metric=metrics, access_token=token).get("data") or []
    except ig.InstagramError:
        # A metric this media type does not support fails the whole call;
        # reach is supported everywhere.
        data = graph.get(f"{post.ig_media_id}/insights", metric="reach", access_token=token).get("data") or []
    return {m["name"]: (m.get("values") or [{}])[0].get("value", 0) for m in data}


def instagram_source(session: Session, graph: ig.Graph, day: date, *, now: datetime
                     ) -> tuple[dict[tuple[str, str], float], list[str]]:
    """Account numbers for `day`, and today's insights onto recent posts.
    Returns (rows, per-post problems): one post failing is not the source
    failing."""
    account = ig.resolve_account(graph)
    token = account.token
    rows: dict[tuple[str, str], float] = {}
    profile = graph.get(account.ig_user_id, fields="followers_count,media_count", access_token=token)
    rows[("followers", "")] = profile.get("followers_count", 0)
    rows[("media", "")] = profile.get("media_count", 0)
    start, end = _bounds(day)
    problems: list[str] = []
    for metric in ACCOUNT_METRICS:
        try:
            data = graph.get(
                f"{account.ig_user_id}/insights",
                metric=metric,
                period="day",
                metric_type="total_value",
                since=int(start.timestamp()),
                until=int(end.timestamp()),
                access_token=token,
            ).get("data") or []
        except ig.InstagramError as exc:  # e.g. a metric Meta retired
            problems.append(f"account {metric}: {exc}")
            continue
        rows[(metric, "")] = sum((m.get("total_value") or {}).get("value", 0) for m in data)

    recent = session.scalars(
        select(SocialPost).where(
            SocialPost.status == "published",
            SocialPost.ig_media_id.is_not(None),
            SocialPost.published_at >= now - POST_WINDOW,
        )
    ).all()
    for post in recent:
        try:
            post.insights = post_insights(graph, post, token)
            post.insights_at = now
        except ig.InstagramError as exc:
            problems.append(f"post {post.id} ({post.title}): {exc}")
    return rows, problems


def configured() -> bool:
    return bool((settings.instagram_page_token and settings.instagram_user_id) or settings.instagram_access_token)


def collect(session: Session, graph: ig.Graph | None = None, *, now: datetime | None = None, log=print) -> Result:
    """Yesterday's account numbers and the recent posts' insights, once a
    day. Skipped when no Instagram token is configured."""
    now = now or datetime.now(UTC)
    day = (now - timedelta(days=1)).date()
    result = Result()
    if not configured():
        log("insights: no Instagram token configured; skipped")
        return result
    if done(session, day):
        return result
    try:
        rows, problems = instagram_source(session, graph or ig.Graph(), day, now=now)
    except ig.InstagramError as exc:
        session.rollback()
        result.error = str(exc)
        log(f"insights: {exc}")
        return result
    rows[(DONE, "")] = 1
    store(session, day, rows, now=now)
    session.commit()
    result.collected, result.rows, result.problems = True, len(rows) - 1, problems
    log(f"insights: {day}: {result.rows} numbers" + (f", {len(problems)} problems" if problems else ""))
    for p in problems:
        log(f"  {p}")
    return result
