"""
The post partner's turn: one card, many turns. Called when the scout files
a card, on each of the admin's comments while the card is in Planned or
Brief ready, on Rethink, and when a card is approved with no brief yet.

The reply is checked before anything is saved (`check_brief`): every
number in the brief, the guidance and a custom slide plan must be in the
source's facts. A reply that fails is sent back once with the problems;
if it fails again the card stays in Planned with them in the thread.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from postdesk import llm
from postdesk.agents.partner import context
from postdesk.board import facts as card_facts
from postdesk.board import transitions
from postdesk.db.models import Card

AGENT = "partner"
MAX_TRIES = 2


def check_brief(d: dict, known: set[str]) -> list[str]:
    problems = []
    for field in ("brief", "guidance"):
        stray = card_facts.stray_numbers(d.get(field) or "", known)
        if stray:
            problems.append(f"the {field} uses {', '.join(stray)}, which the source does not have; use only the "
                            "source's numbers, or ask the admin")
    for i, s in enumerate((d.get("post") or {}).get("slides") or [], 1):
        stray = card_facts.stray_numbers(s.get("brief") or "", known)
        if stray:
            problems.append(f"slide {i} of the plan uses {', '.join(stray)}, which the source does not have")
    return problems


def turn(session: Session, card: Card, *, reason: str | None = None, call=None) -> dict | None:
    session.refresh(card)
    if card.column not in transitions.PARTNER_COLUMNS or card.closed_at is not None:
        return None
    candidates = card_facts.image_candidates(session, card)
    if call is None:
        tools = context.tools(card)

        def call(**kw):
            return llm.structured_call(agent=AGENT, session=session, card_id=card.id, ref=card.source_ref,
                                       tools=tools, **kw)

    system, schema = context.system(), context.schema()
    messages: list[dict] = [{"role": "user", "content": context.user_content(session, card, candidates, reason)}]
    known = card_facts.numbers(card)
    d: dict = {}
    problems: list[str] = []
    for _ in range(MAX_TRIES):
        d = call(system=system, messages=messages, schema=schema)
        problems = check_brief(d, known)
        if not problems:
            break
        messages += [
            {"role": "assistant", "content": json.dumps(d)},
            {"role": "user", "content": "postdesk refused that turn:\n- " + "\n- ".join(problems)
             + "\nSend the whole answer again with those fixed."},
        ]
    transitions.brief(session, card, d, candidates=candidates, problems=problems)
    return d
