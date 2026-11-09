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
app.add_typer(instagram_app, name="instagram")
app.add_typer(db_app, name="db")


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
        tz = typer.prompt("Your timezone (IANA name)", default="Europe/London")
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
    """Check the Claude key, database, migrations, Instagram token and public storage, in order."""
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
) -> None:
    """Run the API, the web UI and the scheduler loop."""
    import uvicorn

    from postdesk.main import create_app

    uvicorn.run(create_app(scheduler=scheduler), host=host, port=port, log_level="info")


@app.command()
def worker() -> None:
    """The job runner for the agents (arrives with the board, M2)."""
    _echo("worker: not built yet (docs/roadmap.md, M2). The scheduler runs inside `serve` and `cron`.")
    raise typer.Exit(1)


@app.command()
def cron(force: bool = typer.Option(False, "--force", help="Publish even outside publisher hours.")) -> None:
    """One pass: publish what is due, then collect insights. For a system cron or a hosted cron job."""
    from postdesk import scheduler

    result = scheduler.run_once(force=force)
    if result.published or result.failed:
        _echo(f"published {len(result.published)}, failed {len(result.failed)}")
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
