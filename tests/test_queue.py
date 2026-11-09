"""The queue (postdesk.queue): validation, the lifecycle, recommended
times in the account's timezone, and publishing against a fake Graph API."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from postdesk import queue
from postdesk.config import config
from postdesk.db.models import ContentAsset, SocialPost
from tests.conftest import FakeGraph, graph_for

LONDON = ZoneInfo("Europe/London")


def _asset(session, content_type="image/jpeg") -> str:
    a = ContentAsset(filename="slide.jpg", content_type=content_type, byte_size=10,
                     sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), created_at=datetime.now(UTC),
                     uploaded_by="test", provenance={"source": "upload"})
    session.add(a)
    session.commit()
    return str(a.id)


def _carousel(session, n=2, caption="A caption.", **kw) -> SocialPost:
    return queue.create_post(session, kind="carousel", title="carousel", caption=caption,
                             slides=[{"asset_id": _asset(session), "alt_text": f"Slide {i}"} for i in range(1, n + 1)],
                             **kw)


def test_create_validates_kind_count_and_jpeg(session):
    with pytest.raises(queue.QueueError, match="carousel takes"):
        _carousel(session, n=1)
    with pytest.raises(queue.QueueError, match="JPEG only"):
        queue.create_post(session, kind="image", title="png", slides=[{"asset_id": _asset(session, "image/png")}])
    with pytest.raises(queue.QueueError, match="no caption"):
        queue.create_post(session, kind="story", title="story", caption="hi", slides=[{"asset_id": _asset(session)}])
    with pytest.raises(queue.QueueError, match="not found"):
        queue.create_post(session, kind="image", title="missing", slides=[{"asset_id": str(uuid.uuid4())}])
    with pytest.raises(queue.QueueError, match="30"):
        _carousel(session, caption=" ".join(f"#t{i}" for i in range(31)))


def test_lifecycle_edit_approve_unapprove_reject(session):
    post = _carousel(session)
    assert post.status == "draft" and post.publish_at is not None

    post = queue.update_post(session, post.id, caption="New caption.", alt_texts=["One", "Two"], title="Renamed")
    assert post.caption == "New caption." and [s["alt_text"] for s in post.slides] == ["One", "Two"]
    assert post.title == "Renamed"

    with pytest.raises(queue.QueueError, match="timezone"):
        queue.approve(session, post.id, publish_at=datetime(2030, 1, 1), approved_by="admin")
    when = datetime.now(UTC) + timedelta(days=1)
    post = queue.approve(session, post.id, publish_at=when, approved_by="admin")
    assert (post.status, post.approved_by) == ("approved", "admin")

    # An approved post is immutable.
    with pytest.raises(queue.QueueError, match="needs draft"):
        queue.update_post(session, post.id, caption="Sneaky edit.")
    assert post not in queue.due(session)

    post = queue.unapprove(session, post.id)
    assert post.status == "draft" and post.approved_by is None
    post = queue.reject(session, post.id)
    assert post.status == "rejected"
    with pytest.raises(queue.QueueError):
        queue.approve(session, post.id, publish_at=when, approved_by="admin")


def test_a_reel_is_kept_in_the_schema_but_not_approvable(session):
    post = queue.create_post(session, kind="reel", title="reel", caption="Soon.",
                             slides=[{"asset_id": _asset(session)}])
    with pytest.raises(queue.QueueError, match="reels"):
        queue.approve(session, post.id, publish_at=datetime.now(UTC) + timedelta(days=1), approved_by="admin")


def test_publish_due_posts_only_due_approved(session):
    due_post = _carousel(session)
    later = _carousel(session)
    draft = _carousel(session)
    now = datetime.now(UTC)
    queue.approve(session, due_post.id, publish_at=now - timedelta(minutes=5), approved_by="admin")
    queue.approve(session, later.id, publish_at=now + timedelta(days=2), approved_by="admin")

    fake = FakeGraph()
    attempted = queue.publish_due(session, graph_for(fake), now=now, log=lambda *_: None)

    assert [p.id for p in attempted] == [due_post.id]
    for p in (due_post, later, draft):
        session.refresh(p)
    assert (due_post.status, due_post.ig_media_id, due_post.permalink) == (
        "published", "M1", "https://www.instagram.com/p/xyz/")
    assert later.status == "approved" and draft.status == "draft"
    items = [c for c in fake.created if c.get("is_carousel_item") == "true"]
    assert [c["alt_text"] for c in items] == ["Slide 1", "Slide 2"]
    assert items[0]["image_url"] == f"https://postdesk.example/media/{due_post.slides[0]['asset_id']}"
    carousel = next(c for c in fake.created if c.get("media_type") == "CAROUSEL")
    assert carousel["children"] == "C1,C2" and carousel["caption"] == "A caption."

    # Nothing left due: a second run is a no-op.
    assert queue.publish_due(session, graph_for(FakeGraph()), now=now, log=lambda *_: None) == []


def test_publish_due_respects_max_per_run(session, monkeypatch):
    monkeypatch.setattr(config.schedule, "max_per_run", 2)
    now = datetime.now(UTC)
    posts = [_carousel(session) for _ in range(3)]
    for i, p in enumerate(posts):
        queue.approve(session, p.id, publish_at=now - timedelta(minutes=10 - i), approved_by="admin")
    attempted = queue.publish_due(session, graph_for(FakeGraph()), now=now, log=lambda *_: None)
    assert [p.id for p in attempted] == [posts[0].id, posts[1].id]
    session.refresh(posts[2])
    assert posts[2].status == "approved"


def test_publish_due_failure_is_recorded_and_retryable(session):
    post = _carousel(session)
    queue.approve(session, post.id, publish_at=datetime.now(UTC) - timedelta(minutes=1), approved_by="admin")

    queue.publish_due(session, graph_for(FakeGraph(fail_publish=True)), log=lambda *_: None)
    session.refresh(post)
    assert post.status == "failed" and "Media upload has failed" in post.error

    post = queue.back_to_draft(session, post.id)
    assert post.status == "draft" and post.approved_by is None


def test_publish_due_refuses_a_localhost_media_url(session, monkeypatch):
    from postdesk.config import settings

    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8080")
    post = _carousel(session)
    queue.approve(session, post.id, publish_at=datetime.now(UTC) - timedelta(minutes=1), approved_by="admin")
    queue.publish_due(session, graph_for(FakeGraph()), log=lambda *_: None)
    session.refresh(post)
    assert post.status == "failed" and "not reachable by Instagram" in post.error


# A fixed future "now": Monday 3 March 2031, 10:00 in London (GMT, so UTC too).
REC_NOW = datetime(2031, 3, 3, 10, 0, tzinfo=UTC)


def _london(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=LONDON).astimezone(UTC)


def _queued(session, kind, ref, now=REC_NOW) -> SocialPost:
    n = 1 if kind == "story" else 2
    return queue.create_post(session, kind=kind, title=f"{kind} {ref}", caption="" if kind == "story" else "A caption.",
                             slides=[{"asset_id": _asset(session)} for _ in range(n)], source_ref=ref, now=now)


def test_drafts_get_one_feed_slot_a_day_and_stories_follow(session):
    a = _queued(session, "carousel", "rec:a")
    b = _queued(session, "carousel", "rec:b")
    story_b = _queued(session, "story", "rec:b")
    loose = _queued(session, "story", "rec:none")

    assert a.publish_at == _london(2031, 3, 3, 18)  # 18:00 local, same day
    assert b.publish_at == _london(2031, 3, 4, 18)  # next free day
    assert story_b.publish_at == b.publish_at + timedelta(minutes=15)
    assert loose.publish_at == _london(2031, 3, 3, 18, 15)

    # Too late for today's slot (2h lead), so the next free day.
    late = queue.recommend_publish_at(session, "image", now=REC_NOW.replace(hour=17), exclude=None)
    assert late == _london(2031, 3, 5, 18)

    # A rejected post frees its day.
    queue.reject(session, a.id)
    assert queue.recommend_publish_at(session, "image", now=REC_NOW) == a.publish_at


def test_slots_follow_the_account_timezone_across_dst(session, monkeypatch):
    # 1 April 2031 is British Summer Time: 18:00 London is 17:00 UTC.
    now = datetime(2031, 4, 1, 10, 0, tzinfo=UTC)
    post = _queued(session, "carousel", "rec:dst", now=now)
    assert post.publish_at == datetime(2031, 4, 1, 17, 0, tzinfo=UTC)
    assert post.publish_at.astimezone(LONDON).hour == 18

    monkeypatch.setattr(config.account, "timezone", "America/New_York")
    ny = queue.recommend_publish_at(session, "carousel", now=now, exclude=post.id)
    assert ny == datetime(2031, 4, 1, 22, 0, tzinfo=UTC)  # 18:00 EDT


def test_two_feed_posts_a_day_when_configured(session, monkeypatch):
    monkeypatch.setattr(config.schedule, "max_feed_per_day", 2)
    a = _queued(session, "carousel", "rec:a")
    b = _queued(session, "carousel", "rec:b")
    c = _queued(session, "carousel", "rec:c")
    assert a.publish_at == _london(2031, 3, 3, 18)
    assert b.publish_at == _london(2031, 3, 3, 19)  # same day, an hour later
    assert c.publish_at == _london(2031, 3, 4, 18)


def test_suggest_times_fills_missing_and_overwrites_drafts_only(session):
    feed = _queued(session, "carousel", "rec:c")
    story = _queued(session, "story", "rec:c")
    approved = _queued(session, "carousel", "rec:d")
    queue.approve(session, approved.id, publish_at=_london(2031, 3, 3, 18), approved_by="admin")
    for p in (feed, story):
        p.publish_at = None
    session.commit()

    filled = queue.suggest_times(session, now=REC_NOW)
    assert {p.id for p in filled} >= {feed.id, story.id}
    # The approved post holds Monday, so the draft moves to Tuesday, and its
    # Story follows it even though the Story was queued second.
    assert feed.publish_at == _london(2031, 3, 4, 18)
    assert story.publish_at == feed.publish_at + timedelta(minutes=15)
    assert approved.publish_at == _london(2031, 3, 3, 18)

    assert all(p.id != approved.id for p in queue.suggest_times(session, now=REC_NOW, overwrite=True))
    session.refresh(feed)
    assert feed.publish_at == _london(2031, 3, 4, 18)


def test_publisher_hours_are_local():
    assert queue.within_publisher_hours(datetime(2031, 3, 3, 6, 0, tzinfo=UTC))
    assert not queue.within_publisher_hours(datetime(2031, 3, 3, 5, 59, tzinfo=UTC))
    assert not queue.within_publisher_hours(datetime(2031, 3, 3, 21, 1, tzinfo=UTC))
    # In BST, 05:30 UTC is 06:30 London: open.
    assert queue.within_publisher_hours(datetime(2031, 7, 1, 5, 30, tzinfo=UTC))
    nxt = queue.next_publisher_window(datetime(2031, 3, 3, 22, 0, tzinfo=UTC))
    assert nxt == _london(2031, 3, 4, 6)
