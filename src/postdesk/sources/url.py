"""
The `url` source: one page, on demand ("post about this"), from the web
UI's form or `POST /api/sources/manual` with only a link. The page is
fetched and reduced to Markdown text (`web.readable`); its numbers are the
figures. It has no feed, so `candidates` is empty. A ref is `url:<url>`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from postdesk.sources import web
from postdesk.sources.base import Candidate, Facts, SourceError

KEY = "url"


class UrlSource:
    key = KEY

    def __init__(self, template: str = "article", client: httpx.Client | None = None, **_ignored) -> None:
        self.template = template
        self.client = client

    def candidates(self, since: datetime) -> list[Candidate]:
        return []

    def facts(self, ref: str) -> Facts:
        url = ref.removeprefix("url:")
        final, html, ctype = web.fetch(url, http=self.client)
        if "html" not in ctype and ctype:
            raise SourceError(f"{url} is {ctype}, not a web page")
        title, body, meta = web.readable(html, final)
        if not title or not body:
            raise SourceError(f"{url} has no readable article on it")
        published = None
        if meta.get("published"):
            try:
                published = datetime.fromisoformat(meta["published"].replace("Z", "+00:00"))
            except ValueError:
                published = None
        site = meta.get("site_name") or web.domain(final)
        return Facts(
            title=title, summary=meta.get("description", "")[:400], body_md=body, url=final,
            published_at=published or datetime.now(UTC), citations=[{"label": site, "url": final}],
            extra={"site": site} if site else {}, ref=f"url:{url}", template=self.template,
        )
