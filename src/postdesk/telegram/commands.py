"""
One inbound update -> one board action. The order of checks is the point:

1. Which chat is it? Before pairing, only `/start` does anything (it gives
   a code). After pairing, every chat but the paired one is told the bot is
   private, and nothing else happens: no comment, no button, no command.
2. The paired chat acts as `telegram:<chat id>`, through the same moves as
   the web UI (board/transitions.py, board/actions.py). The gates check
   that actor against the paired chat again, so a chat that is not the
   paired one cannot pass them even if it got this far.

Which card a command is about: the card of the message it replies to, else
the one card that the command fits (the only card at Brief ready, say),
else the bot asks you to reply to the card's message.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from postdesk import brand_library, control, instagram, queue, storage
from postdesk.board import actions, cards, posts, transitions
from postdesk.board.cards import BoardError
from postdesk.config import config
from postdesk.db.models import Card, SocialPost, TelegramMessage
from postdesk.sources import SourceError
from postdesk.telegram import pairing, render
from postdesk.telegram.api import Bot, TelegramError
from postdesk.telegram.render import Out, esc

HELP = """<b>What I do</b>
Reply to any card's message to talk to the partner about it; send a photo in reply, with where it is from in its \
caption, to offer it a picture.

/new &lt;text or link&gt;: a post about this
/board: every open card by column
/queue: what is scheduled
/status: the account, the renderer, this month's spend
/review: show the held post and its review
/go: approve the brief
/rethink &lt;note&gt;: back to the partner (or the maker) with a note
/schedule [time]: schedule on Instagram
/schedule_both [time]: schedule the same post on Instagram and X
/unschedule: pull a scheduled post back
/hold [why], /drop: hold it, or take it off the board
/pause, /resume: stop or start the scout and the agents"""

PRIVATE = "This bot is private."
UNPAIRED = "This bot is not paired yet. Send /start for a pairing code."
# Which cards a command fits, when it is not a reply to one.
FITS = {
    "go": ("Brief ready",),
    "rethink": ("Brief ready", "Planned", "In review", "Awaiting approval", "On hold"),
    "schedule": ("Awaiting approval",),
    "unschedule": ("Scheduled",),
    "hold": ("Planned", "Brief ready", "Approved", "Making", "In review", "Awaiting approval", "Scheduled"),
    "drop": ("Inbox", "Planned", "Brief ready", "Awaiting approval", "On hold"),
    "comment": transitions.PARTNER_COLUMNS,
    "review": ("On hold",),
}
WHAT = {
    "go": "at Brief ready",
    "rethink": "with a brief or drafts to rethink",
    "schedule": "awaiting approval",
    "unschedule": "scheduled",
    "hold": "in progress",
    "drop": "open",
    "comment": "with the partner",
    "review": "on hold",
}


class Refused(Exception):
    """A request the bot answers with a reason instead of acting."""


@dataclass
class Deps:
    """The outside world, for what a command reaches (tests fake it)."""

    fetch: httpx.Client | None = None  # /new with a link
    graph: object | None = None  # /status: the Instagram account
    renderer: object | None = None  # /status: the renderer
    now: datetime | None = None
    log: object = print
    sent: list = field(default_factory=list)


# ---------------------------------------------------------------- sending


def send(session: Session, bot: Bot, chat_id: str, outs: list[Out], *, card: Card | None = None,
         reply_to: int | None = None) -> list[int]:
    """Send `outs` in order; each message about a card is remembered, so a
    reply to it lands on that card. Returns the message ids."""
    ids: list[int] = []
    photo_failed = False
    for o in outs:
        msgs: list[dict] = []
        if o.photos:
            try:
                if len(o.photos) == 1:
                    photo, caption = o.photos[0]
                    msgs = [bot.send_photo(chat_id, photo, caption=caption)]
                else:
                    msgs = bot.send_media_group(chat_id, o.photos)
            except TelegramError as e:  # a picture Telegram will not take is not worth losing the text for
                photo_failed = True
                msgs = [bot.send_message(chat_id, f"(a picture could not be sent: {esc(str(e))[:200]})")]
        if o.text:
            buttons = o.buttons
            if photo_failed and buttons:
                rows = [[b for b in row if not b.get("callback_data", "").startswith("ok:")]
                        for row in buttons.get("inline_keyboard", [])]
                buttons = {**buttons, "inline_keyboard": rows}
                buttons["inline_keyboard"] = [row for row in buttons["inline_keyboard"] if row]
            try:
                msgs.append(bot.send_message(chat_id, o.text, reply_markup=buttons, reply_to=reply_to))
            except TelegramError as e:
                if "parse" not in str(e).lower():
                    raise
                # The HTML did not parse: the same words, plain.
                msgs.append(bot.send_message(chat_id, esc(render.visible(o.text))[:render.LIMIT],
                                             reply_markup=buttons, reply_to=reply_to))
        for m in msgs:
            if card is not None and m and m.get("message_id") is not None:
                remember(session, chat_id, m["message_id"], card, o.purpose)
            if m and m.get("message_id") is not None:
                ids.append(m["message_id"])
    session.commit()
    return ids


def remember(session: Session, chat_id: str, message_id: int, card: Card, purpose: str = "card") -> None:
    session.add(TelegramMessage(chat_id=str(chat_id), message_id=int(message_id), card_id=card.id, purpose=purpose,
                                created_at=datetime.now(UTC)))


def say(session: Session, bot: Bot, chat_id: str, text: str, *, card: Card | None = None,
        buttons: dict | None = None, purpose: str = "card", reply_to: int | None = None) -> None:
    send(session, bot, chat_id, render.messages([text], buttons, purpose=purpose), card=card, reply_to=reply_to)


def ask(session: Session, bot: Bot, chat_id: str, text: str, card: Card, purpose: str) -> None:
    """A question whose reply is the answer (a rethink note, a time)."""
    m = bot.send_message(chat_id, text, reply_markup={"force_reply": True, "selective": True,
                                                      "input_field_placeholder": "Type here"})
    remember(session, chat_id, m["message_id"], card, purpose)
    session.commit()


# ---------------------------------------------------------------- which card


def mapping(session: Session, chat_id: str, message_id: int) -> TelegramMessage | None:
    return session.execute(select(TelegramMessage).where(TelegramMessage.chat_id == str(chat_id),
                                                         TelegramMessage.message_id == int(message_id))
                           ).scalar_one_or_none()


def target(session: Session, chat_id: str, msg: dict, fits: str) -> tuple[Card, str]:
    """(the card, what a reply means) for a message: the card it replies
    to, else the only open card the command fits."""
    rt = msg.get("reply_to_message")
    if rt:
        m = mapping(session, chat_id, rt["message_id"])
        if m is None or m.card_id is None:
            raise Refused("That message is not about a card. Reply to a card's message, or /board to see them.")
        card = session.get(Card, m.card_id)
        if card is None or card.closed_at is not None:
            raise Refused("That card is closed.")
        return card, m.purpose
    open_ = cards.open_cards(session, set(FITS[fits]))
    if len(open_) == 1:
        return open_[0], "card"
    if not open_:
        raise Refused(f"No card is {WHAT[fits]}.")
    names = "\n".join(f"• {esc(c.title)} ({esc(c.column)})" for c in open_[:8])
    raise Refused(f"{len(open_)} cards are {WHAT[fits]}; reply to the card's message to say which:\n{names}")


# ---------------------------------------------------------------- the update


def command_of(text: str) -> tuple[str, str] | None:
    m = re.match(r"^/([A-Za-z_]+)(?:@\w+)?(?:\s+(.*))?$", (text or "").strip(), re.S)
    if not m:
        return None
    return m.group(1).lower(), (m.group(2) or "").strip()


def handle(session: Session, bot: Bot, update: dict, deps: Deps | None = None) -> None:
    deps = deps or Deps()
    msg = update.get("message")
    cb = update.get("callback_query")
    chat = (msg or (cb or {}).get("message") or {}).get("chat")
    if not chat:
        return
    chat_id = str(chat["id"])
    linked = pairing.linked(session)
    if linked is None:
        cmd = command_of((msg or {}).get("text") or "")
        if cmd and cmd[0] == "start":
            code = pairing.new_code(session, chat_id, pairing.chat_title(chat))
            bot.send_message(chat_id, f"Your pairing code is <b>{code}</b>.\n\nPaste it into the web page's "
                                      f"Settings → Telegram, or run\n<code>postdesk telegram link {code}</code>\n\n"
                                      "It is good for 30 minutes.")
        elif cb:
            bot.answer_callback(cb["id"], UNPAIRED)
        else:
            bot.send_message(chat_id, UNPAIRED)
        return
    if chat_id != str(linked["chat_id"]):
        if cb:
            bot.answer_callback(cb["id"], PRIVATE)
        elif msg:
            bot.send_message(chat_id, PRIVATE)
        deps.log(f"telegram: refused an update from chat {chat_id} (not the paired chat)")
        return
    actor = f"{cards.TELEGRAM}{chat_id}"
    if cb:
        button(session, bot, chat_id, cb, actor, deps)
    elif msg:
        message(session, bot, chat_id, msg, actor, deps)


def message(session: Session, bot: Bot, chat_id: str, msg: dict, actor: str, deps: Deps) -> None:
    try:
        if msg.get("photo"):
            photo(session, bot, chat_id, msg, actor, deps)
            return
        text = msg.get("text") or ""
        cmd = command_of(text)
        if cmd:
            command(session, bot, chat_id, msg, cmd[0], cmd[1], actor, deps)
        elif text.strip():
            free_text(session, bot, chat_id, msg, text.strip(), actor)
    except (Refused, BoardError, queue.QueueError, SourceError) as e:
        session.rollback()
        bot.send_message(chat_id, esc(str(e)) if not isinstance(e, Refused) else str(e),
                         reply_to=msg.get("message_id"))


def free_text(session: Session, bot: Bot, chat_id: str, msg: dict, text: str, actor: str) -> None:
    """A reply: the note for a rethink, the time to schedule at, or a comment."""
    card, purpose = target(session, chat_id, msg, "comment")
    if purpose == "rethink":
        do_rethink(session, bot, chat_id, card, text, actor)
    elif purpose in ("pick_time", "pick_both_time"):
        do_schedule(session, bot, chat_id, card, text, actor, both=purpose == "pick_both_time")
    elif purpose == "edit_shared_caption":
        from twitter_text import parse_tweet
        if card.column != "Awaiting approval":
            raise Refused("This card is no longer awaiting approval. Open it from /board.")
        parsed = parse_tweet(text)
        if not parsed.valid:
            raise Refused(f"That caption counts as {parsed.weightedLength} characters on X; maximum 280. "
                          "Reply to the caption request again.")
        feed = [p for p in posts.drafts(session, card.id)
                if p.channel == "instagram" and p.kind in ("image", "carousel")]
        if len(feed) != 1:
            raise Refused("This card no longer has one editable Instagram feed draft. Open it from /board.")
        queue.update_post(session, feed[0].id, caption=text)
        transitions.send_to_review(session, card, actor=actor,
                                   note="Shared Instagram and X caption edited in Telegram; review the updated draft.")
        say(session, bot, chat_id, f"Caption updated on <b>{esc(card.title)}</b> and sent to the editor. "
            "Nothing is scheduled; choose both platforms after review.", card=card)
    else:
        asked = actions.comment(session, card, text)
        say(session, bot, chat_id, f"Noted on <b>{esc(card.title)}</b>."
            + (" The partner is answering." if asked else ""), card=card)


def photo(session: Session, bot: Bot, chat_id: str, msg: dict, actor: str, deps: Deps | None = None) -> None:
    if not msg.get("reply_to_message"):
        raise Refused("Send the photo as a reply to the card's message, so I know which post it is for.")
    card, purpose = target(session, chat_id, msg, "comment")
    if purpose.startswith("image:"):
        _, post_id, index, version = purpose.split(":")
        post, slide = image_target(session, card, post_id, index, version)
        biggest = max(msg["photo"], key=lambda p: p.get("file_size") or p.get("width", 0))
        if biggest.get("file_size", 0) > storage.MAX_IMAGE_BYTES:
            raise Refused("That image is too large. Send a photo under 10 MB.")
        say(session, bot, chat_id, "Updating the slide with your photo. This can take a little while.", card=card)
        replace_image(session, bot, chat_id, card, post, slide, bot.get_file(biggest["file_id"]), actor,
                      "Telegram upload", deps_renderer=deps.renderer if deps else None)
        return
    note = (msg.get("caption") or "").strip()
    if not note:
        raise Refused("Say where the photo is from in its caption (\"Photo: Jo Baker, mine to use\"), so it can be "
                      "credited. Then send it again.")
    biggest = max(msg["photo"], key=lambda p: p.get("file_size") or p.get("width", 0))
    data = bot.get_file(biggest["file_id"])
    actions.attach_image(session, card, data, filename=f"telegram-{biggest['file_id'][:12]}.jpg", note=note,
                         actor=actor)
    say(session, bot, chat_id, f"Attached to <b>{esc(card.title)}</b> with your note. "
        + ("The partner will judge it." if card.column in transitions.PARTNER_COLUMNS else
           "It is on the card; send it back to the partner to use it."), card=card)


def image_version(slide: dict) -> str:
    return hashlib.sha256(str(slide.get("asset_id") or "").encode()).hexdigest()[:8]


def image_field(slide: dict) -> str | None:
    from postdesk.maker.render import schema
    spec = slide.get("spec") or {}
    fields = schema()["$defs"].get(spec.get("type"), {}).get("properties", {})
    return next((field for field in ("image", "background") if field in fields), None)


def image_target(session: Session, card: Card, post_id: str, index: str, version: str):
    try:
        post = session.get(SocialPost, uuid.UUID(hex=post_id))
        i = int(index)
    except (ValueError, TypeError):
        raise Refused("That image choice is no longer valid. Open the card and choose again.") from None
    if (card.column not in ("On hold", "Awaiting approval") or post is None or post.card_id != card.id
            or post.status != "draft" or post.channel != "instagram" or not 0 <= i < len(post.slides)
            or image_version(post.slides[i]) != version or not image_field(post.slides[i])):
        raise Refused("That slide changed or is no longer a draft. Open the card and choose again.")
    return post, i


def choose_slide(session: Session, bot: Bot, chat_id: str, card: Card) -> None:
    if card.column not in ("On hold", "Awaiting approval"):
        raise Refused("Open a held post or draft awaiting approval to change its image.")
    rows = []
    for post in posts.drafts(session, card.id):
        if post.channel != "instagram":
            continue
        for i, slide in enumerate(post.slides):
            if image_field(slide):
                label = "Story" if post.kind == "story" else "Post"
                rows.append([{"text": f"{label} · slide {i + 1}",
                              "callback_data": f"is:{post.id.hex}:{i}:{image_version(slide)}"}])
    if not rows:
        raise Refused("These drafts don't have a slide layout that supports an image.")
    say(session, bot, chat_id, "<b>Change an image</b>\nChoose the slide or Story to update.", card=card,
        buttons={"inline_keyboard": rows})


def library_choices(session: Session, card: Card) -> list[dict]:
    items = brand_library.available(session)
    selected = brand_library.select_for_card(items, f"{card.title} {card.body_md or ''}",
                                             limit=brand_library.MAX_OBJECTS)
    keys = {"brand-" + hashlib.sha256(x["key"].encode()).hexdigest()[:16]: x for x in items
            if x.get("enabled") and x.get("type") == "image"}
    return [keys[x["key"]] for x in selected if x["key"] in keys]


def brand_image_bytes(session: Session, item: dict) -> bytes:
    if not 0 < item["size"] <= storage.MAX_IMAGE_BYTES:
        raise Refused("That image is empty or exceeds 10 MB. Choose another image.")
    try:
        connection = brand_library.read(session)
        body = brand_library._connection(connection).get_object(Bucket=connection["bucket"], Key=item["key"])["Body"]
        try:
            return body.read(storage.MAX_IMAGE_BYTES + 1)
        finally:
            body.close()
    except Exception as e:
        raise Refused("I couldn't load that brand image. Please try again or upload a photo.") from e


def image_choices(session: Session, bot: Bot, chat_id: str, card: Card, post: SocialPost, i: int, page: int) -> None:
    library_error = False
    try:
        choices = library_choices(session, card)
    except ValueError:
        choices, library_error = [], True
    version = image_version(post.slides[i])
    prefix = f"{post.id.hex}:{i}:{version}"
    page = max(0, page)
    page_items = choices[page * 4:(page + 1) * 4]
    say(session, bot, chat_id, f"<b>{'Story' if post.kind == 'story' else 'Post'} · slide {i + 1}</b>\n"
        + ("Brand images are unavailable right now. You can still upload a photo." if library_error else
           "Tap an image below, or upload your own." if page_items else
           "No more brand images found. You can upload your own."), card=card)
    shown = 0
    for item in page_items:
        key = hashlib.sha256(item["key"].encode()).hexdigest()[:8]
        try:
            data = brand_image_bytes(session, item)
            if len(data) > storage.MAX_IMAGE_BYTES:
                continue
            jpeg, _ = instagram.to_jpeg_bytes(data)
            msg = bot.send_photo(chat_id, jpeg, caption=esc(item["name"][:200]),
                                 reply_markup={"inline_keyboard": [[{"text": "Use this image",
                                     "callback_data": f"ic:{prefix}:{key}"}]]})
            remember(session, chat_id, msg["message_id"], card)
            session.commit()
            shown += 1
        except (TelegramError, Refused, OSError, ValueError):
            continue
    rows = [[{"text": "Upload a photo", "callback_data": f"iu:{prefix}"}]]
    nav = []
    if page:
        nav.append({"text": "Previous", "callback_data": f"in:{prefix}:{page - 1}"})
    if (page + 1) * 4 < len(choices):
        nav.append({"text": "More images", "callback_data": f"in:{prefix}:{page + 1}"})
    if nav:
        rows.append(nav)
    rows.append([{"text": "Choose another slide", "callback_data": render.data("im", card.id)}])
    prompt = "Choose an option:"
    if page_items and not shown:
        prompt = "These images couldn't be previewed in Telegram. Try more images or upload a photo."
    say(session, bot, chat_id, prompt, card=card, buttons={"inline_keyboard": rows})


def replace_image(session: Session, bot: Bot, chat_id: str, card: Card, post: SocialPost, i: int, data: bytes,
                  actor: str, source: str, deps_renderer=None) -> None:
    from postdesk.maker import render as renderer_module
    from postdesk.maker.run import provenance
    if not data or len(data) > storage.MAX_IMAGE_BYTES:
        raise Refused("That image is empty or exceeds 10 MB. Choose another image.")
    version = image_version(post.slides[i])
    try:
        jpeg, _ = instagram.to_jpeg_bytes(data)
    except (OSError, ValueError) as e:
        raise Refused("That file could not be opened as an image. Choose another image.") from e
    try:
        asset = storage.store_asset(session, data=jpeg, filename="telegram-image.jpg", content_type="image/jpeg",
                                    uploaded_by=actor, provenance={"source": source, "generated": False})
    except storage.AssetRejected as e:
        raise Refused("That image could not be stored. Choose a smaller image and try again.") from e
    spec = dict(post.slides[i]["spec"])
    spec[image_field(post.slides[i])] = {"asset_id": str(asset.id), "url": storage.asset_url(asset.id),
                                        "credit": "Brand asset" if source == "Brand library" else "Owner supplied"}
    renderer = deps_renderer or renderer_module.Renderer()
    try:
        image = renderer.render(spec, session=session)
    except renderer_module.RenderError as e:
        raise Refused("I couldn't render the updated slide. The previous draft is unchanged; please try again.") from e
    rendered = storage.store_asset(session, data=image, filename="telegram-revised-slide.jpg",
                                   content_type="image/jpeg", uploaded_by=actor,
                                   provenance=provenance(spec, "telegram image change", renderer.theme))
    session.refresh(card)
    session.refresh(post)
    image_target(session, card, post.id.hex, str(i), version)
    queue.update_post(session, post.id, rendered={i: (str(rendered.id), spec)})
    transitions.send_to_review(session, card, actor=actor,
                               note=f"Image changed on slide {i + 1}; review the image and update alt text.")
    try:
        preview = bot.send_photo(chat_id, image, caption=f"Updated slide {i + 1} · awaiting review")
        remember(session, chat_id, preview["message_id"], card)
        session.commit()
    except TelegramError:
        pass  # The editor will still send the complete draft for review.
    say(session, bot, chat_id, "Image updated. I’m sending the new slide to the editor for review. "
        "Nothing has been scheduled.", card=card)


# ---------------------------------------------------------------- commands


def command(session: Session, bot: Bot, chat_id: str, msg: dict, name: str, args: str, actor: str,
            deps: Deps) -> None:
    if name in ("start", "help"):
        head = "Paired. This chat runs postdesk.\n\n" if name == "start" else ""
        bot.send_message(chat_id, head + HELP)
    elif name == "new":
        new(session, bot, chat_id, args, actor, deps)
    elif name == "board":
        open_ = cards.open_cards(session)
        buttons = {"inline_keyboard": [[{"text": f"{c.column}: {c.title}"[:60],
                                          "callback_data": render.data("vw", c.id)}] for c in open_[:30]]}
        bot.send_message(chat_id, board_text(session), reply_markup=buttons if open_ else None)
    elif name == "queue":
        bot.send_message(chat_id, queue_text(session, now=deps.now))
    elif name == "status":
        bot.send_message(chat_id, status_text(session, deps))
    elif name == "review":
        card, _ = target(session, chat_id, msg, "review")
        send(session, bot, chat_id, render.on_hold(session, card, card.stuck_reason), card=card)
    elif name == "pause":
        control.pause(session, by=actor)
        bot.send_message(chat_id, "Paused: the scout and the agents stop after what they are doing now. What is "
                                  "already scheduled still posts. /resume to start them again.")
    elif name == "resume":
        was = control.resume(session, by=actor)
        s = control.spend(session)
        extra = f"\n\n{esc(control.ceiling_message(s))}" if s.over else ""
        bot.send_message(chat_id, ("Resumed." if was else "They were not paused.") + extra)
    elif name in ("go", "approve"):
        card, _ = target(session, chat_id, msg, "go")
        do_approve(session, bot, chat_id, card, actor, note=args or None)
    elif name == "rethink":
        card, _ = target(session, chat_id, msg, "rethink")
        if not args:
            ask(session, bot, chat_id, f"What should change on <b>{esc(card.title)}</b>? Reply to this message.",
                card, "rethink")
            return
        do_rethink(session, bot, chat_id, card, args, actor)
    elif name in ("schedule", "schedule_both"):
        card, _ = target(session, chat_id, msg, "schedule")
        do_schedule(session, bot, chat_id, card, args, actor, both=name == "schedule_both")
    elif name == "unschedule":
        card, _ = target(session, chat_id, msg, "unschedule")
        transitions.unschedule(session, card, actor=actor)
        say(session, bot, chat_id, f"Unscheduled <b>{esc(card.title)}</b>; it is back to awaiting approval.",
            card=card, buttons=render.schedule_buttons(card, render.when(posts.recommended_time(session, card))))
    elif name == "hold":
        card, _ = target(session, chat_id, msg, "hold")
        if card.column == "Scheduled":
            transitions.unschedule(session, card, actor=actor)
        transitions.hold(session, card, actor=actor, note=args or "held from Telegram")
        say(session, bot, chat_id, f"On hold: <b>{esc(card.title)}</b>.", card=card,
            buttons=render.hold_buttons(session, card))
    elif name == "drop":
        card, _ = target(session, chat_id, msg, "drop")
        transitions.drop(session, card, actor=actor, note=args or "dropped from Telegram")
        bot.send_message(chat_id, f"Dropped <b>{esc(card.title)}</b>; its drafts are rejected.")
    else:
        bot.send_message(chat_id, f"I don't know /{esc(name)}. /help lists what I do.")


def new(session: Session, bot: Bot, chat_id: str, args: str, actor: str, deps: Deps) -> None:
    if not args:
        raise Refused("Send /new followed by a link, or a few lines on what to post about.")
    words = args.split()
    link = words[0] if len(words) == 1 and re.match(r"https?://\S+$", words[0]) else None
    if link:
        card = actions.file_post(session, title="", text="", link=link, column="Planned", kind="post",
                                 template=None, actor=actor, client=deps.fetch, log=deps.log)
    else:
        first, _, rest = args.partition("\n")
        title = first.strip()[:120]
        card = actions.file_post(session, title=title, text=rest.strip() or args, link=None, column="Planned",
                                 kind="post", template=None, actor=actor)
    say(session, bot, chat_id, f"New post to plan: <b>{esc(card.title)}</b>. The partner is writing the brief; "
        "it comes here when it is ready.", card=card)


def do_approve(session: Session, bot: Bot, chat_id: str, card: Card, actor: str, *, note: str | None = None) -> None:
    transitions.approve(session, card, actor=actor, note=note)
    if card.column == "Approved":
        say(session, bot, chat_id, f"Approved <b>{esc(card.title)}</b>. The maker is making it; the slides come "
            "here once the editor has read them.", card=card)
    else:
        say(session, bot, chat_id, f"<b>{esc(card.title)}</b> had no brief yet; the partner is writing it.",
            card=card)


def do_rethink(session: Session, bot: Bot, chat_id: str, card: Card, note: str, actor: str) -> None:
    transitions.rethink(session, card, actor=actor, note=note)
    who = "the partner" if card.column == "Planned" else "the maker"
    say(session, bot, chat_id, f"Back to {who} with your note: <b>{esc(card.title)}</b>.", card=card)


def do_schedule(session: Session, bot: Bot, chat_id: str, card: Card, when_text: str, actor: str,
                *, both: bool = False) -> None:
    when = posts.parse_when(when_text or "")
    if both and card.column == "Awaiting approval":
        from twitter_text import parse_tweet
        feed = [p for p in posts.drafts(session, card.id)
                if p.channel == "instagram" and p.kind in ("image", "carousel")]
        if len(feed) == 1:
            parsed = parse_tweet(feed[0].caption)
            if not parsed.valid and feed[0].caption:
                ask(session, bot, chat_id, f"Nothing was scheduled. This caption counts as {parsed.weightedLength} "
                    "characters on X; the limit is 280. Reply with a replacement caption for both platforms. "
                    "It will go back to the editor for review before you schedule it.", card, "edit_shared_caption")
                return
    transitions.schedule(session, card, actor=actor, when=when, crosspost_x=both)
    say(session, bot, chat_id, render.scheduled_text(session, card), card=card)


# ---------------------------------------------------------------- buttons


def acknowledge(bot: Bot, callback_id: str, deps: Deps) -> None:
    """Stop Telegram's spinner before doing work; an expired callback must not discard the action."""
    try:
        bot.answer_callback(callback_id)
    except TelegramError as e:
        deps.log(f"telegram: could not acknowledge button: {e}")


