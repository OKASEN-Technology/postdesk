"""
Where slide images live. Instagram fetches every image from a public URL
(there is no upload), so the bytes must be reachable from the internet:

- **local**: `<POSTDESK_DATA_DIR>/uploads/<sha[:2]>/<sha>` on the app's
  disk, served at `<PUBLIC_BASE_URL>/media/<id>`. Works when the app itself
  is reachable from the internet.
- **s3**: any S3-compatible bucket (`STORAGE_S3_*`), key
  `assets/<sha[:2]>/<sha>`. A public bucket URL is optional; without one,
  the app serves referenced assets from its public `/media/<id>` endpoint.

Content-addressed: the same bytes uploaded twice reuse one row, and a URL's
bytes never change, so they are cached forever. `postdesk doctor` checks
that whichever backend is in use is actually public.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from postdesk.config import settings
from postdesk.db.models import ContentAsset
from postdesk.storage import local, s3

ALLOWED_IMAGE = frozenset({"image/png", "image/jpeg", "image/webp"})
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
IMMUTABLE = "public, max-age=31536000, immutable"


class AssetRejected(ValueError):
    """Bad content type or an oversize upload."""


def backend() -> str:
    return "s3" if settings.s3_enabled() else "local"


def store_asset(
    session: Session,
    *,
    data: bytes,
    filename: str,
    content_type: str,
    uploaded_by: str | None = None,
    provenance: dict | None = None,
) -> ContentAsset:
    """Store `data`. `provenance` says where the image came from; a hand-made
    slide gets `{"source": "upload"}` so every asset has a record. An
    existing row without one takes it; a row whose bytes went missing (a
    volume that was not persistent, a bucket emptied by hand) is healed by
    re-uploading the same bytes."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct not in ALLOWED_IMAGE | {"video/mp4"}:
        raise AssetRejected(f"unsupported content type: {ct!r} (PNG, JPEG or WebP)")
    if not data:
        raise AssetRejected("empty file")
    limit = MAX_VIDEO_BYTES if ct == "video/mp4" else MAX_IMAGE_BYTES
    if len(data) > limit:
        raise AssetRejected(f"file too large: {len(data)} bytes (limit {limit})")

    sha = hashlib.sha256(data).hexdigest()
    where = backend()
    existing = session.execute(select(ContentAsset).where(ContentAsset.sha256 == sha)).scalar_one_or_none()
    if existing is not None:
        if existing.storage == "s3":
            if where == "s3" and not s3.has(sha):
                s3.put(sha, data, existing.content_type)
        elif where == "s3":
            s3.put(sha, data, existing.content_type)
            existing.storage = "s3"
        else:
            local.put(sha, data)
        if provenance and not existing.provenance:
            existing.provenance = provenance
        session.commit()
        return existing

    if where == "s3":
        s3.put(sha, data, ct)
    else:
        local.put(sha, data)
    asset = ContentAsset(
        filename=filename or sha,
        content_type=ct,
        byte_size=len(data),
        sha256=sha,
        created_at=datetime.now(UTC),
        uploaded_by=uploaded_by,
        storage=where,
        provenance=provenance or {"source": "upload"},
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def asset_bytes(asset: ContentAsset) -> bytes:
    if asset.storage == "s3":
        return s3.get(asset.sha256)
    return local.get(asset.sha256)


def asset_url(asset_id: uuid.UUID | str | None) -> str | None:
    """`<PUBLIC_BASE_URL>/media/<id>`: serves local bytes, redirects to the
    bucket for s3 rows. The URL the web UI shows."""
    if asset_id is None:
        return None
    return f"{settings.public_base_url.rstrip('/')}/media/{asset_id}"


def public_url(asset: ContentAsset) -> str:
    """The URL Instagram fetches: a public bucket URL if set, else the app URL."""
    if asset.storage == "s3" and settings.storage_s3_public_base_url:
        return f"{settings.storage_s3_public_base_url.rstrip('/')}/{s3.key(asset.sha256)}"
    return asset_url(asset.id)  # type: ignore[return-value]
