"""
The suite runs against a throwaway Postgres: `POSTDESK_TEST_DATABASE_URL`,
default `postgresql+psycopg://postgres:postgres@localhost:5432/postdesk_test`
(what CI's `services:` block provides; locally, `docker run -e
POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16`). The migrations are
applied from scratch at the start, so they are tested too, and every table
is emptied after each test. Without a reachable database the tests that
need one are skipped, unless `CI` is set, in which case they fail.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from postdesk.config import config, settings
from postdesk.db import migrate
from postdesk.db import session as db_session

TEST_DB = os.environ.get("POSTDESK_TEST_DATABASE_URL",
                         "postgresql+psycopg://postgres:postgres@localhost:5432/postdesk_test")
TABLES = ("social_post", "content_asset", "metric", "setting", "image_permission", "agent_run")


def pytest_configure(config):
    config.addinivalue_line("markers", "db: needs the test database")


@pytest.fixture(scope="session")
def database():
    settings.database_url = TEST_DB
    db_session.reset_engine()
    try:
        with db_session.engine().connect() as conn:
            conn.execute(text("select 1"))
    except Exception as e:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"no test database at {TEST_DB} ({type(e).__name__}); set POSTDESK_TEST_DATABASE_URL")
    with db_session.engine().begin() as conn:
        conn.execute(text("drop schema public cascade; create schema public"))
    migrate.upgrade(TEST_DB)
    yield TEST_DB
    db_session.reset_engine()


@pytest.fixture
def session(database, tmp_path):
    s = db_session.SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
        with db_session.engine().begin() as conn:
            conn.execute(text("truncate " + ", ".join(TABLES)))


@pytest.fixture(autouse=True)
def cfg(monkeypatch, tmp_path: Path):
    """A known configuration: London time, the example slots, local storage
    in a temp dir, a public address Instagram could reach."""
    monkeypatch.setattr(config.account, "timezone", "Europe/London")
    monkeypatch.setattr(config.schedule, "feed_slot", "18:00")
    monkeypatch.setattr(config.schedule, "story_slot", "18:15")
    monkeypatch.setattr(config.schedule, "max_feed_per_day", 1)
    monkeypatch.setattr(config.schedule, "max_story_per_day", 1)
    monkeypatch.setattr(config.schedule, "min_lead_hours", 2)
    monkeypatch.setattr(config.schedule, "max_per_run", 3)
    monkeypatch.setattr(config.schedule, "publisher_hours", ["06:00", "21:00"])
    monkeypatch.setattr(config.voice, "max_hashtags", 5)
    monkeypatch.setattr(config.voice, "retired_phrases", ["game-changer"])
    monkeypatch.setattr(settings, "public_base_url", "https://postdesk.example")
    monkeypatch.setattr(settings, "postdesk_data_dir", tmp_path / "data")
    for k in ("storage_s3_endpoint", "storage_s3_bucket", "storage_s3_access_key_id",
              "storage_s3_secret_access_key", "storage_s3_public_base_url", "postdesk_admin_password",
              "postdesk_api_token", "postdesk_secret_key", "anthropic_api_key"):
        monkeypatch.setattr(settings, k, None)
    monkeypatch.setattr(settings, "instagram_access_token", "USER")
    monkeypatch.setattr(settings, "instagram_page_token", None)
    monkeypatch.setattr(settings, "instagram_user_id", None)
    monkeypatch.setattr(settings, "meta_app_id", None)
    monkeypatch.setattr(settings, "meta_app_secret", None)
    from postdesk.instagram import publish

    monkeypatch.setattr(publish, "POLL_INTERVAL_S", 0)


class FakeGraph:
    """Enough of the Graph API for one publish: the Page lookup, media
    containers, status polls, publish, permalink."""

    def __init__(self, fail_publish=False, username="bakery_tales"):
        self.fail_publish = fail_publish
        self.username = username
        self.created: list[dict] = []
        self.calls: list[tuple[str, str, dict]] = []
        self.polls: dict[str, int] = {}
        self.n = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        path = request.url.path.split("/", 2)[2]  # drop /vNN.N/
        self.calls.append((request.method, path, params))
        if path == "me/accounts":
            assert params["access_token"] == "USER"
            return httpx.Response(200, json={"data": [
                {"id": "P1", "name": "Some other page", "access_token": "X"},
                {"id": "P2", "name": "The Bakery", "access_token": "PAGE",
                 "instagram_business_account": {"id": "IG1", "username": self.username}},
            ]})
        assert params.get("access_token") == "PAGE", path
        if path == "IG1/media":
            self.created.append(params)
            self.n += 1
            return httpx.Response(200, json={"id": f"C{self.n}"})
        if path == "IG1/media_publish":
            if self.fail_publish:
                return httpx.Response(400, json={"error": {"message": "Media upload has failed."}})
            return httpx.Response(200, json={"id": "M1"})
        if path == "IG1/content_publishing_limit":
            return httpx.Response(200, json={"data": [{"quota_usage": 2, "config": {"quota_total": 100}}]})
        if path == "M1":
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/p/xyz/"})
        if path == "IG1":
            return httpx.Response(200, json={"followers_count": 120, "media_count": 9})
        if path == "IG1/insights":
            return httpx.Response(200, json={"data": [{"name": params["metric"], "total_value": {"value": 300}}]})
        if path.endswith("/insights"):
            return httpx.Response(200, json={"data": [{"name": m, "values": [{"value": 7}]}
                                                      for m in params["metric"].split(",")]})
        # container status: IN_PROGRESS once, then FINISHED
        self.polls[path] = self.polls.get(path, 0) + 1
        return httpx.Response(200, json={"status_code": "FINISHED" if self.polls[path] > 1 else "IN_PROGRESS"})


@pytest.fixture
def fake_graph():
    return FakeGraph()


def graph_for(fake):
    from postdesk import instagram as ig

    return ig.Graph(httpx.Client(transport=httpx.MockTransport(fake)))
