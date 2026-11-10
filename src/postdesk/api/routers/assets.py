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
    data = await file.read(storage.MAX_VIDEO_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if file.content_type == "video/mp4":
        return _store_video(session, data, file.filename or "reel.mp4", actor, source)
    if len(data) > storage.MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image is larger than 10 MB")
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


def _store_video(session: Session, data: bytes, filename: str, actor: str, source: str) -> AssetOut:
    from postdesk.instagram.video import validate_video

    try:
        w, h = validate_video(data)
        asset = storage.store_asset(session, data=data, filename=filename, content_type="video/mp4",
                                    uploaded_by=actor, provenance={"source": source})
    except (ValueError, storage.AssetRejected) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AssetOut(id=str(asset.id), url=storage.asset_url(asset.id), public_url=storage.public_url(asset),
                    content_type=asset.content_type, byte_size=asset.byte_size, width=w, height=h,
                    storage=asset.storage)


@router.post("/brand-video", response_model=AssetOut)
def import_brand_video(body: dict, actor: str = Depends(require_auth),
                       session: Session = Depends(get_session)) -> AssetOut:
    from postdesk import brand_library

    try:
        connection = brand_library.read(session)
        if not connection:
            raise ValueError("Connect your brand library first.")
        item = next((x for x in brand_library.available(session) if x["key"] == body.get("key")
                     and x["enabled"] and x["type"] == "video"), None)
        if not item:
            raise ValueError("Video not found or excluded from the brand library.")
        if item["size"] > storage.MAX_VIDEO_BYTES:
            raise ValueError("Video is larger than the 50 MB limit.")
        obj = brand_library._connection(connection).get_object(Bucket=connection["bucket"], Key=item["key"])["Body"]
        try:
            data = obj.read(storage.MAX_VIDEO_BYTES + 1)
        finally:
            obj.close()
        return _store_video(session, data, item["name"].rsplit("/", 1)[-1], actor, "brand library")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
