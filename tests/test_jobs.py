"""
The job runner (postdesk.jobs): one running job per card, transient
failures retried with backoff, every other failure (a refusal, a reply cut
off, the spending ceiling) putting the card On hold once without a retry;
and the whole path end to end, from an RSS item to a card awaiting the
admin's approval and then live, with Claude, the feed and the renderer
faked.
"""

from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from postdesk import jobs, llm
from postdesk.board import cards, posts, scout
from postdesk.config import config
from postdesk.db import session as db_session
from postdesk.db.models import Card, Job
from postdesk.main import create_app
from postdesk.maker.render import RenderError, RendererUnavailable
from tests import boardkit as kit


@pytest.fixture(autouse=True)
def _voice(monkeypatch):
    monkeypatch.setattr(config.voice, "hashtags", ["#homebaking", "#sourdough", "#weekendbake"])
    monkeypatch.setattr(config.voice, "max_hashtags", 3)


def _quiet(_m):
    pass


def test_renderer_outage_retries_but_invalid_render_does_not():
    assert jobs.is_transient(RendererUnavailable("gateway unavailable"))
    assert not jobs.is_transient(RenderError("invalid slide"))


def test_one_running_job_per_card(session):
    a, b = kit.card(session), kit.card(session)
    jobs.enqueue(session, "partner", a.id)
    jobs.enqueue(session, "review", a.id)
    jobs.enqueue(session, "partner", b.id)
    first = jobs.claim(session)
    assert first.card_id == a.id and first.status == "running" and first.attempts == 1
    second = jobs.claim(session)
    assert second.card_id == b.id  # a's other job waits for its running one
    assert jobs.claim(session) is None

    # The index holds the rule even if two workers race past the check.
    other = session.scalars(select(Job).where(Job.card_id == a.id, Job.status == "queued")).one()
    with pytest.raises(IntegrityError):
        session.execute(update(Job).where(Job.id == other.id).values(status="running"))
        session.commit()
    session.rollback()


def test_a_job_locked_by_another_worker_is_skipped(session):
    a, b = kit.card(session), kit.card(session)
    ja = jobs.enqueue(session, "partner", a.id)
    jobs.enqueue(session, "partner", b.id)
    other = db_session.SessionLocal()
    try:
        other.execute(select(Job).where(Job.id == ja.id).with_for_update()).scalar_one()  # held, not committed
        got = jobs.claim(session)
        assert got.card_id == b.id
    finally:
        other.rollback()
        other.close()


def test_a_transient_failure_is_retried_with_backoff_then_gives_up(session, monkeypatch):
    c = kit.card(session)
    jobs.enqueue(session, "partner", c.id)

    def flaky(**_kw):
        raise httpx.ConnectError("no route")

    deps = kit.deps(partner=flaky)
    job = jobs.run(session, jobs.claim(session), deps, log=_quiet)
    assert job.status == "queued" and job.attempts == 1 and "ConnectError" in job.error
    assert job.run_after > jobs.now_utc() + timedelta(seconds=jobs.BACKOFF_S - 5)
    assert jobs.claim(session) is None  # not due yet
    assert c.column == "Planned"

    monkeypatch.setattr(jobs, "MAX_ATTEMPTS", 2)
    job.run_after = jobs.now_utc()
    session.commit()
    job = jobs.run(session, jobs.claim(session), deps, log=_quiet)
    assert job.status == "failed" and job.attempts == 2
    session.refresh(c)
    assert c.column == "On hold" and "the partner job failed: ConnectError: no route" in c.stuck_reason


@pytest.mark.parametrize("error, why", [
    (llm.LLMError("Claude declined to answer (refusal)"), "the partner's turn failed: Claude declined"),
    (llm.LLMError("the reply was cut off at max_tokens"), "the partner's turn failed: the reply was cut off"),
])
def test_a_refusal_or_a_cut_off_reply_holds_the_card_without_a_retry(session, error, why):
    c = kit.card(session)
    jobs.enqueue(session, "partner", c.id)
    calls = []

    def refuse(**kw):
        calls.append(kw)
        raise error

    done = jobs.run_pending(session, kit.deps(partner=refuse), log=_quiet)
    assert [j.status for j in done] == ["failed"] and len(calls) == 1
    session.refresh(c)
    assert c.column == "On hold" and c.stuck_reason.startswith(why)
    assert cards.thread(session, c)[-1].body_md.startswith("On hold:")


def test_the_ceiling_reached_mid_job_queues_it_again_without_holding_the_card(session):
    c = kit.card(session)
    jobs.enqueue(session, "partner", c.id)

    def over(**kw):
        raise llm.BudgetExceeded("this month's agent runs have cost $30.00, over the $30.00 ceiling")

    done = jobs.run_pending(session, kit.deps(partner=over), log=_quiet)
    assert [j.status for j in done] == ["queued"] and done[0].attempts == 0
    assert done[0].run_after > jobs.now_utc()
    session.refresh(c)
    assert c.column == "Planned" and c.stuck_reason is None


def test_the_real_call_refuses_on_a_refusal_or_max_tokens(monkeypatch):
    class Resp:
        def __init__(self, stop):
            self.stop_reason, self.content = stop, []
            self.usage = type("U", (), {"input_tokens": 1, "output_tokens": 1, "server_tool_use": None,
                                        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})()

    for stop, match in (("refusal", "the partner declined"), ("max_tokens", "reply was cut off")):
        monkeypatch.setattr(llm, "_create", lambda *a, _s=stop, **k: Resp(_s))
        monkeypatch.setattr(llm, "_client", lambda: None)
        with pytest.raises(llm.LLMError, match=match):
            llm.structured_call(agent="partner", system="s", messages=[{"role": "user", "content": "x"}],
                                schema={"type": "object"})


