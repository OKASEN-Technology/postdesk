"""
The renderer's client. A slide spec is checked against the slide schema
(the renderer's own `schema/slide.json`, shipped here as a copy) before it
goes over the wire, so a bad spec fails with the field named rather than a
400 from the service. Images a stored asset backs travel as data URIs, so
the renderer needs no route back to the app.
"""

from __future__ import annotations

import base64
import io
import json
import time
import uuid
from functools import lru_cache
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator
from PIL import Image as PILImage
from sqlalchemy.orm import Session

from postdesk import storage
from postdesk.config import config, settings
from postdesk.db.models import ContentAsset

SCHEMA_PATH = Path(__file__).with_name("slide.schema.json")
TIMEOUT_S = 60
JPEG_QUALITY = 92
RETRY_DELAYS_S = (3, 8, 15, 30)
TRANSIENT_STATUSES = {502, 503, 504}


class RenderError(RuntimeError):
    """A spec the schema refuses, or a renderer that is missing, down or unhappy."""


class RendererUnavailable(RenderError):
    """A temporary renderer outage that can be retried by the job queue."""


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


SHARED_DEFS = ("text", "optionalText", "image", "optionalImage", "chrome")


def slide_types() -> tuple[str, ...]:
    return tuple(k for k in schema()["$defs"] if k not in SHARED_DEFS)


@lru_cache(maxsize=8)
def _validator(slide_type: str) -> Draft202012Validator:
    s = schema()
    return Draft202012Validator({"$ref": f"#/$defs/{slide_type}", "$defs": s["$defs"]})


def validate_spec(spec: dict) -> list[str]:
    """Problems with the spec, each naming its field; empty when valid."""
    if not isinstance(spec, dict):
        return ["spec: must be an object"]
    t = spec.get("type")
    if t not in slide_types():
        return [f"type: must be one of {', '.join(slide_types())}"]
    out = []
    for e in sorted(_validator(t).iter_errors(spec), key=lambda e: list(e.path)):
        where = ".".join(str(p) for p in e.path) or "spec"
        if e.validator == "additionalProperties":
            extra = set(e.instance) - set(e.schema.get("properties", {}))
            out.append(f"{where}: unknown field {', '.join(sorted(extra))}")
        elif e.validator == "required":
            out.append(f"{where}: {e.message}")
        else:
            out.append(f"{where}: {e.message}")
    return out[:8]


def _data_uri(session: Session, asset_id: str) -> str:
    try:
        asset = session.get(ContentAsset, uuid.UUID(str(asset_id)))
    except ValueError:
        asset = None
    if asset is None:
        raise RenderError(f"image asset {asset_id} not found")
    data = storage.asset_bytes(asset)
    return f"data:{asset.content_type};base64,{base64.b64encode(data).decode('ascii')}"


def render_spec(spec: dict, session: Session | None = None) -> dict:
    """The spec as the renderer takes it: image dicts cut to url, credit
    and fit, a stored asset's bytes inlined, chrome and provenance keys
    dropped."""
    out = {k: v for k, v in spec.items() if k not in ("theme", "provenance")}
    for field in ("image", "background"):
        img = out.get(field)
        if isinstance(img, dict):
            url = img.get("url")
            if img.get("asset_id") and session is not None:
                url = _data_uri(session, img["asset_id"])
            elif img.get("asset_id") and not url:
                raise RenderError("an image backed by a stored asset needs a database session to render")
            out[field] = {k: v for k, v in
                          {"url": url, "credit": img.get("credit"), "fit": img.get("fit")}.items() if v}
    return out


class Renderer:
    """`POST /render` on the Node service (renderer/README.md)."""

    def __init__(self, url: str | None = None, token: str | None = None, *, client: httpx.Client | None = None,
                 theme: str | None = None) -> None:
        self.url = (url or settings.renderer_url or "").rstrip("/")
        self.token = token if token is not None else settings.renderer_token
        self.theme = theme or config.theme.name
        self.client = client or httpx.Client(timeout=TIMEOUT_S)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _require(self) -> None:
        if not self.url:
            raise RenderError("RENDERER_URL is not set in .env; the maker needs the renderer (renderer/README.md)")

    def health(self) -> dict:
        self._require()
        try:
            resp = self.client.get(f"{self.url}/health", timeout=10)
        except httpx.HTTPError as e:
            raise RenderError(f"renderer at {self.url} is not reachable ({type(e).__name__}: {e})") from e
        if resp.status_code != 200:
            raise RenderError(f"renderer at {self.url} answered {resp.status_code} to /health")
        return resp.json()

    def render(self, spec: dict, *, session: Session | None = None, theme: str | None = None) -> bytes:
        """A JPEG for `spec`. The spec is validated first."""
        self._require()
        body = render_spec(spec, session)
        problems = validate_spec(body)
        if problems:
            raise RenderError(f"the {spec.get('type', '?')} slide's spec is invalid: " + "; ".join(problems))
        theme = theme or self.theme
        for attempt in range(len(RETRY_DELAYS_S) + 1):
            try:
                resp = self.client.post(f"{self.url}/render", json=body, headers=self._headers(),
                                        params={"theme": theme} if theme else None)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt == len(RETRY_DELAYS_S):
                    raise RendererUnavailable(f"rendering a {spec['type']} slide failed: the renderer is temporarily "
                                      f"unavailable ({type(e).__name__}). Please try again.") from e
            except httpx.HTTPError as e:
                raise RenderError(f"rendering a {spec['type']} slide failed ({type(e).__name__}: {e})"[:500]) from e
            else:
                if resp.status_code not in TRANSIENT_STATUSES:
                    break
                if attempt == len(RETRY_DELAYS_S):
                    raise RendererUnavailable(f"rendering a {spec['type']} slide failed ({resp.status_code}): "
                                      "the renderer is temporarily unavailable. Please try again.")
            # A sleeping service may reject POSTs without starting. A small GET
            # can wake it before the next attempt; a failed probe must not mask
            # the original error or prevent the normal retries.
            try:
                self.client.get(f"{self.url}/health", timeout=TIMEOUT_S)
            except httpx.HTTPError:
                pass
            time.sleep(RETRY_DELAYS_S[attempt])
        if resp.status_code != 200:
            try:
                detail = resp.json().get("detail") or resp.text
            except ValueError:
                detail = resp.text
            raise RenderError(f"rendering a {spec['type']} slide failed ({resp.status_code}): {str(detail)[:300]}")
        ct = resp.headers.get("content-type", "")
        if ct.startswith("image/jpeg"):
            return resp.content
        if not ct.startswith("image/"):
            raise RenderError(f"rendering a {spec['type']} slide returned {ct or 'no content type'}, not an image")
        with PILImage.open(io.BytesIO(resp.content)) as im:
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return buf.getvalue()
