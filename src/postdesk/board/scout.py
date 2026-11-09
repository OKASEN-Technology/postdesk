"""
The scout: what is new to post about. For each source in `[sources]`, the
candidates from the last `[scout].days`; a candidate with no card (by its
fingerprint, `post:<source>:<ref>`) and no post on its `source_ref` gets a
card in Planned with the source's facts, and the partner is queued to
write its brief. At most `[scout].max_per_run` cards a run; the rest wait
for the next one. No model is called here.

Runs every `[scout].every_hours` inside `postdesk serve` (or `postdesk
worker`) between the local `[scout].hours`, and on demand as `postdesk
scout [--days N]` or `POST /api/sources/{key}/scan`.

A `[sources]` entry is keyed by the source's name, or by any name with a
`type`:

    [sources]
    rss = { url = "https://example.com/feed.xml", template = "article" }
    [sources.news]
    type = "rss"
    url = "https://example.org/news.atom"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from postdesk import jobs
from postdesk.board import cards
from postdesk.config import config
from postdesk.db.models import Setting, SocialPost
from postdesk.sources import source_class
from postdesk.sources.base import Candidate, SourceError

LAST_RUN = "scout_last_run"


@dataclass
class ScoutResult:
    filed: list[str] = field(default_factory=list)  # card ids
    seen: int = 0
    deferred: int = 0
    errors: list[str] = field(default_factory=list)


def fingerprint(key: str, ref: str) -> str:
    return f"post:{key}:{ref}"


def configured(client: httpx.Client | None = None, only: str | None = None) -> list[tuple[str, object]]:
    """(name, source) for each `[sources]` entry (or just `only`)."""
    out = []
    for name, opts in (config.sources or {}).items():
        if only and name != only:
            continue
        opts = dict(opts or {})
        kind = str(opts.pop("type", name))
        cls = source_class(kind)
        try:
            src = cls(client=client, **opts)
        except TypeError:  # a plugin that fetches on its own
            src = cls(**opts)
        out.append((name, src))
    if only and not out:
        raise SourceError(f"no source {only!r} in postdesk.toml [sources]")
    return out


def _covered(session: Session, ref: str) -> bool:
    return session.execute(select(SocialPost.id).where(SocialPost.source_ref == ref, SocialPost.status != "rejected")
                           .limit(1)).first() is not None


def _body(name: str, c: Candidate, summary: str) -> str:
    link = f"[{c.title}]({c.url})" if c.url else c.title
    lines = [f"From **{name}**: {link}, published {c.published_at:%Y-%m-%d}."]
    if summary:
        lines += ["", f"> {summary}"]
    return "\n".join(lines)


def run(session: Session, *, days: int | None = None, only: str | None = None, client: httpx.Client | None = None,
        now: datetime | None = None, log=print) -> ScoutResult:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days or config.scout.days)
    result = ScoutResult()
    room = config.scout.max_per_run
    for name, src in configured(client, only):
        try:
            found = sorted(src.candidates(since), key=lambda c: c.published_at)
        except Exception as e:  # one broken feed must not stop the others
            result.errors.append(f"{name}: {e}")
            log(f"scout: {name}: {e}")
            continue
        for c in found:
            result.seen += 1
            fp = fingerprint(name, c.ref)
            if cards.by_fingerprint(session, fp) is not None or _covered(session, c.ref):
                continue
            if room <= 0:
                result.deferred += 1
                continue
            try:
                f = src.facts(c.ref)
            except Exception as e:
                result.errors.append(f"{name}: {c.ref}: {e}")
                log(f"scout: {name}: {c.ref}: {e}")
                continue
            f.template = f.template or c.template
            domains = src.domains() if hasattr(src, "domains") else []
            card = cards.file_card(
                session, title=c.title, created_by="scout", column="Planned",
                kind="story" if f.template == "story-only" else "post", body_md=_body(name, c, f.summary),
                source_ref=c.ref, fingerprint=fp, facts=f.to_json(),
                plan={"template": f.template, "source": name, "domains": domains},
                reason=f"new in {name}",
            )
            jobs.enqueue(session, "partner", card.id, {"reason": "the scout just filed this card: write its brief"})
            result.filed.append(str(card.id))
            room -= 1
            log(f"scout: {c.title!r} -> Planned")
    _set_last_run(session, now)
    if result.deferred:
        log(f"scout: {result.deferred} more next run")
    return result


def _set_last_run(session: Session, now: datetime) -> None:
    row = session.get(Setting, LAST_RUN)
    if row is None:
        session.add(Setting(key=LAST_RUN, value=now.isoformat(), updated_at=now))
    else:
        row.value, row.updated_at = now.isoformat(), now
    session.commit()


def last_run(session: Session) -> datetime | None:
    row = session.get(Setting, LAST_RUN)
    return datetime.fromisoformat(row.value) if row and isinstance(row.value, str) else None


def due(session: Session, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    start, end = config.scout.window
    t = now.astimezone(config.account.tz).time()
    if not (start <= t < end):
        return False
    last = last_run(session)
    return last is None or now - last >= timedelta(hours=config.scout.every_hours)


def run_if_due(session: Session, *, client: httpx.Client | None = None, now: datetime | None = None,
               log=print) -> ScoutResult | None:
    if not config.sources or not due(session, now):
        return None
    return run(session, client=client, now=now, log=log)
