"""
`attachment`: an image the admin attaches to a card, in the web UI or as a
photo sent in reply to a card's message in Telegram, with a note on where
it is from. It is stored at once (board/actions.py) with the note as its
provenance; once the partner uses it, `record_credit` writes the credit the
partner took from the admin's words onto the stored asset, so its record
says what the slide says. Nothing is looked up, so `find` finds nothing.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from postdesk.db.models import ContentAsset
from postdesk.images import Context, Found

KEY = "attachment"


class Attachments:
    key = KEY

    def find(self, session: Session, request: dict, ctx: Context) -> list[Found]:
        return []


def record_credit(session: Session, images: list[dict]) -> None:
    """For each used attachment in a brief's image list, its credit and what
    it shows on the asset's provenance. Not committed."""
    for img in images:
        if not (img.get("used") and str(img.get("key", "")).startswith("attachment-") and img.get("asset_id")):
            continue
        asset = session.get(ContentAsset, uuid.UUID(str(img["asset_id"])))
        if asset is None:
            continue
        prov = dict(asset.provenance or {})
        prov.update({"source": KEY, "credit": img.get("credit"), "what": img.get("what"),
                     "subject": img.get("for"), "licence": prov.get("licence")})
        asset.provenance = prov
