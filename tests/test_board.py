"""
The board (postdesk.board): every move and its precondition, the two gates
(only the admin moves a card into Approved or Scheduled; anyone else is
refused and the refusal is logged on the card), the queue-to-board sync,
filing hand-made drafts, and the cards API. Claude and the renderer are
faked; nothing here leaves the machine.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from postdesk import jobs, queue
from postdesk.board import cards, posts, sync, transitions
from postdesk.board.cards import BoardError, GateRefused
from postdesk.config import config, settings
from postdesk.db.models import Job, SocialPost
from postdesk.main import create_app
from postdesk.queue.times import now_utc
from tests import boardkit as kit


@pytest.fixture(autouse=True)
def _voice(monkeypatch):
    monkeypatch.setattr(config.voice, "hashtags", ["#homebaking", "#sourdough", "#weekendbake"])
    monkeypatch.setattr(config.voice, "max_hashtags", 3)


def _jobs(session, card, status="queued"):
    return [(j.kind, j.payload) for j in session.scalars(
        select(Job).where(Job.card_id == card.id, Job.status == status).order_by(Job.created_at))]


def _events(session, card):
    return [(e.from_column, e.to_column, e.actor, e.refused) for e in cards.events(session, card)]


def _briefed(session):
    c = kit.card(session)
    c.brief_md, c.guidance = "A carousel on the 72 hours.", "Lead with the 72 hours."
    session.commit()
    cards.set_column(session, c, "Brief ready", actor="partner")
    return c


def _made(session, rendered=None):
    """A card at In review with the maker's carousel and Story on it."""
    c = _briefed(session)
    transitions.approve(session, c, actor="admin")
    transitions.make(session, c, call=kit._fake_call(), renderer=kit._renderer(rendered))
    assert c.column == "In review"
    return c


def _awaiting(session):
    c = _made(session)
    transitions.review(session, c, kit.review())
    assert c.column == "Awaiting approval"
    return c


# ---------------------------------------------------------------- the gates


@pytest.mark.parametrize("actor", ["partner", "editor", "maker", "scout", "system", "worker", "publisher"])
@pytest.mark.parametrize("gate", ["Approved", "Scheduled"])
def test_only_the_admin_moves_a_card_into_a_gate_column_and_a_refusal_is_logged(session, actor, gate):
    c = _awaiting(session) if gate == "Scheduled" else _briefed(session)
    before = c.column
    with pytest.raises(GateRefused, match=f"{actor} may not move a card into {gate}"):
        cards.set_column(session, c, gate, actor=actor)
    with pytest.raises(GateRefused):
        transitions.move(session, c, gate, actor=actor)
    session.refresh(c)
    assert c.column == before
    refused = [e for e in cards.events(session, c) if e.refused]
    assert [(e.to_column, e.actor) for e in refused] == [(gate, actor), (gate, actor)]
    assert all("only the admin" in e.reason for e in refused)
    # Nothing was approved in the queue.
    assert all(p.status == "draft" for p in posts.for_card(session, c.id))


def test_no_card_is_filed_straight_into_a_gate(session):
    for gate in ("Approved", "Scheduled"):
        with pytest.raises(GateRefused):
            kit.card(session, column=gate)
    with pytest.raises(BoardError, match="no column"):
        kit.card(session, column="Doing")


def test_the_admin_and_the_api_token_are_people(session):
    c = _briefed(session)
    transitions.move(session, c, "Approved", actor="api")
    assert c.column == "Approved" and _jobs(session, c) == [("make", {})]
    assert _events(session, c)[-1] == ("Brief ready", "Approved", "api", False)


# ---------------------------------------------------------------- the admin's moves


def test_plan_takes_a_card_from_the_inbox_to_the_partner(session):
    c = kit.card(session, column="Inbox")
    transitions.move(session, c, "Planned", actor="admin", note="Lead with the crumb.")
    assert c.column == "Planned"
    assert _jobs(session, c) == [("partner", {"reason": "the card was just planned: write its brief"})]
    assert cards.thread(session, c)[0].body_md == "Lead with the crumb."
    transitions.move(session, c, "Inbox", actor="admin")
    assert c.column == "Inbox"


def test_approve_needs_a_brief_and_the_right_column(session):
    c = kit.card(session)
    transitions.move(session, c, "Approved", actor="admin")
    assert c.column == "Planned" and _jobs(session, c)[-1][0] == "partner"
    assert "no brief" in cards.thread(session, c)[-1].body_md

    c = _briefed(session)
    transitions.move(session, c, "Approved", actor="admin", note="Go.")
    assert c.column == "Approved" and _jobs(session, c) == [("make", {})]

    c = _made(session)
    with pytest.raises(BoardError, match="moving it to Approved needs Planned or Brief ready or On hold"):
        transitions.move(session, c, "Approved", actor="admin")


