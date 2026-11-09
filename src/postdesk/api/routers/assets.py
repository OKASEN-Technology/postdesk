"""Uploads. Any PNG, JPEG or WebP is converted to the JPEG Instagram takes,
stored content-addressed, and given a provenance record."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from postdesk import instagram as ig
from postdesk import storage
from postdesk.api.auth import require_auth
from postdesk.api.schemas import AssetOut
from postdesk.db.session import get_session

router = APIRouter(prefix="/api/assets", tags=["assets"], dependencies=[Depends(require_auth)])


@router.post("", response_model=AssetOut)
async def upload_asset(
    file: UploadFile = File(...),
    credit: str | None = Form(None),
    source: str = Form("upload"),
    actor: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> AssetOut:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        jpeg, (w, h) = ig.to_jpeg_bytes(data)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"not an image: {e}") from e
    if len(jpeg) > ig.IMAGE_MAX_BYTES:
        raise HTTPException(status_code=400, detail=f"{len(jpeg) // 1024} KB as JPEG; Instagram's limit is "
                            f"{ig.IMAGE_MAX_BYTES // 1024} KB")
    provenance = {"source": source, "credit": credit or None, "uploaded_by": actor}
    try:
        asset = storage.store_asset(session, data=jpeg, filename=(file.filename or "slide").rsplit(".", 1)[0] + ".jpg",
                                    content_type="image/jpeg", uploaded_by=actor, provenance=provenance)
    except storage.AssetRejected as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return AssetOut(id=str(asset.id), url=storage.asset_url(asset.id), public_url=storage.public_url(asset),
                    content_type=asset.content_type, byte_size=asset.byte_size, width=w, height=h,
                    storage=asset.storage)
