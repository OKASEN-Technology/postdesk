"""
What a card knows: the source's facts as filed, the images a brief may
use, the numbers the brief and the copy may contain, and the facts the
maker gets once the brief is approved.

The images a card may carry, in M2: the source's own hero picture and its
subjects' pictures (`hero`, `subject-1`, ...), and the images a person
attached to the thread (`attachment-1`, ...). The partner says which are
used; the maker gets only those (an approved brief fixes the images).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from postdesk.board import cards
from postdesk.db.models import Card
from postdesk.maker.words import known_numbers
from postdesk.sources.base import Facts, Image, numbers_in

# The slide types a partner's own plan may use, and what the model fills on each.
CUSTOM_SLIDES = {
    "cover": {"fields": ["headline", "sub", "figure"],
              "fixed": {"kicker": "{{ kicker }}", "image": "{{ facts.hero }}", "link": "{{ account.site }}"}},
    "statement": {"fields": ["kicker", "headline", "headline_em", "body", "source"]},
    "stat": {"fields": ["figure", "figure_sub", "kicker", "note", "source"]},
    "cta": {"fields": ["headline", "headline_em", "body"], "fixed": {"kicker": "{{ kicker }}"}},
    "story": {"fields": ["headline", "line", "figure"],
              "fixed": {"kicker": "{{ kicker }}", "image": "{{ facts.hero }}"}},
}


def facts_of(card: Card) -> Facts:
    """The card's facts; a card filed without a source has its title and body."""
    if card.facts:
        return Facts.from_json(card.facts)
    return Facts(title=card.title, body_md=card.body_md or "", ref=card.source_ref,
                 published_at=card.created_at or datetime.now(UTC))


def numbers(card: Card) -> set[str]:
    """Every number the brief, the guidance and the copy may use."""
    return known_numbers(facts_of(card))


def stray_numbers(text: str, known: set[str]) -> list[str]:
    """Numbers in `text` that are not in `known`, leaving out what a brief
    says about itself rather than the subject: slide and frame numbers
    ("slide 3", "3 slides", a numbered list), clock times, and the counts
    one to ten."""
    import re

    from postdesk.maker.words import _figure_ok

    t = re.sub(r"(?im)^\s*\d{1,2}[.)]\s", " ", text or "")
    t = re.sub(r"(?i)\b(slides?|frames?|slots?|round|step|no\.)\s*\d{1,2}(\s*(-|–|to|and)\s*\d{1,2})?\b", " ", t)
    t = re.sub(r"(?i)\b\d{1,2}\s*(slides?|frames?|lines?|sentences?|words?|hashtags?|paragraphs?|characters?)\b",
               " ", t)
    t = re.sub(r"\b\d{1,2}:\d{2}\b", " ", t)
    out = []
    for n in sorted(numbers_in(t)):
        try:
            small = float(n) <= 10 and float(n).is_integer()
        except ValueError:
            small = False
        if not small and not _figure_ok(n, known):
            out.append(n)
    return out


def image_candidates(session: Session, card: Card) -> list[dict]:
    """[{key, for, url, asset_id, credit, licence, what, source}] the brief
    may use: the source's hero and subjects' pictures, then attachments."""
    f = facts_of(card)
    out: list[dict] = []

    def add(key: str, who: str, img: Image, source: str) -> None:
        if img.url or img.asset_id:
            out.append({"key": key, "for": who, "url": img.url, "asset_id": img.asset_id, "credit": img.credit,
                        "licence": img.licence, "what": img.what, "source": source})

    if f.hero:
        add("hero", "hero", f.hero, "the source's own picture")
    for i, s in enumerate(f.subjects, 1):
        if s.image:
            add(f"subject-{i}", s.name, s.image, "the source's picture of the subject")
    for a in cards.attachments(session, card):
        out.append({"key": a["key"], "for": None, "url": a.get("url"), "asset_id": a.get("asset_id"),
                    "credit": None, "licence": None, "what": None, "note": a.get("note"),
                    "source": "attached by a person"})
    return out


def resolve_images(candidates: list[dict], use: list[str], attached: list[dict]) -> tuple[list[dict], list[str]]:
    """The brief's image list ({...candidate, used}) from the partner's
    `use_images` keys and `attached_images` entries ({attachment, for,
    credit, what}); plus the problems with them."""
    by_key = {c["key"]: dict(c) for c in candidates}
    problems: list[str] = []
    for a in attached or []:
        c = by_key.get(a.get("attachment", ""))
        if c is None or not c["key"].startswith("attachment-"):
            problems.append(f"`{a.get('attachment')}` is not an image attached to this card")
            continue
        if not (a.get("credit") or "").strip():
            problems.append(f"`{c['key']}` needs a credit before it can be used")
            continue
        c.update({"for": a.get("for") or "hero", "credit": a["credit"].strip(), "what": a.get("what") or None})
        use = [*use, c["key"]]
    keep = set(use or [])
    for k in keep - set(by_key):
        problems.append(f"`{k}` is not one of the card's images")
    out = []
    for c in by_key.values():
        used = c["key"] in keep and (not c["key"].startswith("attachment-") or bool(c.get("credit")))
        out.append({**c, "used": used})
    return out, problems


def facts_for_make(card: Card) -> Facts:
    """The facts with only the images the approved brief uses: a picture it
    left out is dropped, an attachment it chose takes its place (on the
    cover, or on the subject it names)."""
    f = facts_of(card)
    images = card.plan.get("images")
    if images is None:
        return f  # a card the partner never briefed (a hand-made post): nothing to filter
    used = {i["key"]: i for i in images if i.get("used")}
    if "hero" not in used:
        f.hero = None
    for i, s in enumerate(f.subjects, 1):
        if f"subject-{i}" not in used:
            s.image = None
    for img in used.values():
        if not img["key"].startswith("attachment-"):
            continue
        pic = Image(url=img.get("url"), asset_id=img.get("asset_id"), credit=img.get("credit"), what=img.get("what"))
        target = next((s for s in f.subjects if s.name == img.get("for")), None)
        if target is not None:
            target.image = pic
        else:
            f.hero = pic
    return f


def custom_template(post: dict, title: str) -> dict:
    """The partner's own slide plan as a template the maker runs."""
    kind = "story" if post.get("kind") == "story" else "carousel"
    slides = []
    for s in post.get("slides") or []:
        t = "story" if kind == "story" else s.get("type")
        spec = CUSTOM_SLIDES.get(t)
        if spec is None or (kind != "story" and t == "story"):
            continue
        slides.append({"type": t, "brief": s.get("brief") or "", "fields": spec["fields"],
                       "fixed": spec.get("fixed", {})})
    tpl = {"name": "custom", "kind": kind, "description": f"The brief's own plan for {title}",
           "kicker": "{{ facts.extra.kicker or 'New post' }}", "slides": slides}
    if kind != "story":
        tpl["story"] = {"brief": "A 9:16 companion to the post: the headline in 3 to 8 words, one line ending on "
                                 "what is behind the link.", "fields": ["headline", "line"]}
    return tpl


def facts_prompt(card: Card) -> str:
    """The facts as an agent's user turn shows them: data, never instructions."""
    return facts_of(card).prompt_text()


def as_json(value) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)
