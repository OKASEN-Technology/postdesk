"""The `manual` source: a title and pasted text, with an optional link and
picture, from the web form or `postdesk make --title --text`. It has no
feed to scan, so `candidates` is empty; the ref is whatever the caller
names (a card id from M2 on)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from postdesk.sources.base import Candidate, Facts, Image, SourceError
from postdesk.sources.markdown import facts_from_text

KEY = "manual"


def facts_from(title: str, text: str, *, link: str | None = None, image: Image | None = None,
               ref: str | None = None, figures: dict[str, str] | None = None) -> Facts:
    if not title.strip():
        raise SourceError("a title is required")
    if not text.strip():
        raise SourceError("some text is required")
    if image and not image.credit:
        raise SourceError("the picture needs a credit")
    f = facts_from_text(f"# {title.strip()}\n\n{text.strip()}", base=Path("."), ref=ref)
    f.url = link or None
    f.hero = image
    f.figures = dict(figures or {})
    f.published_at = datetime.now(UTC)
    return f


class ManualSource:
    key = KEY

    def __init__(self, **_ignored) -> None:
        pass

    def candidates(self, since: datetime) -> list[Candidate]:
        return []

    def facts(self, ref: str) -> Facts:
        raise SourceError("the manual source keeps nothing to look up; its facts come with the form")
