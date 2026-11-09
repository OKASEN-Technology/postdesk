"""
The publishing queue.

    draft --approve(publish_at)--> approved --publish_due--> publishing --> published
      ^  \\                           |                          \\-> failed --back_to_draft--> draft
      |   \\--reject--> rejected      |
      \\-------- unapprove ----------/

A post reaches Instagram only from `approved`, and only a person approves:
`approve` is called from the web UI (or the API) with the time it should go
out. `publish_due` is what `postdesk cron` and the loop in `serve` run. A
draft carries a recommended publish_at (times.py), which the approver keeps
or changes. An approved post is immutable: edit it and you must unapprove
first, so what was approved is exactly what goes out.

A row is flipped to `publishing` and committed *before* any Graph call, so
two overlapping runs cannot both post it (`FOR UPDATE SKIP LOCKED`), and a
run that dies mid-publish leaves the row in `publishing` rather than
retrying it into a duplicate post. A stuck row needs a person to check
Instagram first (docs/troubleshooting.md).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from postdesk import instagram as ig
from postdesk.config import config
from postdesk.db.models import ContentAsset, SocialPost
from postdesk.queue.times import now_utc, recommend_publish_at
from postdesk.storage import public_url

KINDS = ("image", "carousel", "story", "reel")
STATUSES = ("draft", "approved", "publishing", "published", "failed", "rejected")


class QueueError(ValueError):
    """Invalid post, or a transition the post's status does not allow."""


def _validate(session: Session, kind: str, caption: str, slides: list[dict]) -> None:
    if kind not in KINDS:
        raise QueueError(f"kind must be one of {', '.join(KINDS)}")
    n = len(slides)
    if kind in ("image", "story") and n != 1:
        raise QueueError(f"a {kind} is one image ({n} given)")
    if kind == "reel" and n != 1:
        raise QueueError(f"a reel is one cover image ({n} given)")
    if kind == "carousel" and not ig.CAROUSEL_MIN <= n <= ig.CAROUSEL_MAX:
        raise QueueError(f"a carousel takes {ig.CAROUSEL_MIN}–{ig.CAROUSEL_MAX} images ({n} given)")
    if kind == "story" and caption:
        raise QueueError("stories have no caption")
    errors, _ = ig.lint_caption(caption)
    if errors:
        raise QueueError("; ".join(errors))
    for s in slides:
        try:
            asset = session.get(ContentAsset, uuid.UUID(str(s.get("asset_id"))))
        except ValueError:
            asset = None
        if asset is None:
            raise QueueError(f"asset {s.get('asset_id')} not found")
        if asset.content_type != "image/jpeg":
            raise QueueError(f"asset {asset.id} is {asset.content_type}; Instagram takes JPEG only")


def _normalise_slides(slides: list[dict]) -> list[dict]:
    """`spec` is the maker's render spec for the slide (M1); the publisher
    reads only the asset and alt text."""
    return [{"asset_id": str(s["asset_id"]), "alt_text": (s.get("alt_text") or "").strip(),
             **({"spec": s["spec"]} if s.get("spec") else {})} for s in slides]


