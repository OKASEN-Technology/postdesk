"""
The post partner's turn: one card, many turns. Called when the scout files
a card, on each of the admin's comments while the card is in Planned or
Brief ready, on Rethink, when a card is approved with no brief yet, and
after its own image lookups came back.

The reply is checked before anything is saved (`check_brief`): every
number in the brief, the guidance and a custom slide plan must be in the
source's facts. A reply that fails is sent back once with the problems;
if it fails again the card stays in Planned with them in the thread.

Image lookups the reply asks for (`image_lookups`) run after the turn is
applied (images.run_lookups); what they find is filed on the card and the
partner gets another turn to choose.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from postdesk import images, jobs, llm
from postdesk.agents.partner import context
from postdesk.board import cards, transitions
from postdesk.board import facts as card_facts
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


def turn(session: Session, card: Card, *, reason: str | None = None, call=None, fetch=None, graph=None,
         pick=None, log=print) -> dict | None:
    session.refresh(card)
    if card.column not in transitions.PARTNER_COLUMNS or card.closed_at is not None:
        return None
    from postdesk import brand_assets

    candidates = card_facts.image_candidates(session, card)
    brand, warning = brand_assets.load(config_url=brand_assets.configured_url())
    candidates += brand
    if call is None:
        tools = context.tools(card)

        def call(**kw):
            return llm.structured_call(agent=AGENT, session=session, card_id=card.id, ref=card.source_ref,
                                       tools=tools, **kw)

    system, schema = context.system(), context.schema()
    content = context.user_content(session, card, candidates, reason)
    if warning:
        content.append({"type": "text", "text": f"Brand assets could not be loaded: {warning}. Tell the admin."})
    messages: list[dict] = [{"role": "user", "content": content}]
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
    if d.get("image_lookups") and not problems:
        look(session, card, d["image_lookups"], images.Context(card=card, client=fetch, graph=graph, pick=pick,
                                                                log=log))
    return d


def look(session: Session, card: Card, requests: list[dict], ctx: images.Context) -> list[dict]:
    """Run the lookups, say in the thread what came back, and give the
    partner a turn to choose (only if something did, or it needs telling)."""
    session.refresh(card)
    if card.column not in transitions.PARTNER_COLUMNS or card.closed_at is not None:
        return []
    added, problems = images.run_lookups(session, card, requests, ctx)
    lines = [f"- {c['key']}: {c['credit']} ({c.get('licence') or 'credit only, no licence'}), for {c['for']}"
             for c in added]
    lines += [f"- {p}" for p in problems]
    cards.comment(session, card, "system", "Image lookups:\n\n" + "\n".join(lines))
    if added:
        jobs.enqueue(session, "partner", card.id,
                     {"reason": f"your image lookups came back with {len(added)} picture(s): "
                                f"{', '.join(c['key'] for c in added)}; choose, and update the brief"})
    return added
