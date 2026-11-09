"""
The queue: list, read, create, edit a draft, approve with a time, unapprove,
reject, send a failed post back to draft, fill in recommended times. No
endpoint here publishes; `postdesk cron` and the loop in `serve` do, and
only for approved posts whose time has come.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from postdesk import instagram as ig
from postdesk import maker, queue
from postdesk.api.auth import require_auth
from postdesk.api.schemas import CaptionLint, PostApprove, PostCreate, PostItem, PostRow, PostUpdate, Slide
from postdesk.db.models import SocialPost
from postdesk.db.session import get_session
from postdesk.storage import asset_url

router = APIRouter(prefix="/api/posts", tags=["posts"], dependencies=[Depends(require_auth)])

# "open" = everything still needing a person or about to go out.
STATUS_FILTERS = {
    "open": ("draft", "approved", "publishing", "failed"),
    "done": ("published", "rejected"),
}
ERROR_EXCERPT_CHARS = 200
MAX_PAGE_SIZE = 200
TOTAL_COUNT_HEADER = "X-Total-Count"


def item(post: SocialPost) -> PostItem:
    return PostItem(
        id=str(post.id),
        channel=post.channel,
        kind=post.kind,
        status=post.status,
        title=post.title,
        caption=post.caption,
        slides=[Slide(asset_id=s["asset_id"], alt_text=s.get("alt_text", ""), url=asset_url(s["asset_id"]),
                      spec=s.get("spec"), words=maker.slide_words(s.get("spec")))
                for s in post.slides],
        card_id=str(post.card_id) if post.card_id else None,
        source_ref=post.source_ref,
        publish_at=post.publish_at,
        approved_by=post.approved_by,
        approved_at=post.approved_at,
        published_at=post.published_at,
        permalink=post.permalink,
        error=post.error,
        insights=post.insights,
        insights_at=post.insights_at,
        created_by=post.created_by,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


def row(post: SocialPost) -> PostRow:
    error = post.error
    if error and len(error) > ERROR_EXCERPT_CHARS:
        error = error[: ERROR_EXCERPT_CHARS - 1] + "…"
    return PostRow(
        id=str(post.id),
        channel=post.channel,
        kind=post.kind,
        status=post.status,
        title=post.title,
        first_slide_url=asset_url(post.slides[0]["asset_id"]) if post.slides else None,
        slide_count=len(post.slides),
        card_id=str(post.card_id) if post.card_id else None,
        source_ref=post.source_ref,
        publish_at=post.publish_at,
        published_at=post.published_at,
        error_excerpt=error,
        created_by=post.created_by,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


def _run(fn, *args, **kwargs) -> PostItem:
    try:
        return item(fn(*args, **kwargs))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except queue.QueueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except maker.MakerError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except maker.RenderError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("", response_model=list[PostRow])
def list_posts(
    status: Literal["open", "done", "all"] = "open",
    limit: int | None = None,
    offset: int = 0,
    session: Session = Depends(get_session),
    response: Response = None,  # injected by FastAPI; None when called directly
) -> list[PostRow]:
    """Rows (no caption or alt texts). `limit`/`offset` page the list;
    `X-Total-Count` is the unpaged size."""
    where = [SocialPost.status.in_(STATUS_FILTERS[status])] if status != "all" else []
    total = session.execute(select(func.count()).select_from(SocialPost).where(*where)).scalar_one()
    if response is not None:
        response.headers[TOTAL_COUNT_HEADER] = str(total)
    if status == "done":
        order = (func.coalesce(SocialPost.published_at, SocialPost.updated_at).desc(),)
    else:
        # Scheduled posts in publish order, then undated drafts newest first.
        order = (SocialPost.publish_at.asc().nulls_last(), SocialPost.created_at.desc())
    stmt = select(SocialPost).where(*where).order_by(*order, SocialPost.id).offset(max(offset, 0))
    if limit is not None:
        stmt = stmt.limit(min(max(limit, 1), MAX_PAGE_SIZE))
    return [row(p) for p in session.execute(stmt).scalars()]


@router.post("", response_model=PostItem)
def create_post(body: PostCreate, actor: str = Depends(require_auth),
                session: Session = Depends(get_session)) -> PostItem:
    try:
        return item(queue.create_post(
            session,
            kind=body.kind,
            title=body.title,
            caption=body.caption,
            slides=[s.model_dump(exclude={"url"}) for s in body.slides],
            source_ref=body.source_ref,
            created_by=body.created_by or actor,
            publish_at=body.publish_at,
        ))
    except queue.QueueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/suggest-times", response_model=list[PostItem])
def suggest_times(overwrite: bool = False, session: Session = Depends(get_session)) -> list[PostItem]:
    """Recommended publish times for drafts that have none (every draft
    with ?overwrite=true). Approved posts are never touched."""
    return [item(p) for p in queue.suggest_times(session, overwrite=overwrite)]


@router.post("/lint-caption", response_model=CaptionLint)
def lint_caption(body: dict) -> CaptionLint:
    """The same checks the queue applies, for the editor to show as you type."""
    caption = str(body.get("caption", ""))
    errors, warnings = ig.lint_caption(caption)
    return CaptionLint(errors=errors, warnings=warnings, hashtags=len(ig.hashtags(caption)))


@router.get("/{post_id}", response_model=PostItem)
def get_post(post_id: uuid.UUID, session: Session = Depends(get_session)) -> PostItem:
    post = session.get(SocialPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="post not found")
    return item(post)


@router.patch("/{post_id}", response_model=PostItem)
def update_post(post_id: uuid.UUID, body: PostUpdate, actor: str = Depends(require_auth),
                session: Session = Depends(get_session)) -> PostItem:
    """Edit a draft: title, caption, alt text, and the words on a made
    slide (`slides: [{index, fields}]`), which is checked against the
    post's numbers and re-rendered. 409 on an approved post, 422 when the
    words fail the checks, 502 when the renderer is down."""
    if body.slides:
        return _run(maker.revise, session, post_id, title=body.title, caption=body.caption, alt_texts=body.alt_texts,
                    slides=[p.model_dump() for p in body.slides], by=actor)
    return _run(queue.update_post, session, post_id, title=body.title, caption=body.caption, alt_texts=body.alt_texts)


@router.post("/{post_id}/approve", response_model=PostItem)
def approve_post(post_id: uuid.UUID, body: PostApprove, actor: str = Depends(require_auth),
                 session: Session = Depends(get_session)) -> PostItem:
    return _run(queue.approve, session, post_id, publish_at=body.publish_at, approved_by=body.approved_by or actor)


@router.post("/{post_id}/unapprove", response_model=PostItem)
def unapprove_post(post_id: uuid.UUID, session: Session = Depends(get_session)) -> PostItem:
    return _run(queue.unapprove, session, post_id)


@router.post("/{post_id}/reject", response_model=PostItem)
def reject_post(post_id: uuid.UUID, session: Session = Depends(get_session)) -> PostItem:
    return _run(queue.reject, session, post_id)


@router.post("/{post_id}/back-to-draft", response_model=PostItem)
def back_to_draft(post_id: uuid.UUID, session: Session = Depends(get_session)) -> PostItem:
    return _run(queue.back_to_draft, session, post_id)
