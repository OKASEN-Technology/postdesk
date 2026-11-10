"""Publish separately claimed Page copies, with no automatic retry after ambiguity."""
from __future__ import annotations

import json
import uuid

import httpx
from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session, aliased

from postdesk import queue, storage
from postdesk.config import config
from postdesk.db.models import ContentAsset, SocialPost
from postdesk.facebook import credentials
from postdesk.instagram.graph import Graph, InstagramError
from postdesk.queue.times import now_utc


def validate_post(session: Session, post: SocialPost) -> None:
    if post.kind not in ("image", "carousel", "story"):
        raise queue.QueueError("Facebook Reel publishing is not yet supported; this post cannot be scheduled.")
    for slide in post.slides:
        asset = session.get(ContentAsset, uuid.UUID(str(slide["asset_id"])))
        if asset is None or asset.content_type != "image/jpeg":
            raise queue.QueueError("Facebook needs all images to be available as JPEG files.")


def publish(session: Session, post: SocialPost, auth: dict, graph: Graph) -> str | None:
    page_id, token = auth["page_id"], auth["page_token"]
    ids = []
    for slide in post.slides:
        asset = session.get(ContentAsset, uuid.UUID(str(slide["asset_id"])))
        if asset is None:
            raise ValueError("A Facebook image is no longer available.")
        # Meta fetches from the existing public asset URL, just as Instagram does.
        url = storage.public_url(asset)
        photo = graph.post(f"{page_id}/photos", url=url, published="false", access_token=token)
        ids.append(photo["id"])
    if post.kind == "story":
        graph.post(f"{page_id}/photo_stories", photo_id=ids[0], access_token=token)
        return None  # Meta's story post ID is not a reliable public permalink.
    attached = json.dumps([{"media_fbid": photo_id} for photo_id in ids])
    result = graph.post(f"{page_id}/feed", message=post.caption, attached_media=attached, access_token=token)
    return f"https://www.facebook.com/{result['id']}" if result.get("id") else None


def _claim_ready(session: Session) -> SocialPost | None:
    source = aliased(SocialPost)
    stmt = (select(SocialPost)
            .join(source, SocialPost.media["instagram_post_id"].as_string() == cast(source.id, String))
            .where(SocialPost.channel == "facebook", SocialPost.status == "approved",
                   SocialPost.publish_at <= now_utc(), source.status == "published")
            .order_by(SocialPost.publish_at).with_for_update(of=SocialPost, skip_locked=True))
    post = session.scalars(stmt).first()
    if post is not None:
        post.status, post.updated_at = "publishing", now_utc()
        session.commit()
    return post


def publish_due(session: Session, *, log=print, graph: Graph | None = None) -> list[SocialPost]:
    graph = graph or Graph()
    attempted = []
    for _ in range(config.schedule.max_per_run):
        post = _claim_ready(session)
        if post is None:
            break
        attempted.append(post)
        try:
            validate_post(session, post)
            auth = credentials.read(session)
            if not auth:
                raise ValueError("Connect the Facebook Page in Settings.")
            post.permalink = publish(session, post, auth, graph)
        except httpx.HTTPError:
            # The publish request may have reached Meta. A person must check before retrying.
            post.status = "publishing"
            post.error = "Facebook did not confirm the result. Check the Page before retrying."
        except (InstagramError, ValueError, OSError, queue.QueueError, KeyError) as exc:
            post.status, post.error = "failed", str(exc)
        else:
            post.status, post.error, post.published_at = "published", None, now_utc()
        post.updated_at = now_utc()
        session.commit()
        log(f"Facebook post {post.id}: {post.status}")
    return attempted
