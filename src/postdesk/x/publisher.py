"""Validate and publish separately claimed copies of approved Instagram posts."""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session
from twitter_text import parse_tweet

from postdesk import queue, storage
from postdesk.config import config
from postdesk.db.models import ContentAsset, SocialPost
from postdesk.queue.queue import _claim_next
from postdesk.queue.times import now_utc
from postdesk.x import credentials
from postdesk.x.client import MAX_IMAGE_BYTES, MAX_PHOTOS, XClient, XError


def validate_post(session: Session, post: SocialPost) -> None:
    if post.kind not in ("image", "carousel"):
        raise queue.QueueError("Only image posts and carousels can also be posted to X.")
    if len(post.slides) > MAX_PHOTOS:
        raise queue.QueueError("X allows at most four images. Reduce the images before scheduling to both.")
    parsed = parse_tweet(post.caption)
    if not parsed.valid and post.caption:
        raise queue.QueueError(f"The caption counts as {parsed.weightedLength} characters on X; maximum 280.")
    for slide in post.slides:
        asset = session.get(ContentAsset, uuid.UUID(str(slide["asset_id"])))
        if asset is None or asset.byte_size > MAX_IMAGE_BYTES:
            raise queue.QueueError("Each image must exist and be at most 5 MB for X.")


def publish_due(session: Session, *, log=print) -> list[SocialPost]:
    attempted = []
    for _ in range(config.schedule.max_per_run):
        post = _claim_next(session, now_utc(), channel="x")
        if post is None:
            break
        attempted.append(post)
        try:
            validate_post(session, post)
            auth = credentials.read(session)
            if auth is None:
                raise XError("X is not connected. Connect it in Settings.")
            client = XClient(auth)
            ids = []
            for slide in post.slides:
                asset = session.get(ContentAsset, uuid.UUID(slide["asset_id"]))
                if asset is None:
                    raise XError("An image is no longer available.")
                ids.append(client.upload_image(storage.asset_bytes(asset)))
            post_id = client.create_post(post.caption, ids)
        except XError as exc:
            post.status = "publishing" if exc.uncertain else "failed"
            post.error = str(exc)
        except (ValueError, OSError, queue.QueueError) as exc:
            post.status = "failed"
            post.error = str(exc)
        else:
            post.status = "published"
            post.published_at = now_utc()
            post.permalink = f"https://x.com/i/web/status/{post_id}"
            post.error = None
        post.updated_at = now_utc()
        session.commit()
        log(f"X post {post.id}: {post.status}")
    return attempted
