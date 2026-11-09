"""Insights (postdesk.instagram.insights): account numbers into `metric`,
per-post numbers onto recent posts, once a day."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from postdesk.db.models import Metric, SocialPost
from postdesk.instagram import graph as ig
from postdesk.instagram import insights

NOW = datetime(2031, 3, 8, 6, 0, tzinfo=UTC)
DAY = date(2031, 3, 7)


class StubGraph:
    """A Graph whose `get` answers in memory (insights only reads)."""

    def __init__(self, fail_media=()):
        self.fail_media = set(fail_media)

    def get(self, path, **params):
        if path == "me/accounts":
            return {"data": [{"id": "P", "name": "Page", "access_token": "PAGE",
                              "instagram_business_account": {"id": "17841", "username": "bakery_tales"}}]}
        if path == "17841":
            return {"followers_count": 120, "media_count": 9}
        if path == "17841/insights":
            if params["metric"] == "accounts_engaged":
                raise ig.InstagramError("metric retired")
            return {"data": [{"name": params["metric"], "total_value": {"value": 300}}]}
        media = path.split("/")[0]
        if media in self.fail_media:
            raise ig.InstagramError("boom")
        if params["metric"] != "reach" and media == "story-1":
            raise ig.InstagramError("unsupported metric")
        return {"data": [{"name": m, "values": [{"value": 7}]} for m in params["metric"].split(",")]}


def _published(session, media_id, kind="carousel", days_ago=2):
    p = SocialPost(kind=kind, status="published", title=media_id, slides=[], ig_media_id=media_id,
                   published_at=NOW - timedelta(days=days_ago), created_at=NOW, updated_at=NOW)
    session.add(p)
    session.commit()
    return p


def test_instagram_account_and_post_insights(session):
    feed = _published(session, "media-1")
    story = _published(session, "story-1", kind="story")
    old = _published(session, "media-old", days_ago=40)
    broken = _published(session, "media-broken")

    rows, problems = insights.instagram_source(session, StubGraph(fail_media={"media-broken"}), DAY, now=NOW)
    assert rows[("followers", "")] == 120 and rows[("reach", "")] == 300
    assert ("accounts_engaged", "") not in rows and any("accounts_engaged" in p for p in problems)
    assert feed.insights["saved"] == 7 and feed.insights_at == NOW
    assert story.insights == {"reach": 7}  # fell back to reach
    assert old.insights is None  # outside the 30-day window
    assert broken.insights is None and any("media-broken" in p for p in problems)


def test_collect_runs_once_a_day_and_retries_after_a_failure(session):
    logs = []
    _published(session, "media-1")

    class Down(StubGraph):
        def get(self, path, **params):
            raise ig.InstagramError("(#190) token expired")

    r = insights.collect(session, Down(), now=NOW, log=logs.append)
    assert r.error and "token expired" in r.error and not r.collected
    assert session.scalar(select(Metric.id).limit(1)) is None

    r = insights.collect(session, StubGraph(), now=NOW, log=logs.append)
    assert r.collected and r.rows >= 4
    assert insights.done(session, DAY)
    stored = {(m.metric, m.dimension): float(m.value) for m in session.scalars(select(Metric))}
    assert stored[("followers", "")] == 120 and stored[(insights.DONE, "")] == 1

    r = insights.collect(session, StubGraph(), now=NOW + timedelta(minutes=15), log=logs.append)
    assert not r.collected and r.error is None  # already done today


def test_collect_skips_without_a_token(session, monkeypatch):
    from postdesk.config import settings

    monkeypatch.setattr(settings, "instagram_access_token", None)
    r = insights.collect(session, StubGraph(), now=NOW, log=lambda *_: None)
    assert not r.collected and r.error is None


@pytest.mark.parametrize("kind,metrics", [("carousel", insights.FEED_METRICS), ("story", insights.STORY_METRICS)])
def test_post_metrics_depend_on_kind(session, kind, metrics):
    asked = []

    class Spy(StubGraph):
        def get(self, path, **params):
            asked.append(params.get("metric"))
            return super().get(path, **params)

    post = _published(session, "media-1", kind=kind)
    insights.post_insights(Spy(), post, "PAGE")
    assert asked[0] == metrics