def test_rethink_sends_a_brief_back_and_a_made_post_to_the_revision(session):
    c = _briefed(session)
    with pytest.raises(BoardError, match="say what to change"):
        transitions.rethink(session, c, actor="admin", note=" ")
    transitions.move(session, c, "Planned", actor="admin", note="Make it a Story.")
    assert c.column == "Planned" and _jobs(session, c) == [("partner", {"reason": "rethink: Make it a Story."})]

    c = _awaiting(session)
    transitions.move(session, c, "Making", actor="admin", note="Warmer caption.")
    assert c.column == "Making"
    assert _jobs(session, c)[-1] == ("revise", {"note": "Warmer caption.", "from": "user"})

    c = kit.card(session, column="Inbox")
    with pytest.raises(BoardError, match="rethink works on"):
        transitions.rethink(session, c, actor="admin", note="x")


def test_hold_back_to_the_partner_send_to_review_and_drop(session):
    c = _awaiting(session)
    transitions.move(session, c, "On hold", actor="admin", note="Waiting on the baker.")
    assert c.column == "On hold" and c.stuck_reason == "Waiting on the baker."
    transitions.move(session, c, "In review", actor="admin")
    assert c.column == "In review" and c.review_round == 0 and c.stuck_reason is None
    assert _jobs(session, c)[-1] == ("review", {})

    transitions.move(session, c, "Planned", actor="admin")
    assert c.column == "Planned" and _jobs(session, c)[-1][0] == "partner"

    drafts = posts.drafts(session, c.id)
    transitions.move(session, c, "Closed", actor="admin", note="Not this week.")
    assert c.closed_at is not None and _events(session, c)[-1][1] == "Closed"
    assert {session.get(SocialPost, p.id).status for p in drafts} == {"rejected"}
    with pytest.raises(BoardError, match="closed"):
        transitions.move(session, c, "Approved", actor="admin")


def test_send_to_review_needs_drafts(session):
    c = kit.card(session)
    transitions.hold(session, c, actor="admin")
    with pytest.raises(BoardError, match="no drafts"):
        transitions.move(session, c, "In review", actor="admin")


def test_columns_only_the_agents_or_the_publisher_set_are_not_moves(session):
    c = _briefed(session)
    for col in ("Live", "Measured", "Brief ready"):
        with pytest.raises(BoardError, match="set by the agents or the publisher"):
            transitions.move(session, c, col, actor="admin")
    with pytest.raises(BoardError, match="no column"):
        transitions.move(session, c, "Done", actor="admin")
    with pytest.raises(BoardError, match="through its own turn"):
        transitions.move(session, c, "On hold", actor="partner")


def test_schedule_approves_the_drafts_at_the_time_and_unschedule_takes_it_back(session):
    c = _awaiting(session)
    with pytest.raises(BoardError, match="needs Awaiting approval"):
        transitions.schedule(session, _briefed(session), actor="admin")
    when = posts.parse_when("tomorrow 18:00")
    approved = transitions.schedule(session, c, actor="admin", when=when)
    feed, story = approved
    assert c.column == "Scheduled" and {p.status for p in approved} == {"approved"}
    assert feed.publish_at == when and story.publish_at == when + timedelta(minutes=15)
    assert feed.approved_by == "admin" and "Scheduled:" in cards.thread(session, c)[-1].body_md

    transitions.move(session, c, "Awaiting approval", actor="admin")
    assert c.column == "Awaiting approval"
    assert {p.status for p in posts.for_card(session, c.id)} == {"draft"}


def test_parse_when_reads_what_a_person_types():
    now = kit.NOW  # Monday 10:00 in London
    assert posts.parse_when("", now=now) is None
    assert posts.parse_when("18:00", now=now).isoformat() == "2031-03-03T18:00:00+00:00"
    assert posts.parse_when("tomorrow 7:30", now=now).isoformat() == "2031-03-04T07:30:00+00:00"
    assert posts.parse_when("fri", now=now).isoformat() == "2031-03-07T18:00:00+00:00"
    assert posts.parse_when("mon 9am", now=now).isoformat() == "2031-03-10T09:00:00+00:00"
    assert posts.parse_when("2031-03-05T12:00", now=now).isoformat() == "2031-03-05T12:00:00+00:00"
    for bad in ("someday", "2031-03-03T09:00", "2031-12-01T09:00", "25:00"):
        with pytest.raises(queue.QueueError):
            posts.parse_when(bad, now=now)


