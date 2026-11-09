"""What `postdesk instagram whoami` and `page-token` do, shared with the API
and `doctor`."""

from __future__ import annotations

from datetime import UTC, datetime

from postdesk.config import settings
from postdesk.instagram import graph as ig


def expiry(info: dict) -> str:
    exp = info.get("expires_at")
    if exp is None:
        return "unknown (set META_APP_ID and META_APP_SECRET to see it)"
    if exp == 0:
        return "never"
    when = datetime.fromtimestamp(exp, UTC)
    days = (when - datetime.now(UTC)).days
    return f"{when:%Y-%m-%d} ({days} days)"


def whoami(graph: ig.Graph) -> tuple[ig.Account, dict, int, int]:
    """(account, token info, quota used, quota total)."""
    acct = ig.resolve_account(graph)
    token = acct.token if acct.token_kind == "page" else settings.instagram_access_token
    info = ig.token_info(graph, token or "")
    used, total = ig.publishing_limit(graph, acct)
    return acct, info, used, total


def page_token(graph: ig.Graph) -> tuple[ig.Account, dict]:
    """Swap the user token for the linked Page's token, which does not
    expire. Returns the account (its `token` is the Page token) and the
    token's debug info."""
    if not settings.instagram_access_token:
        raise ig.InstagramError("INSTAGRAM_ACCESS_TOKEN is not set.")
    info = ig.token_info(graph, settings.instagram_access_token)
    if info.get("expires_at") and (info["expires_at"] - datetime.now(UTC).timestamp()) < 2 * 86400:
        # A short-lived Explorer token: its Page token would die with it.
        settings.instagram_access_token = ig.exchange_for_long_lived(graph, settings.instagram_access_token)
    saved = settings.instagram_page_token
    settings.instagram_page_token = None  # force the me/accounts lookup
    try:
        acct = ig.resolve_account(graph)
    finally:
        settings.instagram_page_token = saved
    return acct, ig.token_info(graph, acct.token)
