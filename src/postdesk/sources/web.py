"""
Fetching a web page and reducing it to Markdown text: a small readability
pass for the `rss` and `url` sources. It keeps the article's headings,
paragraphs, lists, quotes and links, and drops the navigation, scripts,
forms and footers around them. Whatever it returns is data the agents
write from, never instructions to them.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from postdesk import __version__
from postdesk.sources.base import SourceError

TIMEOUT_S = 20.0
MAX_BYTES = 5 * 1024 * 1024
MAX_BODY_CHARS = 20000
USER_AGENT = f"postdesk/{__version__} (+https://github.com/OKASEN-Technology/postdesk)"
BLOCKS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre")


def client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_S, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def fetch(url: str, *, http: httpx.Client | None = None) -> tuple[str, bytes, str]:
    """(final url, body, content type). Refuses anything but http(s) and
    anything bigger than MAX_BYTES."""
    if urlparse(url).scheme not in ("http", "https"):
        raise SourceError(f"{url!r} is not an http(s) address")
    own = http is None
    http = http or client()
    try:
        resp = http.get(url)
    except httpx.HTTPError as e:
        raise SourceError(f"fetching {url} failed ({type(e).__name__}: {e})") from e
    finally:
        if own:
            http.close()
    if resp.status_code >= 400:
        raise SourceError(f"fetching {url} failed (HTTP {resp.status_code})")
    if len(resp.content) > MAX_BYTES:
        raise SourceError(f"{url} is over {MAX_BYTES // 1024 // 1024} MB")
    return str(resp.url), resp.content, resp.headers.get("content-type", "")


def _inline(node, base: str) -> str:
    out = []
    for child in node.children:
        if isinstance(child, NavigableString):
            out.append(str(child))
        elif isinstance(child, Tag):
            if child.name == "a" and child.get("href"):
                text = _inline(child, base).strip()
                href = urljoin(base, str(child["href"]))
                out.append(f"[{text}]({href})" if text and href.startswith("http") else text)
            elif child.name == "br":
                out.append("\n")
            elif child.name in ("strong", "b"):
                t = _inline(child, base).strip()
                out.append(f"**{t}**" if t else "")
            elif child.name in ("em", "i"):
                t = _inline(child, base).strip()
                out.append(f"*{t}*" if t else "")
            else:
                out.append(_inline(child, base))
    return re.sub(r"[ \t\r\f\v]+", " ", "".join(out))


def _main(soup: BeautifulSoup) -> Tag:
    for sel in ("article", "main", "[role=main]", "#content", ".post-content", ".entry-content"):
        found = soup.select(sel)
        if found:
            return max(found, key=lambda t: len(t.get_text(" ", strip=True)))
    # The element holding the most paragraph text.
    best, score = soup.body or soup, 0
    for div in soup.find_all(["div", "section"]):
        s = sum(len(p.get_text(" ", strip=True)) for p in div.find_all("p", recursive=False))
        if s > score:
            best, score = div, s
    return best


def readable(html: str | bytes, base_url: str) -> tuple[str, str, dict]:
    """(title, body as Markdown, meta {description, site_name, published})."""
    soup = BeautifulSoup(html, "html.parser")
    meta = {}
    for key, names in (("description", ("og:description", "description")), ("site_name", ("og:site_name",)),
                       ("published", ("article:published_time", "date", "pubdate")),
                       ("title", ("og:title",))):
        for n in names:
            tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if tag and tag.get("content"):
                meta[key] = str(tag["content"]).strip()
                break
    title = meta.get("title") or (soup.title.get_text(strip=True) if soup.title else "")
    for t in soup.find_all(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe", "svg",
                            "button", "template", "dialog"]):
        t.decompose()
    root = _main(soup)
    h1 = root.find("h1") or soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(" ", strip=True)
    lines: list[str] = []
    for el in root.find_all(BLOCKS):
        if el.find_parent(BLOCKS):  # a <p> inside an <li>: the outer block carries it
            continue
        text = _inline(el, base_url).strip()
        if not text or (el.name == "h1" and text == title):
            continue
        if el.name[0] == "h" and el.name[1:].isdigit():
            lines.append("#" * int(el.name[1:]) + " " + text)
        elif el.name == "li":
            lines.append(f"- {text}")
        elif el.name == "blockquote":
            lines.append("> " + text.replace("\n", "\n> "))
        else:
            lines.append(text)
    body = "\n\n".join(lines).strip()
    if not body:
        body = root.get_text("\n", strip=True)
    return title.strip(), body[:MAX_BODY_CHARS], meta


def html_to_text(html: str) -> str:
    """A feed's summary (often HTML) as plain text."""
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "html.parser").get_text(" ")).strip()


def domain(url: str | None) -> str | None:
    host = urlparse(url or "").hostname
    return host.removeprefix("www.") if host else None
