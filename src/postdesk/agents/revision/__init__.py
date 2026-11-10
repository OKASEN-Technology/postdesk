"""
The revision turn: the editor's notes (or the admin's Rethink note)
applied to a card in Making, words only, through the maker's one-slide
re-render (`maker.revise`), then back to In review. A slide patch with a
number the post's sources lack is refused there; one that touches a
source line is refused before it.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from postdesk import llm
from postdesk.agents.revision import context
from postdesk.board import posts, transitions
from postdesk.db.models import Card

AGENT = "revision"


def strip_source_lines(d: dict) -> tuple[dict, list[str]]:
    """The reply without any change to a slide's source line, and what was dropped."""
    dropped = []
    for p in d.get("posts") or []:
        for s in p.get("slides") or []:
            for f in list((s.get("fields") or {}).keys()):
                if f in context.NOT_WORDS:
                    del s["fields"][f]
                    dropped.append(f"slide {int(s.get('index', 0)) + 1}'s {f} line (a person changes that)")
    return d, dropped


def turn(session: Session, card: Card, payload: dict, *, call=None, renderer=None) -> dict | None:
    session.refresh(card)
    if card.column != "Making" or card.closed_at is not None:
        return None
    drafts = posts.drafts(session, card.id)
    if not drafts:
        transitions.stuck(session, card, "nothing to revise: the card has no drafts", actor="maker")
        return None
    if renderer is None and call is None:
        # Wake the renderer before paying for an AI response that cannot be applied.
        from postdesk.maker.render import Renderer

        renderer = Renderer()
        renderer.health()
    if call is None:
        def call(**kw):
            return llm.structured_call(agent=AGENT, session=session, card_id=card.id, ref=card.source_ref, **kw)

    from postdesk import brand_library

    candidates = []
    try:
        if brand_library.read(session):
            candidates = brand_library.select_for_card(
                brand_library.available(session), f"{card.title} {card.body_md or ''} {payload.get('note') or ''}")
    except ValueError:
        pass  # The revision can still change words and report unavailable media.
    d = call(system=context.system(), schema=context.schema(),
             messages=[{"role": "user", "content": context.user_content(session, card, drafts, payload, candidates)}])
    d, dropped = strip_source_lines(d)
    if dropped:
        d["reply"] = (d.get("reply") or "") + " Not changed: " + "; ".join(dropped) + "."
    transitions.revise(session, card, d, renderer=renderer, candidates=candidates,
                       for_user=payload.get("from") == "user")
    return d
