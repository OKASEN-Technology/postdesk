"""The `postdesk` command. `postdesk --help` lists everything; the README
walks through the ones you need."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import typer

from postdesk import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
instagram_app = typer.Typer(no_args_is_help=True, help="The account behind the token.")
db_app = typer.Typer(no_args_is_help=True, help="The database.")
telegram_app = typer.Typer(no_args_is_help=True, help="The Telegram bot: pair your chat, test it.")
permission_app = typer.Typer(no_args_is_help=True, help="Permissions on file: a rights holder's yes to their images.")
app.add_typer(instagram_app, name="instagram")
app.add_typer(db_app, name="db")
app.add_typer(telegram_app, name="telegram")
app.add_typer(permission_app, name="permission")


def _echo(msg: str = "") -> None:
    typer.echo(msg)


def _err(msg: str) -> None:
    typer.echo(f"error: {msg}", err=True)


def _graph():
    from postdesk import instagram as ig

    return ig.Graph()


@app.command()
def version() -> None:
    """Print the version."""
    _echo(__version__)


# --- setup -------------------------------------------------------------------


@app.command()
def init(
    directory: Path = typer.Option(Path("."), "--dir", help="Where to write .env and postdesk.toml."),
    run_doctor: bool = typer.Option(True, "--doctor/--no-doctor", help="Run `postdesk doctor` afterwards."),
) -> None:
    """Set up: a few questions, then .env and postdesk.toml are written and doctor runs."""
    from postdesk import init as wizard

    env_path, toml_path = directory / ".env", directory / "postdesk.toml"
    for p in (env_path, toml_path):
        if p.exists() and not typer.confirm(f"{p} exists. Overwrite it?", default=False):
            _echo(f"Keeping {p}.")
            raise typer.Exit(1)

    _echo("postdesk setup. Enter keeps the default in brackets.\n")
    handle = typer.prompt("Your Instagram username (without the @)", default="yourhandle")
    site = typer.prompt("The site your bio links to", default="https://example.com")
    while True:
        tz = typer.prompt("Your timezone (IANA name)", default="America/New_York")
        try:
            ZoneInfo(tz)
            break
        except (ZoneInfoNotFoundError, ValueError):
            _echo(f"  {tz!r} is not a timezone name like Europe/London or America/New_York.")
    audience = typer.prompt("Who you post for, in one line", default="")
    anthropic = typer.prompt("Anthropic API key (Enter to add later)", default="", hide_input=True)
    while True:
        password = typer.prompt("A password for the web page (8+ characters; Enter to set it in the browser)",
                                default="", hide_input=True)
        if not password or len(password) >= 8:
            break
        _echo("  At least 8 characters.")
    public = typer.prompt("Where this app is reachable from the internet (Enter if not yet)",
                          default="http://localhost:8080")

    env = {"POSTDESK_ADMIN_PASSWORD": password, "PUBLIC_BASE_URL": public}
    if anthropic:
        env["ANTHROPIC_API_KEY"] = anthropic
    env_path.write_text(wizard.render_env(env), encoding="utf-8")
    toml_path.write_text(wizard.render_toml({"handle": handle, "site": site, "timezone": tz, "audience": audience}),
                         encoding="utf-8")
    _echo(f"\nWrote {env_path} and {toml_path}.")
    _echo("Next: connect Instagram (docs/instagram-setup.md), then `postdesk instagram page-token`.")
    if run_doctor:
        from postdesk.config import reload

        reload()
        _echo()
        doctor()


@app.command()
def doctor() -> None:
    """Check the Claude key, database, Instagram token, public storage, Telegram, the spend and the renderer."""
    from postdesk import doctor as dr

    checks = dr.run()
    failed = False
    for c in checks:
        mark = "ok  " if c.ok and not c.warn else ("warn" if c.ok or c.warn else "FAIL")
        _echo(f"{mark}  {c.name}: {c.detail}")
        if c.fix and (not c.ok or c.warn):
            _echo(f"      -> {c.fix}")
        if not c.ok and not c.warn:
            failed = True
    if failed:
        _echo("\nFix the first FAIL, then run `postdesk doctor` again.")
        raise typer.Exit(1)
    _echo("\nEverything the scheduler needs is in place.")


# --- running -----------------------------------------------------------------


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Interface to listen on."),
    port: int = typer.Option(8080, help="Port."),
    scheduler: bool = typer.Option(True, "--scheduler/--no-scheduler",
                                   help="Run the publish loop inside this process (off if you use `postdesk cron`)."),
    worker: bool = typer.Option(True, "--worker/--no-worker",
                                help="Run the agents' jobs inside this process (off if you run `postdesk worker`)."),
    telegram: bool = typer.Option(True, "--telegram/--no-telegram",
                                  help="Run the Telegram bot inside this process (when TELEGRAM_BOT_TOKEN is set)."),
) -> None:
    """Run the API, the web UI, the scheduler loop, the agents' worker and the Telegram bot."""
    import uvicorn

    from postdesk.main import create_app

    uvicorn.run(create_app(scheduler=scheduler, worker=worker, telegram_bot=telegram), host=host, port=port,
                log_level="info")


