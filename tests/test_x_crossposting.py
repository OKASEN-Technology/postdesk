from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from postdesk import queue
from postdesk.db.models import SocialPost
from postdesk.x import credentials, publisher
from postdesk.x.client import XClient, XCredentials, XError
from tests.test_queue import _carousel


def test_x_client_signed_and_uncertain():
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == "/2/tweets":
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json={"data": {"id": "123", "username": "vigilfoundry"}})
    client = XClient(XCredentials("key", "secret", "token", "token-secret"),
                     client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.whoami()["username"] == "vigilfoundry"
    assert client.upload_image(b"picture") == "123"
    assert requests[0].headers["authorization"].startswith("OAuth ")
    with pytest.raises(XError) as error:
        client.create_post("caption", ["123"])
    assert error.value.uncertain


def test_crosspost_validation_and_cancellation(session, monkeypatch):
    monkeypatch.setattr(credentials, "read", lambda _: object())
    post = _carousel(session)
    now = datetime.now(UTC) + timedelta(hours=1)
    queue.approve(session, post.id, publish_at=now, approved_by="admin", crosspost_x=True)
    copy = session.scalars(select(SocialPost).where(SocialPost.channel == "x")).one()
    assert copy.caption == post.caption and copy.slides == post.slides
    assert [p.id for p in queue.due(session, now=now)] == [post.id]
    queue.unapprove(session, post.id)
    session.refresh(copy)
    assert copy.status == "rejected"
    queue.approve(session, post.id, publish_at=now, approved_by="admin", crosspost_x=True)
    assert len(session.scalars(select(SocialPost).where(SocialPost.channel == "x")).all()) == 2


def test_x_limits_and_duplicate_prevention(session, monkeypatch):
    monkeypatch.setattr(credentials, "read", lambda _: object())
    when = datetime.now(UTC) + timedelta(hours=1)
    too_many = _carousel(session, n=5)
    with pytest.raises(queue.QueueError, match="four images"):
        queue.approve(session, too_many.id, publish_at=when, approved_by="admin", crosspost_x=True)
    with pytest.raises(queue.QueueError, match="280"):
        _carousel(session, caption="x" * 281)
    post = _carousel(session)
    queue.approve(session, post.id, publish_at=when, approved_by="admin", crosspost_x=True)
    copy = session.scalars(select(SocialPost).where(SocialPost.channel == "x")).one()
    copy.status = "published"
    session.commit()
    queue.unapprove(session, post.id)
    with pytest.raises(queue.QueueError, match="already exists"):
        queue.approve(session, post.id, publish_at=when, approved_by="admin", crosspost_x=True)


def test_uncertain_x_post_is_not_retried(session, monkeypatch):
    monkeypatch.setattr(credentials, "read", lambda _: object())
    monkeypatch.setattr(publisher.storage, "asset_bytes", lambda _: b"picture")
    class FakeX:
        def __init__(self, auth): pass
        def upload_image(self, data): return "123"
        def create_post(self, text, ids): raise XError("Check X", uncertain=True)
    monkeypatch.setattr(publisher, "XClient", FakeX)
    post = _carousel(session)
    queue.approve(session, post.id, publish_at=datetime.now(UTC) - timedelta(minutes=1),
                  approved_by="admin", crosspost_x=True)
    attempted = publisher.publish_due(session, log=lambda *_: None)
    assert len(attempted) == 1 and attempted[0].status == "publishing"
    assert publisher.publish_due(session, log=lambda *_: None) == []
    session.refresh(post)
    assert post.status == "approved"


def test_published_instagram_can_be_scheduled_on_x_once(session, monkeypatch):
    monkeypatch.setattr(credentials, "read", lambda _: object())
    post = _carousel(session)
    post.status = "published"
    session.commit()
    old = SocialPost(channel="x", kind=post.kind, status="rejected", title=post.title,
                     caption=post.caption, slides=post.slides,
                     media={"instagram_post_id": str(post.id)}, created_by="admin",
                     created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    session.add(old)
    session.commit()
    copy = queue.crosspost_published_to_x(session, post.id, approved_by="admin")
    assert copy.status == "approved" and copy.caption == post.caption and copy.slides == post.slides
    assert post.status == "published"
    with pytest.raises(queue.QueueError, match="already exists"):
        queue.crosspost_published_to_x(session, post.id, approved_by="admin")


def test_only_published_instagram_can_be_recovered_to_x(session, monkeypatch):
    monkeypatch.setattr(credentials, "read", lambda _: object())
    post = _carousel(session)
    with pytest.raises(queue.QueueError, match="published Instagram"):
        queue.crosspost_published_to_x(session, post.id, approved_by="admin")


def test_x_error_explains_operation_and_redacts_credentials():
    def handler(request):
        return httpx.Response(403, json={"title": "Forbidden", "detail": "Access denied for token-secret"})
    client = XClient(XCredentials("key", "secret", "token", "token-secret"),
                     client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(XError) as error:
        client.upload_image(b"picture")
    assert "image upload" in str(error.value)
    assert "Forbidden" in str(error.value)
    assert "token-secret" not in str(error.value)
    assert not error.value.uncertain
