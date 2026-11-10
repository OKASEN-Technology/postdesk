"""
The Telegram bot (postdesk.telegram): pairing; only the paired chat acting;
every button and command going through the board's own moves and gates;
the notices the board leaves for it; the 4,096-character split; the
digest; polling and the webhook. Telegram is faked (tests/telegramkit.py);
so are Claude, the renderer and Instagram.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from postdesk import control, jobs, llm, queue, storage, store
from postdesk.board import cards, posts, sync, transitions
from postdesk.board.cards import GateRefused
from postdesk.config import config, settings
from postdesk.db.models import AgentRun, Card, Job, Notice, TelegramMessage
from postdesk.main import create_app
from postdesk.telegram import api, commands, pairing, render
from postdesk.telegram import bot as bot_mod
from tests import boardkit as kit
from tests import telegramkit as tk
from tests.conftest import FakeGraph, graph_for
from tests.test_board import _awaiting, _briefed, _made


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", tk.TOKEN)
    monkeypatch.setattr(config.voice, "hashtags", ["#homebaking", "#sourdough", "#weekendbake"])
    monkeypatch.setattr(config.voice, "max_hashtags", 3)


@pytest.fixture
def fake():
    return tk.FakeTelegram()


@pytest.fixture
def bot(fake):
    return tk.bot(fake)


@pytest.fixture
def paired(session):
    code = pairing.new_code(session, tk.CHAT, "Sam")
    pairing.link(session, code, by="admin")
    return tk.CHAT


def _quiet(_m):
    pass


def handle(session, bot, update, **deps):
    commands.handle(session, bot, update, commands.Deps(log=_quiet, **deps))


def _sent_about(session, card):
    return list(session.scalars(select(TelegramMessage).where(TelegramMessage.card_id == card.id)
                                .order_by(TelegramMessage.created_at)))


def _jobs(session, card):
    return [j.kind for j in session.scalars(select(Job).where(Job.card_id == card.id, Job.status == "queued"))]


# ---------------------------------------------------------------- pairing


def test_start_gives_a_code_that_pairs_the_chat(session, bot, fake):
    assert pairing.linked(session) is None
    handle(session, bot, tk.message("/start"))
    text = fake.last()["text"]
    code = text.split("<b>")[1].split("</b>")[0]
    assert len(code) == 6 and f"postdesk telegram link {code}" in text
    handle(session, bot, tk.message("/start"))  # asking again gives the same code
    assert code in fake.last()["text"]

    with pytest.raises(pairing.PairingError):
        pairing.link(session, "ZZZZZZ", by="admin")
    linked = pairing.link(session, code.lower(), by="admin")
    assert linked["chat_id"] == tk.CHAT and linked["title"] == "Sam"
    with pytest.raises(pairing.PairingError):  # a code works once
        pairing.link(session, code, by="admin")


def test_a_code_expires(session):
    code = pairing.new_code(session, tk.CHAT, now=datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(pairing.PairingError, match="last 30 minutes"):
        pairing.link(session, code, by="admin")


def test_pairing_from_the_settings_api(session, bot, fake, monkeypatch):
    monkeypatch.setattr(api, "default_client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    handle(session, bot, tk.message("/start"))
    code = fake.last()["text"].split("<b>")[1].split("</b>")[0]
    with TestClient(create_app(), base_url="https://postdesk.example") as client:
        client.post("/api/auth/setup", json={"password": "correct horse battery"})
        assert client.post("/api/telegram/link", json={"code": "WRONG1"}).status_code == 400
        r = client.post("/api/telegram/link", json={"code": code})
        assert r.status_code == 200 and r.json()["linked"]["chat_id"] == tk.CHAT
        assert "Paired" in fake.last()["text"]
        assert client.post("/api/telegram/test").status_code == 200
        assert client.post("/api/telegram/unlink").json()["linked"] is None


def test_an_unpaired_bot_does_nothing_but_give_codes(session, bot, fake):
    c = kit.card(session, column="Brief ready")
    c.brief_md = "A brief."
    session.commit()
    handle(session, bot, tk.message("/new Rye week"))
    handle(session, bot, tk.press(render.data("go", c.id)))
    assert fake.texts() == [commands.UNPAIRED]
    assert fake.of("answerCallbackQuery")[-1]["text"] == commands.UNPAIRED
    assert session.scalars(select(Card)).all() == [c] and c.column == "Brief ready"


def test_another_chat_is_told_the_bot_is_private_and_changes_nothing(session, bot, fake, paired):
    c = _briefed(session)
    for update in (tk.message("/new Rye week", chat=tk.OTHER), tk.message("/go", chat=tk.OTHER),
                   tk.message("lead with the rye", chat=tk.OTHER), tk.message("/pause", chat=tk.OTHER),
                   tk.press(render.data("go", c.id), chat=tk.OTHER)):
        handle(session, bot, update)
    assert fake.texts(tk.OTHER) == [commands.PRIVATE] * 4
    assert fake.of("answerCallbackQuery")[-1]["text"] == commands.PRIVATE
    session.refresh(c)
    assert c.column == "Brief ready" and _jobs(session, c) == [] and control.paused(session) is None
    assert len(session.scalars(select(Card)).all()) == 1
    assert cards.thread(session, c) == []


def test_a_gate_move_as_any_chat_but_the_paired_one_is_refused_by_the_board(session, paired):
    c = _briefed(session)
    with pytest.raises(GateRefused):
        transitions.approve(session, c, actor=f"telegram:{tk.OTHER}")
    refused = [e for e in cards.events(session, c) if e.refused]
    assert [(e.to_column, e.actor) for e in refused] == [("Approved", f"telegram:{tk.OTHER}")]
    pairing.unlink(session)  # once unpaired, the old chat is no one either
    with pytest.raises(GateRefused):
        transitions.approve(session, c, actor=f"telegram:{tk.CHAT}")
    code = pairing.new_code(session, tk.CHAT)
    pairing.link(session, code, by="admin")
    transitions.approve(session, c, actor=f"telegram:{tk.CHAT}")
    assert c.column == "Approved" and cards.events(session, c)[-1].actor == f"telegram:{tk.CHAT}"


# ---------------------------------------------------------------- the brief


def _brief_with_picture(session):
    """A card the partner has briefed, using a found picture with a licence."""
    c = kit.card(session)
    asset = storage.store_asset(session, data=tk.jpeg(), filename="loaf.jpg", content_type="image/jpeg",
                                provenance={"source": "wikimedia", "licence": "CC BY 4.0"})
    plan = dict(c.plan)
    plan["found"] = [{"key": "found-1", "for": "hero", "url": storage.asset_url(asset.id), "asset_id": str(asset.id),
                      "credit": "Photo: Jo Baker / Wikimedia Commons, CC BY 4.0", "licence": "CC BY 4.0",
                      "what": "a loaf", "source": "found by the wikimedia image source"}]
    c.plan = plan
    session.commit()
    jobs.enqueue(session, "partner", c.id)
    jobs.run_pending(session, kit.deps(partner=kit.scripted(kit.partner_reply(use_images=["found-1"]))), log=_quiet)
    assert c.column == "Brief ready"
    return c


def test_brief_ready_arrives_with_its_picture_licence_and_buttons(session, bot, fake, paired):
    c = _brief_with_picture(session)
    bot_mod.send_notices(session, bot, log=_quiet)
    photo = fake.of("sendPhoto")[0]
    assert photo["_files"] == ["photo"] and "CC BY 4.0" in photo["caption"] and "Jo Baker" in photo["caption"]
    msg = fake.last()
    assert msg["text"].startswith(f"<b>Brief ready: {c.title}</b>") and "72 hours" in msg["text"]
    assert fake.buttons(msg) == ["go", "rt", "hd"]
    assert {m.purpose for m in _sent_about(session, c)} == {"card"}
    assert len(_sent_about(session, c)) == 3  # filed, then the picture and the brief
    assert bot_mod.send_notices(session, bot, log=_quiet) == 0  # sent once


def test_credit_only_picture_says_so(session):
    assert render.licence_line({"credit": "Image: bakes.example", "licence": None}) == \
        "Image: bakes.example · credit only, no licence"


def test_the_approve_button_approves_and_clears_its_buttons(session, bot, fake, paired):
    c = _briefed(session)
    handle(session, bot, tk.press(render.data("go", c.id), message_id=77))
    session.refresh(c)
    assert c.column == "Approved" and _jobs(session, c) == ["make"]
    assert cards.events(session, c)[-1].actor == f"telegram:{tk.CHAT}"
    assert fake.of("answerCallbackQuery")
    assert fake.of("editMessageReplyMarkup")[-1]["message_id"] == 77
    assert "Approved" in fake.last()["text"]


def test_go_and_a_button_on_the_wrong_column_say_why(session, bot, fake, paired):
    c = kit.card(session, column="Inbox")
    handle(session, bot, tk.press(render.data("go", c.id)))
    assert "needs Planned or Brief ready or On hold" in fake.last()["text"]
    assert "Not done" in fake.last()["text"] and c.column == "Inbox"
    handle(session, bot, tk.message("/go"))
    assert fake.last()["text"] == "No card is at Brief ready."


def test_the_rethink_button_asks_and_the_reply_is_the_note(session, bot, fake, paired):
    c = _briefed(session)
    handle(session, bot, tk.press(render.data("rt", c.id)))
    ask = fake.last()
    assert ask["reply_markup"]["force_reply"] is True
    asked = _sent_about(session, c)[-1]
    assert asked.purpose == "rethink"
    handle(session, bot, tk.message("Lead with the rye, not the hydration.", reply_to=asked.message_id))
    session.refresh(c)
    assert c.column == "Planned" and _jobs(session, c) == ["partner"]
    assert cards.thread(session, c)[-1].body_md == "Lead with the rye, not the hydration."
    assert "Back to the partner" in fake.last()["text"]


def test_a_reply_to_a_card_message_is_a_comment_the_partner_answers(session, bot, fake, paired):
    c = _brief_with_picture(session)
    bot_mod.send_notices(session, bot, log=_quiet)
    brief_msg = _sent_about(session, c)[-1]
    jobs.run_pending(session, kit.deps(), log=_quiet)
    handle(session, bot, tk.message("Can the cover say three loaves?", reply_to=brief_msg.message_id))
    assert cards.thread(session, c)[-1].author == "user" and _jobs(session, c) == ["partner"]
    assert "The partner is answering" in fake.last()["text"]

    # Its answer comes back to the chat, as a reply on the same card.
    jobs.run_pending(session, kit.deps(partner=kit.scripted(kit.partner_reply(
        column="Planned", brief=None, guidance=None, reply="Yes: Three loaves, one long rest."))), log=_quiet)
    bot_mod.send_notices(session, bot, log=_quiet)
    assert "<b>The partner, on" in fake.last()["text"] and "Three loaves, one long rest" in fake.last()["text"]


def test_free_text_that_is_not_a_reply_goes_to_the_one_card_with_the_partner(session, bot, fake, paired):
    handle(session, bot, tk.message("hello"))
    assert fake.last()["text"] == "No card is with the partner."
    c = _briefed(session)
    handle(session, bot, tk.message("Shorter, please."))
    assert cards.thread(session, c)[-1].body_md == "Shorter, please."
    _briefed(session)
    handle(session, bot, tk.message("Which one?"))
    assert "2 cards are with the partner" in fake.last()["text"]


def test_a_reply_to_a_message_about_no_card_is_refused(session, bot, fake, paired):
    handle(session, bot, tk.message("lead with rye", reply_to=123456))
    assert "not about a card" in fake.last()["text"]


def test_a_photo_in_reply_with_its_credit_is_attached_for_the_partner(session, bot, fake, paired):
    c = _briefed(session)
    commands.say(session, bot, tk.CHAT, "a card message", card=c)
    mid = _sent_about(session, c)[-1].message_id
    handle(session, bot, tk.photo(None, reply_to=mid))
    assert "Say where the photo is from" in fake.last()["text"] and not cards.attachments(session, c)
    handle(session, bot, tk.photo("Photo: Jo Baker, mine to use", reply_to=mid))
    assert fake.of("getFile")[-1]["file_id"] == "big-photo-id"
    att = cards.attachments(session, c)
    assert len(att) == 1 and att[0]["note"] == "Photo: Jo Baker, mine to use"
    assert _jobs(session, c) == ["partner"] and "Attached" in fake.last()["text"]
    handle(session, bot, tk.photo("Photo: me"))  # not a reply: which card?
    assert "as a reply to the card's message" in fake.last()["text"]


def test_attachments_off_in_config_are_refused(session, bot, fake, paired, monkeypatch):
    monkeypatch.setattr(config.images, "sources", ["wikimedia"])
    c = _briefed(session)
    commands.say(session, bot, tk.CHAT, "a card message", card=c)
    handle(session, bot, tk.photo("Photo: Jo", reply_to=_sent_about(session, c)[-1].message_id))
    assert "attached images are off" in fake.last()["text"]


# ---------------------------------------------------------------- the made post


def test_awaiting_approval_sends_the_slides_caption_alt_text_checks_and_schedule(session, bot, fake, paired):
    c = _awaiting(session)
    bot_mod.send_notices(session, bot, log=_quiet)
    album = fake.of("sendMediaGroup")
    assert album and len(album[0]["media"]) == len(posts.drafts(session, c.id)[0].slides)
    msg = fake.last()
    assert f"Ready to schedule: {c.title}" in "".join(fake.texts())
    assert "<blockquote expandable><b>Alt text</b>" in "".join(fake.texts())
    assert "✓ voice" in "".join(fake.texts()) and "Recommended time" in msg["text"]
    assert fake.buttons(msg) == ["sc", "bx", "pk", "bt", "im", "rt", "hd"]
    assert msg["reply_markup"]["inline_keyboard"][0][0]["text"].startswith("Instagram · ")


def test_the_schedule_button_schedules_at_the_recommended_time(session, bot, fake, paired):
    c = _awaiting(session)
    rec = posts.recommended_time(session, c)
    handle(session, bot, tk.press(render.data("sc", c.id)))
    session.refresh(c)
    feed = posts.for_card(session, c.id)[0]
    assert c.column == "Scheduled" and feed.status == "approved" and feed.publish_at == rec
    assert feed.approved_by == f"telegram:{tk.CHAT}"
    assert "Reply /unschedule to pull it." in fake.last()["text"]
    # The web's "Scheduled" notice is not sent again to the chat that did it.
    bot_mod.send_notices(session, bot, log=_quiet)
    assert sum("Reply /unschedule" in t for t in fake.texts()) == 1

    handle(session, bot, tk.message("/unschedule"))
    session.refresh(c)
    assert c.column == "Awaiting approval" and {p.status for p in posts.for_card(session, c.id)} == {"draft"}


def test_pick_a_time_then_reply_with_one(session, bot, fake, paired):
    c = _awaiting(session)
    handle(session, bot, tk.press(render.data("pk", c.id)))
    asked = _sent_about(session, c)[-1]
    assert asked.purpose == "pick_time"
    handle(session, bot, tk.message("someday", reply_to=asked.message_id))
    assert "can't read" in fake.last()["text"] and c.column == "Awaiting approval"
    handle(session, bot, tk.message("tomorrow 18:00", reply_to=asked.message_id))
    session.refresh(c)
    assert c.column == "Scheduled"
    feed = posts.for_card(session, c.id)[0]
    assert feed.publish_at.astimezone(config.account.tz).strftime("%H:%M") == "18:00"


def test_schedule_command_with_a_time(session, bot, fake, paired):
    c = _awaiting(session)
    handle(session, bot, tk.message("/schedule fri 7:30"))
    session.refresh(c)
    feed = posts.for_card(session, c.id)[0]
    assert c.column == "Scheduled" and feed.publish_at.astimezone(config.account.tz).strftime("%a %H:%M") == \
        "Fri 07:30"


def test_rethink_with_a_note_sends_drafts_to_the_revision(session, bot, fake, paired):
    c = _awaiting(session)
    handle(session, bot, tk.message("/rethink"))
    assert fake.last()["reply_markup"]["force_reply"] is True and c.column == "Awaiting approval"
    handle(session, bot, tk.message("/rethink The caption's first line is flat."))
    session.refresh(c)
    assert c.column == "Making" and "revise" in _jobs(session, c)
    assert "Back to the maker" in fake.last()["text"]


def test_hold_and_drop(session, bot, fake, paired):
    c = _awaiting(session)
    handle(session, bot, tk.press(render.data("hd", c.id)))
    session.refresh(c)
    assert c.column == "On hold" and c.stuck_reason == "held from Telegram"
    assert fake.buttons(fake.last()) == ["im", "rv", "bp", "dr"]
    d = _briefed(session)
    handle(session, bot, tk.message("/hold waiting on the photo", reply_to=_reply_target(session, bot, d)))
    session.refresh(d)
    assert d.column == "On hold" and d.stuck_reason == "waiting on the photo"
    handle(session, bot, tk.message("/drop", reply_to=_reply_target(session, bot, d)))
    session.refresh(d)
    assert d.closed_at is not None and "Dropped" in fake.last()["text"]
    handle(session, bot, tk.press(render.data("dr", c.id)))
    session.refresh(c)
    assert c.closed_at is not None and {p.status for p in posts.for_card(session, c.id)} == {"rejected"}


def _editor_hold(session):
    card = _made(session)
    transitions.review(session, card, kit.review("hold", notes=[{
        "target": "slide", "slide": 1, "problem": "The cover needs a different image.", "fix": None,
    }], summary="Review the cover image."))
    return card


def test_telegram_upload_replaces_chosen_slide_and_returns_it_for_review(session, bot, fake, paired, monkeypatch):
    card = _editor_hold(session)
    monkeypatch.setattr(commands, "library_choices", lambda *_: [])
    commands.choose_slide(session, bot, tk.CHAT, card)
    choice_message = _sent_about(session, card)[-1].message_id
    choice = fake.last()["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    rendered = []
    deps = commands.Deps(renderer=kit._renderer(rendered))
    handle(session, bot, tk.press(choice, message_id=choice_message), renderer=deps.renderer)
    upload_message = _sent_about(session, card)[-1].message_id
    upload = fake.last()["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    handle(session, bot, tk.press(upload, message_id=upload_message), renderer=deps.renderer)
    prompt = _sent_about(session, card)[-1].message_id
    handle(session, bot, tk.photo(None, reply_to=prompt), renderer=deps.renderer)
    assert rendered and any(str(rendered[-1].get(field, {}).get("url", "")).startswith("data:image/jpeg;base64,")
                            for field in ("image", "background"))
    session.refresh(card)
    assert card.column == "On hold"
    assert card.stuck_reason == transitions.IMAGE_EDIT_HOLD
    assert fake.of("sendMediaGroup") and fake.of("sendPhoto")
    assert fake.buttons(fake.last()) == ["ok", "im", "rv", "rt", "bp", "dr"]
    assert any("asset_id" in (s.get("spec", {}).get("image") or {})
               for p in posts.drafts(session, card.id) for s in p.slides)
    assert not any(p.status == "approved" for p in posts.for_card(session, card.id))
    before = [(p.id, [s["asset_id"] for s in p.slides]) for p in posts.drafts(session, card.id)]
    approval = fake.last()["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    handle(session, bot, tk.press(approval), renderer=deps.renderer)
    session.refresh(card)
    assert card.column == "Awaiting approval"
    assert [(p.id, [s["asset_id"] for s in p.slides]) for p in posts.drafts(session, card.id)] == before
    assert all(p.status == "draft" for p in posts.drafts(session, card.id))


def test_telegram_offers_brand_images_and_rejects_old_choices(session, bot, fake, paired, monkeypatch):
    import io

    card = _editor_hold(session)
    item = {"key": "vigil/device.jpg", "name": "device.jpg", "url": "https://example.com/device.jpg",
            "size": len(fake.photo), "type": "image", "enabled": True}
    monkeypatch.setattr(commands, "library_choices", lambda *_: [item])
    monkeypatch.setattr(commands.brand_library, "read", lambda *_: {"bucket": "test"})

    class FakeR2:
        def get_object(self, **_):
            return {"Body": io.BytesIO(fake.photo)}

    monkeypatch.setattr(commands.brand_library, "_connection", lambda *_: FakeR2())
    deps = commands.Deps(renderer=kit._renderer())
    commands.choose_slide(session, bot, tk.CHAT, card)
    choice_message = _sent_about(session, card)[-1].message_id
    choice = fake.last()["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    handle(session, bot, tk.press(choice, message_id=choice_message), renderer=deps.renderer)
    photo = fake.of("sendPhoto")[-1]
    assert "photo" in photo["_files"]
    pick = photo["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    photo_message = _sent_about(session, card)[-2].message_id
    handle(session, bot, tk.press(pick, message_id=photo_message), renderer=deps.renderer)
    session.refresh(card)
    assert card.column == "On hold" and card.stuck_reason == transitions.IMAGE_EDIT_HOLD
    handle(session, bot, tk.press(pick, message_id=photo_message), renderer=deps.renderer)
    assert "no longer a draft" in fake.last()["text"]


def test_image_button_acknowledges_before_loading_and_survives_expired_callback(session, bot, fake, paired,
                                                                                  monkeypatch):
    card = _editor_hold(session)
    commands.choose_slide(session, bot, tk.CHAT, card)
    choice_message = _sent_about(session, card)[-1].message_id
    choice = fake.last()["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    fake.clear()
    fake.fail["answerCallbackQuery"] = "Bad Request: query is too old"

    def load_choices(*_):
        assert fake.calls[0][0] == "answerCallbackQuery"
        assert any("Finding brand images" in text for text in fake.texts())
        return []

    monkeypatch.setattr(commands, "library_choices", load_choices)
    handle(session, bot, tk.press(choice, message_id=choice_message))
    assert "No more brand images" in " ".join(fake.texts())
    assert "Upload a photo" in str(fake.last()["reply_markup"])
    assert card.column == "On hold"


def test_image_button_reports_unexpected_library_failure(session, bot, fake, paired, monkeypatch):
    card = _editor_hold(session)
    commands.choose_slide(session, bot, tk.CHAT, card)
    choice_message = _sent_about(session, card)[-1].message_id
    choice = fake.last()["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    def broken_library(*_):
        raise RuntimeError("connection failed")

    monkeypatch.setattr(commands, "library_choices", broken_library)
    handle(session, bot, tk.press(choice, message_id=choice_message))
    assert "couldn't finish the image request" in fake.last()["text"]
    assert card.column == "On hold"


def test_held_review_shows_images_notes_and_approval_without_scheduling(session, bot, fake, paired):
    card = _editor_hold(session)
    fake.clear()
    bot_mod.send_notices(session, bot, log=_quiet)
    assert fake.of("sendMediaGroup") and fake.of("sendPhoto")
    message = fake.last()
    text = "\n".join(fake.texts())
    assert "The cover needs a different image" in text and "caption" in text
    assert "scheduling is a separate step" in text
    assert fake.buttons(message) == ["ok", "im", "rt", "bp", "dr"]
    data = message["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    handle(session, bot, tk.press(data))
    session.refresh(card)
    assert card.column == "Awaiting approval"
    assert all(p.status == "draft" for p in posts.drafts(session, card.id))
    assert fake.buttons(fake.last()) == ["sc", "bx", "pk", "bt", "im", "rt", "hd"]


def test_held_review_can_be_reopened_and_rejects_stale_or_unpaired_approval(session, bot, fake, paired):
    card = _editor_hold(session)
    handle(session, bot, tk.message("/review"))
    data = fake.last()["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    handle(session, bot, tk.press(data, chat=tk.OTHER))
    assert card.column == "On hold" and fake.of("answerCallbackQuery")[-1]["text"] == commands.PRIVATE
    post = posts.drafts(session, card.id)[0]
    post.updated_at = post.updated_at + timedelta(seconds=1)
    session.commit()
    handle(session, bot, tk.press(data))
    assert card.column == "On hold" and "out of date" in fake.last()["text"]


def test_cannot_accept_held_review_when_telegram_cannot_send_photo(session, bot, fake, paired):
    card = _editor_hold(session)
    fake.fail["sendMediaGroup"] = "bad image"
    handle(session, bot, tk.message("/review"))
    assert "ok" not in fake.buttons(fake.last())
    assert card.column == "On hold"


def test_failed_revision_reports_current_failure_and_invalidates_old_approval(session, bot, fake, paired):
    card = _editor_hold(session)
    handle(session, bot, tk.message("/review"))
    old_button = fake.last()["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    transitions.stuck(session, card, "the revision job failed: renderer unavailable", actor="worker")
    fake.clear()
    handle(session, bot, tk.message("/review"))
    assert "revision job failed" in "\n".join(fake.texts())
    assert "previous draft" in "\n".join(fake.texts())
    assert "ok" not in fake.buttons(fake.last())
    handle(session, bot, tk.press(old_button))
    assert card.column == "On hold"
    assert "out of date" in fake.last()["text"]


def _reply_target(session, bot, card) -> int:
    commands.say(session, bot, tk.CHAT, "about this card", card=card)
    return _sent_about(session, card)[-1].message_id


def test_hold_on_a_scheduled_card_unschedules_it_first(session, bot, fake, paired):
    c = _awaiting(session)
    transitions.schedule(session, c, actor="admin")
    handle(session, bot, tk.message("/hold"))
    session.refresh(c)
    assert c.column == "On hold" and {p.status for p in posts.for_card(session, c.id)} == {"draft"}


def test_to_the_editor_and_back_to_the_partner_buttons(session, bot, fake, paired):
    c = _awaiting(session)
    transitions.hold(session, c, actor="admin")
    handle(session, bot, tk.press(render.data("rv", c.id)))
    session.refresh(c)
    assert c.column == "In review" and "review" in _jobs(session, c)
    d = _briefed(session)
    transitions.hold(session, d, actor="admin")
    handle(session, bot, tk.press(render.data("bp", d.id)))
    session.refresh(d)
    assert d.column == "Planned" and _jobs(session, d) == ["partner"]


def test_a_failed_publish_is_held_and_back_to_draft_puts_it_back(session, bot, fake, paired):
    c = _awaiting(session)
    transitions.schedule(session, c, actor="admin")
    for p in posts.for_card(session, c.id):
        p.status, p.error = "failed", "Media upload has failed."
    session.commit()
    sync.sync(session, log=_quiet)
    assert c.column == "On hold"
    fake.clear()
    bot_mod.send_notices(session, bot, log=_quiet)
    held = fake.last()
    assert "On hold" in held["text"] and "Media upload has failed" in held["text"]
    assert fake.buttons(held) == ["bd", "dr"]
    handle(session, bot, tk.press(render.data("bd", c.id)))
    session.refresh(c)
    assert c.column == "Awaiting approval" and {p.status for p in posts.for_card(session, c.id)} == {"draft"}
    assert fake.buttons(fake.last()) == ["sc", "bx", "pk", "bt", "im", "rt", "hd"]


def test_it_went_live_and_a_week_later_the_numbers(session, bot, fake, paired):
    c = _awaiting(session)
    transitions.schedule(session, c, actor="admin")
    week_ago = datetime.now(UTC) - timedelta(days=8)
    for p in posts.for_card(session, c.id):
        p.status, p.permalink, p.published_at = "published", f"https://www.instagram.com/p/{p.kind}/", week_ago
        p.insights = {"reach": 1200, "saved": 30, "shares": 12, "profile_visits": 9}
    session.commit()
    sync.sync(session, log=_quiet)
    assert c.column == "Measured"
    bot_mod.send_notices(session, bot, log=_quiet)
    texts = "\n".join(fake.texts())
    assert "Scheduled:" in texts  # the admin scheduled it on the web: the chat hears of it
    assert "<b>Live:" in texts and "https://www.instagram.com/p/carousel/" in texts
    assert "A week on" in texts and "reach 2,400 · saves 60 · shares 24 · profile visits 18" in texts


# ---------------------------------------------------------------- commands


def test_new_files_a_card_for_the_partner(session, bot, fake, paired):
    handle(session, bot, tk.message("/new"))
    assert "followed by a link" in fake.last()["text"]
    handle(session, bot, tk.message("/new Rye week\nWe baked rye all week; 72 hours, 78% hydration."))
    c = session.scalars(select(Card)).one()
    assert c.column == "Planned" and c.title == "Rye week" and c.created_by == f"telegram:{tk.CHAT}"
    assert _jobs(session, c) == ["partner"] and "New post to plan" in fake.last()["text"]
    assert session.scalars(select(Notice).where(Notice.kind == "filed")).all() == []  # told directly instead
    assert _sent_about(session, c)


def test_new_with_a_link_reads_the_page_and_its_picture(session, bot, fake, paired):
    page = """<html><head><title>Rye | Bakes</title><meta property="og:site_name" content="Bakes">
    <meta property="og:image" content="/img/rye.jpg"></head><body><article><h1>A week of rye</h1>
    <p>Seven loaves of rye, 72 hours each.</p></article></body></html>"""

    def site(request):
        if request.url.path == "/img/rye.jpg":
            return httpx.Response(200, content=tk.jpeg(), headers={"content-type": "image/jpeg"})
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(site))
    handle(session, bot, tk.message("/new https://bakes.example/rye"), fetch=client)
    c = session.scalars(select(Card)).one()
    assert c.title == "A week of rye" and c.source_ref == "url:https://bakes.example/rye"
    hero = c.facts["hero"]
    assert hero["credit"] == "Image: Bakes" and hero["licence"] is None and hero["asset_id"]
    asset = session.get(storage.ContentAsset, __import__("uuid").UUID(hero["asset_id"]))
    assert asset.provenance["source"] == "page" and asset.provenance["permalink"] == "https://bakes.example/rye"
    handle(session, bot, tk.message("/new https://bakes.example/rye"), fetch=client)
    assert "already a card" in fake.last()["text"]


def test_board_queue_status_help_and_an_unknown_command(session, bot, fake, paired):
    handle(session, bot, tk.message("/board"))
    assert "The board is empty" in fake.last()["text"]
    c = _awaiting(session)
    _briefed(session)
    handle(session, bot, tk.message("/board"))
    assert "<b>Brief ready</b> (1)" in fake.last()["text"] and "<b>Awaiting approval</b> (1)" in fake.last()["text"]
    handle(session, bot, tk.message("/queue"))
    assert "Nothing is scheduled" in fake.last()["text"]
    transitions.schedule(session, c, actor="admin")
    handle(session, bot, tk.message("/queue"))
    assert "<b>Scheduled</b>" in fake.last()["text"] and "carousel" in fake.last()["text"]

    session.add(AgentRun(agent="partner", model="claude-opus-5-5", cost_usd=1.25, status="ok",
                         started_at=datetime.now(UTC)))
    session.commit()
    handle(session, bot, tk.message("/status"), graph=graph_for(FakeGraph()), renderer=kit._renderer())
    text = fake.last()["text"]
    assert "Instagram: @bakery_tales" in text and "2 of 100 posts" in text and "Renderer: up" in text
    assert "Spend: $1.25 of $30.00 this month" in text and "Agents: running" in text
    handle(session, bot, tk.message("/help"))
    assert "/schedule [time]" in fake.last()["text"]
    handle(session, bot, tk.message("/frobnicate"))
    assert "I don't know /frobnicate" in fake.last()["text"]


def test_pause_stops_the_scout_and_the_agents_and_resume_starts_them(session, bot, fake, paired):
    c = kit.card(session)
    jobs.enqueue(session, "partner", c.id)
    handle(session, bot, tk.message("/pause"))
    assert control.paused(session)["by"] == f"telegram:{tk.CHAT}"
    call = kit.scripted(kit.partner_reply())
    assert jobs.run_pending(session, kit.deps(partner=call), log=_quiet) == [] and call.calls == []
    handle(session, bot, tk.message("/resume"))
    assert control.paused(session) is None and fake.last()["text"].startswith("Resumed.")
    assert [j.status for j in jobs.run_pending(session, kit.deps(partner=call), log=_quiet)] == ["done"]


# ---------------------------------------------------------------- the spend ceiling


def test_the_spend_ceiling_stops_the_agents_and_says_so_once(session, bot, fake, paired, monkeypatch):
    monkeypatch.setattr(config.llm, "monthly_usd", 5)
    session.add(AgentRun(agent="partner", model="claude-opus-5-5", cost_usd=5.10, status="ok",
                         started_at=datetime.now(UTC)))
    session.commit()
    c = kit.card(session)
    jobs.enqueue(session, "partner", c.id)
    call = kit.scripted(kit.partner_reply())
    assert jobs.run_pending(session, kit.deps(partner=call), log=_quiet) == []
    assert call.calls == [] and _jobs(session, c) == ["partner"] and c.column == "Planned"
    jobs.run_pending(session, kit.deps(partner=call), log=_quiet)
    bot_mod.send_notices(session, bot, log=_quiet)
    stopped = [t for t in fake.texts() if "The agents have stopped" in t]
    assert len(stopped) == 1 and "$5.10" in stopped[0] and "$5.00" in stopped[0]

    with pytest.raises(llm.BudgetExceeded):  # and every call checks it too
        llm.structured_call(agent="partner", system="s", messages=[], schema={}, session=session)

    monkeypatch.setattr(config.llm, "monthly_usd", 50)  # raised: they go on
    assert [j.status for j in jobs.run_pending(session, kit.deps(partner=call), log=_quiet)] == ["done"]


def test_status_and_the_digest_show_the_ceiling(session, bot, fake, paired, monkeypatch):
    monkeypatch.setattr(config.llm, "monthly_usd", 1)
    session.add(AgentRun(agent="maker", model="claude-sonnet-5-5", cost_usd=2, status="ok",
                         started_at=datetime.now(UTC)))
    session.commit()
    handle(session, bot, tk.message("/status"), graph=graph_for(FakeGraph()), renderer=kit._renderer())
    assert "the agents have stopped" in fake.last()["text"] and "stopped at the spend ceiling" in fake.last()["text"]
    assert "The agents are stopped" in commands.digest_text(session)


# ---------------------------------------------------------------- the digest


def test_the_digest_goes_once_a_day_at_its_time(session, bot, fake, paired):
    _briefed(session)
    c = _awaiting(session)
    transitions.schedule(session, c, actor="admin")
    early = datetime(2031, 3, 3, 7, 59, tzinfo=UTC)  # London is on UTC in March
    assert bot_mod.send_digest(session, bot, now=early) is False
    at = datetime(2031, 3, 3, 8, 1, tzinfo=UTC)
    assert bot_mod.send_digest(session, bot, now=at) is True
    text = fake.last()["text"]
    assert "Today on @bakery_tales" in text and "<b>Needs you</b> (1)" in text and "brief to approve" in text
    assert "Spend: $0.00 of $30.00 this month." in text
    assert bot_mod.send_digest(session, bot, now=at + timedelta(hours=3)) is False
    assert bot_mod.send_digest(session, bot, now=at + timedelta(days=1)) is True


# ---------------------------------------------------------------- the 4,096-character limit


def test_brief_and_draft_buttons_link_to_the_private_preview(session):
    card = _briefed(session)
    url = f"https://postdesk.example/cards/{card.id}/preview"
    for buttons in (render.brief_buttons(card), render.schedule_buttons(card, "tomorrow")):
        assert [{"text": "Preview Instagram & X", "url": url}] in buttons["inline_keyboard"]
        assert any("callback_data" in button for row in buttons["inline_keyboard"] for button in row)


def test_a_long_brief_is_split_at_paragraphs_with_the_buttons_on_the_last(session, bot, fake, paired):
    paras = [f"Paragraph {i}: " + ("the dough rests and rises slowly overnight in the cold. " * 12) for i in range(40)]
    c = _briefed(session)
    c.brief_md = "\n\n".join(paras)
    session.commit()
    outs = render.brief(session, c)
    texts = [o.text for o in outs if o.text]
    assert len(texts) >= 3
    assert all(len(render.visible(t)) <= render.LIMIT for t in texts)
    assert [o.buttons is not None for o in outs if o.text] == [False] * (len(texts) - 1) + [True]
    shown = render.visible("\n\n".join(texts))
    assert all(p.strip() in shown for p in paras)  # nothing lost, no paragraph cut
    commands.send(session, bot, tk.CHAT, outs, card=c)
    assert all(len(render.visible(p["text"])) <= render.LIMIT for p in fake.of("sendMessage"))
    assert len(_sent_about(session, c)) == len(texts)


def test_split_cuts_a_paragraph_too_long_for_one_message_at_lines_then_spaces():
    one = "word " * 2000
    pieces = render.split(one, limit=1000)
    assert all(len(p) <= 1000 for p in pieces) and " ".join(pieces).split() == one.split()
    lines = "\n".join(f"line {i} " + "x" * 50 for i in range(100))
    pieces = render.split(lines, limit=1000)
    assert all(len(p) <= 1000 for p in pieces) and "\n".join(pieces) == lines
    assert render.split("") == [] and render.split("short") == ["short"]


def test_markdown_becomes_telegram_html():
    md = ("## The hook\n**Post**: a carousel of *six* slides\n- one <b>&\n- two `code`\n"
          "|---|---|\n[the article](https://bakes.example/a?x=1&y=2)")
    out = render.md_to_html(md)
    assert out.splitlines()[0] == "<b>The hook</b>"
    assert "<b>Post</b>: a carousel of <i>six</i> slides" in out
    assert "• one &lt;b&gt;&amp;" in out and "<code>code</code>" in out and "|---|" not in out
    assert '<a href="https://bakes.example/a?x=1&amp;y=2">the article</a>' in out


def test_html_telegram_cannot_parse_is_sent_plain(session, bot, fake, paired):
    fake.fail["sendMessage"] = "Bad Request: can't parse entities"
    commands.say(session, bot, tk.CHAT, "<b>broken")
    assert fake.last()["text"] == "&lt;b&gt;broken" or fake.last()["text"] == "broken"


# ---------------------------------------------------------------- polling and the webhook


def test_polling_handles_each_update_once_and_survives_a_bad_one(session, bot, fake, paired, monkeypatch):
    fake.updates = [tk.message("/help"), {"update_id": 99999, "message": {"chat": {"id": int(tk.CHAT)}}},
                    tk.message("/board")]
    real = commands.handle

    def flaky(session, bot, update, deps=None):
        if update["update_id"] == 99999:
            raise RuntimeError("boom")
        return real(session, bot, update, deps)

    monkeypatch.setattr(commands, "handle", flaky)
    assert bot_mod.poll_once(session, bot, timeout=0, log=_quiet) == 3
    assert len(fake.texts()) == 2
    assert store.get(session, bot_mod.OFFSET) == fake.of("getUpdates")[0].get("offset", 0) or \
        store.get(session, bot_mod.OFFSET) > 0
    bot_mod.poll_once(session, bot, timeout=0, log=_quiet)
    assert fake.of("getUpdates")[-1]["offset"] == store.get(session, bot_mod.OFFSET)


def test_polling_without_a_public_url_and_a_webhook_with_one(monkeypatch, fake, bot):
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8080")
    assert bot_mod.mode() == "polling"
    assert bot_mod.start(bot, log=_quiet) == "polling" and fake.of("deleteWebhook")
    monkeypatch.setattr(settings, "public_base_url", "https://desk.bakes.example")
    assert bot_mod.mode() == "webhook"
    bot_mod.start(bot, log=_quiet)
    hook = fake.of("setWebhook")[-1]
    assert hook["url"] == "https://desk.bakes.example/api/telegram/webhook"
    assert hook["secret_token"] == bot_mod.webhook_secret()
    monkeypatch.setattr(config.telegram, "mode", "polling")
    assert bot_mod.mode() == "polling"


def test_the_webhook_needs_the_secret_and_handles_the_update(session, fake, paired, monkeypatch):
    monkeypatch.setattr(api, "default_client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    with TestClient(create_app(), base_url="https://postdesk.example") as client:
        update = tk.message("/help")
        assert client.post("/api/telegram/webhook", json=update).status_code == 403
        assert client.post("/api/telegram/webhook", json=update,
                           headers={"X-Telegram-Bot-Api-Secret-Token": "nope"}).status_code == 403
        r = client.post("/api/telegram/webhook", json=update,
                        headers={"X-Telegram-Bot-Api-Secret-Token": bot_mod.webhook_secret()})
        assert r.status_code == 200 and "/schedule [time]" in fake.last()["text"]
        r = client.post("/api/telegram/webhook", json=tk.message("/help", chat=tk.OTHER),
                        headers={"X-Telegram-Bot-Api-Secret-Token": bot_mod.webhook_secret()})
        assert r.status_code == 200 and fake.texts(tk.OTHER) == [commands.PRIVATE]


def test_notices_wait_for_a_paired_chat_and_stale_ones_are_dropped(session, bot, fake):
    c = kit.card(session)  # filed before any chat was paired: no notice
    assert session.scalars(select(Notice)).all() == []
    code = pairing.new_code(session, tk.CHAT)
    pairing.link(session, code, by="admin")
    d = kit.card(session)
    n = session.scalars(select(Notice)).one()
    assert (n.kind, n.card_id) == ("filed", d.id)
    n.created_at -= timedelta(days=3)
    session.commit()
    assert bot_mod.send_notices(session, bot, log=_quiet) == 0 and fake.texts() == []
    assert n.sent_at is not None and "too old" in n.error and c.id != d.id


def test_a_notice_that_fails_is_tried_again(session, bot, fake, paired):
    kit.card(session)
    fake.fail["sendMessage"] = "Too Many Requests"
    assert bot_mod.send_notices(session, bot, log=_quiet) == 0
    n = session.scalars(select(Notice)).one()
    assert n.sent_at is None and n.attempts == 1
    assert bot_mod.send_notices(session, bot, log=_quiet) == 1 and "New post to plan" in fake.last()["text"]


def test_queue_reject_in_the_web_does_not_break_the_chat(session, bot, fake, paired):
    c = _awaiting(session)
    for p in posts.for_card(session, c.id):
        queue.reject(session, p.id)
    sync.queue_changed(session, c.id, actor="admin", log=_quiet)
    handle(session, bot, tk.press(render.data("sc", c.id)))
    assert fake.of("answerCallbackQuery")[-1]["text"] == "That card is closed."


def test_schedule_both_and_unschedule_from_telegram(session, bot, fake, paired, monkeypatch):
    from postdesk.x import credentials

    monkeypatch.setattr(credentials, "read", lambda session: object())
    card = _awaiting(session)
    feed = next(p for p in posts.drafts(session, card.id) if p.kind != "story")
    feed.slides = feed.slides[:4]
    session.commit()
    handle(session, bot, tk.press(render.data("bt", card.id)))
    asked = _sent_about(session, card)[-1]
    assert asked.purpose == "pick_both_time"
    handle(session, bot, tk.message("tomorrow 18:00", reply_to=asked.message_id))
    rows = posts.for_card(session, card.id)
    ig = next(p for p in rows if p.channel == "instagram" and p.kind != "story")
    x = next(p for p in rows if p.channel == "x")
    story = next(p for p in rows if p.kind == "story")
    assert x.caption == ig.caption and x.slides == ig.slides and x.publish_at == ig.publish_at
    assert story.channel == "instagram" and story.publish_at == ig.publish_at + timedelta(minutes=15)
    assert "Instagram" in fake.last()["text"] and "X" in fake.last()["text"]
    handle(session, bot, tk.message("/unschedule"))
    assert card.column == "Awaiting approval"
    assert all(p.status != "approved" for p in posts.for_card(session, card.id))


def test_long_shared_caption_is_edited_and_reviewed_before_scheduling(session, bot, fake, paired):
    card = _awaiting(session)
    feed = next(p for p in posts.drafts(session, card.id) if p.kind != "story")
    queue.update_post(session, feed.id, caption="a" * 500)
    handle(session, bot, tk.press(render.data("bx", card.id)))
    assert card.column == "Awaiting approval"
    assert all(p.status == "draft" for p in posts.for_card(session, card.id))
    asked = _sent_about(session, card)[-1]
    assert asked.purpose == "edit_shared_caption" and "Nothing was scheduled" in fake.last()["text"]
    handle(session, bot, tk.message("A shared caption.", reply_to=asked.message_id))
    assert feed.caption == "A shared caption." and card.column == "In review"
    assert "review" in _jobs(session, card)
    assert all(p.status == "draft" for p in posts.for_card(session, card.id))


def test_failed_crosspost_validation_is_atomic(session, bot, fake, paired, monkeypatch):
    from postdesk.x import credentials

    monkeypatch.setattr(credentials, "read", lambda session: None)
    card = _awaiting(session)
    feed = next(p for p in posts.drafts(session, card.id) if p.kind != "story")
    feed.slides = feed.slides[:4]
    session.commit()
    handle(session, bot, tk.press(render.data("bx", card.id)))
    assert "Connect X" in fake.last()["text"]
    assert card.column == "Awaiting approval"
    assert all(p.status == "draft" for p in posts.for_card(session, card.id))


def test_board_can_reopen_a_specific_card(session, bot, fake, paired):
    first = _awaiting(session)
    _briefed(session)
    handle(session, bot, tk.message("/board"))
    assert "vw" in fake.buttons(fake.last())
    handle(session, bot, tk.press(render.data("vw", first.id)))
    assert "Ready to schedule" in "".join(fake.texts())


def test_retry_failed_x_does_not_repost_instagram(session, bot, fake, paired, monkeypatch):
    from postdesk.x import credentials

    monkeypatch.setattr(credentials, "read", lambda session: object())
    card = _awaiting(session)
    feed = next(p for p in posts.drafts(session, card.id) if p.kind != "story")
    feed.slides = feed.slides[:4]
    session.commit()
    handle(session, bot, tk.press(render.data("bx", card.id)))
    rows = posts.for_card(session, card.id)
    x = next(p for p in rows if p.channel == "x")
    for p in rows:
        p.status = "failed" if p.channel == "x" else "published"
    x.error = "X rejected the request"
    session.commit()
    sync.sync(session, log=_quiet)
    handle(session, bot, tk.press(render.data("bd", card.id)))
    assert "Retry X" in fake.last()["reply_markup"]["inline_keyboard"][0][0]["text"]
    handle(session, bot, tk.press(render.data("sc", card.id)))
    assert x.status == "approved"
    assert all(p.status == "published" for p in rows if p.channel == "instagram")
