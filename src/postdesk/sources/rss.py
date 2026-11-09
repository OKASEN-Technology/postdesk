"""
The `rss` source: a feed (RSS or Atom) whose new items become cards.

    [sources]
    rss = { url = "https://example.com/feed.xml", template = "article" }

`candidates(since)` reads the feed; `facts(ref)` fetches the item's page and
reduces it to Markdown text (`web.readable`), falling back to the feed's
own content when the page will not load. The figures are the numbers in
that text, nothing else. The item's picture in the feed (a media or image
enclosure) is the hero, credited to the site; it carries no licence, and
the brief says so. A ref is `rss:<the item's guid, else its link>`.
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime

import feedparser
import httpx

from postdesk.sources import web
from postdesk.sources.base import Candidate, Facts, Image, SourceError

KEY = "rss"


def _when(entry) -> datetime | None:
    for k in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(k)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=UTC)
    return None


def _ref(entry) -> str | None:
    guid = entry.get("id") or entry.get("guid") or entry.get("link")
    return f"rss:{guid}" if guid else None


def _image(entry, site: str | None) -> Image | None:
    urls = [m.get("url") for m in entry.get("media_content") or [] if m.get("url")
            and (m.get("medium") == "image" or str(m.get("type", "")).startswith("image/"))]
    urls += [m.get("url") for m in entry.get("media_thumbnail") or [] if m.get("url")]
    urls += [e.get("href") for e in entry.get("enclosures") or [] if str(e.get("type", "")).startswith("image/")]
    url = next((u for u in urls if u and u.startswith(("http://", "https://"))), None)
    if not url:
        return None
    return Image(url=url, credit=f"Image: {site}" if site else "Image: the source", licence=None)


class RssSource:
    key = KEY

    def __init__(self, url: str = "", template: str = "article", client: httpx.Client | None = None,
                 fetch_pages: bool = True, **_ignored) -> None:
        if not url:
            raise SourceError("[sources].rss needs a url")
        self.url = url
        self.template = template
        self.client = client
        self.fetch_pages = fetch_pages
        self._entries: dict[str, dict] | None = None
        self._feed_title: str | None = None

    def _feed(self) -> dict[str, dict]:
        if self._entries is None:
            _, body, _ = web.fetch(self.url, http=self.client)
            parsed = feedparser.parse(body)
            if parsed.bozo and not parsed.entries:
                raise SourceError(f"{self.url} is not a feed I can read ({parsed.get('bozo_exception')})")
            self._feed_title = (parsed.feed.get("title") or "").strip() or web.domain(self.url)
            self._entries = {r: e for e in parsed.entries if (r := _ref(e))}
        return self._entries

    def domains(self) -> list[str]:
        """The sites this source's items come from (the partner may check a
        date or a name there)."""
        out = {web.domain(self.url)}
        for e in (self._entries or {}).values():
            out.add(web.domain(e.get("link")))
        return sorted(d for d in out if d)

    def candidates(self, since: datetime) -> list[Candidate]:
        out = []
        for ref, e in self._feed().items():
            when = _when(e)
            if when is None or when < since:
                continue
            out.append(Candidate(ref=ref, title=(e.get("title") or "Untitled").strip(), published_at=when,
                                 template=self.template, url=e.get("link")))
        return sorted(out, key=lambda c: c.published_at)

    def facts(self, ref: str) -> Facts:
        e = self._feed().get(ref)
        if e is None:
            raise SourceError(f"{ref} is no longer in {self.url}")
        link = e.get("link")
        site = self._feed_title or web.domain(link)
        title = (e.get("title") or "").strip()
        summary = web.html_to_text(e.get("summary") or "")[:400]
        body = ""
        if self.fetch_pages and link:
            try:
                final, html, ctype = web.fetch(link, http=self.client)
                if "html" in ctype or not ctype:
                    page_title, body, meta = web.readable(html, final)
                    title = title or page_title
                    summary = summary or meta.get("description", "")
            except SourceError:
                body = ""
        if not body:
            content = e.get("content") or []
            body = web.html_to_text(content[0].get("value", "")) if content else summary
        if not title:
            raise SourceError(f"{ref} has no title")
        return Facts(
            title=title, summary=summary, body_md=body, url=link, published_at=_when(e) or datetime.now(UTC),
            citations=[{"label": site or "the source", "url": link}] if link else [], hero=_image(e, site),
            extra={"site": site} if site else {}, ref=ref, template=self.template,
        )
