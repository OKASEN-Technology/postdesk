"""
What a person does to the board besides moving a card, the same from the
web UI and from Telegram: file a post to make ("post about this"), comment
on a card, attach an image for the partner. The moves themselves are in
transitions.py.
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.orm import Session

from postdesk import instagram as ig
from postdesk import jobs, storage
from postdesk.board import cards, scout, transitions
from postdesk.board.cards import BoardError
from postdesk.config import config
from postdesk.db.models import Card
from postdesk.sources import SourceError
from postdesk.sources.manual import facts_from
from postdesk.sources.url import UrlSource


def file_post(session: Session, *, title: str, text: str, link: str | None, column: str, kind: str,
              template: str | None, actor: str, client: httpx.Client | None = None, log=None) -> Card:
    """A card from a title and text, or from a link alone (the page is
    fetched, and its own picture found if `page` is in `[images].sources`).
    In Planned the partner starts on it."""
    if link and not (text or "").strip():
        f = UrlSource(template=template or "article", client=client).facts(f"url:{link}")
        ref = f.ref
        if f.hero is None and "page" in config.images.sources:
            from postdesk.images import page

            try:
                f.hero = page.hero(session, f.url or link, client=client)
            except Exception as e:  # a missing picture never stops the card
                if log:
                    log(f"page image for {link}: {e}")
    else:
        ref = f"manual:{uuid.uuid4()}"
        f = facts_from(title or (text or "").strip().splitlines()[0][:80], text or title, link=link, ref=ref)
    if template:
        f.template = template
    fp = scout.fingerprint("url", ref) if ref.startswith("url:") else None
    if fp and cards.by_fingerprint(session, fp):
        raise BoardError(f"there is already a card for {link}")
    card = cards.file_card(session, title=(title or "").strip() or f.title, created_by=actor, column=column,
                           kind=kind, body_md=(text or "").strip() or f.summary, source_ref=ref, fingerprint=fp,
                           facts=f.to_json(), plan={"template": f.template} if f.template else {},
                           reason="filed by the admin")
    if column == "Planned":
        jobs.enqueue(session, "partner", card.id, {"reason": "the admin just filed this card: write its brief"})
    return card


def comment(session: Session, card: Card, body_md: str) -> bool:
    """The admin's comment. True if the partner was asked to answer it (the
    card is in Planned or Brief ready)."""
    if not (body_md or "").strip():
        raise BoardError("say something")
    cards.comment(session, card, "user", body_md)
    if card.column in transitions.PARTNER_COLUMNS and card.closed_at is None:
        jobs.enqueue(session, "partner", card.id, {"reason": "the admin commented: answer them"})
        return True
    return False


def attach_image(session: Session, card: Card, data: bytes, *, filename: str, note: str, actor: str) -> dict:
    """An image for the partner to judge, with a note on where it is from
    (its credit), stored with that note as its provenance (the `attachment`
    image source, images/attachment.py). The partner can use it only with
    a credit."""
    if "attachment" not in config.images.sources:
        raise BoardError("attached images are off ([images].sources in postdesk.toml has no `attachment`)")
    if not data:
        raise BoardError("empty file")
    try:
        jpeg, _ = ig.to_jpeg_bytes(data)
    except OSError as e:
        raise SourceError(f"not an image: {e}") from e
    try:
        asset = storage.store_asset(
            session, data=jpeg, filename=(filename or "attachment").rsplit(".", 1)[0] + ".jpg",
            content_type="image/jpeg", uploaded_by=actor,
            provenance={"source": "attachment", "card_id": str(card.id), "note": note.strip() or None,
                        "credit": None, "licence": None, "uploaded_by": actor, "generated": False})
    except storage.AssetRejected as e:
        raise SourceError(str(e)) from e
    att = {"asset_id": str(asset.id), "url": storage.asset_url(asset.id), "filename": filename,
           "note": note.strip() or None}
    cards.comment(session, card, "user", note.strip() or "An image for this post.", attachments=[att])
    if card.column in transitions.PARTNER_COLUMNS and card.closed_at is None:
        jobs.enqueue(session, "partner", card.id, {"reason": "the admin attached an image: judge it"})
    return att