@app.command()
def worker(
    once: bool = typer.Option(False, "--once", help="Run the jobs waiting now (and one housekeeping pass), then exit."),
    scout: bool = typer.Option(True, "--scout/--no-scout", help="Let the worker run the scout on its schedule."),
) -> None:
    """The agents' job runner: the partner, maker, editor and revision turns,
    the board sync and the scout. `serve` runs one too; run this instead with
    `serve --no-worker` to keep it in its own process."""
    import signal
    import threading

    from postdesk import jobs
    from postdesk.db.session import SessionLocal

    if once:
        with SessionLocal() as session:
            jobs.housekeeping(session, None, log=_echo, scout=scout)
            done = jobs.run_pending(session, None, log=_echo)
        _echo(f"worker: ran {len(done)} job(s)")
        return
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        jobs.loop(stop, log=_echo, scout=scout)
    except KeyboardInterrupt:
        stop.set()


@app.command("scout")
def scout_cmd(
    days: int = typer.Option(None, "--days", help="How far back to look (default `[scout].days`)."),
    source: str = typer.Option(None, "--source", help="Only this `[sources]` key."),
) -> None:
    """Look through the sources now and file new items as cards in Planned
    (at most max_per_run in the scout settings); the worker writes their briefs."""
    from postdesk.board import scout
    from postdesk.db.session import SessionLocal
    from postdesk.sources import SourceError

    with SessionLocal() as session:
        try:
            r = scout.run(session, days=days, only=source, log=_echo)
        except SourceError as e:
            _err(str(e))
            raise typer.Exit(1) from None
    _echo(f"scout: {len(r.filed)} filed, {r.seen} seen, {r.deferred} left for the next run")
    for e in r.errors:
        _err(e)
    raise typer.Exit(1 if r.errors and not r.filed else 0)


