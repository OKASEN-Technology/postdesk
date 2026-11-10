"""
What the bot says. Messages are Telegram HTML (`parse_mode=HTML`): the
brief and the partner's replies are Markdown, turned into the few tags
Telegram knows (`md_to_html`). A message holds at most 4,096 characters
of text, so a long one is sent whole as several, split at paragraphs
(`split`, `pack`); the buttons go on the last.

Each builder returns `Out`s: a text with buttons, or photos (a brief's
images with their credit and licence, a post's slides as an album).
"""

from __future__ import annotations

import html
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from postdesk import storage
from postdesk.board import posts
from postdesk.config import settings
from postdesk.db.models import Card, ContentAsset

LIMIT = 4096
CHUNK = 3800  # Markdown per piece, leaving room for a heading


@dataclass
class Out:
    text: str | None = None
    photos: list[tuple[bytes | str, str | None]] = field(default_factory=list)
    buttons: dict | None = None
    purpose: str = "card"  # what a reply to it means (commands.py)


def esc(s: str | None) -> str:
    return html.escape(s or "", quote=False)


def visible(text: str) -> str:
    """The text Telegram shows once the tags are parsed."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _inline(s: str) -> str:
    out = []
    for part in re.split(r"(`[^`\n]+`)", s):
        if len(part) > 1 and part.startswith("`") and part.endswith("`"):
            out.append(f"<code>{esc(part[1:-1])}</code>")
            continue
        t = esc(part)
        t = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)",
                   lambda m: f'<a href="{m.group(2).replace(chr(34), "%22")}">{m.group(1)}</a>', t)
        t = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"<i>\1</i>", t)
        out.append(t)
    return "".join(out)


def md_to_html(md: str) -> str:
    """Markdown as the agents write it -> Telegram HTML: headings bold,
    bullets as •, **bold**, *italic*, `code`, [links](https://...). A
    table's rule line is dropped; its rows stay as text."""
    lines = []
    for line in (md or "").splitlines():
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", line)
        if m:
            lines.append(f"<b>{_inline(m.group(1).strip())}</b>")
            continue
        if re.fullmatch(r"\s*\|?[\s:|-]*-{3,}[\s:|-]*", line):
            continue
        m = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if m:
            lines.append(f"{m.group(1)}• {_inline(m.group(2))}")
            continue
        lines.append(_inline(line))
    return "\n".join(lines).strip()


def _hard(text: str, limit: int) -> list[str]:
    out = []
    while len(text) > limit:
        cut = text.rfind(" ", 0, limit)
        cut = cut if cut > limit // 2 else limit
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return [*out, text] if text else out


def split(text: str, limit: int = CHUNK) -> list[str]:
    """`text` in pieces of at most `limit` characters, cut at paragraphs,
    else at lines, else at a space. Nothing is dropped."""
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    units: list[tuple[str, str]] = []  # (piece, the separator before it)
    for i, para in enumerate(re.split(r"\n\s*\n", text)):
        sep = "\n\n" if i else ""
        if len(para) <= limit:
            units.append((para, sep))
            continue
        for j, line in enumerate(para.split("\n")):
            for k, piece in enumerate(_hard(line, limit)):
                units.append((piece, sep if j == 0 and k == 0 else ("\n" if k == 0 else " ")))
    chunks: list[str] = []
    cur = ""
    for piece, sep in units:
        if cur and len(cur) + len(sep) + len(piece) > limit:
            chunks.append(cur)
            cur = piece
        else:
            cur = f"{cur}{sep}{piece}" if cur else piece
    if cur:
        chunks.append(cur)
    return chunks


def pack(blocks: list[str], limit: int = LIMIT) -> list[str]:
    """HTML blocks (each under `limit` once shown) into as few messages as
    fit, a blank line between blocks."""
    out: list[str] = []
    cur = ""
    for b in (b for b in blocks if b and b.strip()):
        joined = f"{cur}\n\n{b}" if cur else b
        if cur and len(visible(joined)) > limit:
            out.append(cur)
            cur = b
        else:
            cur = joined
    if cur:
        out.append(cur)
    return out


def md_blocks(md: str) -> list[str]:
    return [md_to_html(piece) for piece in split(md)]


def messages(blocks: list[str], buttons: dict | None = None, *, purpose: str = "card") -> list[Out]:
    texts = pack(blocks)
    return [Out(text=t, buttons=buttons if i == len(texts) - 1 else None, purpose=purpose)
            for i, t in enumerate(texts)]


# ---------------------------------------------------------------- buttons

ACTIONS = {
    "go": "Approve",
    "rt": "Rethink",
    "hd": "Hold",
    "sc": "Schedule",
    "pk": "Pick a time",
    "bd": "Back to draft",
    "rv": "To the editor",
    "bp": "Back to the partner",
    "dr": "Drop",
}


def data(action: str, card_id: uuid.UUID) -> str:
    return f"{action}:{card_id.hex}"


def parse_data(value: str) -> tuple[str, uuid.UUID] | None:
    action, _, hexid = (value or "").partition(":")
    if action not in ACTIONS:
        return None
    try:
        return action, uuid.UUID(hex=hexid)
    except ValueError:
        return None


def keyboard(card: Card, rows: list[list[str | tuple[str, str]]]) -> dict:
    """Inline buttons; a row item is an action key, or (action, label)."""
    out = []
    for row in rows:
        out.append([{"text": item[1] if isinstance(item, tuple) else ACTIONS[item],
                     "callback_data": data(item[0] if isinstance(item, tuple) else item, card.id)} for item in row])
    return {"inline_keyboard": out}


def brief_buttons(card: Card) -> dict:
    buttons = keyboard(card, [["go"], ["rt", "hd"]])
    buttons["inline_keyboard"].insert(0, [{"text": "Preview Instagram & X", "url": preview_url(card)}])
    return buttons


def schedule_buttons(card: Card, when: str) -> dict:
    buttons = keyboard(card, [[("sc", f"Schedule for {when}")], ["pk"], ["rt", "hd"]])
    buttons["inline_keyboard"].insert(1, [{"text": "Preview Instagram & X", "url": preview_url(card)}])
    return buttons


def preview_url(card: Card) -> str:
    return f"{settings.public_base_url.rstrip('/')}/cards/{card.id}/preview"


def hold_buttons(session: Session, card: Card) -> dict:
    rows = posts.for_card(session, card.id)
    if any(p.status == "failed" for p in rows):
        return keyboard(card, [["bd"], ["dr"]])
    if posts.drafts(session, card.id):
        return keyboard(card, [["rv"], ["bp", "dr"]])
    return keyboard(card, [["bp"], ["dr"]])


# ---------------------------------------------------------------- images


def image_bytes(session: Session, asset_id: str | None) -> bytes | None:
    if not asset_id:
        return None
    try:
        asset = session.get(ContentAsset, uuid.UUID(str(asset_id)))
        return storage.asset_bytes(asset) if asset is not None else None
    except (OSError, ValueError):
        return None


def licence_line(img: dict) -> str:
    """"Photo: Jo Baker · CC BY 4.0", or "· credit only, no licence"."""
    credit = img.get("credit") or "no credit"
    lic = img.get("licence")
    return f"{credit} · {lic}" if lic else f"{credit} · credit only, no licence"


def photo_of(session: Session, img: dict) -> bytes | str | None:
    data_ = image_bytes(session, img.get("asset_id"))
    if data_:
        return data_
    url = img.get("url") or ""
    return url if url.startswith("https://") else None


# ---------------------------------------------------------------- the card messages


def when(dt) -> str:
    from postdesk.board.transitions import _when

    return _when(dt)


def brief(session: Session, card: Card, *, reply: str | None = None, buttons: bool = True) -> list[Out]:
    """Brief ready: the images it uses (each with its credit and licence),
    then the brief, then Approve · Rethink · Hold."""
    out: list[Out] = []
    for img in (card.plan or {}).get("images") or []:
        if not img.get("used"):
            continue
        photo = photo_of(session, img)
        who = img.get("for") or "the post"
        caption = f"<b>{esc(who)}</b>: {esc(licence_line(img))}"
        if photo is None:
            continue
        out.append(Out(photos=[(photo, caption)]))
    blocks = [f"<b>Brief ready: {esc(card.title)}</b>"]
    if reply and reply.strip():
        blocks += md_blocks(reply)
    blocks += md_blocks(card.brief_md or "(no brief)")
    blocks.append("<i>Reply to this message to change anything.</i>")
    out += messages(blocks, brief_buttons(card) if buttons else None)
    return out


def partner_reply(card: Card, reply: str | None, problems: list[str] | None = None) -> list[Out]:
    blocks = [f"<b>The partner, on {esc(card.title)}</b>"]
    blocks += md_blocks(reply or "")
    if problems:
        blocks.append("<b>Not ready to make:</b>\n" + "\n".join(f"• {esc(p)}" for p in problems))
    blocks.append("<i>Reply to this message to answer.</i>")
    return messages(blocks)


def _alt_blocks(alts: list[str]) -> list[str]:
    lines = [f"{i}. {esc(a)}" for i, a in enumerate(alts, 1)]
    out, cur = [], []
    for line in lines:
        if cur and len("\n".join([*cur, line])) > CHUNK:
            out.append(cur)
            cur = []
        cur.append(line)
    if cur:
        out.append(cur)
    return ["<blockquote expandable><b>Alt text</b>\n" + "\n".join(c) + "</blockquote>" for c in out]


def drafts(session: Session, card: Card, *, buttons: bool = True) -> list[Out]:
    """Awaiting approval: each post's slides as an album, its caption, its
    alt text folded away, the editor's summary and checks, the recommended
    time, then Schedule · Pick a time · Rethink · Hold."""
    out: list[Out] = []
    blocks = [f"<b>Ready to schedule: {esc(card.title)}</b>"]
    for p in posts.drafts(session, card.id):
        photos = [(b, None) for b in (image_bytes(session, s.get("asset_id")) for s in p.slides) if b]
        for i in range(0, len(photos), 10):
            out.append(Out(photos=photos[i:i + 10]))
        label = "Story" if p.kind == "story" else ("Carousel" if p.kind == "carousel" else "Post")
        head = f"<b>{label}</b>, {len(p.slides)} {'frame' if p.kind == 'story' else 'slide'}" \
               f"{'s' if len(p.slides) != 1 else ''}"
        blocks.append(head)
        if p.caption:
            blocks += [esc(piece) for piece in split(p.caption)]
        blocks += _alt_blocks([s.get("alt_text") or "" for s in p.slides])
    review = card.review or {}
    if review:
        lines = [f"<b>The editor</b>: {esc(review.get('summary') or '')}"]
        for c in review.get("checks") or []:
            mark = "✓" if c.get("ok") else "✗"
            note = f": {esc(str(c.get('note') or ''))}" if c.get("note") else ""
            lines.append(f"{mark} {esc(c.get('check'))}{note}")
        blocks.append("\n".join(lines)[:CHUNK])
    rec = posts.recommended_time(session, card)
    blocks.append(f"Recommended time: <b>{esc(when(rec))}</b>. Reply to this message to comment, or /schedule "
                  "with a time.")
    out += messages(blocks, schedule_buttons(card, when(rec)) if buttons else None)
    return out


def on_hold(session: Session, card: Card, reason: str | None, *, buttons: bool = True) -> list[Out]:
    blocks = [f"<b>On hold: {esc(card.title)}</b>", *md_blocks(reason or card.stuck_reason or "")]
    return messages(blocks, hold_buttons(session, card) if buttons else None)


def live(session: Session, card: Card) -> list[Out]:
    rows = [p for p in posts.for_card(session, card.id) if p.status == "published"]
    links = "\n".join(f"{esc(p.kind)}: {esc(p.permalink or '(no permalink)')}" for p in rows)
    return messages([f"<b>Live: {esc(card.title)}</b>", links])


def scheduled_text(session: Session, card: Card) -> str:
    at = [p.publish_at for p in posts.for_card(session, card.id) if p.status == "approved" and p.publish_at]
    return (f"Scheduled for <b>{esc(when(min(at)) if at else 'its time')}</b>: {esc(card.title)}. "
            "Reply /unschedule to pull it.")
