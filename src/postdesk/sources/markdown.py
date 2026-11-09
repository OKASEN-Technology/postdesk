"""
The `markdown` source: one file, or a folder of them. Front matter (YAML
between `---` lines) carries what the body cannot:

    ---
    title: Three loaves, one long rest
    date: 2026-11-03
    summary: What 72 hours in the fridge does to a sourdough crumb.
    url: https://example.com/bakes/three-loaves      # optional, "link in bio"
    template: article                                # optional, the default
    hero: ./photos/crumb.jpg                         # optional: a file, or an https URL
    hero_credit: "Photo: Jo Baker"                   # required when hero is set
    figures:                                         # optional: named numbers, verbatim
      hydration: "78%"
      rest: "72 hours"
    subjects:                                        # optional: who or what may carry an image
      - name: Jo Baker
        role: the baker
        meta: Bristol
        image: ./photos/jo.jpg
        credit: "Photo: Jo Baker"
        facts: [{label: Loaves, value: 3 a week}]
    rows:                                            # optional: a `list` template's table
      - {rank: 1, name: Country loaf, delta: 2}
    ---
    The body, in Markdown. Its numbers are the ones the copy may use.

Without front matter the first `# heading` is the title and the first
paragraph the summary. A ref is `md:<path>`, relative to the folder.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from postdesk.sources.base import Candidate, Facts, Image, SourceError, Subject

FRONT = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.S)
HEADING = re.compile(r"^#\s+(.+?)\s*$", re.M)
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
KEY = "markdown"


def split_front_matter(text: str) -> tuple[dict, str]:
    m = FRONT.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise SourceError(f"front matter is not valid YAML: {e}") from e
    if not isinstance(meta, dict):
        raise SourceError("front matter must be a mapping (key: value lines)")
    return meta, text[m.end():]


def _when(value, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        try:
            d = datetime.fromisoformat(value.strip())
        except ValueError as e:
            raise SourceError(f"date {value!r} is not ISO (2026-11-03)") from e
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    return fallback


def _image(value, credit, base: Path, *, fit: str = "cover") -> Image | None:
    if not value:
        return None
    if isinstance(value, dict):
        credit = value.get("credit") or credit
        fit = value.get("fit") or fit
        value = value.get("url") or value.get("path")
    value = str(value)
    if value.startswith(("http://", "https://", "data:image/")):
        return Image(url=value, credit=credit, fit=fit)
    p = (base / value).resolve()
    if not p.is_file():
        raise SourceError(f"image {value!r} not found next to the file")
    return Image(path=str(p), credit=credit, fit=fit)


def _subjects(items, base: Path) -> list[Subject]:
    out = []
    for item in items or []:
        if isinstance(item, str):
            out.append(Subject(name=item))
            continue
        if not isinstance(item, dict) or not item.get("name"):
            raise SourceError("each subject needs at least a name")
        out.append(Subject(
            name=str(item["name"]), role=str(item.get("role") or ""), link=item.get("link") or item.get("url"),
            meta=item.get("meta"), image=_image(item.get("image"), item.get("credit"), base, fit="contain"),
            facts=[{"label": str(f.get("label")), "value": str(f.get("value"))} for f in item.get("facts") or []],
        ))
    return out


def facts_from_text(text: str, *, base: Path, ref: str | None = None, fallback_date: datetime | None = None) -> Facts:
    meta, body = split_front_matter(text)
    body = body.strip()
    title = str(meta.get("title") or "").strip()
    if not title:
        h = HEADING.search(body)
        if h:
            title = h.group(1).strip()
            body = (body[: h.start()] + body[h.end():]).strip()
    if not title:
        raise SourceError("no title: add `title:` to the front matter or a `# heading`")
    summary = str(meta.get("summary") or "").strip()
    if not summary:
        summary = next((p.strip() for p in re.split(r"\n\s*\n", body) if p.strip() and not p.lstrip().startswith("#")),
                       "")[:300]
    hero = _image(meta.get("hero") or meta.get("image"), meta.get("hero_credit") or meta.get("credit"), base)
    if hero and not hero.credit:
        raise SourceError("hero image needs a `hero_credit:` (who took it, on what terms)")
    figures = {str(k): str(v) for k, v in (meta.get("figures") or {}).items()}
    extra = {k: v for k, v in meta.items()
             if k not in ("title", "date", "summary", "url", "link", "template", "hero", "image", "hero_credit",
                          "credit", "figures", "subjects")}
    citations = [{"label": label, "url": url} for label, url in LINK.findall(body)][:30]
    return Facts(
        title=title, summary=summary, body_md=body, url=meta.get("url") or meta.get("link"),
        published_at=_when(meta.get("date"), fallback_date or datetime.now(UTC)), citations=citations, hero=hero,
        figures=figures, subjects=_subjects(meta.get("subjects"), base), extra=extra, ref=ref,
        template=str(meta["template"]) if meta.get("template") else None,
    )


def facts_from_file(path: Path | str, *, ref: str | None = None) -> Facts:
    p = Path(path)
    if not p.is_file():
        raise SourceError(f"{p} is not a file")
    mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
    return facts_from_text(p.read_text(encoding="utf-8"), base=p.parent, ref=ref or f"md:{p.name}", fallback_date=mtime)


class MarkdownSource:
    """`[sources].markdown = { path = "./posts", template = "article" }`."""

    key = KEY

    def __init__(self, path: str = ".", template: str = "article", **_ignored) -> None:
        self.root = Path(path)
        self.template = template

    def _files(self) -> list[Path]:
        if self.root.is_file():
            return [self.root]
        return sorted(p for p in self.root.rglob("*.md") if p.is_file())

    def ref_for(self, p: Path) -> str:
        rel = p.relative_to(self.root) if self.root.is_dir() and p.is_relative_to(self.root) else p.name
        return f"md:{rel.as_posix()}"

    def candidates(self, since: datetime) -> list[Candidate]:
        out = []
        for p in self._files():
            try:
                f = facts_from_file(p, ref=self.ref_for(p))
            except SourceError:
                continue  # a draft without a title is not a candidate yet
            if f.published_at and f.published_at >= since:
                out.append(Candidate(ref=f.ref or self.ref_for(p), title=f.title, published_at=f.published_at,
                                     template=f.template or self.template, url=f.url))
        return sorted(out, key=lambda c: c.published_at, reverse=True)

    def facts(self, ref: str) -> Facts:
        rel = ref.removeprefix("md:")
        p = (self.root / rel) if self.root.is_dir() else self.root
        if not p.is_file():
            raise SourceError(f"{ref}: no such file under {self.root}")
        f = facts_from_file(p, ref=ref)
        f.template = f.template or self.template
        return f
