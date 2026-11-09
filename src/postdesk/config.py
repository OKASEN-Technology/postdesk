"""
Two files configure postdesk (README "Make it yours"):

- `.env`: secrets and the few values that differ per machine. Read by
  `Settings` (pydantic-settings); every variable is listed in
  `.env.example`.
- `postdesk.toml`: everything else, checked in by the user if they like.
  Read by `load_config`; every key is listed in `examples/postdesk.toml`.

Both are module-level singletons (`settings`, `config`) so the rest of the
package reads them without passing them around; tests set attributes on
them. `reload()` re-reads both after `postdesk init` writes them.
"""

from __future__ import annotations

import os
import tomllib
from datetime import time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """`.env`. Names match the variables there, lower-cased."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str | None = None
    telegram_bot_token: str | None = None

    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    meta_graph_version: str = "v23.0"
    instagram_access_token: str | None = None
    instagram_page_token: str | None = None
    instagram_user_id: str | None = None

    database_url: str = "postgresql+psycopg://postdesk:postdesk@localhost:5432/postdesk"
    public_base_url: str = "http://localhost:8080"

    storage_s3_endpoint: str | None = None
    storage_s3_bucket: str | None = None
    storage_s3_access_key_id: str | None = None
    storage_s3_secret_access_key: str | None = None
    storage_s3_public_base_url: str | None = None

    renderer_url: str | None = None
    renderer_token: str | None = None

    postdesk_admin_password: str | None = None
    postdesk_secret_key: str | None = None
    # A bearer token for scripts and for `postdesk` run on another machine;
    # inside Docker the CLI talks to the database directly and needs none.
    postdesk_api_token: str | None = None
    # Where local storage keeps the slide files. Docker Compose mounts a volume.
    postdesk_data_dir: Path = Path("var")
    # Where postdesk.toml is. Default: the working directory.
    postdesk_config: Path = Path("postdesk.toml")

    slack_webhook_url: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        # `KEY=` in .env.example means "not set", not "the empty string".
        return None if isinstance(v, str) and v.strip() == "" else v

    def s3_enabled(self) -> bool:
        return all((self.storage_s3_endpoint, self.storage_s3_bucket, self.storage_s3_access_key_id,
                    self.storage_s3_secret_access_key, self.storage_s3_public_base_url))


def _parse_hhmm(value: str) -> time:
    try:
        h, m = value.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError) as e:
        raise ValueError(f"{value!r} is not a time like 18:00") from e


class Account(BaseModel):
    handle: str = ""
    site: str = ""
    timezone: str = "UTC"
    audience: str = ""

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"{v!r} is not an IANA timezone like Europe/London") from e
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class Schedule(BaseModel):
    """When posts are recommended to go out, in the account's timezone
    (queue/times.py)."""

    feed_slot: str = "18:00"
    story_slot: str = "18:15"
    max_feed_per_day: int = Field(1, ge=1)
    max_story_per_day: int = Field(1, ge=1)
    min_lead_hours: float = Field(2, ge=0)
    story_frame_gap_minutes: float = Field(2, ge=0)
    max_per_run: int = Field(3, ge=1)
    publisher_hours: list[str] = ["06:00", "21:00"]

    @field_validator("feed_slot", "story_slot")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        _parse_hhmm(v)
        return v

    @field_validator("publisher_hours")
    @classmethod
    def _two_times(cls, v: list[str]) -> list[str]:
        if len(v) != 2:
            raise ValueError("publisher_hours is [start, end], like [\"06:00\", \"21:00\"]")
        start, end = (_parse_hhmm(x) for x in v)
        if not start < end:
            raise ValueError("publisher_hours must start before it ends")
        return v

    @property
    def feed_time(self) -> time:
        return _parse_hhmm(self.feed_slot)

    @property
    def story_time(self) -> time:
        return _parse_hhmm(self.story_slot)

    @property
    def min_lead(self) -> timedelta:
        return timedelta(hours=self.min_lead_hours)

    @property
    def story_frame_gap(self) -> timedelta:
        return timedelta(minutes=self.story_frame_gap_minutes)

    @property
    def publisher_window(self) -> tuple[time, time]:
        return _parse_hhmm(self.publisher_hours[0]), _parse_hhmm(self.publisher_hours[1])


class Voice(BaseModel):
    file: str = "voice.md"
    hashtags: list[str] = []
    max_hashtags: int = Field(5, ge=0)
    retired_phrases: list[str] = []


class Theme(BaseModel):
    name: str = "default"


class Images(BaseModel):
    sources: list[str] = ["page", "wikimedia", "business_discovery", "attachment"]


class Review(BaseModel):
    max_rounds: int = 2
    extra_checks: dict[str, str] = {}


class Model(BaseModel):
    model: str = "claude-sonnet-5-5"
    effort: str = "medium"


class LLM(BaseModel):
    partner: Model = Model(model="claude-opus-5-5")
    editor: Model = Model(model="claude-opus-5-5")
    maker: Model = Model(model="claude-sonnet-5-5", effort="low")
    monthly_usd: float = 30


class Telegram(BaseModel):
    digest_at: str = "08:00"


class Config(BaseModel):
    """`postdesk.toml`. Sections the later milestones read are kept so a
    full file validates today."""

    account: Account = Account()
    schedule: Schedule = Schedule()
    voice: Voice = Voice()
    theme: Theme = Theme()
    sources: dict[str, dict] = {}
    images: Images = Images()
    review: Review = Review()
    llm: LLM = LLM()
    telegram: Telegram = Telegram()
    notify: dict[str, dict] = {}
    # Where it came from; None when no file was found and defaults are in use.
    path: Path | None = None


def load_config(path: Path | None = None) -> Config:
    """Read `postdesk.toml` (the given path, else `settings.postdesk_config`,
    else `./postdesk.toml`). A missing file gives the defaults, so the app
    starts before `postdesk init` has run; `doctor` points that out."""
    p = Path(path or os.environ.get("POSTDESK_CONFIG") or settings.postdesk_config)
    if not p.is_file():
        return Config()
    with p.open("rb") as f:
        data = tomllib.load(f)
    cfg = Config.model_validate(data)
    cfg.path = p.resolve()
    return cfg


settings = Settings()
config = load_config()


def reload() -> None:
    """Re-read both files in place, so modules that imported the singletons
    see the new values (after `postdesk init`)."""
    global settings, config
    fresh = Settings()
    for k, v in fresh.model_dump().items():
        setattr(settings, k, v)
    new = load_config()
    for k, v in new.__dict__.items():
        setattr(config, k, v)
