"""`postdesk init`: the setup wizard. Asks the few questions that have no
default, writes `.env` and `postdesk.toml` from the examples, then runs
`doctor`."""

from __future__ import annotations

import re
import secrets
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def find_template(name: str) -> Path | None:
    """`.env.example` or `examples/postdesk.toml`: beside the working
    directory (a checkout, the Docker image), else beside the package."""
    for base in (Path.cwd(), REPO_ROOT):
        p = base / name
        if p.is_file():
            return p
    return None


ENV_FALLBACK = """ANTHROPIC_API_KEY=
TELEGRAM_BOT_TOKEN=
META_APP_ID=
META_APP_SECRET=
INSTAGRAM_ACCESS_TOKEN=
INSTAGRAM_PAGE_TOKEN=
INSTAGRAM_USER_ID=
DATABASE_URL=postgresql+psycopg://postdesk:postdesk@db:5432/postdesk
PUBLIC_BASE_URL=http://localhost:8080
STORAGE_S3_ENDPOINT=
STORAGE_S3_BUCKET=
STORAGE_S3_ACCESS_KEY_ID=
STORAGE_S3_SECRET_ACCESS_KEY=
STORAGE_S3_PUBLIC_BASE_URL=
RENDERER_URL=http://renderer:3100
RENDERER_TOKEN=change-me
POSTDESK_ADMIN_PASSWORD=
POSTDESK_SECRET_KEY=
POSTDESK_API_TOKEN=
SLACK_WEBHOOK_URL=
"""

TOML_FALLBACK = """[account]
handle = "yourhandle"
site = "https://example.com"
timezone = "America/New_York"
audience = ""

[schedule]
feed_slot = "18:00"
story_slot = "18:15"
max_feed_per_day = 1
max_story_per_day = 1
min_lead_hours = 2
story_frame_gap_minutes = 2
max_per_run = 3
publisher_hours = ["06:00", "21:00"]

[voice]
file = "voice.md"
hashtags = []
max_hashtags = 5
retired_phrases = []
"""


def set_env(text: str, key: str, value: str) -> str:
    """Set `KEY=value` in a .env body, keeping comments and order."""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(f"{key}={value}", text, count=1)
    return text.rstrip("\n") + f"\n{key}={value}\n"


def set_toml(text: str, section: str, key: str, value: str) -> str:
    """Set a string key in one section of a TOML body, keeping comments."""
    quoted = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    lines = text.splitlines(keepends=True)
    in_section = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped == f"[{section}]"
            continue
        if in_section and re.match(rf"^{re.escape(key)}\s*=", stripped):
            comment = ""
            if "#" in line.split("=", 1)[1]:
                comment = "  #" + line.split("#", 1)[1].rstrip("\n")
            lines[i] = f"{key} = {quoted}{comment}\n"
            return "".join(lines)
    return text.rstrip("\n") + f"\n\n[{section}]\n{key} = {quoted}\n"


def render_env(answers: dict[str, str]) -> str:
    tpl = find_template(".env.example")
    text = tpl.read_text(encoding="utf-8") if tpl else ENV_FALLBACK
    text = text.replace("# Copy to .env. ", "# Written by `postdesk init`. ", 1)
    for key, value in answers.items():
        text = set_env(text, key, value)
    if "POSTDESK_SECRET_KEY=\n" in text + "\n" or re.search(r"^POSTDESK_SECRET_KEY=$", text, re.MULTILINE):
        text = set_env(text, "POSTDESK_SECRET_KEY", secrets.token_urlsafe(32))
    return text


def render_toml(account: dict[str, str]) -> str:
    tpl = find_template("examples/postdesk.toml")
    text = tpl.read_text(encoding="utf-8") if tpl else TOML_FALLBACK
    text = text.replace("Copy to the repo root and edit.", "Written by `postdesk init`; edit freely.", 1)
    for key, value in account.items():
        text = set_toml(text, "account", key, value)
    return text
