"""
The one-way notifiers (postdesk.notify): which channels a notice goes to,
what Slack and the webhook are sent (and the webhook's signature), the
signed links for the two gate actions and the page that acts on them, and
`postdesk doctor`'s spend and Telegram lines. Slack and the webhook are
faked; nothing leaves the machine.
"""

import hashlib
import hmac
import json
import re
import time
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from postdesk import control, doctor, notify
from postdesk.board import posts
from postdesk.config import config, settings
from postdesk.db.models import AgentRun, Notice
from postdesk.main import create_app
from postdesk.notify import links
from postdesk.telegram import pairing
from tests import boardkit as kit
from tests import telegramkit as tk
from tests.test_board import _awaiting, _briefed


def _quiet(_m):
    pass


class Inbox:
    def __init__(self, status=200):
        self.got: list[httpx.Request] = []
        self.status = status

    def __call__(self, request):
        request.read()
        self.got.append(request)
        return httpx.Response(self.status, text="ok")

    def json(self, i=-1):
        return json.loads(self.got[i].content)


@pytest.fixture
def slack(monkeypatch):
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.test/T/B/x")
    monkeypatch.setattr(config, "notify", {"slack": {}})


def test_nothing_is_queued_without_a_channel(session):
    kit.card(session)
    assert session.scalars(select(Notice)).all() == []
    assert notify.configured() == {}


def test_a_notifier_needs_its_table_and_its_secret(monkeypatch):
    monkeypatch.setattr(config, "notify", {"slack": {}, "webhook": {}, "email": {"to": "me@bakes.example"},
                                           "pager": {}})
    assert notify.configured() == {}
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.test/x")
    monkeypatch.setattr(settings, "smtp_from", "desk@bakes.example")
    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    assert sorted(notify.configured()) == ["email", "slack"]
    monkeypatch.setattr(config, "notify", {"slack": {"enabled": False}})
    assert notify.configured() == {}
    with pytest.raises(notify.NotifyError, match="not set up"):
        notify.test("slack")


def test_slack_gets_the_brief_with_a_signed_approve_link(session, slack):
    c = _briefed(session)
    notify.emit(session, "brief_ready", c)  # what the partner's turn leaves (board/transitions.py)
    session.commit()
    inbox = Inbox()
    assert notify.dispatch_one_way(session, client=httpx.Client(transport=httpx.MockTransport(inbox)),
                                   log=_quiet) == 2  # filed, then the brief
    assert str(inbox.got[0].url) == "https://hooks.slack.test/T/B/x"
    text = inbox.json()["text"]
    assert text.startswith(f"*Brief ready: {c.title}*") and "A carousel on the 72 hours." in text
    assert f"<https://postdesk.example/cards/{c.id}|Open the card>" in text
    link = re.search(r"<([^|<>]+)\|Approve>", text).group(1)
    assert link.startswith("https://postdesk.example/act/")
    assert links.verify(session, link.rsplit("/", 1)[1]) == (c.id, "approve")
    assert notify.dispatch_one_way(session, client=httpx.Client(transport=httpx.MockTransport(inbox)),
                                   log=_quiet) == 0


def test_events_filter_and_a_failure_is_tried_again(session, slack, monkeypatch):
    monkeypatch.setattr(config, "notify", {"slack": {"events": ["live", "on_hold"]}})
    kit.card(session)
    assert session.scalars(select(Notice)).all() == []
    c = _awaiting(session)
    from postdesk.board import transitions

    transitions.hold(session, c, actor="partner", note="the renderer is down")
    (n,) = session.scalars(select(Notice)).all()
    assert (n.channel, n.kind) == ("slack", "on_hold")
    down = httpx.Client(transport=httpx.MockTransport(Inbox(status=500)))
    assert notify.dispatch_one_way(session, client=down, log=_quiet) == 0
    session.refresh(n)
    assert n.attempts == 1 and n.sent_at is None and "Slack answered 500" in n.error
    inbox = Inbox()
    assert notify.dispatch_one_way(session, client=httpx.Client(transport=httpx.MockTransport(inbox)),
                                   log=_quiet) == 1
    assert "the renderer is down" in inbox.json()["text"]


def test_the_webhook_body_is_signed(session, monkeypatch):
    monkeypatch.setattr(settings, "notify_webhook_url", "https://hooks.bakes.example/postdesk")
    monkeypatch.setattr(settings, "postdesk_secret_key", "s3cret")
    monkeypatch.setattr(config, "notify", {"webhook": {}})
    c = _awaiting(session)
    inbox = Inbox()
    notify.dispatch_one_way(session, client=httpx.Client(transport=httpx.MockTransport(inbox)), log=_quiet)
    req = next(r for r in inbox.got if json.loads(r.content)["event"] == "awaiting_approval")
    body = json.loads(req.content)
    assert body["title"] == f"Ready to schedule: {c.title}" and body["card_id"] == str(c.id)
    assert "Recommended time" in body["text"]
    (label,) = [k for k in body["links"] if k.startswith("Schedule for ")]
    want = hmac.new(b"s3cret", req.content, hashlib.sha256).hexdigest()
    assert req.headers["x-postdesk-signature"] == f"sha256={want}"
    assert links.verify(session, body["links"][label].rsplit("/", 1)[1]) == (c.id, "schedule")


