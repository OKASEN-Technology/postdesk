"""
The queue and the board in step:

- `sync`: a card's posts all published -> Live, with the permalinks; one
  failed -> On hold with the error (saying what is already live, if any);
  every post rejected -> the card is closed. Run by the worker every
  minute and after each publisher pass.
- `queue_changed`: a person approved, unapproved or rejected a card's post
  in the queue screens instead of on the board; the card follows, with
  that person as the actor (approving every draft there is the Schedule
  gate, passed by the admin).
- `file_drafts`: a post made by hand in the queue (or with `postdesk
  make`) that has no card gets one in In review, so the editor reads it
  and the board stays the one place to look.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from postdesk import jobs
from postdesk.board import cards, posts, transitions
from postdesk.board.cards import BoardError
from postdesk.db.models import Card, SocialPost

MAX_FILE_PER_RUN = 5
SYNCED = {"Making", "In review", "Awaiting approval", "Scheduled", "Approved"}


def sync_action(rows: list[SocialPost]) -> str:
    """live | partial | failed | rejected | scheduled | wait, from a card's posts."""
    st = [p.status for p in rows]
    active = [s for s in st if s != "rejected"]
    if not active:
        return "rejected"
    if any(s == "failed" for s in active):
        return "partial" if "published" in active else "failed"
    if all(s == "published" for s in active):
        return "live"
    if all(s in ("approved", "publishing", "published") for s in active):
        return "scheduled"
    return "wait"


def _live_comment(rows: list[SocialPost]) -> str:
    return "Live:\n\n" + "\n".join(f"- {p.kind}: {p.permalink or '(no permalink)'}" for p in rows
                                   if p.status == "published")


def sync_card(session: Session, card: Card, *, log=print) -> str:
    rows = posts.for_card(session, card.id)
    if not rows:
        return "wait"
    action = sync_action(rows)
    if action == "live" and card.column != "Live":
        cards.comment(session, card, "system", _live_comment(rows), commit=False)
        cards.set_column(session, card, "Live", actor="publisher", reason="published")
    elif action == "failed" and card.column != "On hold":
        errors = "; ".join(f"{p.kind}: {p.error}" for p in rows if p.status == "failed")
        transitions.stuck(session, card, f"publishing failed ({errors[:500]}). Check the account before you retry it "
                                         "(Back to draft in the queue, then schedule it again)", actor="publisher")
    elif action == "partial" and card.column != "On hold":
        live = ", ".join(f"the {p.kind} ({p.permalink or 'no permalink'})" for p in rows if p.status == "published")
        failed = " and the ".join(sorted({p.kind for p in rows if p.status == "failed"}))
        errors = "; ".join(f"{p.kind}: {p.error}" for p in rows if p.status == "failed")
        transitions.stuck(session, card, f"a partial failure: {live} is live, but the {failed} failed "
                                         f"({errors[:500]})", actor="publisher")
    elif action == "rejected" and card.closed_at is None:
        cards.comment(session, card, "system", "Every post on this card was rejected, so the card is closed.",
                      commit=False)
        cards.close(session, card, actor="publisher", reason="every post rejected")
    else:
        return "wait"
    log(f"card {card.id}: {action}")
    return action


def sync(session: Session, *, log=print) -> dict[str, str]:
    out = {}
    for card in cards.open_cards(session, SYNCED | {"On hold"}):
        if card.column == "On hold" and sync_action(posts.for_card(session, card.id)) not in ("live", "rejected"):
            continue  # a held card only leaves On hold when it went out after all, or was dropped
        out[str(card.id)] = sync_card(session, card, log=log)
    return out


def queue_changed(session: Session, card_id: uuid.UUID | None, *, actor: str, log=print) -> None:
    """After a person changed a card's post in the queue screens."""
    if card_id is None:
        return
    card = session.get(Card, card_id)
    if card is None or card.closed_at is not None:
        return
    rows = posts.for_card(session, card.id)
    action = sync_action(rows)
    try:
        if action == "scheduled" and card.column != "Scheduled" and cards.is_human(actor):
            when = "; ".join(f"{p.kind} {transitions._when(p.publish_at)}" for p in rows if p.status != "rejected")
            cards.set_column(session, card, "Scheduled", actor=actor, reason=f"approved in the queue: {when}")
        elif action == "wait" and card.column == "Scheduled":
            cards.set_column(session, card, "Awaiting approval", actor=actor, reason="unapproved in the queue")
        elif action == "rejected":
            sync_card(session, card, log=log)
    except BoardError as e:
        log(f"card {card.id}: {e}")


def file_drafts(session: Session, *, log=print) -> list[Card]:
    """Hand-made drafts with no card -> a card each in In review, sent to
    the editor. Fingerprinted on the post's source, so a run that died
    between filing and linking links the same card next time."""
    filed = []
    for g in posts.unfiled(session)[:MAX_FILE_PER_RUN]:
        fp = f"post:queue:{g['source_ref']}"
        card = cards.by_fingerprint(session, fp)
        if card is None:
            lead = g["posts"][0]
            kind = "story" if all(p.kind == "story" for p in g["posts"]) else "post"
            card = cards.file_card(session, title=lead.title, created_by="system", column="In review", kind=kind,
                                   body_md=f"Made by hand in the queue by {lead.created_by}.",
                                   source_ref=lead.source_ref, fingerprint=fp,
                                   reason="a draft made by hand, filed for the editor")
        posts.link(session, card, [p.id for p in g["posts"]])
        jobs.enqueue(session, "review", card.id)
        filed.append(card)
        log(f"card {card.id}: filed {g['title']!r} for review")
    return filed
