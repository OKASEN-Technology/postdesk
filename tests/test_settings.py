"""
The settings screen's API: edits kept in the `setting` table and laid over
postdesk.toml in place (and taken back), what may not be edited there, the
agents' pause, and the status the header shows.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from postdesk import config as config_mod
from postdesk import control, store
from postdesk.config import config
from postdesk.db.models import AgentRun
from postdesk.main import create_app


@pytest.fixture
def http(session):
    with TestClient(create_app(), base_url="https://postdesk.example") as client:
        client.post("/api/auth/setup", json={"password": "correct horse battery"})
        yield client


def test_settings_show_the_values_and_the_choices(http):
    d = http.get("/api/settings").json()
    assert d["values"]["account"]["handle"] == "bakery_tales"
    assert d["values"]["llm"]["monthly_usd"] == 30
    assert d["values"]["llm"]["partner"]["model"] == "claude-opus-5-5"
    assert d["values"]["voice"]["from_file"] is True
    assert "wikimedia" in d["choices"]["image_sources"] and "claude-haiku-5-5" in d["choices"]["models"]
    assert d["telegram"] == {"configured": False, "linked": None, "mode": None}
    assert d["spend"] == {"month_usd": 0, "ceiling_usd": 30, "over": False}
    assert d["overridden"] == {}
    assert [n["name"] for n in d["notifiers"]] == ["slack", "webhook", "email"]


def test_an_edit_applies_at_once_and_null_goes_back_to_the_file(http, session):
    d = http.put("/api/settings", json={"schedule": {"feed_slot": "19:30"}, "llm": {"monthly_usd": 12},
                                        "voice": {"text": "Warm, plain, a little dry."},
                                        "images": {"sources": ["page", "attachment"]}}).json()
    assert config.schedule.feed_slot == "19:30" and config.llm.monthly_usd == 12
    assert config.images.sources == ["page", "attachment"]
    assert d["values"]["voice"] == {**d["values"]["voice"], "text": "Warm, plain, a little dry.", "from_file": False}
    assert d["overridden"] == {"images": ["sources"], "llm": ["monthly_usd"], "schedule": ["feed_slot"],
                               "voice": ["text"]}
    assert store.overrides(session)["schedule"] == {"feed_slot": "19:30"}

    from postdesk.maker.words import voice_text

    assert voice_text() == "Warm, plain, a little dry."
    http.put("/api/settings", json={"schedule": {"feed_slot": None}, "voice": None})
    assert config.schedule.feed_slot == "18:00"  # the file's (here, the defaults')
    assert config.voice.text is None and config.llm.monthly_usd == 12
    assert "schedule" not in store.overrides(session) and "voice" not in store.overrides(session)


def test_another_process_picks_the_edit_up(session):
    store.put(session, store.OVERRIDES, {"telegram": {"digest_at": "07:15"}})
    assert config.telegram.digest_at == "08:00"
    store.refresh_config(session)
    assert config.telegram.digest_at == "07:15" and config_mod.applied_overrides() == \
        {"telegram": {"digest_at": "07:15"}}
    store.delete(session, store.OVERRIDES)
    store.refresh_config(session)
    assert config.telegram.digest_at == "08:00"


@pytest.mark.parametrize("body,msg", [
    ({"account": {"instagram_user_id": "1"}}, "not editable here"),
    ({"partner": {"window_days": 3}}, "not editable here"),
    ({"schedule": {"feed_slot": "25:00"}}, "feed_slot"),
    ({"llm": {"monthly_usd": -1}}, "monthly_usd"),
    ({"telegram": {"digest_at": "noon"}}, "digest_at"),
    ({"schedule": "18:00"}, "must be a table"),
])
def test_a_bad_edit_is_refused_and_changes_nothing(http, session, body, msg):
    r = http.put("/api/settings", json=body)
    assert r.status_code == 400 and msg in r.json()["detail"]
    assert store.overrides(session) == {} and config.schedule.feed_slot == "18:00"


def test_pause_resume_and_status(http, session):
    s = http.get("/api/status").json()
    assert s["paused"] is None and s["stopped"] is None and s["telegram"] == "off"
    s = http.post("/api/agents/pause").json()
    assert s["paused"]["by"] == "admin" and s["stopped"].startswith("paused by admin")
    assert http.post("/api/agents/resume").json()["paused"] is None
    session.add(AgentRun(agent="partner", model="claude-opus-5-5", cost_usd=31, status="ok",
                         started_at=datetime.now(UTC)))
    session.commit()
    s = http.get("/api/status").json()
    assert s["over"] and "ceiling" in s["stopped"] and s["month_usd"] == 31
    http.put("/api/settings", json={"llm": {"monthly_usd": 50}})
    assert http.get("/api/status").json()["stopped"] is None
    assert control.paused(session) is None


def test_settings_need_the_admin(session):
    with TestClient(create_app(), base_url="https://postdesk.example") as anon:
        assert anon.get("/api/settings").status_code == 401
        assert anon.put("/api/settings", json={"llm": {"monthly_usd": 0}}).status_code == 401
        assert anon.post("/api/agents/pause").status_code == 401
        assert anon.post("/api/telegram/link", json={"code": "ABCDEF"}).status_code == 401
