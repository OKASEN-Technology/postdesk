"""
The rights record: which licences an open image source may take, and the
permissions on file (`image_permission`): a rights holder's standing yes,
recorded by a person in the web UI or with `postdesk permission add`,
never by an agent. The editor's rights check reads an image's provenance
and these rows.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from postdesk.db.models import ImagePermission

HOLDER_KINDS = ("person", "organisation", "photographer", "other")


class RightsError(ValueError):
    pass


def open_licence(short_name: str | None) -> str | None:
    """The licence as recorded if an open source may use it (public domain,
    CC0, CC BY of any version), else None: share-alike, non-commercial,
    no-derivatives, GFDL and anything unknown are skipped."""
    name = re.sub(r"\s+", " ", (short_name or "").strip())
    low = name.lower()
    if not low:
        return None
    if low in ("public domain", "pd", "no restrictions") or low.startswith(("public domain", "pd-", "pdm")):
        return "Public domain"
    if low.startswith("cc0") or low == "cc-zero":
        return "CC0"
    m = re.fullmatch(r"cc[ -]by(?:[ -](\d(?:\.\d)?))?(?:[ -]([a-z]+))?", low)
    if m and not m.group(2):
        return f"CC BY {m.group(1)}" if m.group(1) else "CC BY"
    return None


def active(p: ImagePermission, today: date | None = None) -> bool:
    today = today or datetime.now(UTC).date()
    return p.revoked_at is None and (p.expires_on is None or p.expires_on >= today)


def covering(session: Session, *, instagram: str | None = None, domain: str | None = None,
             subject: str | None = None, today: date | None = None) -> ImagePermission | None:
    """The permission on file that covers an image from `instagram` (a
    handle) or `domain`, for `subject`: one for that subject, else one for
    everything the holder has."""
    conds = []
    if instagram:
        conds.append(ImagePermission.instagram == instagram.lstrip("@").lower())
    if domain:
        conds.append(ImagePermission.domain == domain.lower().removeprefix("www."))
    if not conds:
        return None
    rows = session.scalars(select(ImagePermission).where(or_(*conds))
                           .order_by(ImagePermission.granted_on.desc())).all()
    rows = [p for p in rows if active(p, today)]
    for p in rows:
        if subject and p.subject and p.subject.lower() == subject.lower():
            return p
    return next((p for p in rows if not p.subject), None)


def add(session: Session, *, holder: str, holder_kind: str, scope: str, credit: str, evidence: str,
        recorded_by: str, instagram: str | None = None, domain: str | None = None, subject: str | None = None,
        tag: str | None = None, granted_on: date | None = None, expires_on: date | None = None) -> ImagePermission:
    if holder_kind not in HOLDER_KINDS:
        raise RightsError(f"holder_kind is one of {', '.join(HOLDER_KINDS)}")
    for name, value in (("holder", holder), ("scope", scope), ("credit", credit), ("evidence", evidence)):
        if not (value or "").strip():
            raise RightsError(f"{name} is required")
    if not instagram and not domain:
        raise RightsError("give the holder's Instagram handle or web domain, so their images can be matched")
    if recorded_by.startswith(("partner", "editor", "maker", "scout", "worker")):
        raise RightsError("a permission is recorded by a person, not an agent")
    p = ImagePermission(holder=holder.strip(), holder_kind=holder_kind,
                        instagram=instagram.lstrip("@").lower() if instagram else None,
                        domain=domain.lower().removeprefix("www.") if domain else None,
                        subject=(subject or "").strip() or None, scope=scope.strip(), credit=credit.strip(),
                        tag=(tag or "").strip() or None, evidence=evidence.strip(),
                        granted_on=granted_on or datetime.now(UTC).date(), expires_on=expires_on,
                        recorded_by=recorded_by, created_at=datetime.now(UTC))
    session.add(p)
    session.commit()
    return p


def listed(session: Session, *, include_revoked: bool = False) -> list[ImagePermission]:
    stmt = select(ImagePermission).order_by(ImagePermission.created_at.desc())
    if not include_revoked:
        stmt = stmt.where(ImagePermission.revoked_at.is_(None))
    return list(session.scalars(stmt))


def revoke(session: Session, permission_id: uuid.UUID | str) -> ImagePermission:
    p = session.get(ImagePermission, uuid.UUID(str(permission_id)))
    if p is None:
        raise LookupError(f"permission {permission_id} not found")
    if p.revoked_at is None:
        p.revoked_at = datetime.now(UTC)
        session.commit()
    return p
