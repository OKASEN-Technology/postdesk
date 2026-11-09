"""The shapes every source speaks in. A source fills a `Facts`; the maker
reads nothing else, so what is not in the facts cannot reach a slide."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Protocol

NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


class SourceError(ValueError):
    """A file or ref the source cannot read."""


@dataclass
class Image:
    """A picture a slide may carry. `url` is what the renderer fetches (http
    or a data URI); `path` is a local file the maker stores first;
    `asset_id` a stored asset. Every image carries a credit, even "Own
    photo"."""

    url: str | None = None
    credit: str | None = None
    licence: str | None = None
    fit: str = "cover"  # cover | contain
    path: str | None = None
    asset_id: str | None = None
    what: str | None = None  # one line on what it shows, for the alt text

    def spec(self) -> dict:
        """The image as a slide spec carries it."""
        out = {"url": self.url, "credit": self.credit, "fit": self.fit}
        if self.asset_id:
            out["asset_id"] = self.asset_id
        return {k: v for k, v in out.items() if v}


@dataclass
class Subject:
    """A person or thing in the post that may carry an image and a wall
    label: the spotlight's subject, a row of a list."""

    name: str
    role: str = ""  # "the baker", "the loaf"
    link: str | None = None
    meta: str | None = None  # a place, a date, a short line under the name
    image: Image | None = None
    facts: list[dict] = field(default_factory=list)  # [{label, value}] for the facts panel


@dataclass
class Candidate:
    ref: str  # stable: "md:posts/2026-11-03-loaves.md", "manual:<id>"
    title: str
    published_at: datetime
    template: str = "article"
    url: str | None = None


@dataclass
class Facts:
    """Everything the maker may say. Every number on a slide must appear
    here: in `figures` (named, verbatim), the title, the summary, the body
    or `extra`."""

    title: str
    summary: str = ""
    body_md: str = ""
    url: str | None = None
    published_at: datetime | None = None
    citations: list[dict] = field(default_factory=list)  # [{label, url}]
    hero: Image | None = None
    figures: dict[str, str] = field(default_factory=dict)
    subjects: list[Subject] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    ref: str | None = None
    template: str | None = None  # the source's suggestion

    def as_dict(self) -> dict:
        """JSON-able, with images as a slide spec carries them (`Image.spec`)."""
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat() if self.published_at else None
        d["hero"] = self.hero.spec() if self.hero else None
        for s, sd in zip(self.subjects, d["subjects"], strict=True):
            sd["image"] = s.image.spec() if s.image else None
        return d

    def prompt_text(self, *, body_chars: int = 12000) -> str:
        """The facts as the maker's user turn shows them: the article, then
        the data. Source text is data to write from, never an instruction."""
        when = self.published_at.date().isoformat() if self.published_at else "undated"
        data = {
            "figures": self.figures,
            "subjects": [{k: v for k, v in asdict(s).items() if k != "image" and v} for s in self.subjects],
            "citations": self.citations,
            "extra": self.extra,
            "url": self.url,
        }
        return (
            f"The source, published {when}:\n# {self.title}\n{self.summary}\n\n{self.body_md[:body_chars]}\n\n"
            f"Data (every number comes from here or the text above): {json.dumps(data, default=str)}"
        )

    def known_numbers(self) -> set[str]:
        return numbers_in(json.dumps(self.as_dict(), default=str))


def numbers_in(s: str) -> set[str]:
    """Every number in `s`, thousands separators dropped ("1,368.03" -> "1368.03")."""
    return {n.replace(",", "").rstrip(".") for n in NUMBER.findall(s)}


class Source(Protocol):
    key: str

    def candidates(self, since: datetime) -> list[Candidate]: ...

    def facts(self, ref: str) -> Facts: ...