def button(session: Session, bot: Bot, chat_id: str, cb: dict, actor: str, deps: Deps) -> None:
    raw = cb.get("data") or ""
    if raw.startswith(("is:", "ic:", "in:", "iu:")):
        image_button(session, bot, chat_id, cb, actor, deps)
        return
    parsed = render.parse_data(cb.get("data") or "")
    pressed = (cb.get("message") or {}).get("message_id")
    if parsed is None:
        bot.answer_callback(cb["id"], "That button no longer does anything.")
        return
    action, card_id = parsed
    card = session.get(Card, card_id)
    if card is None or card.closed_at is not None:
        bot.answer_callback(cb["id"], "That card is closed.")
        return
    acknowledge(bot, cb["id"], deps)
    try:
        if action == "vw":
            if card.column == "Brief ready":
                send(session, bot, chat_id, render.brief(session, card), card=card)
            elif card.column == "Awaiting approval":
                send(session, bot, chat_id, render.drafts(session, card), card=card)
            elif card.column == "On hold":
                send(session, bot, chat_id, render.on_hold(session, card, card.stuck_reason), card=card)
            elif card.column == "Scheduled":
                say(session, bot, chat_id, render.scheduled_text(session, card), card=card)
            else:
                say(session, bot, chat_id, f"<b>{esc(card.title)}</b> · {esc(card.column)}. "
                    "Reply to this message with a note, or use /help for actions.", card=card)
        elif action == "go":
            do_approve(session, bot, chat_id, card, actor)
        elif action == "im":
            choose_slide(session, bot, chat_id, card)
        elif action == "ok":
            version = (cb.get("data") or "").split(":")[-1]
            if version != render.hold_version(session, card):
                raise BoardError("This review is out of date. Send /review to see the current draft.")
            transitions.accept_review_hold(session, card, actor=actor)
            send(session, bot, chat_id, render.drafts(session, card), card=card)
        elif action == "sc":
            do_schedule(session, bot, chat_id, card, "", actor)
        elif action == "bx":
            do_schedule(session, bot, chat_id, card, "", actor, both=True)
        elif action in ("pk", "bt"):
            drafts = posts.drafts(session, card.id)
            retry_x = bool(drafts) and all(p.channel == "x" for p in drafts)
            platforms = "Instagram and X" if action == "bt" else ("X" if retry_x else "Instagram")
            ask(session, bot, chat_id, f"When should <b>{esc(card.title)}</b> go out on {platforms}? "
                "Reply with a time: "
                "<i>tomorrow 18:00</i>, <i>fri 7:30</i>, or 2031-03-07T18:00.", card,
                "pick_both_time" if action == "bt" else "pick_time")
            return
        elif action == "rt":
            ask(session, bot, chat_id, f"What should change on <b>{esc(card.title)}</b>? Reply to this message.",
                card, "rethink")
            return
        elif action == "hd":
            if card.column == "Scheduled":
                transitions.unschedule(session, card, actor=actor)
            transitions.hold(session, card, actor=actor, note="held from Telegram")
            say(session, bot, chat_id, f"On hold: <b>{esc(card.title)}</b>.", card=card,
                buttons=render.hold_buttons(session, card))
        elif action == "bd":
            transitions.retry(session, card, actor=actor)
            say(session, bot, chat_id, f"Back to draft: <b>{esc(card.title)}</b>. Check the account first, then "
                "schedule it again.", card=card,
                buttons=render.schedule_buttons(card, render.when(posts.recommended_time(session, card)),
                                                retry_x=all(p.channel == "x" for p in posts.drafts(session, card.id))))
        elif action == "rv":
            transitions.send_to_review(session, card, actor=actor, note="sent to the editor from Telegram")
            say(session, bot, chat_id, f"Sent to the editor: <b>{esc(card.title)}</b>.", card=card)
        elif action == "bp":
            transitions.back_to_partner(session, card, actor=actor)
            say(session, bot, chat_id, f"Back to the partner: <b>{esc(card.title)}</b>.", card=card)
        elif action == "dr":
            transitions.drop(session, card, actor=actor, note="dropped from Telegram")
            bot.send_message(chat_id, f"Dropped <b>{esc(card.title)}</b>.")
    except (Refused, BoardError, queue.QueueError, ValueError) as e:
        session.rollback()
        bot.send_message(chat_id, f"Not done: {esc(str(e))}", reply_to=pressed)
        return
    if pressed:
        try:
            bot.clear_buttons(chat_id, pressed)
        except TelegramError:
            pass  # an old message whose buttons can no longer be edited


