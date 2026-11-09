"""
The Graph API client and the account behind the token.

Instagram's publishing API works through a Facebook Page the professional
account is linked to (docs/instagram-setup.md). Every call carries that
Page's token. `postdesk instagram page-token` swaps the 60-day user token
from the Graph API Explorer for a Page token that does not expire.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from postdesk.config import settings


class InstagramError(RuntimeError):
    """Missing config, a failed Graph call, or a post that fails validation."""


class Graph:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=60)
        self.base = f"https://graph.facebook.com/{settings.meta_graph_version}"

    def _call(self, method: str, path: str, **params) -> dict:
        resp = self.client.request(method, f"{self.base}/{path}", params=params)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code != 200 or "error" in body:
            err = body.get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or resp.text[:300]
            raise InstagramError(f"Graph {method} /{path.split('?')[0]} failed ({resp.status_code}): {msg}")
        return body

    def get(self, path: str, **params) -> dict:
        return self._call("GET", path, **params)

    def post(self, path: str, **params) -> dict:
        return self._call("POST", path, **params)


@dataclass
class Account:
    ig_user_id: str
    username: str
    page_id: str | None
    page_name: str | None
    token: str  # the token publishing calls use
    token_kind: str  # "page" (no expiry) | "user" (60 days)


def resolve_account(graph: Graph) -> Account:
    """Prefer the configured Page token; otherwise find the Page linked to
    the Instagram account from the user token's `me/accounts`."""
    if settings.instagram_page_token and settings.instagram_user_id:
        ig = graph.get(settings.instagram_user_id, fields="id,username", access_token=settings.instagram_page_token)
        return Account(ig["id"], ig.get("username", ""), None, None, settings.instagram_page_token, "page")

    if not settings.instagram_access_token:
        raise InstagramError(
            "No Instagram token configured. Set INSTAGRAM_ACCESS_TOKEN in .env (the 60-day token from the "
            "Graph API Explorer); see docs/instagram-setup.md."
        )
    pages = graph.get(
        "me/accounts",
        fields="id,name,access_token,instagram_business_account{id,username}",
        access_token=settings.instagram_access_token,
    ).get("data", [])
    linked = [p for p in pages if p.get("instagram_business_account")]
    if settings.instagram_user_id:
        linked = [p for p in linked if p["instagram_business_account"]["id"] == settings.instagram_user_id]
    if not linked:
        raise InstagramError(
            "The token sees no Facebook Page with a linked Instagram professional account. Regenerate it and "
            "tick both the Page and the Instagram account in the Facebook popup (docs/instagram-setup.md)."
        )
    if len(linked) > 1:
        names = ", ".join(f"@{p['instagram_business_account']['username']}" for p in linked)
        raise InstagramError(f"The token reaches several Instagram accounts ({names}); set INSTAGRAM_USER_ID.")
    p = linked[0]
    ig = p["instagram_business_account"]
    return Account(ig["id"], ig.get("username", ""), p["id"], p["name"], p["access_token"], "user")


def exchange_for_long_lived(graph: Graph, user_token: str) -> str:
    """A short-lived user token -> a 60-day one. Page tokens fetched with a
    long-lived user token never expire."""
    if not (settings.meta_app_id and settings.meta_app_secret):
        raise InstagramError("META_APP_ID and META_APP_SECRET are required to exchange the token.")
    return graph.get(
        "oauth/access_token",
        grant_type="fb_exchange_token",
        client_id=settings.meta_app_id,
        client_secret=settings.meta_app_secret,
        fb_exchange_token=user_token,
    )["access_token"]


def token_info(graph: Graph, token: str) -> dict:
    """debug_token: validity, expiry (0 = never), scopes. Needs the app
    credentials; returns {} without them."""
    if not (settings.meta_app_id and settings.meta_app_secret):
        return {}
    app_token = f"{settings.meta_app_id}|{settings.meta_app_secret}"
    return graph.get("debug_token", input_token=token, access_token=app_token).get("data", {})


def publishing_limit(graph: Graph, account: Account) -> tuple[int, int]:
    """(posts used, allowed) in the rolling 24 hours."""
    data = graph.get(
        f"{account.ig_user_id}/content_publishing_limit",
        fields="config,quota_usage",
        access_token=account.token,
    ).get("data", [{}])[0]
    return int(data.get("quota_usage", 0)), int(data.get("config", {}).get("quota_total", 100))
