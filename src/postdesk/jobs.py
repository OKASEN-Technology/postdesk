"""
The job runner: agent work, one card at a time. A small table-backed queue
(`job` rows) worked by one thread inside `postdesk serve`, or by `postdesk
worker` on its own.

- `enqueue` adds a job (a second one of the same kind for the same card
  while the first is still queued is folded into it).
- `claim` takes the oldest due job whose card has no running job, with
  `SELECT ... FOR UPDATE SKIP LOCKED`; a partial unique index on
  `job(card_id) WHERE status = 'running'` holds the one-per-card rule even
  if two workers race.
- Handlers are idempotent: each re-reads the card's column and does nothing
  if the card has moved on (agents/*/__init__.py, board/transitions.py).
- A transient failure (the network, the renderer down, the API overloaded
  after its own retries) is retried with backoff, `MAX_ATTEMPTS` times.
  Anything else, and every refusal or reply cut off, puts the card On hold
  with the reason, once, without spending again.

Between jobs the worker does the board's housekeeping (`housekeeping`):
the queue-to-board sync, filing hand-made drafts, and the scout on its
schedule.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from postdesk.db.models import Card, Job
from postdesk.db.session import SessionLocal

KINDS = ("partner", "make", "review", "revise")
MAX_ATTEMPTS = 5
BACKOFF_S = 30
# A job still `running` this long after it started belongs to a worker that died.
STALE = timedelta(minutes=30)
POLL_S = 2.0
HOUSEKEEPING_S = 60.0


class Transient(RuntimeError):
    """A failure worth trying again later."""


@dataclass
class Deps:
    """What the handlers call out to; tests replace them. None is the real one."""

    partner_call: Callable | None = None
    editor_call: Callable | None = None
    revision_call: Callable | None = None
    maker_call: Callable | None = None
    renderer: object | None = None
    fetch: httpx.Client | None = None  # for sources (rss, url)


def now_utc() -> datetime:
    return datetime.now(UTC)


def enqueue(session: Session, kind: str, card_id: uuid.UUID, payload: dict | None = None, *,
            delay_s: float = 0, commit: bool = True) -> Job:
    if kind not in KINDS:
        raise ValueError(f"no job kind {kind!r}")
    payload = dict(payload or {})
    queued = session.execute(select(Job).where(Job.card_id == card_id, Job.kind == kind, Job.status == "queued")
                             ).scalar_one_or_none()
    if queued is not None:
        merged = dict(queued.payload or {})
        for k, v in payload.items():
            if k in ("note", "reason") and merged.get(k) and v and v != merged[k]:
                merged[k] = f"{merged[k]}\n{v}"
            else:
                merged[k] = v
        queued.payload = merged
        job = queued
    else:
        now = now_utc()
        job = Job(kind=kind, card_id=card_id, payload=payload, status="queued", attempts=0,
                  run_after=now + timedelta(seconds=delay_s), created_at=now)
        session.add(job)
    if commit:
        session.commit()
    return job


def claim(session: Session, *, now: datetime | None = None) -> Job | None:
    """The next due job whose card has nothing running, marked running."""
    now = now or now_utc()
    running = aliased(Job)
    stmt = (
        select(Job)
        .where(Job.status == "queued", Job.run_after <= now,
               ~exists(select(running.id).where(running.card_id == Job.card_id, running.status == "running")))
        .order_by(Job.run_after, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.execute(stmt).scalar_one_or_none()
    if job is None:
        session.rollback()
        return None
    job.status, job.started_at, job.attempts = "running", now, job.attempts + 1
    try:
        session.commit()
    except IntegrityError:
        # Another worker started a job on the same card in the meantime.
        session.rollback()
        return None
    return job


def reset_stale(session: Session, *, now: datetime | None = None) -> int:
    now = now or now_utc()
    stale = session.scalars(select(Job).where(Job.status == "running", Job.started_at < now - STALE)).all()
    for j in stale:
        j.status, j.error = "queued", "the worker running it stopped; queued again"
    session.commit()
    return len(stale)


def is_transient(e: BaseException) -> bool:
    from postdesk import llm

    if isinstance(e, Transient | llm.LLMTransient | httpx.TransportError):
        return True
    cause = e.__cause__
    return isinstance(cause, httpx.TransportError)


def _handlers() -> dict[str, Callable]:
    from postdesk.agents import editor, partner, revision
    from postdesk.board import transitions

    return {
        "partner": lambda s, card, p, d: partner.turn(s, card, reason=p.get("reason"), call=d.partner_call),
        "make": lambda s, card, p, d: transitions.make(s, card, call=d.maker_call, renderer=d.renderer),
        "review": lambda s, card, p, d: editor.turn(s, card, call=d.editor_call),
        "revise": lambda s, card, p, d: revision.turn(s, card, p, call=d.revision_call, renderer=d.renderer),
    }


def _fail(session: Session, job: Job, card: Card | None, why: str) -> None:
    from postdesk.board import transitions

    job.status, job.error, job.finished_at = "failed", why[:2000], now_utc()
    session.commit()
    if card is not None:
        transitions.stuck(session, card, why, actor="worker")


def run(session: Session, job: Job, deps: Deps | None = None, *, log=print) -> Job:
    """Run one claimed job to done, failed, or queued again for later."""
    from postdesk import llm

    deps = deps or Deps()
    card = session.get(Card, job.card_id) if job.card_id else None
    agent = {"make": "maker", "review": "editor", "revise": "revision"}.get(job.kind, job.kind)
    try:
        if card is None:
            raise RuntimeError("the job's card is gone")
        _handlers()[job.kind](session, card, job.payload or {}, deps)
    except Exception as e:
        session.rollback()
        card = session.get(Card, job.card_id) if job.card_id else None
        if is_transient(e) and job.attempts < MAX_ATTEMPTS:
            job.status, job.error = "queued", f"{type(e).__name__}: {e}"[:2000]
            job.run_after = now_utc() + timedelta(seconds=BACKOFF_S * 2 ** (job.attempts - 1))
            session.commit()
            log(f"job {job.kind} for card {job.card_id}: {e}; trying again after {job.run_after:%H:%M:%S}")
            return job
        if isinstance(e, llm.LLMError):
            why = f"the {agent}'s turn failed: {e}"
        else:
            why = f"the {agent} job failed: {type(e).__name__}: {e}"
        log(f"job {job.kind} for card {job.card_id}: {why}")
        _fail(session, job, card, why)
        return job
    job.status, job.finished_at, job.error = "done", now_utc(), None
    session.commit()
    return job


def run_pending(session: Session, deps: Deps | None = None, *, limit: int = 100, log=print) -> list[Job]:
    """Work the queue until nothing is due (tests, and `postdesk worker --once`)."""
    done = []
    for _ in range(limit):
        job = claim(session)
        if job is None:
            break
        done.append(run(session, job, deps, log=log))
    return done


def housekeeping(session: Session, deps: Deps | None = None, *, log=print, scout: bool = True) -> None:
    """The board's periodic work: the queue-to-board sync, filing hand-made
    drafts, the scout when it is due."""
    from postdesk.board import scout as scout_mod
    from postdesk.board import sync

    for step, fn in (("sync", lambda: sync.sync(session, log=log)),
                     ("file drafts", lambda: sync.file_drafts(session, log=log)),
                     ("scout", lambda: scout_mod.run_if_due(session, client=(deps or Deps()).fetch, log=log)
                      if scout else None)):
        try:
            fn()
        except Exception as e:  # one step failing must not stop the others or the worker
            session.rollback()
            log(f"{step}: {type(e).__name__}: {e}")


def loop(stop: threading.Event, *, deps: Deps | None = None, log=print, poll_s: float = POLL_S,
         housekeeping_s: float = HOUSEKEEPING_S, scout: bool = True) -> None:
    """The worker: jobs as they come, housekeeping every `housekeeping_s`."""
    log("worker: running agent jobs; the scout and the board sync on their schedule")
    with SessionLocal() as session:
        n = reset_stale(session)
        if n:
            log(f"worker: {n} job(s) from a stopped worker queued again")
    last = 0.0
    while not stop.is_set():
        ran = False
        try:
            with SessionLocal() as session:
                if time.monotonic() - last >= housekeeping_s:
                    housekeeping(session, deps, log=log, scout=scout)
                    last = time.monotonic()
                job = claim(session)
                if job is not None:
                    run(session, job, deps, log=log)
                    ran = True
        except Exception as e:
            log(f"worker: {type(e).__name__}: {e}")
        if not ran:
            stop.wait(poll_s)