@app.command()
def cron(force: bool = typer.Option(False, "--force", help="Publish even outside publisher hours.")) -> None:
    """One pass: publish what is due, then collect insights. For a system cron or a hosted cron job."""
    import httpx

    from postdesk import queue, scheduler, store
    from postdesk.config import settings
    from postdesk.db.session import SessionLocal

    # A hosted cron starts in a fresh process, so it must load the same saved
    # account, publishing window and credentials as the web application.
    with SessionLocal() as session:
        store.refresh_config(session, log=_echo)
        # Instagram fetches private-bucket media through the web service. A
        # sleeping Render free instance must be awake before Meta fetches it.
        host = httpx.URL(settings.public_base_url).host or ""
        if host.endswith(".onrender.com") and not settings.storage_s3_public_base_url and queue.due(session):
            try:
                response = httpx.get(f"{settings.public_base_url.rstrip('/')}/api/health", timeout=120)
                response.raise_for_status()
            except httpx.HTTPError:
                _err("Media host is unavailable; posts remain queued for the next run.")
                raise typer.Exit(1) from None

    result = scheduler.run_once(force=force)
    if result.published or result.failed:
        _echo(f"published {len(result.published)}, failed {len(result.failed)}")

    # Run this after publishing so an ideas or wake-up failure cannot block posts.
    if settings.postdesk_daily_ideas:
        from postdesk.board import daily_ideas

        try:
            with SessionLocal() as session:
                wake = daily_ideas.ensure_due(session)
            if wake:
                response = httpx.get(f"{settings.public_base_url.rstrip('/')}/api/health", timeout=120)
                response.raise_for_status()
                _echo("daily idea: worker awake")
        except Exception as e:
            _err(f"daily idea: {type(e).__name__}; will retry on the next cron run")
    raise typer.Exit(0 if result.ok else 1)


# --- instagram ---------------------------------------------------------------


@instagram_app.command()
def whoami() -> None:
    """Which account the token reaches, when it expires, and today's publishing quota."""
    from postdesk import instagram as ig
    from postdesk.instagram.setup import expiry, whoami

    try:
        acct, info, used, total = whoami(_graph())
    except ig.InstagramError as e:
        _err(str(e))
        raise typer.Exit(1) from None
    _echo(f"account:  @{acct.username} ({acct.ig_user_id})")
    if acct.page_name:
        _echo(f"page:     {acct.page_name} ({acct.page_id})")
    var = "INSTAGRAM_PAGE_TOKEN" if acct.token_kind == "page" else "INSTAGRAM_ACCESS_TOKEN"
    _echo(f"token:    {var}, expires {expiry(info)}")
    if acct.token_kind != "page":
        _echo("          run `postdesk instagram page-token` for a token that does not expire")
    _echo(f"quota:    {used} of {total} posts used in the last 24 hours")


@instagram_app.command("page-token")
def page_token() -> None:
    """Print a Page token that does not expire (a secret), to paste into .env."""
    from postdesk import instagram as ig
    from postdesk.instagram.setup import expiry, page_token

    try:
        acct, info = page_token(_graph())
    except ig.InstagramError as e:
        _err(str(e))
        raise typer.Exit(1) from None
    typer.echo(f"# Page token for {acct.page_name}, publishing as @{acct.username}.", err=True)
    typer.echo(f"# Expires: {expiry(info)}. Add these two lines to .env:", err=True)
    _echo(f"INSTAGRAM_PAGE_TOKEN={acct.token}")
    _echo(f"INSTAGRAM_USER_ID={acct.ig_user_id}")


# --- posts -------------------------------------------------------------------


def _build(slides: list[Path], caption: Path | None, story: bool):
    from postdesk import instagram as ig

    caption_text = caption.read_text(encoding="utf-8") if caption else ""
    try:
        post = ig.build_post(slides, caption_text, story=story)
    except ig.InstagramError as e:
        _err(str(e))
        raise typer.Exit(1) from None
    _echo(f"{post.kind}: {len(post.slides)} image(s)")
    for i, s in enumerate(post.slides, start=1):
        alt = f"  alt: {s.alt_text[:70]}{'…' if len(s.alt_text) > 70 else ''}" if s.alt_text else ""
        _echo(f"  {i:>2}. {s.path.name}  {s.size[0]}×{s.size[1]}  {len(s.jpeg) // 1024} KB{alt}")
    if post.caption:
        first = post.caption.splitlines()[0]
        _echo(f"caption: {len(post.caption)} chars, opens: {first[:80]}")
    for w in post.warnings:
        _echo(f"warning: {w}")
    return post


