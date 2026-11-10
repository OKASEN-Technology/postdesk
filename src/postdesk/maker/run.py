"""The run: facts and a template to drafts in the queue, and a draft's
slide re-rendered after a word change."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from postdesk import instagram as ig
from postdesk import llm, queue, storage
from postdesk.config import config
from postdesk.db.models import ContentAsset, SocialPost
from postdesk.maker.plans import Slot, Template, load_template, plan
from postdesk.maker.render import Renderer
from postdesk.maker.words import MakerError, known_numbers, patch_spec, write_copy
from postdesk.sources.base import Facts, Image

AGENT = "maker"


# ---------------------------------------------------------------- images


def _store_file(session: Session, img: Image, *, by: str) -> None:
    """A local picture becomes a stored asset (the renderer reads it as a
    data URI; the web UI shows it at /media)."""
    p = Path(img.path or "")
    data = p.read_bytes()
    jpeg, _ = ig.to_jpeg_bytes(data)
    asset = storage.store_asset(session, data=jpeg, filename=p.with_suffix(".jpg").name, content_type="image/jpeg",
                                uploaded_by=by,
                                provenance={"source": "file", "file": p.name, "credit": img.credit,
                                            "licence": img.licence, "generated": False})
    img.asset_id = str(asset.id)
    img.url = storage.asset_url(asset.id)
    img.path = None


def resolve_images(session: Session, facts: Facts, *, by: str = AGENT) -> None:
    for img in [facts.hero, *(s.image for s in facts.subjects)]:
        if img is not None and img.path and not img.asset_id:
            _store_file(session, img, by=by)


def _images_in(spec: dict) -> list[dict]:
    return [spec[field] for field in ("image", "background") if isinstance(spec.get(field), dict)]


def provenance(spec: dict, template: str, theme: str) -> dict:
    """What a rendered slide's asset records: a render over which images,
    with their credits. Nothing here is generated imagery."""
    images = _images_in(spec)
    return {
        "source": "render",
        "generated": False,
        "template": template,
        "theme": theme,
        "slide_type": spec.get("type"),
        "composited_from": [i.get("asset_id") or i.get("url") for i in images if i.get("asset_id") or i.get("url")],
        "credits": [i["credit"] for i in images if i.get("credit")],
    }


# ---------------------------------------------------------------- the slides


def build_slides(facts: Facts, kind: str, slots: list[Slot], story: Slot | None, copy: dict
                 ) -> tuple[list[dict], list[str], dict | None]:
    """(slide specs, their alt texts, the Story's spec) from the checked copy."""
    by_slot = {s["slot"]: s for s in copy["slides"]}
    kept = []
    for i, slot in enumerate(slots, 1):
        words = {f: str(by_slot[i].get(f) or "").strip() for f in slot.fields}
        if slot.optional and not words.get(slot.primary):
            continue
        kept.append((slot, {**words, **slot.fixed}, str(copy["alt_texts"][i - 1]).strip()))
    total = len(kept)
    specs, alts = [], []
    statements = [s for s, _, _ in kept if s.type == "statement"]
    for n, (slot, spec, alt) in enumerate(kept, 1):
        spec = {k: v for k, v in spec.items() if v not in (None, "", [], {})}
        head, em = spec.get("headline", ""), spec.get("headline_em", "")
        if em and head.lower().rstrip(" .").endswith(em.lower().rstrip(" .")):
            spec["headline"] = head[: len(head.rstrip(" .")) - len(em.rstrip(" ."))].rstrip()
        if slot.type in ("statement", "stat") and facts.url:
            spec.setdefault("source", f"Source: {facts.url}")
        if slot.type == "statement" and len(statements) > 1:
            spec["num"] = f"{statements.index(slot) + 1:02d}"
        chrome = {"n": n, "total": total} if kind != "story" else {}
        specs.append({"type": slot.type, **chrome, **spec})
        alts.append(alt)
    if story is None:
        return specs, alts, None
    cover = specs[0] if specs else {}
    st = {
        "type": "story",
        "kicker": story.fixed.get("kicker") or cover.get("kicker") or "New post",
        "kicker_sub": story.fixed.get("kicker_sub") or cover.get("kicker_sub"),
        "headline": str(copy["story"]["headline"]).strip(),
        "line": str(copy["story"]["line"]).strip(),
        "image": story.fixed.get("image") or (facts.hero.spec() if facts.hero and facts.hero.url else None),
        "figure": story.fixed.get("figure") or cover.get("figure"),
    }
    return specs, alts, {k: v for k, v in st.items() if v not in (None, "", [], {})}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "post"


# ---------------------------------------------------------------- the run


def make(
    session: Session,
    facts: Facts,
    template: str | Path | Template | dict,
    *,
    guidance: str | None = None,
    renderer: Renderer | None = None,
    call=None,
    now: datetime | None = None,
    created_by: str = AGENT,
    source_ref: str | None = None,
    card_id: uuid.UUID | None = None,
    log=None,
) -> list[SocialPost]:
    """Drafts for `facts` laid out by `template`: the feed post and its
    Story, or a Story-only post's frames. `call` replaces the Claude call
    (tests); `renderer` the service client."""
    log = log or (lambda _s: None)
    tpl = template if isinstance(template, Template) else load_template(template)
    source_ref = source_ref or facts.ref
    if renderer is None:
        renderer = Renderer()
        renderer.health()
    resolve_images(session, facts, by=created_by)
    kind, slots, story = plan(tpl, facts)
    log(f"template {tpl.name}: {kind}, {len(slots)} {'frame' if kind == 'story' else 'slide'}(s)")

    if call is None:
        def call(**kw):
            return llm.structured_call(agent=AGENT, session=session, ref=source_ref, card_id=card_id, **kw)

    copy = write_copy(facts, kind, slots, story, call=call, guidance=guidance)
    specs, alts, story_spec = build_slides(facts, kind, slots, story, copy)
    if kind == "carousel" and len(specs) < ig.CAROUSEL_MIN:
        raise MakerError(f"only {len(specs)} slide(s) survived; a carousel needs {ig.CAROUSEL_MIN}")
    if kind == "image" and len(specs) != 1:
        raise MakerError(f"an image post is one slide ({len(specs)} planned)")

    slug = _slug(facts.title)

    def stored(spec: dict, name: str) -> str:
        data = renderer.render(spec, session=session)
        asset = storage.store_asset(session, data=data, filename=f"{slug}-{name}.jpg", content_type="image/jpeg",
                                    uploaded_by=created_by, provenance=provenance(spec, tpl.name, renderer.theme))
        log(f"  rendered {name} ({len(data) // 1024} KB)")
        return str(asset.id)

    if kind == "story":
        frames = [{"asset_id": stored(s, f"frame-{i}"), "alt_text": a, "spec": s}
                  for i, (s, a) in enumerate(zip(specs, alts, strict=True), 1)]
        return [queue.create_post(session, kind="story", title=f"{facts.title} ({i}/{len(frames)})", slides=[f],
                                  source_ref=source_ref, card_id=card_id, created_by=created_by, now=now)
                for i, f in enumerate(frames, 1)]

    slides = [{"asset_id": stored(s, f"{s['n']:02d}-{s['type']}"), "alt_text": a, "spec": s}
              for s, a in zip(specs, alts, strict=True)]
    story_asset = stored(story_spec, "story") if story_spec else None
    post = queue.create_post(session, kind=kind, title=facts.title, caption=str(copy["caption"]).strip(),
                             slides=slides, source_ref=source_ref, card_id=card_id, created_by=created_by, now=now)
    made = [post]
    if story_spec:
        made.append(queue.create_post(
            session, kind="story", title=f"Story: {facts.title}",
            slides=[{"asset_id": story_asset, "alt_text": str(copy["story"]["alt_text"]).strip(), "spec": story_spec}],
            source_ref=source_ref, card_id=card_id, created_by=created_by, now=now,
        ))
    return made


# ---------------------------------------------------------------- a slide redone

NO_SPEC = "has no stored spec (it was uploaded, not made), so it cannot be re-rendered: upload a new image instead"


def _known(session: Session, post: SocialPost) -> set[str]:
    """The numbers a corrected slide may carry: what is already on the
    post's slides and caption, and on its siblings from the same source
    (all written from the same facts)."""
    siblings = session.scalars(select(SocialPost).where(SocialPost.source_ref == post.source_ref,
                                                        SocialPost.status != "rejected")).all() \
        if post.source_ref else [post]
    return known_numbers(*(p.caption for p in siblings), *(s.get("spec") for p in siblings for s in p.slides))


def needs_theme_refresh(session: Session, post: SocialPost, theme: str) -> bool:
    for slide in post.slides:
        if not slide.get("spec") or not slide.get("asset_id"):
            continue
        asset = session.get(ContentAsset, uuid.UUID(slide["asset_id"]))
        if asset and (asset.provenance or {}).get("theme") != theme:
            return True
    return False


def revise(session: Session, post_id: uuid.UUID, *, caption: str | None = None, alt_texts: list[str] | None = None,
           slides: list[dict] | None = None, title: str | None = None, renderer: Renderer | None = None,
           by: str = "admin", refresh_theme: bool = False, images: list[dict] | None = None,
           force_theme: bool = False) -> SocialPost:
    """A draft's caption, alt text, title and the words on its slides. A
    slide patch (`{index (0-based), fields: copy field -> text}`) is merged
    into the slide's stored spec, checked like fresh copy (`patch_spec`)
    and re-rendered; the other slides keep their images. Every patch is
    checked before anything renders, so a refused one leaves the post as
    it was. Refused on an approved post (unapprove first)."""
    rendered: dict[int, tuple[str, dict]] = {}
    if slides or refresh_theme or images or force_theme:
        post = session.get(SocialPost, post_id)
        if post is None:
            raise LookupError(f"post {post_id} not found")
        if post.status != "draft":
            raise queue.QueueError(f"post is {post.status}; this needs draft")
        specs: dict[int, dict] = {}
        known = None
        for p in slides or []:
            i, fields = int(p["index"]), dict(p.get("fields") or {})
            if not 0 <= i < len(post.slides):
                raise queue.QueueError(f"slide index {i} is out of range (0–{len(post.slides) - 1})")
            if i in specs:
                raise queue.QueueError(f"slide {i} is patched twice")
            if not fields:
                raise queue.QueueError(f"slide {i}: no words to change")
            spec = post.slides[i].get("spec")
            if not spec:
                raise queue.QueueError(f"slide {i} {NO_SPEC}")
            known = _known(session, post) if known is None else known
            try:
                specs[i] = patch_spec(spec, fields, known)
            except MakerError as e:
                raise MakerError(f"slide {i + 1}: {e}") from e
        from postdesk.maker.render import schema
        for image in images or []:
            i = int(image["index"])
            if not 0 <= i < len(post.slides):
                raise queue.QueueError("Slide is no longer available.")
            spec = dict(specs.get(i) or post.slides[i].get("spec") or {})
            properties = schema()["$defs"].get(spec.get("type"), {}).get("properties", {})
            field = next ( (f for f in ("image", "background") if f in properties), None)
            if not field:
                raise queue.QueueError(f"Slide {i + 1} does not support a picture.")
            asset = session.get(ContentAsset, uuid.UUID(str(image["asset_id"])))
            if not asset or not asset.content_type.startswith("image/"):
                raise queue.QueueError("The selected image is unavailable.")
            spec[field] = {"asset_id": str(asset.id), "url": storage.asset_url(asset.id),
                           "credit": image.get("credit") or "Owner supplied"}
            specs[i] = spec
        renderer = renderer or Renderer()
        if refresh_theme or force_theme:
            for i, slide in enumerate(post.slides):
                if i in specs or not slide.get("spec") or not slide.get("asset_id"):
                    continue
                asset = session.get(ContentAsset, uuid.UUID(slide["asset_id"]))
                if asset and (force_theme or (asset.provenance or {}).get("theme") != renderer.theme):
                    specs[i] = slide["spec"]
        slug = _slug(post.title)
        for i, spec in specs.items():
            data = renderer.render(spec, session=session)
            asset = storage.store_asset(session, data=data, filename=f"{slug}-{i + 1:02d}-{spec['type']}-revised.jpg",
                                        content_type="image/jpeg", uploaded_by=by,
                                        provenance=provenance(spec, "revised", renderer.theme))
            rendered[i] = (str(asset.id), spec)
    return queue.update_post(session, post_id, title=title, caption=caption, alt_texts=alt_texts, rendered=rendered)


def theme_name() -> str:
    return config.theme.name
