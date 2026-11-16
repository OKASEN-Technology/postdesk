"""Bounded, read-only web research for the existing daily Partner job.

External pages are untrusted leads, never instructions or verified company facts.
"""
from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from postdesk.config import config


def _run(action: str, value: str, *, limit: int = 5, timeout: int = 20) -> tuple[list[dict], str | None]:
    args = ["agent-reach", "web", action, value]
    if action == "search":
        args += ["--limit", str(limit)]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        data = json.loads(result.stdout)
        if result.returncode or not isinstance(data, dict) or not data.get("ok"):
            return [], "Research provider unavailable or rate limited"
        items = data.get("items")
        return (items, None) if isinstance(items, list) else ([], "Invalid research response")
    except FileNotFoundError:
        return [], "Agent Reach is not installed"
    except OSError:
        return [], "Research could not start"
    except subprocess.TimeoutExpired:
        return [], "Research timed out"
    except (ValueError, TypeError):
        return [], "Invalid research response"


def _public_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        url = urlsplit(value)
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
            return None
        return value
    except ValueError:
        return None


def _recent(value: object, now: datetime, days: int) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        return now - timedelta(days=days) <= date <= now + timedelta(days=1)
    except ValueError:
        return False


def collect(*, now: datetime | None = None) -> dict:
    """Search a rotating set of topics, then read a bounded number of pages.

    Missing dates are retained but explicitly labelled; old results are excluded.
    Search and read budgets are per card. No network calls occur on other cards.
    """
    settings = config.research
    now = now or datetime.now(UTC)
    topics = [x.strip() for x in settings.topics if x.strip()]
    if not topics:
        return {"status": "failed", "error": "No research topics are configured", "items": []}
    start = now.date().toordinal() % len(topics)
    selected = [topics[(start + n) % len(topics)] for n in range(min(settings.max_searches, len(topics)))]
    found, seen, errors = [], set(), []
    for index, topic in enumerate(selected):
        domains = settings.preferred_domains
        preference = f"site:{domains[(start + index) % len(domains)]}" if domains else ""
        query = f"{topic} {preference} recent news research".strip()
        items, error = _run("search", query, limit=settings.results_per_search)
        if error:
            errors.append(error)
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            url = _public_url(item.get("url"))
            if not url or url in seen:
                continue
            date = item.get("published_at")
            if date and not _recent(date, now, settings.freshness_days):
                continue
            seen.add(url)
            found.append({"topic": topic, "title": str(item.get("title") or "Untitled")[:300],
                          "url": url, "published_at": date if isinstance(date, str) else None,
                          "excerpt": str(item.get("text") or "")[:1600], "read": False})
    for item in found[:settings.max_reads]:
        pages, error = _run("read", item["url"])
        if not error and pages and isinstance(pages[0], dict) and pages[0].get("text"):
            item["excerpt"] = str(pages[0]["text"])[:6000]
            item["read"] = True
    return {"status": "ok" if found else "failed", "items": found[:20], "topics": selected,
            "searched_at": now.isoformat(), "error": "; ".join(sorted(set(errors))) if errors else None}