def test_a_job_on_a_card_that_moved_on_does_nothing(session):
    c = kit.card(session, column="Inbox")
    jobs.enqueue(session, "partner", c.id)
    call = kit.scripted(kit.partner_reply())
    (job,) = jobs.run_pending(session, kit.deps(partner=call), log=_quiet)
    assert job.status == "done" and call.calls == [] and c.column == "Inbox"


def test_a_job_left_running_by_a_dead_worker_is_queued_again(session):
    c = kit.card(session)
    jobs.enqueue(session, "partner", c.id)
    job = jobs.claim(session)
    job.started_at = jobs.now_utc() - timedelta(hours=1)
    session.commit()
    assert jobs.reset_stale(session) == 1
    session.refresh(job)
    assert job.status == "queued" and "stopped" in job.error


def test_from_an_rss_item_to_awaiting_approval_and_live(session, monkeypatch):
    """The definition of done, end to end: the scout files an RSS item; the
    partner briefs it; the admin approves; the maker makes it; the editor
    asks for one revision and passes it; the admin schedules; the publisher
    publishes and the sync moves the card to Live."""
    monkeypatch.setattr(config, "sources", {"bakes": {"type": "rss", "url": kit.FEED_URL}})
    items = kit.feed_items(1)
    items[0]["body"] = "The country loaf rested 72 hours and came out at 78% hydration."
    rendered = []
    feed_client = kit.feed_client(items)

    r = scout.run(session, client=feed_client, log=_quiet)
    (card,) = session.scalars(select(Card)).all()
    assert r.filed == [str(card.id)] and card.column == "Planned"

    editor_replies = [kit.review("revise", notes=[kit.caption_note()]), kit.review()]
    revisions = []

    def revision_call(**kw):
        feed = posts.drafts(session, card.id)[0]
        revisions.append(kw)
        return kit.revision(feed.id)

    deps = kit.deps(partner=kit.scripted(kit.partner_reply()), editor=kit.scripted(*editor_replies),
                    revision_call=revision_call, rendered=rendered)
    deps.fetch = feed_client

    jobs.run_pending(session, deps, log=_quiet)
    session.refresh(card)
    assert card.column == "Brief ready"

    with TestClient(create_app(), base_url="https://postdesk.example") as admin:
        assert admin.post("/api/auth/setup", json={"password": "correct horse battery"}).status_code == 200
        r = admin.post(f"/api/cards/{card.id}/move", json={"column": "Approved"})
        assert r.status_code == 200 and r.json()["column"] == "Approved" and r.json()["busy"] == "make queued"

        done = jobs.run_pending(session, deps, log=_quiet)
        assert [j.kind for j in done] == ["make", "review", "revise", "review"]
        assert all(j.status == "done" for j in done) and len(revisions) == 1
        session.refresh(card)
        assert card.column == "Awaiting approval" and card.review_round == 2

        detail = admin.get(f"/api/cards/{card.id}").json()
        assert [d["kind"] for d in detail["drafts"]] == ["carousel", "story"]
        assert detail["drafts"][0]["caption"].startswith("Three loaves rested for 72 hours.")
        assert detail["review"]["verdict"] == "pass" and detail["recommended_at"]
        assert [e["to_column"] for e in detail["events"]] == [
            "Planned", "Brief ready", "Approved", "Making", "In review", "Making", "In review", "Awaiting approval"]
        assert not any(e["refused"] for e in detail["events"])
        assert [t["author"] for t in detail["thread"]] == [
            "partner", "system", "maker", "editor", "maker", "editor"]

        r = admin.post(f"/api/cards/{card.id}/move", json={"column": "Scheduled", "when": ""})
        assert r.status_code == 200, r.text
        assert r.json()["column"] == "Scheduled"
        assert {d["status"] for d in r.json()["drafts"]} == {"approved"}

    for p in posts.for_card(session, card.id):
        p.status, p.permalink = "published", "https://www.instagram.com/p/abc/"
    session.commit()
    jobs.housekeeping(session, deps, log=_quiet, scout=False)
    session.refresh(card)
    assert card.column == "Live"


def test_renderer_retry_explains_waiting_on_card(session):
    from postdesk.api.routers.cards import _busy

    c = kit.card(session)
    job = jobs.enqueue(session, "revise", c.id)
    job.error = "RendererUnavailable: temporarily unavailable"
    session.commit()
    assert _busy(session, [c.id])[c.id] == "Waiting for the image renderer; retrying"
    job.error = "RendererNotReady: temporarily unavailable"
    session.commit()
    assert _busy(session, [c.id])[c.id] == "Waiting for the image renderer; retrying"


def test_renderer_preflight_wait_does_not_consume_attempts_and_recovers(session, monkeypatch):
    from postdesk.maker.render import RendererNotReady

    card = kit.card(session, column="Making")
    jobs.enqueue(session, "make", card.id)
    available = False

    def make(_session, _card, _payload, _deps):
        if not available:
            raise RendererNotReady("renderer asleep")

    monkeypatch.setattr(jobs, "_handlers", lambda: {"make": make})
    for _ in range(jobs.MAX_ATTEMPTS + 1):
        job = jobs.run(session, jobs.claim(session), log=_quiet)
        assert job.status == "queued" and job.attempts == 0
        assert job.run_after > jobs.now_utc()
        job.run_after = jobs.now_utc()
        session.commit()
    available = True
    job = jobs.run(session, jobs.claim(session), log=_quiet)
    assert job.status == "done" and job.attempts == 1
    session.refresh(card)
    assert card.column == "Making" and card.stuck_reason is None
