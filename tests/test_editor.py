"""
The editor and the revision turn with Claude scripted: pass -> Awaiting
approval; revise -> Making with the notes, the revision applied (words
only, one slide re-rendered), back to review; hold -> On hold with the
reason; past `[review].max_rounds` -> On hold. The editor's call is a
fresh one: a single user message, never the maker's conversation.
"""

import pytest
from sqlalchemy import select

from postdesk.agents import editor, revision
from postdesk.agents.editor import context
from postdesk.board import cards, posts, transitions
from postdesk.config import config
from postdesk.db.models import Job
from tests import boardkit as kit


@pytest.fixture(autouse=True)
def _voice(monkeypatch):
    monkeypatch.setattr(config.voice, "hashtags", ["#homebaking", "#sourdough", "#weekendbake"])
    monkeypatch.setattr(config.voice, "max_hashtags", 3)


def _in_review(session, rendered=None):
    c = kit.card(session)
    c.brief_md = "A carousel on the 72 hours."
    session.commit()
    cards.set_column(session, c, "Brief ready", actor="partner")
    transitions.approve(session, c, actor="admin")
    transitions.make(session, c, call=kit._fake_call(), renderer=kit._renderer(rendered))
    assert c.column == "In review"
    return c


def _clear(session):
    """The turns are called directly here, so the jobs they queue are dropped as they go."""
    session.execute(Job.__table__.delete())
    session.commit()


def _queued(session, c):
    return [(j.kind, j.payload) for j in session.scalars(select(Job).where(Job.card_id == c.id,
                                                                          Job.status == "queued"))]


def test_a_pass_goes_to_awaiting_approval(session):
    c = _in_review(session)
    call = kit.scripted(kit.review())
    editor.turn(session, c, call=call)
    assert c.column == "Awaiting approval" and c.review["verdict"] == "pass" and c.review_round == 1
    # A fresh context: one user message with the source, the brief and every slide as an image.
    (sent,) = call.calls
    assert [m["role"] for m in sent["messages"]] == ["user"]
    blocks = sent["messages"][0]["content"]
    assert "<source>" in blocks[0]["text"] and "A carousel on the 72 hours." in blocks[0]["text"]
    assert sum(b["type"] == "image" for b in blocks) == 7  # six slides and the Story
    assert '"source": "render"' in next(b["text"] for b in blocks if b["type"] == "text" and "Slide 1" in b["text"])
    review = cards.thread(session, c)[-1]
    assert review.author == "editor" and review.body_md.startswith("### Review, round 1: pass")


def test_revise_goes_to_the_revision_turn_and_back_to_review(session):
    rendered = []
    c = _in_review(session, rendered)
    _clear(session)
    editor.turn(session, c, call=kit.scripted(kit.review("revise", notes=[
        kit.caption_note(), {"target": "slide_text", "slide": 2, "problem": "Too long.", "fix": "Shorter."}])))
    assert c.column == "Making"
    ((kind, payload),) = _queued(session, c)
    assert kind == "revise" and payload["from"] == "editor" and len(payload["notes"]) == 2

    feed = posts.drafts(session, c.id)[0]
    before = [s["asset_id"] for s in feed.slides]
    rendered.clear()
    _clear(session)
    call = kit.scripted(kit.revision(feed.id, slides=[{"index": 1, "fields": {"headline": "Cold dough waits",
                                                                             "source": "Source: me"}}]))
    revision.turn(session, c, payload, call=call, renderer=kit._renderer(rendered))
    assert "The editor's notes" in call.calls[0]["messages"][0]["content"][0]["text"]
    session.refresh(feed)
    assert c.column == "In review" and feed.caption.startswith("Three loaves rested for 72 hours.")
    assert [s["type"] for s in rendered] == ["statement"]  # one slide re-rendered
    after = [s["asset_id"] for s in feed.slides]
    assert after[1] != before[1] and after[0] == before[0]
    assert feed.slides[1]["spec"]["headline"] == "Cold dough waits"
    assert feed.slides[1]["spec"].get("source") != "Source: me"  # a source line is a person's to change
    assert "Not changed: slide 2's source line" in cards.thread(session, c)[-1].body_md
    assert _queued(session, c) == [("review", {})]

    editor.turn(session, c, call=kit.scripted(kit.review()))
    assert c.column == "Awaiting approval" and c.review_round == 2


