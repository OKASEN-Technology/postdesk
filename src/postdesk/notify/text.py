"""A notice as plain text, for the one-way channels: a title, a few lines,
and the links (the card in the web UI; a signed link for a gate action)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from postdesk.config import settings
from postdesk.db.models import Card, Notice


@dataclass
class Message:
    kind: str
    title: str
    text: str
    links: dict[str, str] = field(default_factory=dict)  # label -> url
    card_id: str | None = None

    def plain(self) -> str:
        lines = [self.title, "", self.text] if self.text else [self.title]
        for label, u in self.links.items():
            lines.append(f"{label}: {u}")
        return "\n".join(lines).strip()


def card_url(card: Card) -> str:
    return f"{settings.public_base_url.rstrip('/')}/cards/{card.id}"


def when(dt) -> str:
    from postdesk.board.transitions import _when

    return _when(dt)


def measured_line(card_posts) -> str:
    """"reach 1,204 · saves 31 · shares 12 · profile visits 9", summed over the card's posts."""
    keys = (("reach", "reach"), ("saved", "saves"), ("shares", "shares"), ("profile_visits", "profile visits"))
    totals: dict[str, float] = {}
    for p in card_posts:
        for k, _ in keys:
            v = (p.insights or {}).get(k)
            if isinstance(v, int | float):
                totals[k] = totals.get(k, 0) + v
    parts = [f"{label} {int(totals[k]):,}" for k, label in keys if k in totals]
    return " · ".join(parts) or "no numbers yet"


def render(session: Session, n: Notice) -> Message:
    from postdesk.board import posts
    from postdesk.control import Spend, ceiling_message
    from postdesk.notify import links

    p = n.payload or {}
    if n.kind == "spend_ceiling":
        return Message(n.kind, "postdesk: the agents have stopped",
                       ceiling_message(Spend(float(p.get("month_usd", 0)), float(p.get("ceiling_usd", 0)))),
                       {"Settings": f"{settings.public_base_url.rstrip('/')}/settings"})
    card = session.get(Card, n.card_id) if n.card_id else None
    if card is None:
        return Message(n.kind, f"postdesk: {n.kind}", "(the card is gone)")
    out = Message(n.kind, card.title, "", {"Open the card": card_url(card)}, str(card.id))
    if n.kind == "filed":
        src = (card.plan or {}).get("source")
        out.title = f"New post to plan: {card.title}" + (f" (from {src})" if src else "")
        out.text = "The partner is writing the brief."
    elif n.kind == "brief_ready":
        out.title = f"Brief ready: {card.title}"
        out.text = (card.brief_md or "").strip()
        if card.column == "Brief ready" and card.closed_at is None:
            out.links["Approve"] = links.url(session, card.id, "approve")
    elif n.kind == "partner_reply":
        out.title = f"The partner, on {card.title}"
        out.text = str(p.get("reply") or "").strip()
    elif n.kind == "awaiting_approval":
        rec = posts.recommended_time(session, card)
        review = card.review or {}
        out.title = f"Ready to schedule: {card.title}"
        out.text = "\n".join(x for x in (review.get("summary") or "", f"Recommended time: {when(rec)}") if x)
        if card.column == "Awaiting approval" and card.closed_at is None:
            out.links[f"Schedule for {when(rec)}"] = links.url(session, card.id, "schedule")
    elif n.kind == "scheduled":
        at = [x.publish_at for x in posts.for_card(session, card.id) if x.status == "approved" and x.publish_at]
        out.title = f"Scheduled: {card.title}"
        out.text = f"Scheduled for {when(min(at)) if at else 'its time'}. Unschedule it to pull it."
    elif n.kind == "live":
        live = [x for x in posts.for_card(session, card.id) if x.status == "published"]
        out.title = f"Live: {card.title}"
        out.text = "\n".join(f"{x.kind}: {x.permalink or '(no permalink)'}" for x in live)
    elif n.kind == "on_hold":
        out.title = f"On hold: {card.title}"
        out.text = str(p.get("reason") or card.stuck_reason or "").strip()
    elif n.kind == "measured":
        out.title = f"A week on: {card.title}"
        out.text = measured_line([x for x in posts.for_card(session, card.id) if x.status == "published"])
    return out
