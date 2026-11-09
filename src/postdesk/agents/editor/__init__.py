"""
The editor's turn: a fresh call on a card in In review, never shown the
maker's messages. The verdict goes to `transitions.review`: pass ->
Awaiting approval, revise -> Making with the notes (On hold after
`[review].max_rounds`), hold -> On hold with the reason.

A reply that leaves a check out, repeats one, or passes with a failed
check is sent back once; then the card is held rather than passed.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from postdesk import llm
from postdesk.agents.editor import context
from postdesk.board import posts, transitions
from postdesk.db.models import Card

AGENT = "editor"
MAX_TRIES = 2


def check_review(d: dict) -> list[str]:
    want = context.checks()
    got = [c.get("check") for c in d.get("checks") or []]
    problems = []
    missing = [c for c in want if c not in got]
    if missing:
        problems.append(f"checks missing: {', '.join(missing)}")
    dupes = sorted({c for c in got if got.count(c) > 1})
    if dupes:
        problems.append(f"checks given twice: {', '.join(dupes)}")
    if d.get("verdict") == "pass" and any(not c.get("ok") for c in d.get("checks") or []):
        problems.append("a pass with a failed check; pass only when every check holds")
    if d.get("verdict") == "pass" and d.get("notes"):
        problems.append("a pass has no notes")
    return problems


def turn(session: Session, card: Card, *, call=None) -> dict | None:
    session.refresh(card)
    if card.column != "In review" or card.closed_at is not None:
        return None
    drafts = posts.drafts(session, card.id)
    if not drafts:
        transitions.stuck(session, card, "nothing to review: the card has no drafts", actor=AGENT)
        return None
    if call is None:
        def call(**kw):
            return llm.structured_call(agent=AGENT, session=session, card_id=card.id, ref=card.source_ref, **kw)

    system, schema = context.system(), context.schema()
    messages: list[dict] = [{"role": "user", "content": context.user_content(session, card, drafts)}]
    d: dict = {}
    problems: list[str] = []
    for _ in range(MAX_TRIES):
        d = call(system=system, messages=messages, schema=schema)
        problems = check_review(d)
        if not problems:
            break
        messages += [
            {"role": "assistant", "content": json.dumps(d)},
            {"role": "user", "content": "That review is incomplete:\n- " + "\n- ".join(problems)
             + "\nSend the whole review again."},
        ]
    if problems:
        transitions.stuck(session, card, "the editor's review was incomplete twice (" + "; ".join(problems) + ")",
                          actor=AGENT)
        return d
    transitions.review(session, card, d)
    return d