# ---------------------------------------------------------------- the queue and the board


def test_sync_moves_a_published_card_to_live_and_a_failed_one_to_on_hold(session):
    c = _awaiting(session)
    transitions.schedule(session, c, actor="admin")
    feed, story = posts.for_card(session, c.id)
    for p in (feed, story):
        p.status, p.permalink = "published", f"https://www.instagram.com/p/{p.kind}/"
    session.commit()
    assert sync.sync(session, log=lambda _m: None) == {str(c.id): "live"}
    assert c.column == "Live" and _events(session, c)[-1] == ("Scheduled", "Live", "publisher", False)
    assert "https://www.instagram.com/p/carousel/" in cards.thread(session, c)[-1].body_md

    c = _awaiting(session)
    transitions.schedule(session, c, actor="admin")
    feed, story = posts.for_card(session, c.id)
    feed.status, feed.permalink = "published", "https://www.instagram.com/p/x/"
    story.status, story.error = "failed", "Media upload has failed."
    session.commit()
    sync.sync(session, log=lambda _m: None)
    assert c.column == "On hold" and "partial failure" in c.stuck_reason and "Media upload" in c.stuck_reason


def test_every_post_rejected_closes_the_card(session):
    c = _awaiting(session)
    for p in posts.for_card(session, c.id):
        queue.reject(session, p.id)
    sync.queue_changed(session, c.id, actor="admin", log=lambda _m: None)
    assert c.closed_at is not None


def test_approving_in_the_queue_screens_is_the_schedule_gate_passed_by_that_person(session):
    c = _awaiting(session)
    at = now_utc() + timedelta(days=1)
    for p in posts.drafts(session, c.id):
        queue.approve(session, p.id, publish_at=at, approved_by="admin")
    # The worker's sync never moves a card into Scheduled.
    sync.sync(session, log=lambda _m: None)
    assert c.column == "Awaiting approval"
    sync.queue_changed(session, c.id, actor="worker", log=lambda _m: None)
    assert c.column == "Awaiting approval"
    sync.queue_changed(session, c.id, actor="admin", log=lambda _m: None)
    assert c.column == "Scheduled" and _events(session, c)[-1][2] == "admin"
    queue.unapprove(session, posts.for_card(session, c.id)[0].id)
    sync.queue_changed(session, c.id, actor="admin", log=lambda _m: None)
    assert c.column == "Awaiting approval"


def test_a_hand_made_draft_with_no_card_is_filed_for_the_editor(session):
    from postdesk import storage
    from tests.test_api import _jpeg

    asset = storage.store_asset(session, data=_jpeg(), filename="a.jpg", content_type="image/jpeg",
                                uploaded_by="admin", provenance={"source": "upload"})
    post = queue.create_post(session, kind="image", title="Market day", caption="Out on Saturday.",
                             slides=[{"asset_id": str(asset.id), "alt_text": "A loaf"}], created_by="admin")
    filed = sync.file_drafts(session, log=lambda _m: None)
    assert len(filed) == 1
    c = filed[0]
    assert c.column == "In review" and c.created_by == "system" and c.title == "Market day"
    assert session.get(SocialPost, post.id).card_id == c.id
    assert _jobs(session, c) == [("review", {})]
    assert sync.file_drafts(session, log=lambda _m: None) == []  # filed once

    # Approved by the admin, the maker leaves hand-made drafts alone and the editor reads them.
    transitions.stuck(session, c, "test")
    transitions.approve(session, c, actor="admin")
    transitions.make(session, c, call=kit._fake_call(), renderer=kit._renderer())
    assert c.column == "In review" and [p.id for p in posts.drafts(session, c.id)] == [post.id]


def test_a_new_make_replaces_the_old_drafts(session):
    c = _made(session)
    old = {p.id for p in posts.drafts(session, c.id)}
    transitions.stuck(session, c, "make it again")
    transitions.approve(session, c, actor="admin")
    transitions.make(session, c, call=kit._fake_call(), renderer=kit._renderer())
    new = {p.id for p in posts.drafts(session, c.id)}
    assert new and not new & old
    assert {session.get(SocialPost, i).status for i in old} == {"rejected"}


# ---------------------------------------------------------------- the API


@pytest.fixture
def admin(session):
    with TestClient(create_app(), base_url="https://postdesk.example") as c:
        assert c.post("/api/auth/setup", json={"password": "correct horse battery"}).status_code == 200
        yield c