def image_button(session: Session, bot: Bot, chat_id: str, cb: dict, actor: str, deps: Deps) -> None:
    pressed = (cb.get("message") or {}).get("message_id")
    acknowledge(bot, cb["id"], deps)
    try:
        mapped = mapping(session, chat_id, pressed) if pressed else None
        card = session.get(Card, mapped.card_id) if mapped and mapped.card_id else None
        if card is None or card.closed_at is not None:
            raise Refused("That card is no longer available. Open it from /board.")
        parts = (cb.get("data") or "").split(":")
        if len(parts) not in (4, 5):
            raise Refused("That image choice is no longer valid. Open the card and choose again.")
        action, post_id, index, version = parts[:4]
        post, i = image_target(session, card, post_id, index, version)
        if action == "is" and len(parts) == 4:
            say(session, bot, chat_id, "Finding brand images. This can take a little while.", card=card)
            image_choices(session, bot, chat_id, card, post, i, 0)
        elif action == "in" and len(parts) == 5:
            say(session, bot, chat_id, "Finding more brand images. This can take a little while.", card=card)
            image_choices(session, bot, chat_id, card, post, i, int(parts[4]))
        elif action == "iu" and len(parts) == 4:
            ask(session, bot, chat_id, "Reply to this message with the photo you want to use. "
                "Only send an image you have permission to use.", card, f"image:{post.id.hex}:{i}:{version}")
        elif action == "ic" and len(parts) == 5:
            say(session, bot, chat_id, "Updating the slide with your selected image. This can take a little while.",
                card=card)
            candidates = [x for x in library_choices(session, card)
                          if hashlib.sha256(x["key"].encode()).hexdigest()[:8] == parts[4]]
            if len(candidates) != 1:
                raise Refused("That brand image is no longer available. Choose another.")
            chosen = candidates[0]
            data = brand_image_bytes(session, chosen)
            replace_image(session, bot, chat_id, card, post, i, data, actor, "Brand library", deps.renderer)
        else:
            raise Refused("That image choice is no longer valid. Open the card and choose again.")
    except (Refused, BoardError, queue.QueueError, ValueError, storage.AssetRejected) as e:
        session.rollback()
        bot.send_message(chat_id, f"Not done: {esc(str(e))}", reply_to=pressed)
        return
    except Exception as e:
        session.rollback()
        deps.log(f"telegram: image request failed: {type(e).__name__}: {e}")
        bot.send_message(chat_id, "I couldn't finish the image request. Check /review before trying again.",
                         reply_to=pressed)