def _store_slides(session, post, *, by: str) -> list[dict]:
    from postdesk import storage

    out = []
    for sl in post.slides:
        asset = storage.store_asset(session, data=sl.jpeg, filename=sl.path.with_suffix(".jpg").name,
                                    content_type="image/jpeg", uploaded_by=by,
                                    provenance={"source": "upload", "file": sl.path.name, "uploaded_by": by})
        _echo(f"  stored {sl.path.name} -> {storage.public_url(asset)}")
        out.append({"asset_id": str(asset.id), "alt_text": sl.alt_text})
    return out


@app.command()
def post(
    slides: list[Path] = typer.Argument(..., help="Image files in slide order (PNG, JPEG or WebP)."),
    caption: Path | None = typer.Option(None, "--caption", help="Caption file; a `---` / ALT TEXT block sets alt."),
    story: bool = typer.Option(False, "--story", help="Post the one image as a Story."),
    title: str = typer.Option("", "--title", help="Internal label for the queue's history."),
    publish: bool = typer.Option(False, "--publish", help="Actually post. Without it, a dry run."),
) -> None:
    """Post by hand, now. Dry run by default: validates, shows the account and quota, posts nothing."""
    from postdesk import instagram as ig
    from postdesk import storage
    from postdesk.db.models import SocialPost
    from postdesk.db.session import SessionLocal

    built = _build(slides, caption, story)
    graph = _graph()
    try:
        acct = ig.resolve_account(graph)
        used, total = ig.publishing_limit(graph, acct)
        _echo(f"account: @{acct.username}, {used} of {total} posts used in the last 24 hours")
        if used >= total:
            raise ig.InstagramError("publishing quota is used up; try again later")
        s3 = storage.backend() == "s3"
        ig.check_public((storage.settings.storage_s3_public_base_url if s3 else storage.settings.public_base_url) or "")
    except ig.InstagramError as e:
        _err(str(e))
        raise typer.Exit(1) from None
    if not publish:
        _echo("\nDry run. Nothing was stored or posted. Add --publish to post.")
        return

    _echo("\nPublishing…")
    now = datetime.now(UTC)
    with SessionLocal() as session:
        stored = _store_slides(session, built, by="cli")
        row = SocialPost(kind=built.kind, status="publishing", title=title or f"Posted by hand {now:%Y-%m-%d %H:%M}",
                         caption=built.caption, slides=stored, publish_at=now, approved_by="cli", approved_at=now,
                         created_by="cli", created_at=now, updated_at=now)
        session.add(row)
        session.commit()
        items = [(storage.public_url(session.get(storage.ContentAsset, s["asset_id"])), s["alt_text"]) for s in stored]
        try:
            ig.verify_hosted(graph.client, items[0][0])
            result = ig.publish_hosted(built.kind, items, built.caption, graph, acct, log=_echo)
        except ig.InstagramError as e:
            row.status, row.error, row.updated_at = "failed", str(e), datetime.now(UTC)
            session.commit()
            _err(str(e))
            raise typer.Exit(1) from None
        row.status, row.published_at = "published", datetime.now(UTC)
        row.ig_media_id, row.permalink, row.updated_at = result["id"], result["permalink"], datetime.now(UTC)
        session.commit()
    _echo(f"Published: {result['permalink'] or result['id']}")


