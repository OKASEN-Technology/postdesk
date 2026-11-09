"""
`page`: the picture on the source's own page: its `og:image` (or
`twitter:image`, or `image_src`), else the first large image in the
article. Credited to the site ("Image: bakes.example"); it carries no
licence, so it is used on the site's terms and the admin's risk, unless a
permission on file covers that domain. Only pages on the card's own site
(or `[partner].allowed_domains`) are looked at.
"""

from __future__ import annotations

from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from postdesk.config import config
from postdesk.images import Context, Found, ImageError, rights
from postdesk.sources import web
from postdesk.sources.base import Image

KEY = "page"
MIN_WIDTH = 400


def picture(html: str | bytes, base_url: str) -> tuple[str, str | None] | None:
    """(image URL, its alt text) for a page, or None."""
    soup = BeautifulSoup(html, "html.parser")
    for attrs in ({"property": "og:image"}, {"property": "og:image:url"}, {"name": "twitter:image"},
                  {"name": "twitter:image:src"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            alt = soup.find("meta", attrs={"property": "og:image:alt"})
            return urljoin(base_url, str(tag["content"]).strip()), (str(alt["content"]) if alt else None)
    link = soup.find("link", rel="image_src")
    if link and link.get("href"):
        return urljoin(base_url, str(link["href"])), None
    root = soup.find("article") or soup.find("main") or soup.body or soup
    for img in root.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src or str(src).startswith("data:"):
            continue
        try:
            if int(str(img.get("width") or MIN_WIDTH)) < MIN_WIDTH:
                continue
        except ValueError:
            pass
        return urljoin(base_url, str(src)), (str(img.get("alt")) if img.get("alt") else None)
    return None


def own_domains(card) -> set[str]:
    out = set((card.plan or {}).get("domains") or [])
    if card.facts and card.facts.get("url"):
        out.add(web.domain(card.facts["url"]))
    out |= set(config.partner.allowed_domains)
    return {d for d in out if d}


def find_on(session: Session, url: str, client: httpx.Client | None = None, *, subject: str | None = None
            ) -> Found | None:
    final, html, ctype = web.fetch(url, http=client)
    if ctype and "html" not in ctype:
        raise ImageError(f"{url} is {ctype}, not a web page")
    hit = picture(html, final)
    if hit is None:
        return None
    site = web.domain(final) or "the source"
    meta = web.readable(html, final)[2]
    name = meta.get("site_name") or site
    perm = rights.covering(session, domain=site, subject=subject)
    return Found(url=hit[0], source=KEY, credit=perm.credit if perm else f"Image: {name}", permalink=final,
                 licence=None, author=name, permission_id=str(perm.id) if perm else None,
                 tag=perm.tag if perm else None, what=hit[1], subject=subject)


class PageImages:
    key = KEY

    def find(self, session: Session, request: dict, ctx: Context) -> list[Found]:
        url = (request.get("query") or "").strip() or (ctx.card.facts or {}).get("url")
        if not url:
            raise ImageError("this card has no page to look at; give its address")
        if web.domain(url) not in own_domains(ctx.card):
            raise ImageError(f"{web.domain(url)} is not the source's own site (nor in [partner].allowed_domains)")
        hit = find_on(session, url, ctx.client, subject=None if request.get("subject") == "hero"
                      else request.get("subject"))
        return [hit] if hit else []


def hero(session: Session, url: str, *, client: httpx.Client | None = None) -> Image | None:
    """The page's own picture, stored, as a card's hero (a link filed with /new)."""
    from postdesk import images, storage

    found = find_on(session, url, client)
    if found is None:
        return None
    asset = images.store(session, found, by="page", client=client)
    return Image(url=storage.asset_url(asset.id), asset_id=str(asset.id), credit=found.credit,
                 licence=found.licence, what=found.what)
