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

The settings screen edits a few `postdesk.toml` keys (`EDITABLE`) without
touching the file: its edits are kept in the `setting` table and laid over
the file's values by `apply_overrides` (store.refresh_config). Secrets are
never among them.
"""

from __future__ import annotations

import copy
import os
import tomllib
from datetime import time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, PrivateAttr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """`.env`. Names match the variables there, lower-cased."""

    # POSTDESK_ENV_FILE moves it; the test suite points it at nothing so a
    # developer's own .env never reaches a test.
    model_config = SettingsConfigDict(env_file=os.environ.get("POSTDESK_ENV_FILE", ".env"), extra="ignore")

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
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

    postdesk_credentials_key: str | None = None
    postdesk_admin_password: str | None = None
    postdesk_secret_key: str | None = None
    # A bearer token for scripts and for `postdesk` run on another machine;
    # inside Docker the CLI talks to the database directly and needs none.
    postdesk_api_token: str | None = None
    # Where local storage keeps the slide files. Docker Compose mounts a volume.
    postdesk_data_dir: Path = Path("var")
    # Where postdesk.toml is. Default: the working directory.
    postdesk_config: Path = Path("postdesk.toml")

    # One-way notifiers ([notify] in postdesk.toml says which are on).
    slack_webhook_url: str | None = None
    notify_webhook_url: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    resend_api_key: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        # `KEY=` in .env.example means "not set", not "the empty string".
        return None if isinstance(v, str) and v.strip() == "" else v

    def s3_enabled(self) -> bool:
        return all((self.storage_s3_endpoint, self.storage_s3_bucket, self.storage_s3_access_key_id,
                    self.storage_s3_secret_access_key))


def _parse_hhmm(value: str) -> time:
    try:
        h, m = value.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError) as e:
        raise ValueError(f"{value!r} is not a time like 18:00") from e


class Account(BaseModel):
    handle: str = ""
    site: str = ""
    timezone: str = "America/New_York"
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
    # The voice as typed on the settings screen; when set it is used
    # instead of the file.
    text: str | None = None
    brand_assets_url: str | None = None
    hashtags: list[str] = []
    max_hashtags: int = Field(5, ge=0)
    retired_phrases: list[str] = []

    @field_validator("brand_assets_url")
    @classmethod
    def _brand_url(cls, value):
        if not value:
            return None
        from urllib.parse import urlsplit

        url = urlsplit(value)
        if (url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment
                or url.hostname.lower() == "dash.cloudflare.com"):
            raise ValueError("Use the public HTTPS bucket or folder URL, not a Cloudflare dashboard link")
        return value.rstrip("/")


class Theme(BaseModel):
    name: str = "default"


class Images(BaseModel):
    sources: list[str] = ["page", "wikimedia", "business_discovery", "attachment"]


class Review(BaseModel):
    max_rounds: int = 2
    extra_checks: dict[str, str] = {}


class Scout(BaseModel):
    """When the scout looks for new things to post about (board/scout.py)."""

    every_hours: float = Field(2, gt=0)
    days: int = Field(3, ge=1)  # how far back a new item may be
    max_per_run: int = Field(5, ge=1)
    hours: list[str] = ["06:00", "21:00"]  # local; it rests outside these

    @field_validator("hours")
    @classmethod
    def _two_times(cls, v: list[str]) -> list[str]:
        if len(v) != 2:
            raise ValueError("hours is [start, end], like [\"06:00\", \"21:00\"]")
        for x in v:
            _parse_hhmm(x)
        return v

    @property
    def window(self) -> tuple[time, time]:
        return _parse_hhmm(self.hours[0]), _parse_hhmm(self.hours[1])


class Partner(BaseModel):
    """The post partner's web access: it may confirm a date or a name on
    these sites (plus the source's own), never take a figure or an image."""

    allowed_domains: list[str] = []
    web_search: bool = True
    max_searches: int = Field(5, ge=1)


class Model(BaseModel):
    model: str = "claude-sonnet-5-5"
    effort: str = "medium"


class LLM(BaseModel):
    partner: Model = Model(model="claude-opus-5-5")
    editor: Model = Model(model="claude-opus-5-5")
    maker: Model = Model(model="claude-sonnet-5-5", effort="low")
    # Small choices: which of an account's posts shows the subject (images/).
    pick: Model = Model(model="claude-haiku-5-5", effort="low")
    monthly_usd: float = Field(30, ge=0)


class Telegram(BaseModel):
    """The bot (telegram/). The chat is paired with /start, not set here."""

    digest_at: str = "08:00"  # local; the daily digest
    # auto: a webhook when PUBLIC_BASE_URL is a public https address, else
    # long polling (no public URL needed). Or force one: polling | webhook.
    mode: str = "auto"

    @field_validator("digest_at")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        _parse_hhmm(v)
        return v

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in ("auto", "polling", "webhook"):
            raise ValueError("mode is auto, polling or webhook")
        return v

    @property
    def digest_time(self) -> time:
        return _parse_hhmm(self.digest_at)


class Config(BaseModel):
    """`postdesk.toml`. Sections the later milestones read are kept so a
    full file validates today."""

    account: Account = Account()
    schedule: Schedule = Schedule()
    voice: Voice = Voice()
    theme: Theme = Theme()
    sources: dict[str, dict] = {}
    scout: Scout = Scout()
    partner: Partner = Partner()
    images: Images = Images()
    review: Review = Review()
    llm: LLM = LLM()
    telegram: Telegram = Telegram()
    notify: dict[str, dict] = {}
    # Where it came from; None when no file was found and defaults are in use.
    path: Path | None = None
    # The file's own values, before the settings screen's overrides.
    _data: dict = PrivateAttr(default_factory=dict)


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
    cfg._data = data
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
    config._data = new._data
    _applied.clear()


# ---------------------------------------------------------------- overrides

# What the settings screen may change: section -> keys (None: the whole
# table). Everything else stays in the file.
EDITABLE: dict[str, tuple[str, ...] | None] = {
    "account": ("handle", "site", "timezone", "audience"),
    "schedule": ("feed_slot", "story_slot", "max_feed_per_day", "max_story_per_day", "min_lead_hours",
                 "publisher_hours"),
    "voice": ("text", "brand_assets_url", "hashtags", "max_hashtags", "retired_phrases"),
    "theme": ("name",),
    "sources": None,
    "images": ("sources",),
    "llm": ("partner", "editor", "maker", "pick", "monthly_usd"),
    "telegram": ("digest_at",),
}
# The overrides in force (what `apply_overrides` last laid over the file).
_applied: dict = {}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "sources":
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def check_overrides(overrides: dict) -> Config:
    """The configuration `overrides` would give, validated. Raises
    ValueError on a key the screen may not change or a bad value."""
    for section, keys in overrides.items():
        if section not in EDITABLE:
            raise ValueError(f"[{section}] is not editable here; change it in postdesk.toml")
        if keys is None:
            continue
        if not isinstance(keys, dict):
            raise ValueError(f"[{section}] must be a table")
        allowed = EDITABLE[section]
        bad = [k for k in keys if allowed is not None and k not in allowed]
        if bad:
            raise ValueError(f"[{section}] {', '.join(bad)} is not editable here; change it in postdesk.toml")
    return Config.model_validate(_merge(config._data, {k: v for k, v in overrides.items() if v is not None}))


def apply_overrides(overrides: dict) -> None:
    """Lay `overrides` ({section: {key: value}}) over the file's values, in
    place. Only the keys named now or last time are touched, so a key the
    screen never edited keeps whatever it has."""
    merged = check_overrides(overrides)
    touched: dict[str, set[str] | None] = {}
    for src in (_applied, overrides):
        for section, keys in src.items():
            if EDITABLE.get(section) is None or not isinstance(keys, dict):
                touched[section] = None
            elif touched.get(section, set()) is not None:
                touched[section] = touched.get(section, set()) | set(keys)
    for section, keys in touched.items():
        if keys is None:
            setattr(config, section, copy.deepcopy(getattr(merged, section)))
            continue
        target, fresh = getattr(config, section), getattr(merged, section)
        for k in keys:
            setattr(target, k, copy.deepcopy(getattr(fresh, k)))
    _applied.clear()
    _applied.update(copy.deepcopy(overrides))


def applied_overrides() -> dict:
    return copy.deepcopy(_applied)