@app.command()
def queue(
    slides: list[Path] = typer.Argument(..., help="Image files in slide order (PNG, JPEG or WebP)."),
    title: str = typer.Option(..., "--title", help="Internal label shown in the queue."),
    caption: Path | None = typer.Option(None, "--caption", help="Caption file, same format as `post`."),
    story: bool = typer.Option(False, "--story", help="Queue the one image as a Story."),
    source_ref: str | None = typer.Option(None, "--source-ref", help='Groups a post and its Story, e.g. "intro".'),
    created_by: str = typer.Option("cli", "--created-by"),
) -> None:
    """File a draft in the queue with a recommended time. Approve it in the web UI."""
    from postdesk import queue as q
    from postdesk.config import config, settings
    from postdesk.db.session import SessionLocal

    built = _build(slides, caption, story)
    with SessionLocal() as session:
        stored = _store_slides(session, built, by=created_by)
        try:
            row = q.create_post(session, kind=built.kind, title=title, caption=built.caption, slides=stored,
                                source_ref=source_ref, created_by=created_by)
        except q.QueueError as e:
            _err(str(e))
            raise typer.Exit(1) from None
    when = row.publish_at.astimezone(config.account.tz)
    _echo(f"\nQueued as a draft: {row.id}")
    _echo(f"Recommended time: {when:%a %d %b %H:%M} ({config.account.timezone})")
    _echo(f"Approve it at {settings.public_base_url.rstrip('/')}/posts/{row.id}, keeping or changing that time.")


@app.command("suggest-times")
def suggest_times(overwrite: bool = typer.Option(False, "--overwrite", help="Recompute every draft's time.")) -> None:
    """Give drafts a recommended publish time (those without one, or all with --overwrite)."""
    from postdesk import queue as q
    from postdesk.config import config
    from postdesk.db.session import SessionLocal

    with SessionLocal() as session:
        posts = q.suggest_times(session, overwrite=overwrite)
        if not posts:
            _echo("Every draft already has a time (use --overwrite to recompute).")
        for p in sorted(posts, key=lambda p: p.publish_at):
            _echo(f"  {p.publish_at.astimezone(config.account.tz):%Y-%m-%d %H:%M}  {p.kind:<8} {p.title}")


# --- making ------------------------------------------------------------------