def create_post(
    session: Session,
    *,
    kind: str,
    title: str,
    slides: list[dict],
    caption: str = "",
    card_id: uuid.UUID | None = None,
    source_ref: str | None = None,
    created_by: str = "admin",
    now: datetime | None = None,
    publish_at: datetime | None = None,
) -> SocialPost:
    """File a draft. `publish_at`: a time of your own, else the next free
    slot is recommended."""
    slides = _normalise_slides(slides)
    caption = caption.strip()
    _validate(session, kind, caption, slides)
    now = now or now_utc()
    post = SocialPost(
        kind=kind,
        status="draft",
        title=title.strip() or "Untitled post",
        caption=caption,
        slides=slides,
        card_id=card_id,
        source_ref=source_ref,
        publish_at=publish_at or recommend_publish_at(session, kind, source_ref, now=now),
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    return post


def _get(session: Session, post_id: uuid.UUID, *, lock: bool = False) -> SocialPost:
    stmt = select(SocialPost).where(SocialPost.id == post_id)
    if lock:
        stmt = stmt.with_for_update()
    post = session.execute(stmt).scalar_one_or_none()
    if post is None:
        raise LookupError(f"post {post_id} not found")
    return post


def _require(post: SocialPost, *allowed: str) -> None:
    if post.status not in allowed:
        raise QueueError(f"post is {post.status}; this needs {' or '.join(allowed)}")


def update_post(
    session: Session,
    post_id: uuid.UUID,
    *,
    title: str | None = None,
    caption: str | None = None,
    alt_texts: list[str] | None = None,
    rendered: dict[int, tuple[str, dict]] | None = None,
) -> SocialPost:
    """Edit a draft. An approved post has to be unapproved first. `rendered`
    (0-based slide index -> (asset id, spec)) swaps in re-rendered slides
    (M1)."""
    post = _get(session, post_id, lock=True)
    _require(post, "draft")
    slides = list(post.slides)
    for i, (asset_id, spec) in (rendered or {}).items():
        if not 0 <= i < len(slides):
            raise QueueError(f"slide index {i} is out of range (0–{len(slides) - 1})")
        slides[i] = {**slides[i], "asset_id": str(asset_id), "spec": spec}
    if alt_texts is not None:
        if len(alt_texts) != len(slides):
            raise QueueError(f"{len(alt_texts)} alt texts for {len(slides)} slides")
        slides = [{**s, "alt_text": a.strip()} for s, a in zip(slides, alt_texts, strict=True)]
    new_caption = post.caption if caption is None else caption.strip()
    _validate(session, post.kind, new_caption, slides)
    post.caption = new_caption
    post.slides = slides
    if title is not None:
        post.title = title.strip() or post.title
    post.updated_at = now_utc()
    session.commit()
    session.refresh(post)
    return post


def approve(session: Session, post_id: uuid.UUID, *, publish_at: datetime, approved_by: str) -> SocialPost:
    if publish_at.tzinfo is None:
        raise QueueError("publish_at needs a timezone")
    if not approved_by.strip():
        raise QueueError("approved_by is required")
    post = _get(session, post_id, lock=True)
    _require(post, "draft")
    _validate(session, post.kind, post.caption, post.slides)
    if post.kind == "reel":
        raise QueueError("reels cannot be published by this version yet")
    now = now_utc()
    post.status = "approved"
    post.publish_at = publish_at
    post.approved_by = approved_by.strip()
    post.approved_at = now
    post.error = None
    post.updated_at = now
    session.commit()
    session.refresh(post)
    return post


def unapprove(session: Session, post_id: uuid.UUID) -> SocialPost:
    post = _get(session, post_id, lock=True)
    _require(post, "approved")
    post.status = "draft"
    post.approved_by = post.approved_at = None
    post.updated_at = now_utc()
    session.commit()
    session.refresh(post)
    return post


def reject(session: Session, post_id: uuid.UUID) -> SocialPost:
    post = _get(session, post_id, lock=True)
    _require(post, "draft", "approved", "failed")
    post.status = "rejected"
    post.updated_at = now_utc()
    session.commit()
    session.refresh(post)
    return post


def back_to_draft(session: Session, post_id: uuid.UUID) -> SocialPost:
    """A failed post, or one stuck in `publishing` after a person has
    checked Instagram and confirmed it never went out, returns to draft for
    re-approval."""
    post = _get(session, post_id, lock=True)
    _require(post, "failed", "publishing")
    post.status = "draft"
    post.approved_by = post.approved_at = None
    post.updated_at = now_utc()
    session.commit()
    session.refresh(post)
    return post


def due(session: Session, now: datetime | None = None) -> list[SocialPost]:
    now = now or now_utc()
    return list(
        session.execute(
            select(SocialPost)
            .where(SocialPost.status == "approved", SocialPost.publish_at <= now)
            .order_by(SocialPost.publish_at)
        ).scalars()
    )


def _claim_next(session: Session, now: datetime) -> SocialPost | None:
    post = session.execute(
        select(SocialPost)
        .where(SocialPost.status == "approved", SocialPost.publish_at <= now)
        .order_by(SocialPost.publish_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if post is not None:
        post.status = "publishing"
        post.updated_at = now_utc()
        session.commit()
    return post


def publish_due(session: Session, graph: ig.Graph, *, now: datetime | None = None, log=print) -> list[SocialPost]:
    """Publish every approved post whose time has come, up to
    `[schedule].max_per_run` (a week of approvals all set to the past should
    not empty into the feed in one run). Returns the posts it attempted,
    each now `published` or `failed`."""
    now = now or now_utc()
    if not due(session, now):
        return []
    account = ig.resolve_account(graph)
    attempted = []
    for _ in range(config.schedule.max_per_run):
        post = _claim_next(session, now)
        if post is None:
            break
        attempted.append(post)
        log(f"{post.title} ({post.kind}, {post.id})")
        try:
            items = []
            for s in post.slides:
                asset = session.get(ContentAsset, uuid.UUID(s["asset_id"]))
                if asset is None:
                    raise ig.InstagramError(f"asset {s['asset_id']} is gone")
                items.append((public_url(asset), s.get("alt_text", "")))
            ig.check_public(items[0][0])
            result = ig.publish_hosted(post.kind, items, post.caption, graph, account, log=log)
        except ig.InstagramError as e:
            post.status = "failed"
            post.error = str(e)
            log(f"  failed: {e}")
        else:
            post.status = "published"
            post.published_at = now_utc()
            post.ig_media_id = result["id"]
            post.permalink = result["permalink"]
            post.error = None
            log(f"  published: {result['permalink'] or result['id']}")
        post.updated_at = now_utc()
        session.commit()
    return attempted
