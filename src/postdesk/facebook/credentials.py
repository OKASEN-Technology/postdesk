"""Page credentials are encrypted with the existing server-held key."""
from __future__ import annotations

import json

from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session

from postdesk import credentials, store
from postdesk.instagram.graph import Graph, InstagramError

KEY = "encrypted_facebook_page_credentials"


def read(session: Session) -> dict | None:
    encrypted = store.get(session, KEY)
    if not encrypted:
        return None
    try:
        values = json.loads(credentials.cipher().decrypt(encrypted.encode()))
        if not values.get("page_id") or not values.get("page_token"):
            raise ValueError("Missing fields")
        return values
    except (InvalidToken, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Saved Facebook credentials cannot be decrypted. Restore the original storage key.") from exc


def verify(values: dict, graph: Graph | None = None) -> dict:
    page_id, token = values.get("page_id", "").strip(), values.get("page_token", "").strip()
    if not page_id.isdigit() or not token or len(token) > 4096:
        raise ValueError("Enter the Facebook Page ID and Page access token.")
    graph = graph or Graph()
    page = graph.get("me", fields="id,name", access_token=token)
    if page.get("id") != page_id:
        raise ValueError("This token does not belong to that Facebook Page. Use a Page access token.")
    return {"id": page_id, "name": page.get("name", "Facebook Page")}


def save(session: Session, values: dict, graph: Graph | None = None) -> dict:
    try:
        account = verify(values, graph)
        clean = {"page_id": values["page_id"].strip(), "page_token": values["page_token"].strip()}
        store.put(session, KEY, credentials.cipher().encrypt(json.dumps(clean).encode()).decode())
        return account
    except InstagramError as exc:
        raise ValueError(str(exc)) from exc
