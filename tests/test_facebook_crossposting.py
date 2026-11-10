from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from postdesk import queue
from postdesk.db.models import SocialPost
from postdesk.facebook import credentials, publisher
from postdesk.instagram.graph import Graph
from tests.test_queue import _carousel


def _enabled(monkeypatch):
    monkeypatch.setattr(credentials, "read", lambda _: {"page_id": "123", "page_token": "secret"})


def test_facebook_is_automatically_scheduled_and_cancelled(session, monkeypatch):
    _enabled(monkeypatch)
    post = _carousel(session)
    queue.approve(session, post.id, publish_at=datetime.now(UTC) + timedelta(hours=1), approved_by="admin")
    copy = session.scalars(select(SocialPost).where(SocialPost.channel == "facebook")).one()
    assert copy.caption == post.caption and copy.slides == post.slides and copy.status == "approved"
    queue.unapprove(session, post.id)
    assert copy.status == "rejected"


def test_facebook_waits_for_instagram_and_publishes_same_images(session, monkeypatch):
    _enabled(monkeypatch)
    calls = []
    def respond(request):
        calls.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={"id": str(len(calls))})
    graph = Graph(client=httpx.Client(transport=httpx.MockTransport(respond)))
    post = _carousel(session)
    queue.approve(session, post.id, publish_at=datetime.now(UTC) - timedelta(minutes=1), approved_by="admin")
    assert publisher.publish_due(session, graph=graph) == [] and not calls
    post.status = "published"
    session.commit()
    result = publisher.publish_due(session, graph=graph)
    assert len(result) == 1 and result[0].status == "published"
    assert len([path for path, _ in calls if path.endswith("/photos")]) == len(post.slides)
    assert calls[-1][0].endswith("/feed") and calls[-1][1]["message"] == post.caption
    assert publisher.publish_due(session, graph=graph) == []


def test_facebook_uncertain_publish_is_not_retried(session, monkeypatch):
    _enabled(monkeypatch)
    def respond(request):
        if request.url.path.endswith("/feed"):
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json={"id": "photo"})
    graph = Graph(client=httpx.Client(transport=httpx.MockTransport(respond)))
    post = _carousel(session)
    queue.approve(session, post.id, publish_at=datetime.now(UTC) - timedelta(minutes=1), approved_by="admin")
    post.status = "published"
    session.commit()
    result = publisher.publish_due(session, graph=graph)
    assert result[0].status == "publishing" and "Check the Page" in result[0].error
    assert publisher.publish_due(session, graph=graph) == []


def test_facebook_token_must_belong_to_page():
    graph = Graph(client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"id": "123", "name": "Vigil"}))))
    assert credentials.verify({"page_id": "123", "page_token": "token"}, graph)["name"] == "Vigil"
    with pytest.raises(ValueError, match="does not belong"):
        credentials.verify({"page_id": "456", "page_token": "token"}, graph)


def test_instagram_cannot_be_scheduled_without_facebook(session, monkeypatch):
    monkeypatch.setattr(credentials, "read", lambda _: None)
    post = _carousel(session)
    with pytest.raises(queue.QueueError, match="Connect Facebook"):
        queue.approve(session, post.id, publish_at=datetime.now(UTC), approved_by="admin")
    assert post.status == "draft"


def test_facebook_cannot_be_cancelled_independently(session, monkeypatch):
    _enabled(monkeypatch)
    post = _carousel(session)
    queue.approve(session, post.id, publish_at=datetime.now(UTC), approved_by="admin")
    copy = session.scalars(select(SocialPost).where(SocialPost.channel == "facebook")).one()
    with pytest.raises(queue.QueueError, match="Unschedule the Instagram"):
        queue.unapprove(session, copy.id)
    session.rollback()
    with pytest.raises(queue.QueueError, match="Unschedule the Instagram"):
        queue.reject(session, copy.id)


def test_unsupported_reel_is_blocked_before_instagram(session, monkeypatch):
    _enabled(monkeypatch)
    post = _carousel(session)
    post.kind = "reel"
    with pytest.raises(queue.QueueError):
        queue.approve(session, post.id, publish_at=datetime.now(UTC), approved_by="admin")
    assert post.status == "draft"


def test_card_can_unschedule_paired_posts_and_schedule_again(session, monkeypatch):
    from postdesk.board import posts, transitions
    from tests.test_board import _awaiting

    _enabled(monkeypatch)
    card = _awaiting(session)
    transitions.schedule(session, card, actor="admin")
    assert sum(p.channel == "facebook" for p in posts.for_card(session, card.id)) == 2
    transitions.unschedule(session, card, actor="admin")
    assert all(p.status == "draft" for p in posts.for_card(session, card.id) if p.channel == "instagram")
    assert all(p.status == "rejected" for p in posts.for_card(session, card.id) if p.channel == "facebook")
    transitions.schedule(session, card, actor="admin")
    assert sum(p.channel == "facebook" and p.status == "approved" for p in posts.for_card(session, card.id)) == 2


def test_legacy_scheduled_instagram_is_blocked_before_publish(session, monkeypatch):
    from postdesk import instagram as ig

    monkeypatch.setattr(credentials, "read", lambda _: None)
    monkeypatch.setattr(ig, "resolve_account", lambda graph: object())
    post = _carousel(session)
    post.status = "approved"
    post.publish_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()
    attempted = queue.publish_due(session, object(), log=lambda _: None)
    assert attempted == [post]
    assert post.status == "failed" and "scheduled together" in post.error
