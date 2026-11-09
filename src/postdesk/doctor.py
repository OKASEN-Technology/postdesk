"""
`postdesk doctor`: what is still missing, in the order it has to be fixed.
Each check says what it found and, when it fails, what to do. The only
outbound calls are the Instagram `whoami` and one fetch of a test asset
from the public URL; nothing phones home.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy import text

from postdesk import instagram as ig
from postdesk import storage
from postdesk.config import config, settings
from postdesk.db import migrate
from postdesk.db.session import SessionLocal, engine
from postdesk.maker.render import Renderer, RenderError


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str | None = None
    warn: bool = False  # a problem that does not stop posting


def check_claude_key() -> Check:
    if settings.anthropic_api_key:
        return Check("Claude key", True, "ANTHROPIC_API_KEY is set (not called)")
    return Check("Claude key", False, "ANTHROPIC_API_KEY is empty",
                 "Get a key at https://platform.claude.com/ and put it in .env. The scheduler works without it; "
                 "`postdesk make` needs it.", warn=True)


TEST_SPEC = {"type": "statement", "kicker": "Doctor", "headline": "The renderer answers", "body": "A test slide."}


def check_renderer(renderer: Renderer | None = None) -> Check:
    """`GET /health`, then one small slide rendered, so a wrong token or a
    missing font shows up here and not in the first `postdesk make`."""
    if not settings.renderer_url:
        return Check("Renderer", False, "RENDERER_URL is empty",
                     "The scheduler works without it; `postdesk make` needs it. Docker Compose sets it; locally, "
                     "`cd renderer && npm run build && npx postdesk-renderer serve` and set RENDERER_URL in .env.",
                     warn=True)
    r = renderer or Renderer()
    try:
        health = r.health()
        data = r.render(TEST_SPEC)
    except RenderError as e:
        return Check("Renderer", False, str(e),
                     "Check RENDERER_URL and that RENDERER_TOKEN matches the service's (renderer/README.md).")
    themes = ", ".join(health.get("themes") or []) or "none"
    detail = (f"{settings.renderer_url}: version {health.get('version', '?')}, themes {themes}, "
              f"test slide {len(data) // 1024} KB")
    if config.theme.name not in (health.get("themes") or []):
        return Check("Renderer", False, detail + f"; theme {config.theme.name!r} is not among them",
                     "Set [theme].name in postdesk.toml to one the renderer has, or add yours (docs/themes.md).")
    return Check("Renderer", True, detail)


def check_config() -> Check:
    if config.path:
        return Check("postdesk.toml", True, f"{config.path} (timezone {config.account.timezone})")
    return Check("postdesk.toml", False, "not found; defaults in use (timezone UTC)",
                 "Run `postdesk init`, or copy examples/postdesk.toml next to .env and edit it.", warn=True)


def check_database() -> Check:
    try:
        with engine().connect() as conn:
            conn.execute(text("select 1"))
    except Exception as e:
        return Check("Database", False, f"{settings.database_url.split('@')[-1]}: {type(e).__name__}",
                     "Check DATABASE_URL in .env. With Docker Compose: `docker compose up -d db`.")
    return Check("Database", True, f"reachable at {settings.database_url.split('@')[-1]}")


def check_migrations() -> Check:
    try:
        current, head = migrate.current_and_head(engine())
    except Exception as e:
        return Check("Migrations", False, f"could not read: {e}", "Run `postdesk db upgrade`.")
    if current == head:
        return Check("Migrations", True, f"current ({head})")
    return Check("Migrations", False, f"database at {current or 'nothing'}, code expects {head}",
                 "Run `postdesk db upgrade`.")


def check_instagram(graph: ig.Graph | None = None) -> Check:
    if not (settings.instagram_access_token or (settings.instagram_page_token and settings.instagram_user_id)):
        return Check("Instagram", False, "no token in .env",
                     "Follow docs/instagram-setup.md, then run `postdesk instagram page-token`.")
    try:
        from postdesk.instagram.setup import expiry, whoami

        acct, info, used, total = whoami(graph or ig.Graph())
    except ig.InstagramError as e:
        return Check("Instagram", False, str(e), "See docs/troubleshooting.md \"Token\".")
    detail = f"@{acct.username}, {acct.token_kind} token expires {expiry(info)}, {used}/{total} posts used today"
    if acct.token_kind == "user":
        return Check("Instagram", True, detail,
                     "The user token expires in 60 days. Run `postdesk instagram page-token` for one that does not.",
                     warn=True)
    return Check("Instagram", True, detail)


def check_storage(client: httpx.Client | None = None) -> Check:
    """Store a tiny JPEG and fetch it back from the public URL, as Instagram
    would. The test asset is content-addressed, so it is one row forever."""
    where = storage.backend()
    base = settings.storage_s3_public_base_url if where == "s3" else settings.public_base_url
    try:
        ig.check_public(base or "")
    except ig.InstagramError as e:
        return Check("Public storage", False, str(e).split(". ")[0],
                     "Set PUBLIC_BASE_URL to the address this app is reachable at from the internet, or fill in the "
                     "STORAGE_S3_* variables (docs/deploy.md).")
    data = TEST_JPEG
    try:
        with SessionLocal() as session:
            asset = storage.store_asset(session, data=data, filename="doctor.jpg", content_type="image/jpeg",
                                        uploaded_by="doctor", provenance={"source": "doctor"})
            url = storage.public_url(asset)
            if where == "local":
                _ensure_referenced(session, asset)
    except Exception as e:
        return Check("Public storage", False, f"could not store a test file on {where}: {e}",
                     "Check the storage settings in .env and the data volume.")
    try:
        resp = (client or httpx.Client(timeout=15)).get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        return Check("Public storage", False, f"{url}: {e}",
                     "The address is not reachable from this machine. Is the app running there, and the port open?")
    if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/jpeg"):
        return Check("Public storage", False, f"{url} answered {resp.status_code}",
                     "Instagram fetches slides from this URL. Check PUBLIC_BASE_URL (or the bucket's public access).")
    return Check("Public storage", True, f"{where}: {url} serves the test image")


def _ensure_referenced(session, asset) -> None:
    """`/media/<id>` only serves assets a post references, so the doctor's
    test image is pinned by a rejected marker post nobody sees."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from postdesk.db.models import SocialPost

    exists = session.execute(select(SocialPost.id).where(SocialPost.created_by == "doctor")).first()
    if exists:
        return
    now = datetime.now(UTC)
    session.add(SocialPost(kind="image", status="rejected", title="doctor: storage check", caption="",
                           slides=[{"asset_id": str(asset.id), "alt_text": "A test image."}],
                           created_by="doctor", created_at=now, updated_at=now))
    session.commit()


