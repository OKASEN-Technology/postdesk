"""
The agents (docs/guardrails.md): each is a folder with `prompt.md` (its
system prompt), `schema.json` (what it must answer with), `context.py`
(what it is shown) and `__init__.py` (its turn, applied through
board/transitions.py).

- `partner`: writes the brief with you, in the card's thread.
- `editor`: reads the made post in a fresh context and passes, revises or
  holds it.
- `revision`: applies the editor's notes (or yours) to the words.

None of them moves a card into Approved or Scheduled, publishes, or
records a permission; the board refuses the first and nothing else in
the package lets them do the rest.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from postdesk import llm, storage
from postdesk.board import cards
from postdesk.config import config
from postdesk.db.models import Card, ContentAsset
from postdesk.maker.words import voice_text

HERE = Path(__file__).resolve().parent
AUTHOR_LABEL = {
    "user": "the admin (the only person who steers you)",
    "partner": "the partner (an earlier turn)",
    "editor": "the editor",
    "maker": "the maker",
    "system": "postdesk",
}


def prompt(name: str) -> str:
    return (HERE / name / "prompt.md").read_text(encoding="utf-8")


def schema(name: str) -> dict:
    return json.loads((HERE / name / "schema.json").read_text(encoding="utf-8"))


def account_block() -> str:
    a = config.account
    return (f"The account: @{a.handle or 'this account'}" + (f" ({a.site})" if a.site else "")
            + f". Audience: {a.audience or 'the people who follow it'}.\n\n"
            f"How the account sounds, in its own words (the voice file):\n---\n{voice_text()}\n---\n"
            f"Retired phrases, never used: {'; '.join(config.voice.retired_phrases) or '(none listed)'}.")


def system_for(name: str) -> str:
    """The agent's prompt plus the account and its voice. Stable per
    account, so it is cached between turns."""
    return prompt(name).strip() + "\n\n" + account_block()


def thread_text(session: Session, card: Card) -> str:
    lines = []
    for c in cards.thread(session, card):
        att = f" [attached: {', '.join(a.get('key') or a.get('filename') or 'an image' for a in c.attachments)}]" \
            if c.attachments else ""
        lines.append(f"--- {AUTHOR_LABEL.get(c.author, c.author)}, {c.created_at:%Y-%m-%d %H:%M}{att}:\n{c.body_md}")
    return "\n\n".join(lines) or "(no comments yet)"


def image_for(item: dict, session: Session) -> dict | None:
    """An image content block for a candidate or a slide: a stored asset's
    bytes, a local file, or a URL the API fetches. None if it cannot be had."""
    try:
        if item.get("asset_id"):
            asset = session.get(ContentAsset, __import__("uuid").UUID(str(item["asset_id"])))
            if asset is not None:
                return llm.image_block(data=storage.asset_bytes(asset), media_type=asset.content_type)
        if item.get("path"):
            p = Path(item["path"])
            media = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            return llm.image_block(data=p.read_bytes(), media_type=media)
        if item.get("url"):
            return llm.image_block(url=item["url"])
    except (OSError, llm.LLMError, ValueError):
        return None
    return None
