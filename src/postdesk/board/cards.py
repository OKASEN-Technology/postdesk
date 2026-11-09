"""
Cards, their thread and their move log. Every column change goes through
`set_column`, which checks the precondition on the current column, keeps
the two gates and writes a `card_event`:

- **Approved** and **Scheduled** are set only by a person: the admin's web
  session (`admin`), the API token (`api`), a signed link from a notifier
  (`link`, notify/links.py), or the paired Telegram chat
  (`telegram:<chat id>`, and only while that chat is the paired one). An
  agent, a job, the scout, the publisher or any other chat asking for
  either is refused, and the refusal is logged on the card like a move.
- Everything else may be set by whoever the move function says.

A move that the operator should hear about (a post ready to schedule, one
live, one held by an agent) leaves a notice in the same transaction
(notify/).

The move functions themselves, one per move, are in `transitions.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from postdesk.db.models import Card, CardComment, CardEvent

COLUMNS = (
    "Inbox",
    "Planned",
    "Brief ready",
    "Approved",
    "Making",
    "In review",
    "Awaiting approval",
    "Scheduled",
    "Live",
    "Measured",
    "On hold",
)
GATES = frozenset({"Approved", "Scheduled"})
# Who counts as a person: the web session, the API token and a signed link,
# all the admin; and the paired Telegram chat (`is_human`).
HUMANS = frozenset({"admin", "api", "link"})
TELEGRAM = "telegram:"
# Columns a card is finished in; nothing moves it on from there but the publisher.
DONE = frozenset({"Live", "Measured"})
AUTHORS = ("user", "partner", "editor", "maker", "system")


class BoardError(ValueError):
    """A move the card's column does not allow, or one that makes no sense."""


class GateRefused(BoardError):
    """A move into a gate column by anyone but the admin."""


def now_utc() -> datetime:
    return datetime.now(UTC)


def is_human(actor: str, session: Session | None = None) -> bool:
    """A person: the admin, or the Telegram chat paired with the bot right
    now (which needs the session to look up)."""
    if actor in HUMANS:
        return True
    if actor.startswith(TELEGRAM) and session is not None:
        from postdesk.telegram import pairing

        linked = pairing.linked(session)
        return linked is not None and actor == f"{TELEGRAM}{linked['chat_id']}"
    return False


def _notify_move(session: Session, card: Card, to: str, *, actor: str, reason: str | None) -> None:
    from postdesk import notify

    person = is_human(actor, session)
    if to == "Awaiting approval" and not person:
        notify.emit(session, "awaiting_approval", card)
    elif to == "Scheduled":
        notify.emit(session, "scheduled", card, actor=actor)
    elif to == "Live":
        notify.emit(session, "live", card)
    elif to == "Measured":
        notify.emit(session, "measured", card)
    elif to == "On hold" and not person:
        notify.emit(session, "on_hold", card, reason=reason, actor=actor)


def get(session: Session, card_id: uuid.UUID | str, *, lock: bool = False) -> Card:
    stmt = select(Card).where(Card.id == uuid.UUID(str(card_id)))
    if lock:
        stmt = stmt.with_for_update()
    card = session.execute(stmt).scalar_one_or_none()
    if card is None:
        raise LookupError(f"card {card_id} not found")
    return card


def _event(session: Session, card: Card, to: str, *, actor: str, reason: str | None, refused: bool = False,
           frm: str | None = None) -> CardEvent:
    ev = CardEvent(card_id=card.id, from_column=card.column if frm is None else frm, to_column=to, actor=actor,
                   reason=(reason or None) and reason[:2000], refused=refused, at=now_utc())
    session.add(ev)
    return ev


def file_card(
    session: Session,
    *,
    title: str,
    created_by: str,
    column: str = "Planned",
    kind: str = "post",
    body_md: str = "",
    source_ref: str | None = None,
    fingerprint: str | None = None,
    facts: dict | None = None,
    plan: dict | None = None,
    labels: list[str] | None = None,
    reason: str | None = None,
) -> Card:
    """A new card in `column` (never a gate column), with its first event."""
    if column not in COLUMNS:
        raise BoardError(f"no column {column!r}")
    if column in GATES:
        raise GateRefused(f"a card cannot be filed straight into {column}")
    if kind not in ("post", "story"):
        raise BoardError("kind is post or story")
    now = now_utc()
    card = Card(kind=kind, column=column, title=(title.strip() or "Untitled")[:300], body_md=body_md or "",
                source_ref=source_ref, fingerprint=fingerprint, facts=facts, plan=plan or {}, labels=labels or [],
                created_by=created_by, created_at=now, updated_at=now)
    session.add(card)
    session.flush()
    _event(session, card, column, actor=created_by, reason=reason or "filed", frm="")
    if column == "Planned" and not created_by.startswith(TELEGRAM):
        from postdesk import notify

        notify.emit(session, "filed", card)
    session.commit()
    return card