def test_the_cards_api(session, admin, monkeypatch):
    assert TestClient(create_app()).get("/api/cards").status_code == 401

    r = admin.post("/api/cards", json={"title": "Rye week", "body_md": "Four rye loaves, 30% whole grain."})
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["column"] == "Inbox" and card["created_by"] == "admin" and card["gates"] == ["Approved", "Scheduled"]
    cid = card["id"]
    assert [c["id"] for c in admin.get("/api/cards").json()] == [cid]

    r = admin.post(f"/api/cards/{cid}/move", json={"column": "Planned"})
    assert r.status_code == 200 and r.json()["column"] == "Planned" and r.json()["busy"] == "partner queued"
    r = admin.post(f"/api/cards/{cid}/comments", json={"body_md": "Use the 30%."})
    assert r.json()["thread"][-1]["author"] == "user"
    assert admin.post(f"/api/cards/{cid}/review").status_code == 409
    assert admin.post(f"/api/cards/{cid}/partner").status_code == 200

    r = admin.post(f"/api/cards/{cid}/move", json={"column": "Scheduled"})
    assert r.status_code == 409 and "needs Awaiting approval" in r.json()["detail"]
    r = admin.post(f"/api/cards/{cid}/move", json={"column": "Live"})
    assert r.status_code == 409
    assert admin.post("/api/cards/00000000-0000-0000-0000-000000000000/move",
                      json={"column": "Approved"}).status_code == 404

    events = admin.get(f"/api/cards/{cid}/events").json()
    assert [(e["from_column"], e["to_column"], e["actor"]) for e in events] == \
        [("", "Inbox", "admin"), ("Inbox", "Planned", "admin")]

    # Bearer token: the admin too, so it may pass a gate.
    monkeypatch.setattr(settings, "postdesk_api_token", "tok")
    c = _awaiting(session)
    bearer = TestClient(create_app(), base_url="https://postdesk.example",
                        headers={"authorization": "Bearer tok"})
    r = bearer.post(f"/api/cards/{c.id}/move", json={"column": "Scheduled", "when": "tomorrow 18:00"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["column"] == "Scheduled" and {d["status"] for d in got["drafts"]} == {"approved"}
    assert got["events"][-1]["actor"] == "api"
    r = bearer.post(f"/api/cards/{c.id}/move", json={"column": "Awaiting approval"})
    assert r.json()["column"] == "Awaiting approval" and r.json()["recommended_at"]
    r = bearer.post(f"/api/cards/{c.id}/move", json={"column": "Scheduled", "when": "someday"})
    assert r.status_code == 400

    runs = admin.get("/api/runs").json()
    assert runs["month_usd"] == 0 and runs["ceiling_usd"] == config.llm.monthly_usd


def test_an_attachment_is_stored_with_its_note_and_wakes_the_partner(session, admin):
    from tests.test_api import _jpeg

    c = kit.card(session)
    r = admin.post(f"/api/cards/{c.id}/attachments", files={"file": ("crumb.png", _jpeg(), "image/png")},
                   data={"note": "Photo: Jo Baker, mine to use"})
    assert r.status_code == 200, r.text
    att = r.json()["thread"][-1]["attachments"][0]
    assert att["note"] == "Photo: Jo Baker, mine to use" and att["url"].endswith(att["asset_id"])
    assert _jobs(session, c)[-1] == ("partner", {"reason": "the admin attached an image: judge it"})
    # Attached assets are served (the partner and the browser fetch them).
    assert admin.get(f"/media/{att['asset_id']}").status_code == 200
    assert admin.post(f"/api/cards/{c.id}/attachments", files={"file": ("x.txt", b"hello", "text/plain")}
                      ).status_code == 400


def test_post_about_this_files_a_planned_card(session, admin):
    r = admin.post("/api/sources/manual", json={"title": "Rye week", "text": "Four rye loaves, 30% whole grain."})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["column"] == "Planned" and got["facts"]["title"] == "Rye week" and got["busy"] == "partner queued"
    assert admin.post("/api/sources/manual", json={"title": "Only a title"}).status_code == 400
    assert admin.get("/api/sources").json() == []


def test_jobs_are_folded_while_queued(session):
    c = kit.card(session)
    jobs.enqueue(session, "partner", c.id, {"reason": "one"})
    jobs.enqueue(session, "partner", c.id, {"reason": "two"})
    assert _jobs(session, c) == [("partner", {"reason": "one\ntwo"})]