# ---------------------------------------------------------------- what is going on


def board_text(session: Session) -> str:
    open_ = cards.open_cards(session)
    if not open_:
        return "The board is empty. /new to start a post."
    lines = ["<b>The board</b>"]
    for col in cards.COLUMNS:
        here = [c for c in open_ if c.column == col]
        if here:
            lines.append(f"\n<b>{esc(col)}</b> ({len(here)})")
            lines += [f"• {esc(c.title)}" + (f": {esc(c.stuck_reason[:80])}" if col == "On hold" and c.stuck_reason
                                              else "") for c in here[:15]]
    return render.pack(["\n".join(lines)])[0][:render.LIMIT]


def queue_text(session: Session, *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    rows = session.scalars(select(SocialPost).where(SocialPost.status.in_(("approved", "publishing")))
                           .order_by(SocialPost.publish_at)).all()
    if not rows:
        return "Nothing is scheduled. Cards ready to schedule are on /board."
    lines = ["<b>Scheduled</b>"]
    for p in rows[:30]:
        state = ""
        if p.status == "publishing":
            state = " (status unconfirmed; check the platform before retrying)" if p.error else " (publishing now)"
        lines.append(f"• {esc(render.when(p.publish_at))}: {esc(p.channel.title())} {esc(p.kind)}, {esc(p.title)}"
                     + state)
    return "\n".join(lines)


def status_text(session: Session, deps: Deps) -> str:
    from postdesk import instagram as ig
    from postdesk.instagram.setup import expiry, whoami
    from postdesk.maker.render import Renderer, RenderError

    lines = ["<b>Status</b>"]
    try:
        acct, info, used, total = whoami(deps.graph or ig.Graph())
        lines.append(f"Instagram: @{esc(acct.username)}, token expires {esc(expiry(info))}, "
                     f"{used} of {total} posts used in the last 24 hours")
    except Exception as e:
        lines.append(f"Instagram: {esc(str(e))[:300]}")
    try:
        (deps.renderer or Renderer()).health()
        lines.append("Renderer: up")
    except (RenderError, httpx.HTTPError) as e:
        lines.append(f"Renderer: {esc(str(e))[:200]}")
    s = control.spend(session)
    lines.append(f"Spend: {s.line()}" + (" (the ceiling: the agents have stopped)" if s.over else ""))
    p = control.paused(session)
    lines.append(f"Agents: paused by {esc(p.get('by'))}; /resume" if p else
                 ("Agents: stopped at the spend ceiling" if s.over else "Agents: running"))
    soon = session.scalars(select(SocialPost).where(SocialPost.status == "approved")).all()
    lines.append(f"Scheduled: {len(soon)} post(s); /queue")
    return "\n".join(lines)


def digest_text(session: Session, *, now: datetime | None = None) -> str:
    """The daily digest: what needs you, what is scheduled this week, the spend."""
    now = now or datetime.now(UTC)
    need = cards.open_cards(session, {"Brief ready", "Awaiting approval", "On hold", "Inbox"})
    lines = [f"<b>Today on @{esc(config.account.handle or 'your account')}</b>"]
    if need:
        lines.append(f"\n<b>Needs you</b> ({len(need)})")
        label = {"Brief ready": "brief to approve", "Awaiting approval": "ready to schedule", "On hold": "on hold",
                 "Inbox": "in the inbox"}
        lines += [f"• {esc(c.title)}: {label[c.column]}" for c in need[:15]]
    else:
        lines.append("\nNothing needs you.")
    week = session.scalars(select(SocialPost).where(SocialPost.status == "approved",
                                                    SocialPost.publish_at <= now + timedelta(days=7))
                           .order_by(SocialPost.publish_at)).all()
    if week:
        lines.append(f"\n<b>Scheduled this week</b> ({len(week)})")
        lines += [f"• {esc(render.when(p.publish_at))}: {esc(p.kind)}, {esc(p.title)}" for p in week[:15]]
    s = control.spend(session, now)
    lines.append(f"\nSpend: {s.line()}.")
    why = control.stopped(session, now)
    if why:
        lines.append(f"<b>The agents are stopped</b>: {esc(why)}")
    return "\n".join(lines)