def test_a_channel_turned_off_drops_what_it_had_waiting(session, slack, monkeypatch):
    kit.card(session)
    monkeypatch.setattr(config, "notify", {})
    notify.dispatch_one_way(session, log=_quiet)
    (n,) = session.scalars(select(Notice)).all()
    assert n.sent_at is not None and n.error == "dropped: the channel is off"


# ---------------------------------------------------------------- signed links


def test_a_link_is_for_one_card_one_action_and_expires(session):
    c = _briefed(session)
    tok = links.token(session, c.id, "approve")
    assert links.verify(session, tok) == (c.id, "approve")
    body, sig = tok.split(".")
    other = links.token(session, c.id, "schedule").split(".")[0]
    for bad in (f"{other}.{sig}", f"{body}.x{sig[1:]}", "nonsense", ""):
        with pytest.raises(links.LinkError, match="not valid"):
            links.verify(session, bad)
    old = links.token(session, c.id, "approve", now=time.time() - links.TTL_S - 5)
    with pytest.raises(links.LinkError, match="expired"):
        links.verify(session, old)
    with pytest.raises(links.LinkError):
        links.token(session, c.id, "drop")


def test_the_act_page_asks_first_and_the_post_approves_as_a_person(session):
    c = _briefed(session)
    tok = links.token(session, c.id, "approve")
    with TestClient(create_app(), base_url="https://postdesk.example") as http:
        r = http.get(f"/act/{tok}")
        assert r.status_code == 200 and "Approve the brief" in r.text and "<form method=\"post\">" in r.text
        session.refresh(c)
        assert c.column == "Brief ready"  # a link preview changes nothing
        r = http.post(f"/act/{tok}")
        assert r.status_code == 200 and "Approved" in r.text
        session.refresh(c)
        assert c.column == "Approved"
        from postdesk.board import cards

        assert cards.events(session, c)[-1].actor == "link"
        assert http.post(f"/act/{tok}").status_code == 409  # not twice
        assert http.get("/act/garbage").status_code == 400


def test_the_schedule_link_schedules_at_the_recommended_time(session):
    c = _awaiting(session)
    rec = posts.recommended_time(session, c)
    with TestClient(create_app(), base_url="https://postdesk.example") as http:
        r = http.post(f"/act/{links.token(session, c.id, 'schedule')}")
    assert r.status_code == 200 and "Scheduled for" in r.text
    session.refresh(c)
    assert c.column == "Scheduled" and posts.for_card(session, c.id)[0].publish_at == rec


# ---------------------------------------------------------------- spend, pause, doctor


def test_the_ceiling_notice_goes_once_a_month_for_each_ceiling(session, slack, monkeypatch):
    monkeypatch.setattr(config.llm, "monthly_usd", 1)
    session.add(AgentRun(agent="partner", model="claude-opus-5-5", cost_usd=1.5, status="ok",
                         started_at=datetime.now(UTC)))
    session.commit()
    assert "ceiling" in control.stopped(session)
    control.stopped(session)
    session.commit()
    assert [n.kind for n in session.scalars(select(Notice))] == ["spend_ceiling"]
    monkeypatch.setattr(config.llm, "monthly_usd", 1.25)
    control.stopped(session)
    session.commit()
    assert len(session.scalars(select(Notice)).all()) == 2
    inbox = Inbox()
    notify.dispatch_one_way(session, client=httpx.Client(transport=httpx.MockTransport(inbox)), log=_quiet)
    assert "*postdesk: the agents have stopped*" in inbox.json()["text"]
    assert "https://postdesk.example/settings" in inbox.json()["text"]


def test_doctor_shows_the_month_s_spend_and_the_bot(session, monkeypatch):
    session.add(AgentRun(agent="partner", model="claude-opus-5-5", cost_usd=2.5, status="ok",
                         started_at=datetime.now(UTC)))
    session.commit()
    check = doctor.check_spend()
    assert check.ok and not check.warn and "$2.50 of $30.00" in check.detail
    monkeypatch.setattr(config.llm, "monthly_usd", 2)
    assert doctor.check_spend().warn and "the agents have stopped" in doctor.check_spend().detail

    assert doctor.check_telegram().warn  # no token: fine, but said
    monkeypatch.setattr(settings, "telegram_bot_token", tk.TOKEN)
    fake = tk.FakeTelegram()
    check = doctor.check_telegram(tk.bot(fake))
    assert check.ok and check.warn and "no chat is paired" in check.detail
    pairing.link(session, pairing.new_code(session, tk.CHAT, "Sam"), by="admin")
    check = doctor.check_telegram(tk.bot(fake))
    assert check.ok and not check.warn and "paired with Sam" in check.detail
    fake.fail["getMe"] = "Unauthorized"
    assert not doctor.check_telegram(tk.bot(fake)).ok