def check_move(session: Session, card: Card, to: str, *, actor: str, allowed_from: tuple[str, ...] | None,
               reason: str | None = None) -> None:
    """Raise if `actor` may not move `card` to `to` now. A gate refusal is
    logged (committed) before it is raised, so it shows on the card."""
    if to not in COLUMNS:
        raise BoardError(f"no column {to!r}; the columns are {', '.join(COLUMNS)}")
    if to in GATES and not is_human(actor, session):
        _event(session, card, to, actor=actor, refused=True,
               reason=f"refused: only the admin moves a card into {to}" + (f" ({reason})" if reason else ""))
        session.commit()
        raise GateRefused(f"{actor} may not move a card into {to}; only the admin can")
    if card.closed_at is not None:
        raise BoardError("the card is closed")
    if allowed_from is not None and card.column not in allowed_from:
        raise BoardError(f"the card is in {card.column}; moving it to {to} needs {' or '.join(allowed_from)}")


def set_column(session: Session, card: Card, to: str, *, actor: str, reason: str | None = None,
               allowed_from: tuple[str, ...] | None = None, commit: bool = True) -> Card:
    """Move `card` to `to`, checked (`check_move`) and logged. Moving to the
    column it is already in is a no-op without an event."""
    check_move(session, card, to, actor=actor, allowed_from=allowed_from, reason=reason)
    if card.column == to:
        if commit:
            session.commit()  # what the caller staged with it (a comment) still lands
        return card
    _event(session, card, to, actor=actor, reason=reason)
    _notify_move(session, card, to, actor=actor, reason=reason)
    card.column = to
    if to != "On hold":
        card.stuck_reason = None
    card.updated_at = now_utc()
    if commit:
        session.commit()
    return card


def close(session: Session, card: Card, *, actor: str, reason: str) -> Card:
    """Take a card off the board (every draft rejected, or dropped by the
    admin). It keeps its column for the record."""
    if card.closed_at is None:
        _event(session, card, "Closed", actor=actor, reason=reason)
        card.closed_at = card.updated_at = now_utc()
        session.commit()
    return card


def comment(session: Session, card: Card, author: str, body_md: str, *, attachments: list[dict] | None = None,
            commit: bool = True) -> CardComment:
    if author not in AUTHORS:
        raise BoardError(f"author is one of {', '.join(AUTHORS)}")
    c = CardComment(card_id=card.id, author=author, body_md=(body_md or "").strip(), attachments=attachments or [],
                    created_at=now_utc())
    session.add(c)
    card.updated_at = c.created_at
    if commit:
        session.commit()
    return c


def thread(session: Session, card: Card) -> list[CardComment]:
    return list(session.scalars(select(CardComment).where(CardComment.card_id == card.id)
                                .order_by(CardComment.created_at, CardComment.id)))


def events(session: Session, card: Card) -> list[CardEvent]:
    return list(session.scalars(select(CardEvent).where(CardEvent.card_id == card.id)
                                .order_by(CardEvent.at, CardEvent.id)))


def attachments(session: Session, card: Card) -> list[dict]:
    """Every image attached to the card's thread, keyed `attachment-1`, ...
    in the order they were added (what the partner sees)."""
    out = []
    for c in thread(session, card):
        for a in c.attachments or []:
            out.append({**a, "key": f"attachment-{len(out) + 1}"})
    return out


def by_fingerprint(session: Session, fingerprint: str) -> Card | None:
    return session.execute(select(Card).where(Card.fingerprint == fingerprint)).scalar_one_or_none()


def open_cards(session: Session, columns: set[str] | None = None) -> list[Card]:
    stmt = select(Card).where(Card.closed_at.is_(None))
    if columns:
        stmt = stmt.where(Card.column.in_(columns))
    return list(session.scalars(stmt.order_by(Card.updated_at.desc())))
