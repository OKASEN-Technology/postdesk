"""Read-only, automatically discovered brand media stored in Cloudflare R2."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session

from postdesk import credentials, store
from postdesk.storage.s3 import _client

KEY = "encrypted_brand_library"
EXCLUDED_KEY = "brand_library_excluded"
AI_IMAGE = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE = AI_IMAGE | {".avif", ".gif"}
VIDEO = {".mp4", ".mov", ".m4v", ".webm"}
MAX_OBJECTS = 10000


def read(session: Session) -> dict | None:
    encrypted = store.get(session, KEY)
    if not encrypted:
        return None
    try:
        return json.loads(credentials.cipher().decrypt(encrypted.encode()))
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("Brand library credentials cannot be decrypted. Restore the server credential key.") from exc


def _validate(values: dict) -> dict:
    fields = ("account_id", "bucket", "prefix", "public_url", "access_key_id", "secret_access_key")
    v = {k: str(values.get(k) or "").strip() for k in fields}
    if not re.fullmatch(r"[a-fA-F0-9]{32}", v["account_id"]):
        raise ValueError("Enter the Cloudflare account ID.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", v["bucket"]):
        raise ValueError("Enter a valid R2 bucket name.")
    v["prefix"] = v["prefix"].strip("/")
    if any(p in (".", "..") for p in v["prefix"].split("/")) or "\\" in v["prefix"]:
        raise ValueError("Enter a valid folder prefix.")
    v["prefix"] = v["prefix"] + "/" if v["prefix"] else ""
    p = urlsplit(v["public_url"])
    if (p.scheme != "https" or not p.hostname or p.hostname.lower() == "dash.cloudflare.com" or p.username
            or p.password or p.query or p.fragment or p.port not in (None, 443)):
        raise ValueError("Enter the public HTTPS URL for the selected folder, not the Cloudflare dashboard URL.")
    v["public_url"] = v["public_url"].rstrip("/") + "/"
    if not v["access_key_id"] or not v["secret_access_key"]:
        raise ValueError("Enter the read-only R2 access key ID and secret.")
    return v


def _connection(v: dict):
    return _client(f"https://{v['account_id']}.r2.cloudflarestorage.com", v["access_key_id"], v["secret_access_key"])


def objects(v: dict) -> list[dict]:
    out = []
    try:
        paginator = _connection(v).get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=v["bucket"], Prefix=v["prefix"],
                                   PaginationConfig={"MaxItems": MAX_OBJECTS})
        for page in pages:
            for item in page.get("Contents", []):
                key = item["Key"]
                ext = PurePosixPath(key).suffix.lower()
                if ext not in IMAGE | VIDEO or not key.startswith(v["prefix"]):
                    continue
                relative = key[len(v["prefix"]):]
                if not relative or any(part in (".", "..") for part in relative.split("/")):
                    continue
                out.append({"key": key, "name": relative, "url": v["public_url"] + quote(relative, safe="/"),
                            "type": "image" if ext in IMAGE else "video", "size": item.get("Size", 0)})
    except Exception as exc:
        raise ValueError("Could not list the brand bucket. Check the bucket name and read-only key "
                         "permissions.") from exc
    return out


def save(session: Session, values: dict) -> list[dict]:
    v = _validate(values)
    found = objects(v)  # Do not replace a working connection until the new one is verified.
    store.put(session, KEY, credentials.cipher().encrypt(json.dumps(v).encode()).decode())
    store.delete(session, EXCLUDED_KEY)
    return found


def available(session: Session) -> list[dict]:
    v = read(session)
    if not v:
        return []
    excluded = set(store.get(session, EXCLUDED_KEY, []))
    return [{**item, "enabled": item["key"] not in excluded} for item in objects(v)]


def select_for_card(items: list[dict], text: str, limit: int = 8) -> list[dict]:
    """Bound provider usage and use filenames as hints, not claims about content."""
    words = {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}
    images = [x for x in items if x["enabled"] and x["type"] == "image"
              and PurePosixPath(x["name"]).suffix.lower() in AI_IMAGE]
    def score(item):
        terms = set(re.findall(r"[a-z0-9]+", item["name"].lower()))
        return (-len(words & terms), hashlib.sha256(item["key"].encode()).hexdigest())
    selected = sorted(images, key=score)[:limit]
    return [{"key": "brand-" + hashlib.sha256(x["key"].encode()).hexdigest()[:16], "for": "hero", "url": x["url"],
             "asset_id": None, "credit": "Brand asset", "licence": "Owner supplied",
             "what": "File name: " + x["name"][:250], "source": "account brand assets"} for x in selected]


def video_context(session: Session, items: list[dict], text: str) -> list[dict]:
    """Sample up to three frames from one relevant short video, bounded in size and time."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    from postdesk import llm

    if not shutil.which("ffmpeg"):
        return []
    words = {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}
    videos = [x for x in items if x["enabled"] and x["type"] == "video" and 0 < x["size"] <= 30 * 1024 * 1024]
    if not videos:
        return []
    videos.sort(key=lambda x: (-len(words & set(re.findall(r"[a-z0-9]+", x["name"].lower()))), x["name"]))
    chosen = videos[0]
    connection = read(session)
    if not connection:
        return []
    try:
        with tempfile.TemporaryDirectory(prefix="postdesk-brand-") as folder:
            source = Path(folder) / "video"
            body = _connection(connection).get_object(Bucket=connection["bucket"], Key=chosen["key"])["Body"]
            try:
                with source.open("wb") as dest:
                    remaining = 30 * 1024 * 1024 + 1
                    while remaining:
                        chunk = body.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        dest.write(chunk)
                        remaining -= len(chunk)
                if source.stat().st_size > 30 * 1024 * 1024:
                    return []
            finally:
                body.close()
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file,pipe",
                            "-i", str(source), "-vf",
                            "fps=1/5,scale=640:-2", "-frames:v", "3", str(Path(folder) / "%02d.jpg")],
                           check=True, timeout=25, capture_output=True)
            frames = sorted(Path(folder).glob("*.jpg"))
            if not frames:
                return []
            blocks = [{"type": "text", "text": f"Sample frames from brand video {chosen['name']}; these are visual "
                       "context only, not a complete view of the video. Do not claim to have heard the audio."}]
            blocks.extend(llm.image_block(data=frame.read_bytes(), media_type="image/jpeg") for frame in frames)
            return blocks
    except Exception:
        # A missing or unreadable video must not block the rest of the brief.
        return []
