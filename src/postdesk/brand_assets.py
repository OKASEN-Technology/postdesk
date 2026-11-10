"""Public brand image index for the briefing agent.

R2 public URLs serve objects but do not list a bucket. An operator supplies
postdesk-brand.json in the public folder; its entries must be relative paths.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from postdesk.config import config

MAX_INDEX_BYTES = 65536
MAX_IMAGES = 12


def configured_url() -> str | None:
    return config.voice.brand_assets_url


def _public_host(host: str) -> bool:
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)
    except (OSError, ValueError):
        return False


def load(*, config_url: str | None) -> tuple[list[dict], str | None]:
    if not config_url:
        return [], None
    base = config_url.rstrip("/") + "/"
    parsed = urlsplit(base)
    try:
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or not _public_host(parsed.hostname)):
            return [], "use an internet-accessible HTTPS folder"
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", urljoin(base, "postdesk-brand.json")) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_INDEX_BYTES:
                        return [], "postdesk-brand.json is too large"
        doc = json.loads(data)
        entries = doc.get("images") if isinstance(doc, dict) else None
        if not isinstance(entries, list) or len(entries) > MAX_IMAGES:
            return [], "postdesk-brand.json must list at most 12 images"
        candidates = []
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                return [], "each image needs a relative path"
            path = item["path"]
            target = urlsplit(urljoin(base, path))
            if (target.scheme != "https" or target.netloc != parsed.netloc or not target.path.startswith(parsed.path)
                    or target.query or target.fragment or path.startswith("/") or "%" in path):
                return [], "image paths must stay inside the public folder"
            description = str(item.get("description") or "Brand image")[:300]
            candidates.append({"key": f"brand-{len(candidates) + 1}", "for": "hero", "url": target.geturl(),
                               "asset_id": None, "credit": "Brand asset", "licence": "Owner supplied",
                               "what": description, "source": "account brand assets"})
        return candidates, None
    except (httpx.HTTPError, ValueError, OSError):
        return [], "check that postdesk-brand.json exists and is publicly accessible"
