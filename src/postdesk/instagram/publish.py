"""
Publishing images, carousels and Stories through the Graph API.

Publishing is a three-step dance the API imposes:

  1. host each image at a public URL (Instagram fetches it; there is no
     direct upload): postdesk.storage;
  2. create a media container per image, then a CAROUSEL container over them;
  3. poll until Instagram has processed the container, then `media_publish`.

Reels keep their place in the schema (`kind = "reel"`) but are not published
here yet (docs/roadmap.md, M5).
"""

from __future__ import annotations

import io
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from PIL import Image as PILImage

from postdesk.config import config
from postdesk.instagram.graph import Account, Graph, InstagramError

# Instagram's own limits (Content Publishing API reference).
CAPTION_MAX = 2200
HASHTAG_MAX = 30
MENTION_MAX = 20
CAROUSEL_MIN, CAROUSEL_MAX = 2, 20
FEED_RATIO_MIN, FEED_RATIO_MAX = 4 / 5, 1.91  # width / height
IMAGE_MAX_BYTES = 8 * 1024 * 1024

POLL_INTERVAL_S = 3
POLL_TIMEOUT_S = 180


# --- post bundle -------------------------------------------------------------


@dataclass
class Slide:
    path: Path
    alt_text: str = ""
    jpeg: bytes = b""
    size: tuple[int, int] = (0, 0)


@dataclass
class Post:
    kind: str  # "image" | "carousel" | "story"
    slides: list[Slide]
    caption: str = ""
    warnings: list[str] = field(default_factory=list)


def parse_caption_file(text: str) -> tuple[str, dict[int, str]]:
    """A caption file: the caption, then a `---` line and an `ALT TEXT`
    block of `N: text` lines (1-based slide numbers). Returns
    (caption, {slide_number: alt_text})."""
    caption, sep, rest = text.partition("\n---\n")
    alts: dict[int, str] = {}
    if sep:
        for line in rest.splitlines():
            m = re.match(r"\s*(\d+)\s*:\s*(.+)", line)
            if m:
                alts[int(m.group(1))] = m.group(2).strip()
    return caption.strip(), alts


def to_jpeg(path: Path) -> tuple[bytes, tuple[int, int]]:
    """The Graph API only accepts JPEG for images. Flatten onto white (a
    stray alpha channel would otherwise turn black)."""
    return to_jpeg_bytes(path.read_bytes())


def to_jpeg_bytes(data: bytes) -> tuple[bytes, tuple[int, int]]:
    with PILImage.open(io.BytesIO(data)) as im:
        im.load()
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = PILImage.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.getchannel("A"))
            im = bg
        else:
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue(), im.size


def hashtags(caption: str) -> list[str]:
    """The caption's hashtags. A number after `#` ("#1 in our list", "up
    from #14") is a rank, not a tag: Instagram does not link all-digit
    tags, so they count toward neither cap."""
    return re.findall(r"(?<!\w)#(?!\d+(?!\w))\w+", caption)


def lint_caption(caption: str) -> tuple[list[str], list[str]]:
    """(errors, warnings). Errors are Instagram's hard limits; warnings are
    the user's own rules from `[voice]` in postdesk.toml."""
    errors, warnings = [], []
    if len(caption) > CAPTION_MAX:
        errors.append(f"caption is {len(caption)} characters (Instagram max {CAPTION_MAX})")
    tags = hashtags(caption)
    mentions = re.findall(r"(?<![\w.])@[\w.]+", caption)
    if len(tags) > HASHTAG_MAX:
        errors.append(f"{len(tags)} hashtags (Instagram max {HASHTAG_MAX})")
    elif len(tags) > config.voice.max_hashtags:
        warnings.append(f"{len(tags)} hashtags (your limit is {config.voice.max_hashtags}, postdesk.toml [voice])")
    if len(mentions) > MENTION_MAX:
        errors.append(f"{len(mentions)} @mentions (Instagram max {MENTION_MAX})")
    lowered = caption.lower()
    for phrase in config.voice.retired_phrases:
        if phrase and phrase.lower() in lowered:
            warnings.append(f"caption uses a retired phrase: {phrase!r}")
    return errors, warnings


