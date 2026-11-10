"""Settings and postdesk.toml, and what `postdesk init` writes."""

from pathlib import Path

import pytest

from postdesk import init
from postdesk.config import Config, load_config

ROOT = Path(__file__).resolve().parents[1]


def test_example_toml_loads_and_has_every_schedule_key():
    cfg = load_config(ROOT / "examples" / "postdesk.toml")
    assert cfg.path == (ROOT / "examples" / "postdesk.toml").resolve()
    assert cfg.account.timezone == "America/New_York" and cfg.account.tz.key == "America/New_York"
    assert cfg.schedule.feed_time.hour == 18 and cfg.schedule.story_time.minute == 15
    assert cfg.schedule.max_per_run == 3 and cfg.schedule.story_frame_gap.total_seconds() == 120
    assert cfg.schedule.publisher_window[1].hour == 21
    assert cfg.voice.max_hashtags == 5 and "game-changer" in cfg.voice.retired_phrases


def test_missing_file_gives_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.path is None and cfg.account.timezone == "America/New_York"


def test_bad_values_are_reported():
    with pytest.raises(ValueError, match="IANA timezone"):
        Config.model_validate({"account": {"timezone": "Mars/Olympus"}})
    with pytest.raises(ValueError, match="time like"):
        Config.model_validate({"schedule": {"feed_slot": "6pm"}})
    with pytest.raises(ValueError, match="start before"):
        Config.model_validate({"schedule": {"publisher_hours": ["21:00", "06:00"]}})


def test_init_renders_env_and_toml_from_the_examples(monkeypatch):
    monkeypatch.chdir(ROOT)
    env = init.render_env({"POSTDESK_ADMIN_PASSWORD": "hunter22hunter", "PUBLIC_BASE_URL": "https://ig.example.com"})
    assert "POSTDESK_ADMIN_PASSWORD=hunter22hunter\n" in env
    assert "PUBLIC_BASE_URL=https://ig.example.com\n" in env
    assert "ANTHROPIC_API_KEY=\n" in env  # left for later
    secret = next(line for line in env.splitlines() if line.startswith("POSTDESK_SECRET_KEY="))
    assert len(secret) > len("POSTDESK_SECRET_KEY=") + 20

    toml = init.render_toml({"handle": "bakery_tales", "timezone": "America/New_York", "site": "https://b.example"})
    cfg = Config.model_validate(__import__("tomllib").loads(toml))
    assert cfg.account.handle == "bakery_tales" and cfg.account.timezone == "America/New_York"
    assert cfg.schedule.feed_slot == "18:00"  # the rest of the example survives
    assert "# your Instagram username" in toml  # and so do its comments