def test_still_not_right_after_max_rounds_goes_on_hold(session, monkeypatch):
    monkeypatch.setattr(config.review, "max_rounds", 2)
    c = _in_review(session)
    for rnd in (1, 2):
        _clear(session)
        editor.turn(session, c, call=kit.scripted(kit.review("revise", notes=[kit.caption_note()])))
        assert c.column == "Making", rnd
        feed = posts.drafts(session, c.id)[0]
        revision.turn(session, c, {"notes": [kit.caption_note()]},
                      call=kit.scripted(kit.revision(feed.id, caption=f"Round {rnd}: 72 hours in the cold.")),
                      renderer=kit._renderer())
        assert c.column == "In review"
    _clear(session)
    editor.turn(session, c, call=kit.scripted(kit.review("revise", notes=[kit.caption_note()])))
    assert c.column == "On hold" and c.stuck_reason == "still not right after 2 revisions"
    assert c.review_round == 3
    assert _queued(session, c) == []


def test_hold_and_notes_words_cannot_fix_go_on_hold(session):
    c = _in_review(session)
    editor.turn(session, c, call=kit.scripted(kit.review("hold", failed=("rights",),
                                                         summary="The hero has no licence.")))
    assert c.column == "On hold" and c.stuck_reason == "the editor held it: The hero has no licence."

    c = _in_review(session)
    editor.turn(session, c, call=kit.scripted(kit.review("revise", notes=[
        {"target": "slide", "slide": 1, "problem": "Wrong photo.", "fix": None}, kit.caption_note()])))
    assert c.column == "On hold" and c.stuck_reason == "needs a person (slide)"


def test_an_incomplete_review_is_sent_back_once_then_held(session):
    c = _in_review(session)
    bad = kit.review()
    bad["checks"] = bad["checks"][:-1]
    passed_with_a_failure = kit.review(failed=("voice",))
    call = kit.scripted(bad, passed_with_a_failure)
    editor.turn(session, c, call=call)
    assert len(call.calls) == 2 and "checks missing: links" in call.calls[1]["messages"][-1]["content"]
    assert c.column == "On hold" and "a pass with a failed check" in c.stuck_reason

    c = _in_review(session)
    call = kit.scripted(bad, kit.review())
    editor.turn(session, c, call=call)
    assert c.column == "Awaiting approval"


def test_the_accounts_own_checks_join_the_enum(session, monkeypatch):
    monkeypatch.setattr(config.review, "extra_checks", {"bakery-claims": "No health claims about bread."})
    assert context.checks() == ["voice", "facts", "rights", "ai-label", "accessibility", "links", "bakery-claims"]
    assert context.schema()["properties"]["checks"]["items"]["properties"]["check"]["enum"][-1] == "bakery-claims"
    assert "**bakery-claims**: No health claims" in context.system()
    c = _in_review(session)
    editor.turn(session, c, call=kit.scripted(kit.review()))
    assert c.column == "Awaiting approval" and c.review["checks"][-1]["check"] == "bakery-claims"


def test_the_editors_prompt_judges_and_does_not_rewrite():
    system = context.system().lower()
    assert "judge" in system and "could not verify" in system


def test_a_revision_that_changes_nothing_or_invents_a_figure_goes_on_hold(session):
    c = _in_review(session)
    editor.turn(session, c, call=kit.scripted(kit.review("revise", notes=[kit.caption_note()])))
    revision.turn(session, c, {"notes": []}, call=kit.scripted({"reply": "Nothing words can fix.", "posts": []}),
                  renderer=kit._renderer())
    assert c.column == "On hold" and "could not fix this with words alone: Nothing words can fix." in c.stuck_reason

    c = _in_review(session)
    editor.turn(session, c, call=kit.scripted(kit.review("revise", notes=[kit.caption_note()])))
    feed = posts.drafts(session, c.id)[0]
    revision.turn(session, c, {"notes": []}, renderer=kit._renderer(), call=kit.scripted(
        kit.revision(feed.id, caption=None, slides=[{"index": 1, "fields": {"body": "It rested 96 hours."}}])))
    assert c.column == "On hold" and "refused" in c.stuck_reason and "96 hours" in c.stuck_reason


def test_the_admins_rethink_note_reaches_the_revision(session):
    c = _in_review(session)
    editor.turn(session, c, call=kit.scripted(kit.review()))
    _clear(session)
    transitions.rethink(session, c, actor="admin", note="Warmer first line.")
    ((kind, payload),) = _queued(session, c)
    assert kind == "revise"
    feed = posts.drafts(session, c.id)[0]
    call = kit.scripted(kit.revision(feed.id))
    revision.turn(session, c, payload, call=call, renderer=kit._renderer())
    assert "The admin's note (act on it):\nWarmer first line." in call.calls[0]["messages"][0]["content"][0]["text"]
    assert c.column == "In review"
