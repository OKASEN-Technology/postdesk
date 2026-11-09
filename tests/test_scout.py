"""
The scout and the `rss` / `url` sources: a feed's new items become cards
in Planned (at most `[scout].max_per_run` a run, the rest next time), each
item once, with the page's text as the facts and the partner queued. The
feed and its pages are served by httpx.MockTransport.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from postdesk import queue
from postdesk.board import cards, scout
from postdesk.config import config
from postdesk.db.models import Card, Job
from postdesk.sources import SourceError, web
from postdesk.sources.rss import RssSource
from postdesk.sources.url import UrlSource
from tests import boardkit as kit


@pytest.fixture
def feed(monkeypatch):
    monkeypatch.setattr(config, "sources", {"bakes": {"type": "rss", "url": kit.FEED_URL}})
    monkeypatch.setattr(config.scout, "max_per_run", 2)


def _quiet(_m):
    pass


def test_the_rss_source_reads_items_and_their_pages():
    items = kit.feed_items(2)
    src = RssSource(kit.FEED_URL, client=kit.feed_client(items, down={"loaf-2"}))
    found = src.candidates(datetime.now(UTC) - timedelta(days=1))
    assert [c.ref for c in found] == ["rss:https://bakes.example/loaf-1", "rss:https://bakes.example/loaf-2"]
    assert src.domains() == ["bakes.example"]

    f = src.facts(found[0].ref)
    assert f.title == "Loaf number 1: a long rest" and f.url == "https://bakes.example/loaf-1"
    assert "72 hours at 78% hydration" in f.body_md and "401 grams" in f.body_md
    # The navigation, the footer and the script are gone; the page's own text stays (it is data, not orders).
    assert "Shop" not in f.body_md and "12345" not in f.body_md and "Copyright" not in f.body_md
    assert f.hero.url == "https://bakes.example/img/loaf-1.jpg" and f.hero.credit == "Image: Bakes"
    assert f.hero.licence is None and f.citations == [{"label": "Bakes", "url": "https://bakes.example/loaf-1"}]

    # A page that will not load falls back to the feed's own text.
    f2 = src.facts(found[1].ref)
    assert f2.body_md == "The 2th bake of the month."
    with pytest.raises(SourceError, match="no longer in"):
        src.facts("rss:gone")


def test_a_broken_feed_or_a_bad_address_is_a_source_error():
    with pytest.raises(SourceError, match="not an http"):
        web.fetch("file:///etc/passwd")
    with pytest.raises(SourceError, match="HTTP 404"):
        RssSource("https://bakes.example/nope.xml", client=kit.feed_client([])).candidates(datetime.now(UTC))
    with pytest.raises(SourceError, match="needs a url"):
        RssSource("")


def test_the_url_source_reads_one_page():
    items = kit.feed_items(1)
    f = UrlSource(client=kit.feed_client(items)).facts("url:https://bakes.example/loaf-1")
    assert f.title.startswith("Loaf number 1") and "78% hydration" in f.body_md and f.ref.startswith("url:")


def test_the_scout_files_new_items_a_few_at_a_time_and_each_once(session, feed):
    items = kit.feed_items(3)
    client = kit.feed_client(items)
    r = scout.run(session, client=client, log=_quiet)
    assert (len(r.filed), r.seen, r.deferred, r.errors) == (2, 3, 1, [])
    filed = session.scalars(select(Card).order_by(Card.created_at)).all()
    assert [c.title for c in filed] == ["Loaf number 1: a long rest", "Loaf number 2: a long rest"]
    c = filed[0]
    assert c.column == "Planned" and c.created_by == "scout" and c.source_ref == "rss:https://bakes.example/loaf-1"
    assert c.fingerprint == "post:bakes:rss:https://bakes.example/loaf-1"
    assert c.plan == {"template": "article", "source": "bakes", "domains": ["bakes.example"]}
    assert c.facts["hero"]["url"].endswith("loaf-1.jpg") and "401 grams" in c.facts["body_md"]
    assert c.body_md.startswith("From **bakes**: [Loaf number 1")
    queued = session.scalars(select(Job).where(Job.status == "queued")).all()
    assert sorted((j.kind, str(j.card_id)) for j in queued) == sorted(("partner", str(x.id)) for x in filed)

    # The next run files the one left over, then nothing.
    r = scout.run(session, client=kit.feed_client(items), log=_quiet)
    assert len(r.filed) == 1 and r.deferred == 0
    r = scout.run(session, client=kit.feed_client(items), log=_quiet)
    assert r.filed == [] and r.seen == 3
    assert len(session.scalars(select(Card)).all()) == 3


def test_the_scout_skips_an_item_already_in_the_queue_and_old_items(session, feed):
    from postdesk import storage
    from tests.test_api import _jpeg

    items = kit.feed_items(2)
    items[0]["when"] = datetime.now(UTC) - timedelta(days=30)
    asset = storage.store_asset(session, data=_jpeg(), filename="a.jpg", content_type="image/jpeg",
                                provenance={"source": "upload"})
    queue.create_post(session, kind="image", title="Made already", caption="Hi.",
                      slides=[{"asset_id": str(asset.id), "alt_text": "A"}],
                      source_ref="rss:https://bakes.example/loaf-2")
    r = scout.run(session, client=kit.feed_client(items), log=_quiet)
    assert r.filed == [] and r.seen == 1
    r = scout.run(session, client=kit.feed_client(items), days=60, log=_quiet)
    assert len(r.filed) == 1 and cards.by_fingerprint(session, "post:bakes:rss:https://bakes.example/loaf-1")


def test_one_broken_source_does_not_stop_the_others(session, monkeypatch):
    monkeypatch.setattr(config, "sources", {"broken": {"type": "rss", "url": "https://down.example/feed.xml"},
                                            "bakes": {"type": "rss", "url": kit.FEED_URL}})
    r = scout.run(session, client=kit.feed_client(kit.feed_items(1)), log=_quiet)
    assert len(r.filed) == 1 and r.errors and r.errors[0].startswith("broken:")
    with pytest.raises(SourceError, match="no source 'nope'"):
        scout.run(session, only="nope", log=_quiet)


def test_the_scout_runs_every_few_hours_inside_its_window(session, feed):
    london_morning = datetime(2031, 3, 3, 9, 0, tzinfo=UTC)
    assert scout.due(session, london_morning)
    assert not scout.due(session, datetime(2031, 3, 3, 22, 30, tzinfo=UTC))
    assert not scout.due(session, datetime(2031, 3, 3, 5, 0, tzinfo=UTC))
    scout.run(session, client=kit.feed_client([]), now=london_morning, log=_quiet)
    assert not scout.due(session, london_morning + timedelta(hours=1))
    assert scout.due(session, london_morning + timedelta(hours=2))
    assert scout.run_if_due(session, client=kit.feed_client([]), now=london_morning + timedelta(hours=1),
                            log=_quiet) is None


def test_the_scan_endpoint_runs_one_source(session, feed, monkeypatch):
    from fastapi.testclient import TestClient

    from postdesk.main import create_app

    client = kit.feed_client(kit.feed_items(1))
    real = scout.run
    monkeypatch.setattr(scout, "run", lambda s, **kw: real(s, client=client, **kw))
    with TestClient(create_app(), base_url="https://postdesk.example") as c:
        assert c.post("/api/auth/setup", json={"password": "correct horse battery"}).status_code == 200
        r = c.post("/api/sources/bakes/scan")
        assert r.status_code == 200, r.text
        assert len(r.json()["filed"]) == 1
        assert c.get("/api/sources").json() == [{"key": "bakes", "type": "rss", "options": {"url": kit.FEED_URL}}]
