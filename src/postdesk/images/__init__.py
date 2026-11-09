"""
Where a slide's picture comes from, and on what terms (docs/images.md).

An image source finds a picture of a subject. Every picture it offers
carries where it is from and on what terms (`Found`: source, permalink,
licence, licence URL, author, credit, the permission on file if any), and
the picture picked is stored as a `content_asset` whose `provenance` keeps
all of that. Built in, in `[images].sources`:

- `page`: the picture on the source's own page (og:image, or the image by
  the title), credited to the site. Credit only, no licence.
- `wikimedia`: Wikimedia Commons, public domain, CC0 and CC BY only;
  share-alike, non-commercial and no-derivatives are skipped.
- `business_discovery`: the subject's own Instagram account through the
  Graph API with the account's own token (never scraping); Claude picks
  the post that shows the subject, not a flyer or a selfie. Credit only,
  unless an `image_permission` on file covers that account.
- `attachment`: an image the admin attaches to a card (web or Telegram)
  with where it is from; the partner records the credit.

The partner asks for lookups (`image_lookups` in its reply); `run_lookups`
runs them after its turn, files what came back on the card as `found-N`
candidates, and gives the partner another turn. At most `MAX_LOOKUPS` a
card. A source of your own registers on the `postdesk.images` entry point
with a `find(session, request, ctx) -> list[Found]` method.

`image_permission` rows are a person's record of a rights holder's yes;
nothing here writes one (images/rights.py, `postdesk permission add`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from importlib.metadata import entry_points

import httpx
from sqlalchemy.orm import Session

from postdesk.config import config
from postdesk.db.models import Card

MAX_LOOKUPS = 6
MAX_BYTES = 15 * 1024 * 1024
LOOKUP_SOURCES = ("page", "wikimedia", "business_discovery")


class ImageError(RuntimeError):
    pass


@dataclass
class Found:
    """One picture an image source offers, with its terms."""

    url: str  # where its bytes are
    source: str  # page | wikimedia | business_discovery | attachment | a plugin's key
    credit: str
    permalink: str | None = None  # the page it was found on
    licence: str | None = None  # "CC BY 4.0", "Public domain"; None: credit only
    licence_url: str | None = None
    author: str | None = None
    permission_id: str | None = None  # an image_permission on file that covers it
    tag: str | None = None  # the caption's credit, "@handle"
    what: str | None = None  # what it shows, for the alt text
    subject: str | None = None

    def provenance(self, *, by: str) -> dict:
        return {
            "source": self.source, "permalink": self.permalink, "licence": self.licence,
            "licence_url": self.licence_url, "author": self.author, "credit": self.credit,
            "permission_id": self.permission_id, "tag": self.tag, "what": self.what, "subject": self.subject,
            "url": self.url, "generated": False, "retrieved_at": datetime.now(UTC).isoformat(), "found_by": by,
        }


@dataclass
class Context:
    """What a lookup may use: the card, and the outside world (tests fake it)."""

    card: Card
    client: httpx.Client | None = None
    graph: object | None = None  # instagram.Graph
    pick: object | None = None  # a structured call, for business_discovery's choice
    log: object = print
    notes: list[str] = field(default_factory=list)


def built_in() -> dict[str, type]:
    from postdesk.images import attachment, business_discovery, page, wikimedia

    return {"page": page.PageImages, "wikimedia": wikimedia.WikimediaImages,
            "business_discovery": business_discovery.BusinessDiscovery, "attachment": attachment.Attachments}


def available() -> dict[str, type]:
    out = built_in()
    for ep in entry_points(group="postdesk.images"):
        try:
            out[ep.name] = ep.load()
        except Exception as e:  # a broken plugin should not take the app down
            raise ImageError(f"image source plugin {ep.name!r} failed to load: {e}") from e
    return out


def enabled() -> list[str]:
    known = available()
    return [s for s in config.images.sources if s in known]


def download(url: str, client: httpx.Client | None = None) -> bytes:
    from postdesk.sources import web

    http = client or web.client()
    try:
        resp = http.get(url)
    except httpx.HTTPError as e:
        raise ImageError(f"could not fetch {url}: {type(e).__name__}") from e
    if resp.status_code != 200:
        raise ImageError(f"{url} answered {resp.status_code}")
    ctype = resp.headers.get("content-type", "")
    if ctype and not ctype.startswith("image/"):
        raise ImageError(f"{url} is {ctype}, not an image")
    if len(resp.content) > MAX_BYTES:
        raise ImageError(f"{url} is larger than {MAX_BYTES // (1024 * 1024)} MB")
    return resp.content


def store(session: Session, found: Found, *, by: str, client: httpx.Client | None = None):
    """Download `found` and keep it as a content_asset with its provenance."""
    from postdesk import instagram as ig
    from postdesk import storage

    data = download(found.url, client)
    try:
        jpeg, _ = ig.to_jpeg_bytes(data)
    except OSError as e:
        raise ImageError(f"{found.url} is not an image Pillow can read") from e
    name = (found.subject or found.source or "image").lower().replace(" ", "-")[:40] + ".jpg"
    asset = storage.store_asset(session, data=jpeg, filename=name, content_type="image/jpeg", uploaded_by=by,
                                provenance=found.provenance(by=by))
    if asset.provenance.get("source") != found.source:  # the same bytes were stored before, from elsewhere
        asset.provenance = {**asset.provenance, "also": found.provenance(by=by)}
        session.commit()
    return asset


def candidate(asset, found: Found, key: str) -> dict:
    """A stored find as one of the card's image candidates (board/facts.py)."""
    from postdesk import storage

    return {"key": key, "for": found.subject or "hero", "url": storage.asset_url(asset.id),
            "asset_id": str(asset.id), "credit": found.credit, "licence": found.licence,
            "licence_url": found.licence_url, "permalink": found.permalink, "what": found.what,
            "permission_id": found.permission_id, "tag": found.tag,
            "source": f"found by the {found.source} image source"}


def run_lookups(session: Session, card: Card, requests: list[dict], ctx: Context) -> tuple[list[dict], list[str]]:
    """Run the partner's lookups: ([new candidates], [problems]). Each find
    is stored with its provenance and filed on `card.plan["found"]`."""
    plan = dict(card.plan or {})
    done = int(plan.get("lookups") or 0)
    found_list = list(plan.get("found") or [])
    sources = available()
    added, problems = [], []
    for req in requests:
        src = str(req.get("source") or "")
        if done >= MAX_LOOKUPS:
            problems.append(f"no more lookups on this card (the limit is {MAX_LOOKUPS})")
            break
        if src not in LOOKUP_SOURCES or src not in enabled():
            problems.append(f"`{src}` is not an image source this account uses ([images].sources)")
            continue
        done += 1
        try:
            finds = sources[src]().find(session, req, ctx)
        except Exception as e:  # one failed lookup does not stop the others
            problems.append(f"{src} lookup for {req.get('subject') or 'the post'}: {e}")
            continue
        if not finds:
            problems.append(f"{src} found nothing usable for {req.get('query') or req.get('subject')!r}")
        for f in finds[:3]:
            f.subject = f.subject or (req.get("subject") or "hero")
            try:
                asset = store(session, f, by="partner", client=ctx.client)
            except Exception as e:
                problems.append(f"{src}: {e}")
                continue
            c = candidate(asset, f, f"found-{len(found_list) + 1}")
            found_list.append(c)
            added.append(c)
    plan["found"], plan["lookups"] = found_list, done
    card.plan = plan
    session.commit()
    return added, problems


def as_dict(found: Found) -> dict:
    return asdict(found)