def _tiny_jpeg() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (240, 240, 240)).save(buf, format="JPEG")
    return buf.getvalue()


TEST_JPEG = _tiny_jpeg()


def check_spend() -> Check:
    """This month's agent spend against `[llm].monthly_usd`."""
    from postdesk import control, store

    with SessionLocal() as session:
        store.refresh_config(session)
        s = control.spend(session)
        paused = control.paused(session)
    detail = s.line() + (f"; the agents are paused (by {paused.get('by')})" if paused else "")
    if s.over:
        return Check("Spend", True, detail + "; the ceiling is reached, so the agents have stopped",
                     "Raise [llm].monthly_usd (or the ceiling on the settings page), or wait for the month to turn.",
                     warn=True)
    return Check("Spend", True, detail)


def check_telegram(bot=None) -> Check:
    """The bot token answers, and a chat is paired."""
    from postdesk.telegram import pairing
    from postdesk.telegram.api import Bot, TelegramError

    if not settings.telegram_bot_token:
        return Check("Telegram", False, "TELEGRAM_BOT_TOKEN is empty",
                     "Make a bot with @BotFather (/newbot), put its token in .env and restart. postdesk works "
                     "without it, from the web UI only.", warn=True)
    try:
        me = (bot or Bot()).get_me()
    except TelegramError as e:
        return Check("Telegram", False, str(e), "Check TELEGRAM_BOT_TOKEN against what @BotFather gave you.")
    with SessionLocal() as session:
        linked = pairing.linked(session)
    if linked is None:
        return Check("Telegram", True, f"@{me.get('username')} answers; no chat is paired",
                     f"Send /start to @{me.get('username')}, then paste the code into Settings -> Telegram (or "
                     "`postdesk telegram link <code>`).", warn=True)
    return Check("Telegram", True, f"@{me.get('username')}, paired with {linked.get('title') or linked['chat_id']}")


def run(*, graph: ig.Graph | None = None, renderer: Renderer | None = None, bot=None) -> list[Check]:
    checks = [check_claude_key(), check_config(), check_database()]
    if checks[-1].ok:
        checks.append(check_migrations())
        if checks[-1].ok:
            checks.append(check_instagram(graph))
            checks.append(check_storage())
            checks.append(check_telegram(bot))
            checks.append(check_spend())
    checks.append(check_renderer(renderer))
    return checks
