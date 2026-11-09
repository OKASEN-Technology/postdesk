"""
`/media/<id>`: the public URL Instagram fetches slides from. No login, but
only assets that a post or a card references are served (a slide, an
image attached to a card's thread, a card's hero or subject picture, or a
picture an image lookup found for it); anything else is 404. The bytes are
content-addressed and immutable, so caches may keep them.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, Response
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
def get_media(asset_id: uuid.UUID, session: Session = Depends(get_session)) -> Response:
    asset = session.get(ContentAsset, asset_id)
    if asset is None or not referenced(session, asset_id):
        raise HTTPException(status_code=404, detail="asset not found")
    if asset.storage == "s3":
        url = public_url(asset)
        if not url.endswith(f"/media/{asset_id}"):
            return RedirectResponse(url, status_code=301, headers={"Cache-Control": IMMUTABLE})
    try:
        data = asset_bytes(asset)
    except (FileNotFoundError, OSError) as e:
        raise HTTPException(status_code=410, detail="asset file missing") from e
    return Response(content=data, media_type=asset.content_type,
                    headers={"Cache-Control": IMMUTABLE, "Content-Disposition": f'inline; filename="{asset.filename}"'})
