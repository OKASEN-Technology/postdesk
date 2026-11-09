"""The `postdesk` command, through Typer's runner. The database-backed
commands (queue, suggest-times, cron) run against the test database."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from PIL import Image
from typer.testing import CliRunner

from postdesk import __version__
from postdesk.cli import app
from tests.conftest import FakeGraph

runner = CliRunner()


def _png(tmp_path: Path, name: str, size=(1080, 1350)) -> Path:
    p = tmp_path / name
    Image.new("RGB", size, (90, 120, (sum(map(ord, name)) * 7) % 255)).save(p)
    return p


def _caption(tmp_path: Path) -> Path:
    p = tmp_path / "caption.txt"
    p.write_text("Three loaves.\n\n#homebaking\n\n---\nALT TEXT\n1: Cover.\n2: Crumb.\n", encoding="utf-8")
    return p


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0 and result.output.strip() == __version__


def test_worker_is_a_stub():
    result = runner.invoke(app, ["worker"])
    assert result.exit_code == 1 and "not built yet" in result.output


def test_post_dry_run_validates_and_posts_nothing(tmp_path, monkeypatch):
    from postdesk import cli

    fake = FakeGraph()
    monkeypatch.setattr(cli, "_graph", lambda: _graph(fake))
    slides = [str(_png(tmp_path, "01.png")), str(_png(tmp_path, "02.png"))]
    result = runner.invoke(app, ["post", *slides, "--caption", str(_caption(tmp_path))])
    assert result.exit_code == 0, result.output
    assert "carousel: 2 image(s)" in result.output and "Dry run" in result.output
    assert "@bakery_tales, 2 of 100" in result.output
    assert not any(path == "IG1/media" for _, path, _ in fake.calls)

    tall = str(_png(tmp_path, "tall.png", (1080, 1920)))
    bad = runner.invoke(app, ["post", tall, "--caption", str(_caption(tmp_path))])
    assert bad.exit_code == 1 and "outside the feed" in bad.output


def _graph(fake):
    from postdesk import instagram as ig

    return ig.Graph(httpx.Client(transport=httpx.MockTransport(fake)))


def test_queue_then_suggest_then_cron_publishes(tmp_path, session, monkeypatch):
    from postdesk import queue, scheduler
    from postdesk.db.models import SocialPost

    slides = [str(_png(tmp_path, "01.png")), str(_png(tmp_path, "02.png"))]
    result = runner.invoke(app, ["queue", *slides, "--caption", str(_caption(tmp_path)), "--title", "Loaves",
                                 "--source-ref", "loaves"])
    assert result.exit_code == 0, result.output
    assert "Queued as a draft" in result.output and "Recommended time" in result.output
    post = session.query(SocialPost).one()
    assert post.status == "draft" and post.kind == "carousel" and post.title == "Loaves"
    assert [s["alt_text"] for s in post.slides] == ["Cover.", "Crumb."]
    stored = (tmp_path / "data" / "uploads")
    assert sum(1 for _ in stored.rglob("*") if _.is_file()) == 2

    tall = str(_png(tmp_path, "s.png", (1080, 1920)))
    story = runner.invoke(app, ["queue", tall, "--story", "--title", "Loaves story", "--source-ref", "loaves"])
    assert story.exit_code == 0, story.output
    posts = {p.title: p for p in session.query(SocialPost)}
    assert posts["Loaves story"].publish_at == posts["Loaves"].publish_at + timedelta(minutes=15)

    result = runner.invoke(app, ["suggest-times"])
    assert result.exit_code == 0 and "Every draft already has a time" in result.output
    result = runner.invoke(app, ["suggest-times", "--overwrite"])
    assert result.exit_code == 0 and "Loaves" in result.output

    # Approve it for a time that has passed, as a person would in the UI, then run the cron.
    queue.approve(session, post.id, publish_at=datetime.now(UTC) - timedelta(minutes=1), approved_by="admin")
    fake = FakeGraph()
    logs = []
    r = scheduler.run_once(force=True, log=logs.append, graph=_graph(fake))
    assert r.published == [str(post.id)] and r.failed == []
    session.refresh(post)
    assert post.status == "published" and post.permalink == "https://www.instagram.com/p/xyz/"
    items = [c for c in fake.created if c.get("is_carousel_item") == "true"]
    assert items[0]["image_url"] == f"https://postdesk.example/media/{post.slides[0]['asset_id']}"

    result = runner.invoke(app, ["cron", "--force"])
    assert result.exit_code == 0, result.output  # nothing left due


def test_cron_waits_for_publisher_hours(session, monkeypatch):
    from postdesk import queue, scheduler
    from postdesk.queue import times

    monkeypatch.setattr(times, "now_utc", lambda: datetime(2031, 3, 3, 23, 0, tzinfo=UTC))
    logs = []
    r = scheduler.run_once(log=logs.append, graph=_graph(FakeGraph()))
    assert r.skipped_hours and r.ok
    assert not queue.due(session)


def test_db_upgrade_is_idempotent(database):
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 0 and "database at head" in result.output
