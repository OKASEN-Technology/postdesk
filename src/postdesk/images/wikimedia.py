"""
`wikimedia`: Wikimedia Commons, through its public API. Only pictures
under an open licence a post may use as is: public domain, CC0 and CC BY
(any version). Share-alike, non-commercial, no-derivatives and anything
unlabelled are skipped (rights.open_licence). The credit names the author
and the licence, as CC BY asks: "Photo: Jo Baker / Wikimedia Commons, CC BY 4.0".
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from postdesk.images import Context, Found, ImageError, rights
from postdesk.sources import web

KEY = "wikimedia"
API = "https://commons.wikimedia.org/w/api.php"
WIDTH = 1600
MAX_RESULTS = 3


def _meta(info: dict, key: str, *, html: bool = True) -> str:
    value = str(((info.get("extmetadata") or {}).get(key) or {}).get("value") or "").strip()
    return web.html_to_text(value).strip() if html and "<" in value else value


def search(query: str, client, *, limit: int = 12) -> list[dict]:
    params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": "6", "gsrsearch": query,
              "gsrlimit": str(limit), "prop": "imageinfo", "iiprop": "url|mime|size|extmetadata",
              "iiurlwidth": str(WIDTH)}
    resp = client.get(API, params=params)
    if resp.status_code != 200:
        raise ImageError(f"Wikimedia Commons answered {resp.status_code}")
    pages = (resp.json().get("query") or {}).get("pages") or {}
    return sorted(pages.values(), key=lambda p: p.get("index", 0))


def to_found(page: dict, subject: str | None) -> Found | None:
    info = (page.get("imageinfo") or [{}])[0]
    if not str(info.get("mime") or "").startswith(("image/jpeg", "image/png", "image/webp")):
        return None
    licence = rights.open_licence(_meta(info, "LicenseShortName"))
    if licence is None:
        return None
    author = re.sub(r"\s+", " ", _meta(info, "Artist")) or "unknown"
    credit = f"Photo: {author} / Wikimedia Commons" + ("" if licence == "Public domain" else f", {licence}")
    title = str(page.get("title") or "").removeprefix("File:")
    return Found(url=info.get("thumburl") or info.get("url"), source=KEY, credit=credit[:200],
                 permalink=info.get("descriptionurl"), licence=licence,
                 licence_url=_meta(info, "LicenseUrl", html=False) or None, author=author,
                 what=_meta(info, "ImageDescription")[:200] or title.rsplit(".", 1)[0], subject=subject)


class WikimediaImages:
    key = KEY

    def find(self, session: Session, request: dict, ctx: Context) -> list[Found]:
        query = (request.get("query") or request.get("subject") or "").strip()
        if not query:
            raise ImageError("say what to search Wikimedia Commons for")
        client = ctx.client or web.client()
        subject = None if request.get("subject") == "hero" else request.get("subject")
        out = []
        for page in search(query, client):
            f = to_found(page, subject)
            if f is not None and f.url:
                out.append(f)
            if len(out) >= MAX_RESULTS:
                break
        return out