def build_post(paths: list[Path], caption_text: str = "", *, story: bool = False) -> Post:
    """Load, convert and validate. Raises InstagramError listing every
    problem at once, so a dry run shows the whole picture."""
    caption, alts = parse_caption_file(caption_text) if caption_text else ("", {})
    errors: list[str] = []
    warnings: list[str] = []

    if story:
        kind = "story"
        if len(paths) != 1:
            errors.append(f"a story is one image ({len(paths)} given)")
        if caption:
            warnings.append("stories have no caption; the caption file is ignored")
            caption = ""
    elif len(paths) == 1:
        kind = "image"
    else:
        kind = "carousel"
        if not CAROUSEL_MIN <= len(paths) <= CAROUSEL_MAX:
            errors.append(f"a carousel takes {CAROUSEL_MIN}–{CAROUSEL_MAX} images ({len(paths)} given)")

    slides = []
    for i, p in enumerate(paths, start=1):
        if not p.is_file():
            errors.append(f"{p}: not found")
            continue
        try:
            jpeg, size = to_jpeg(p)
        except OSError as e:
            errors.append(f"{p.name}: not an image ({e})")
            continue
        ratio = size[0] / size[1]
        if not story and not FEED_RATIO_MIN - 0.01 <= ratio <= FEED_RATIO_MAX:
            errors.append(f"{p.name}: {size[0]}×{size[1]} is outside the feed's 4:5 to 1.91:1 range")
        if len(jpeg) > IMAGE_MAX_BYTES:
            errors.append(f"{p.name}: {len(jpeg) // 1024} KB as JPEG (max {IMAGE_MAX_BYTES // 1024} KB)")
        alt = alts.get(i, "")
        if not story and not alt:
            warnings.append(f"slide {i} ({p.name}) has no alt text")
        slides.append(Slide(path=p, alt_text=alt, jpeg=jpeg, size=size))

    if kind == "carousel" and len({s.size for s in slides}) > 1:
        # Instagram crops every carousel item to the first one's ratio.
        errors.append("carousel slides differ in size; Instagram crops them all to the first")

    cap_errors, cap_warnings = lint_caption(caption)
    errors += cap_errors
    warnings += cap_warnings
    if errors:
        raise InstagramError("post failed validation:\n  - " + "\n  - ".join(errors))
    return Post(kind=kind, slides=slides, caption=caption, warnings=warnings)


# --- hosting -----------------------------------------------------------------


def check_public(url: str) -> None:
    """Instagram fetches every image itself, so a localhost URL cannot work."""
    host = httpx.URL(url).host
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost") or host.endswith(".local"):
        raise InstagramError(
            f"{url} is not reachable by Instagram. Set PUBLIC_BASE_URL to where this app is reachable from the "
            "internet, or set the STORAGE_S3_* variables to use a public bucket (docs/deploy.md)."
        )


def verify_hosted(client: httpx.Client, url: str) -> None:
    """Confirm the public URL serves the JPEG before Instagram is asked to
    fetch it; a clearer error than Instagram's."""
    check_public(url)
    try:
        check = client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        raise InstagramError(f"{url} could not be fetched: {e}") from e
    if check.status_code != 200 or not check.headers.get("content-type", "").startswith("image/jpeg"):
        raise InstagramError(f"{url} is not publicly serving the JPEG ({check.status_code})")


# --- publish -----------------------------------------------------------------


def _wait_until_ready(graph: Graph, account: Account, container_id: str, *, timeout: float = POLL_TIMEOUT_S) -> None:
    deadline = time.monotonic() + timeout
    while True:
        st = graph.get(container_id, fields="status_code,status", access_token=account.token)
        code = st.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise InstagramError(f"container {container_id} {code}: {st.get('status', '')}")
        if time.monotonic() > deadline:
            raise InstagramError(f"container {container_id} still {code} after {timeout:.0f}s")
        time.sleep(POLL_INTERVAL_S)


def _publish_container(graph: Graph, account: Account, creation_id: str) -> dict:
    media_id = graph.post(f"{account.ig_user_id}/media_publish", creation_id=creation_id,
                          access_token=account.token)["id"]
    try:
        permalink = graph.get(media_id, fields="permalink", access_token=account.token).get("permalink")
    except InstagramError:
        permalink = None
    return {"id": media_id, "permalink": permalink}


def publish_hosted(
    kind: str,
    items: list[tuple[str, str]],
    caption: str,
    graph: Graph,
    account: Account,
    *,
    log=print,
) -> dict:
    """Publish images already at public URLs: `items` is [(url, alt_text)]
    in slide order. Create containers, wait, publish. Returns {id,
    permalink}; the permalink lookup is best-effort, so a post that went
    live is never reported as a failure (and never retried into a
    duplicate)."""
    if kind == "reel":
        raise InstagramError("reels are not published by this version (docs/roadmap.md)")

    def image_container(url: str, alt: str, **extra) -> str:
        params = {"image_url": url, "access_token": account.token, **extra}
        if alt:
            params["alt_text"] = alt
        return graph.post(f"{account.ig_user_id}/media", **params)["id"]

    if kind == "story":
        # The Graph API rejects alt_text on a STORIES container; it is feed-only.
        creation_id = image_container(items[0][0], "", media_type="STORIES")
    elif kind == "image":
        creation_id = image_container(*items[0], caption=caption)
    else:
        children = [image_container(url, alt, is_carousel_item="true") for url, alt in items]
        for c in children:
            _wait_until_ready(graph, account, c)
        log(f"  {len(children)} carousel items ready")
        creation_id = graph.post(
            f"{account.ig_user_id}/media",
            media_type="CAROUSEL",
            children=",".join(children),
            caption=caption,
            access_token=account.token,
        )["id"]

    _wait_until_ready(graph, account, creation_id)
    return _publish_container(graph, account, creation_id)