@app.command()
def make(
    source: Path = typer.Argument(..., help="A Markdown file with front matter (docs/sources.md)."),
    template: str = typer.Option("", "--template", "-t",
                                 help="article, explainer, spotlight, stat, list, story-only, or one in ./templates."),
    plan: Path | None = typer.Option(None, "--plan", help="A one-off plan (a template YAML written for this post)."),
    guidance: str = typer.Option("", "--guidance", help="A line or two on the angle to take."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan and the brief; call nothing."),
    created_by: str = typer.Option("maker", "--created-by"),
) -> None:
    """Turn a Markdown file into a rendered carousel and its Story, as drafts in the queue."""
    from postdesk import llm, maker
    from postdesk.config import config, settings
    from postdesk.db.session import SessionLocal
    from postdesk.sources import SourceError
    from postdesk.sources.markdown import facts_from_file

    try:
        facts = facts_from_file(source)
        tpl = maker.load_template(plan if plan else (template or facts.template or "article"))
        kind, slots, story = maker.plan(tpl, facts)
    except (SourceError, maker.TemplateError) as e:
        _err(str(e))
        raise typer.Exit(1) from None
    _echo(f"{facts.title}\n  template {tpl.name}: {kind}, {len(slots)} {'frame' if kind == 'story' else 'slide'}(s)"
          f" ({', '.join(s.type for s in slots)}){' + story' if story else ''}")
    if dry_run:
        hero = (facts.hero.url or facts.hero.path) if facts.hero else "none"
        _echo(f"  figures: {facts.figures or 'none named'}; hero: {hero}")
        _echo("\n" + maker.words.brief(facts, kind, slots, story, guidance or None))
        _echo("\nDry run: nothing was called, stored or queued.")
        return
    with SessionLocal() as session:
        try:
            posts = maker.make(session, facts, tpl, guidance=guidance or None, created_by=created_by, log=_echo)
        except (maker.MakerError, maker.RenderError, llm.LLMError, SourceError) as e:
            _err(str(e))
            raise typer.Exit(1) from None
    _echo("\nQueued as drafts:")
    for p in posts:
        when = p.publish_at.astimezone(config.account.tz) if p.publish_at else None
        _echo(f"  {p.kind:<9} {p.title}  {when:%a %d %b %H:%M}" if when else f"  {p.kind:<9} {p.title}")
        _echo(f"            {settings.public_base_url.rstrip('/')}/posts/{p.id}")


@app.command()
def render(
    spec: Path = typer.Option(..., "--spec", help="A slide spec, JSON (examples/slide.json)."),
    out: Path = typer.Option(..., "--out", help="Where to write the JPEG."),
    theme: str = typer.Option("", "--theme", help="A theme the renderer has; default [theme].name."),
) -> None:
    """Render one slide spec to a JPEG through the renderer, to try a theme or a layout."""
    import json

    from postdesk import maker

    try:
        data = maker.Renderer(theme=theme or None).render(json.loads(spec.read_text(encoding="utf-8")))
    except (maker.RenderError, ValueError) as e:
        _err(str(e))
        raise typer.Exit(1) from None
    out.write_bytes(data)
    _echo(f"wrote {out} ({len(data) // 1024} KB)")


@app.command()
def templates() -> None:
    """List the post templates on the search path (./templates, POSTDESK_TEMPLATES, built in)."""
    from postdesk.maker import plans

    for name, path in sorted(plans.available().items()):
        try:
            t = plans.load_template(path)
            _echo(f"{name:<12} {t.kind:<9} {t.description}")
        except plans.TemplateError as e:
            _echo(f"{name:<12} broken: {e}")


# --- telegram ----------------------------------------------------------------


@telegram_app.command("link")
def telegram_link(code: str = typer.Argument(..., help="The code the bot sent you after /start.")) -> None:
    """Pair the chat that sent /start to the bot. From then on the bot talks to that chat only."""
    from postdesk.db.session import SessionLocal
    from postdesk.telegram import pairing

    with SessionLocal() as session:
        try:
            linked = pairing.link(session, code, by="cli")
        except pairing.PairingError as e:
            _err(str(e))
            raise typer.Exit(1) from None
    _echo(f"Paired with {linked['title'] or linked['chat_id']}. The bot talks to that chat from now on.")
    _telegram_send(linked["chat_id"], "Paired. This chat runs postdesk from now on. /help lists what I do.")


def _telegram_send(chat_id: str, text: str) -> bool:
    from postdesk.config import settings
    from postdesk.telegram.api import Bot, TelegramError

    if not settings.telegram_bot_token:
        _err("TELEGRAM_BOT_TOKEN is not set in .env, so nothing was sent")
        return False
    try:
        Bot().send_message(chat_id, text)
    except TelegramError as e:
        _err(str(e))
        return False
    return True


@telegram_app.command("unlink")
def telegram_unlink() -> None:
    """Unpair the chat. The bot answers no one until a chat is paired again."""
    from postdesk.db.session import SessionLocal
    from postdesk.telegram import pairing

    with SessionLocal() as session:
        was = pairing.unlink(session)
    _echo("Unpaired." if was else "No chat was paired.")


@telegram_app.command("test")
def telegram_test() -> None:
    """Send a test message to the paired chat."""
    from postdesk.db.session import SessionLocal
    from postdesk.telegram import pairing

    with SessionLocal() as session:
        linked = pairing.linked(session)
    if linked is None:
        _err("no chat is paired yet: send /start to the bot, then `postdesk telegram link <code>`")
        raise typer.Exit(1)
    if not _telegram_send(linked["chat_id"], "A test from postdesk. This is the chat it talks to."):
        raise typer.Exit(1)
    _echo(f"Sent to {linked['title'] or linked['chat_id']}.")


@telegram_app.command("status")
def telegram_status() -> None:
    """Which bot, which chat, and how updates arrive (polling or webhook)."""
    from postdesk.config import settings
    from postdesk.db.session import SessionLocal
    from postdesk.telegram import bot, pairing
    from postdesk.telegram.api import Bot, TelegramError

    if not settings.telegram_bot_token:
        _err("TELEGRAM_BOT_TOKEN is not set in .env (from @BotFather: /newbot)")
        raise typer.Exit(1)
    try:
        me = Bot().get_me()
        _echo(f"bot:   @{me.get('username')}")
    except TelegramError as e:
        _err(str(e))
        raise typer.Exit(1) from None
    with SessionLocal() as session:
        linked = pairing.linked(session)
    _echo(f"chat:  {linked['title'] or linked['chat_id']} (paired {linked['linked_at'][:10]})" if linked
          else "chat:  none paired; send /start to the bot")
    _echo(f"mode:  {bot.mode()}" + (f" at {bot.webhook_url()}" if bot.mode() == "webhook" else ""))


# --- permissions -------------------------------------------------------------


@permission_app.command("add")
def permission_add(
    holder: str = typer.Option(..., "--holder", help="Who said yes: a person, a shop, a photographer."),
    credit: str = typer.Option(..., "--credit", help='What goes on the image, e.g. "Photo: Jo Baker".'),
    scope: str = typer.Option(..., "--scope", help="What they agreed to, in their words."),
    evidence: str = typer.Option(..., "--evidence", help="Where the yes is: an email, a DM, a contract."),
    instagram: str | None = typer.Option(None, "--instagram", help="Their Instagram handle."),
    domain: str | None = typer.Option(None, "--domain", help="Their web domain."),
    subject: str | None = typer.Option(None, "--subject", help="Only images of this subject (default: all theirs)."),
    tag: str | None = typer.Option(None, "--tag", help='The caption\'s credit, e.g. "@jobakes".'),
    kind: str = typer.Option("person", "--kind", help="person, organisation, photographer or other."),
    expires: str | None = typer.Option(None, "--expires", help="YYYY-MM-DD, if it ends."),
) -> None:
    """Record a rights holder's yes, so their images carry a permission, not just a credit."""
    from datetime import date

    from postdesk.db.session import SessionLocal
    from postdesk.images import rights

    with SessionLocal() as session:
        try:
            p = rights.add(session, holder=holder, holder_kind=kind, scope=scope, credit=credit, evidence=evidence,
                           recorded_by="cli", instagram=instagram, domain=domain, subject=subject, tag=tag,
                           expires_on=date.fromisoformat(expires) if expires else None)
        except ValueError as e:
            _err(str(e))
            raise typer.Exit(1) from None
    _echo(f"Recorded: {p.id}")


@permission_app.command("list")
def permission_list(revoked: bool = typer.Option(False, "--revoked", help="Include revoked ones.")) -> None:
    """The permissions on file."""
    from postdesk.db.session import SessionLocal
    from postdesk.images import rights

    with SessionLocal() as session:
        rows = rights.listed(session, include_revoked=revoked)
        if not rows:
            _echo("No permissions on file.")
        for p in rows:
            who = " ".join(x for x in (f"@{p.instagram}" if p.instagram else "", p.domain or "") if x)
            state = "revoked" if p.revoked_at else ("active" if rights.active(p) else "expired")
            _echo(f"{p.id}  {p.holder} ({who}){' for ' + p.subject if p.subject else ''}: {p.credit} [{state}]")


@permission_app.command("revoke")
def permission_revoke(permission_id: str = typer.Argument(...)) -> None:
    """Revoke a permission; images found afterwards are credit only again."""
    from postdesk.db.session import SessionLocal
    from postdesk.images import rights

    with SessionLocal() as session:
        try:
            rights.revoke(session, permission_id)
        except (LookupError, ValueError) as e:
            _err(str(e))
            raise typer.Exit(1) from None
    _echo("Revoked.")


# --- db ----------------------------------------------------------------------


@db_app.command()
def upgrade(revision: str = typer.Argument("head")) -> None:
    """Bring the database schema up to date (runs on container start)."""
    from postdesk.config import settings
    from postdesk.db import migrate

    migrate.upgrade(settings.database_url, revision)
    _echo(f"database at {revision}")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
