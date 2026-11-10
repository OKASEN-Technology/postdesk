"""
`/media/<id>`: the public URL Instagram fetches slides from. No login, but
only assets that a post or a card references are served (a slide, an
image attached to a card's thread, a card's hero or subject picture, or a
picture an image lookup found for it); anything else is 404. The bytes are
content-addressed and immutable, so caches may keep them.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from postdesk.db.models import Card, CardComment, ContentAsset, SocialPost
from postdesk.db.session import get_session
from postdesk.storage import IMMUTABLE, asset_bytes, public_url

router = APIRouter(prefix="/media", tags=["media"])


def referenced(session: Session, asset_id: uuid.UUID) -> bool:
    ref = [{"asset_id": str(asset_id)}]
    one = {"asset_id": str(asset_id)}
    stmt = select(exists().where(SocialPost.slides.cast(JSONB).contains(ref))
                  | exists().where(CardComment.attachments.cast(JSONB).contains(ref))
                  | exists().where(Card.plan.cast(JSONB).contains({"found": ref}))
                  | exists().where(Card.facts.cast(JSONB).contains({"hero": one}))
                  | exists().where(Card.facts.cast(JSONB).contains({"subjects": [{"image": one}]})))
    return bool(session.execute(stmt).scalar())


@router.get("/{asset_id}")
def get_media(asset_id: uuid.UUID, request: Request, session: Session = Depends(get_session)) -> Response:
    asset = session.get(ContentAsset, asset_id)
    if asset is None or not referenced(session, asset_id):
        raise HTTPException(status_code=404, detail="asset not found")
    if asset.storage == "s3":
        url = public_url(asset)
        if not url.endswith(f"/media/{asset_id}"):
            return RedirectResponse(url, status_code=301, headers={"Cache-Control": IMMUTABLE})
    if asset.content_type == "video/mp4":
        return _video_response(asset, request.headers.get("range"))
    try:
        data = asset_bytes(asset)
    except (FileNotFoundError, OSError) as e:
        raise HTTPException(status_code=410, detail="asset file missing") from e
    return Response(content=data, media_type=asset.content_type,
                    headers={"Cache-Control": IMMUTABLE, "Content-Disposition": f'inline; filename="{asset.filename}"'})


def _video_response(asset: ContentAsset, requested_range: str | None) -> Response:
    """Stream videos, including byte ranges used by video players and Meta."""
    import re

    from postdesk.config import settings
    from postdesk.storage import local, s3

    size = asset.byte_size
    start, end = 0, size - 1
    if requested_range:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested_range.strip())
        if not match or not any(match.groups()):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        left, right = match.groups()
        if not left:
            count = int(right)
            start = max(0, size - count)
        else:
            start = int(left)
            end = min(int(right), end) if right else end
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    headers = {"Cache-Control": IMMUTABLE, "Accept-Ranges": "bytes", "Content-Length": str(end - start + 1),
               "Content-Disposition": "inline"}
    if requested_range:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    def chunks():
        if asset.storage == "s3":
            stream = s3.client().get_object(Bucket=settings.storage_s3_bucket, Key=s3.key(asset.sha256),
                                            Range=f"bytes={start}-{end}")["Body"]
        else:
            stream = local.path_for(asset.sha256).open("rb")
            stream.seek(start)
        try:
            remaining = end - start + 1
            while remaining:
                data = stream.read(min(1024 * 1024, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data
        finally:
            stream.close()

    return StreamingResponse(chunks(), status_code=206 if requested_range else 200,
                             media_type="video/mp4", headers=headers)
