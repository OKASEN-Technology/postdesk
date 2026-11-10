"""
The board over HTTP: cards, their thread, attachments and move log; the
admin's moves (`POST /api/cards/{id}/move`, where the two gates are); an
agent's turn queued now; the spend; the sources and the scout.

Everyone who reaches these endpoints is the admin (the session cookie or
the API token), so a move here is a person's move. The agents never call
this API; they move cards through board/transitions.py, which refuses
them the gate columns.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from postdesk import jobs, llm, queue, storage
from postdesk.api.auth import require_auth
from postdesk.api.routers.posts import item as post_item
from postdesk.api.schemas import (
    CardCreate,
    CardDetail,
    CardMove,
    CardRow,
    CommentIn,
    CommentOut,
    DraftOut,
    EventOut,
    JobOut,
    ManualSource,
    RunOut,
    RunsOut,
    ScanOut,
    SourceOut,
)
from postdesk.board import actions, cards, posts, scout, transitions
from postdesk.board.cards import BoardError, GateRefused
from postdesk.config import config
from postdesk.db.models import AgentRun, Card, Job
from postdesk.db.session import get_session
from postdesk.sources import SourceError

router = APIRouter(prefix="/api", tags=["board"], dependencies=[Depends(require_auth)])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except GateRefused as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except BoardError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except queue.QueueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except SourceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _card(session: Session, card_id: uuid.UUID) -> Card:
    return _guard(cards.get, session, card_id)


def _busy(session: Session, card_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not card_ids:
        return {}
    rows = session.execute(select(Job.card_id, Job.kind, Job.status, Job.error)
                           .where(Job.card_id.in_(card_ids), Job.status.in_(("queued", "running")))).all()
    out: dict[uuid.UUID, str] = {}
    for cid, kind, status, error in rows:
        if status == "running" or cid not in out:
            if status == "queued" and error and error.startswith(("RendererUnavailable", "RendererNotReady")):
                out[cid] = "Waiting for the image renderer; retrying"
            else:
                out[cid] = f"{kind} {'working' if status == 'running' else 'queued'}"
    return out


def _thumb(session: Session, card: Card) -> str | None:
    for p in posts.for_card(session, card.id):
        if p.status != "rejected" and p.slides:
            return storage.asset_url(p.slides[0]["asset_id"])
    for img in card.plan.get("images") or []:
        if img.get("used"):
            return storage.asset_url(img["asset_id"]) if img.get("asset_id") else img.get("url")
    hero = (card.facts or {}).get("hero") or {}
    return storage.asset_url(hero["asset_id"]) if hero.get("asset_id") else hero.get("url")


def _row(session: Session, card: Card, busy: dict) -> CardRow:
    return CardRow(id=str(card.id), kind=card.kind, column=card.column, title=card.title, source_ref=card.source_ref,
                   thumbnail_url=_thumb(session, card), stuck_reason=card.stuck_reason,
                   verdict=(card.review or {}).get("verdict"), busy=busy.get(card.id),
                   closed=card.closed_at is not None,
                   created_by=card.created_by, created_at=card.created_at, updated_at=card.updated_at)


def _run_out(r: AgentRun) -> RunOut:
    return RunOut(id=str(r.id), agent=r.agent, card_id=str(r.card_id) if r.card_id else None, model=r.model,
                  input_tokens=r.input_tokens, output_tokens=r.output_tokens,
                  cost_usd=float(r.cost_usd) if r.cost_usd is not None else None, status=r.status, error=r.error,
                  started_at=r.started_at)


def _images(card: Card) -> list[dict]:
    out = []
    for img in card.plan.get("images") or []:
        out.append({**img, "url": storage.asset_url(img["asset_id"]) if img.get("asset_id") else img.get("url")})
    return out


def detail(session: Session, card: Card) -> CardDetail:
    busy = _busy(session, [card.id])
    row = _row(session, card, busy)
    runs = session.scalars(select(AgentRun).where(AgentRun.card_id == card.id).order_by(AgentRun.started_at.desc())
                           .limit(50)).all()
    spend = session.execute(select(func.sum(AgentRun.cost_usd)).where(AgentRun.card_id == card.id)).scalar_one()
    job_rows = session.scalars(select(Job).where(Job.card_id == card.id).order_by(Job.created_at.desc())
                               .limit(10)).all()
    drafts = []
    for p in posts.for_card(session, card.id):
        it = post_item(p)
        drafts.append(DraftOut(id=it.id, kind=it.kind, status=it.status, title=it.title, caption=it.caption,
                               slides=it.slides, publish_at=it.publish_at, permalink=it.permalink, error=it.error))
    thread = [CommentOut(id=str(c.id), author=c.author, body_md=c.body_md, attachments=c.attachments or [],
                         created_at=c.created_at) for c in cards.thread(session, card)]
    events = [EventOut(id=str(e.id), from_column=e.from_column, to_column=e.to_column, actor=e.actor, reason=e.reason,
                       refused=e.refused, at=e.at) for e in cards.events(session, card)]
    rec = posts.recommended_time(session, card) if card.column == "Awaiting approval" else None
    return CardDetail(
        **row.model_dump(), body_md=card.body_md, facts=card.facts, brief_md=card.brief_md, guidance=card.guidance,
        plan=card.plan or {}, images=_images(card), review=card.review, review_round=card.review_round,
        thread=thread, events=events, drafts=drafts, runs=[_run_out(r) for r in runs], spend_usd=float(spend or 0),
        jobs=[JobOut(id=str(j.id), kind=j.kind, card_id=str(j.card_id) if j.card_id else None, status=j.status,
                     attempts=j.attempts, run_after=j.run_after, error=j.error) for j in job_rows],
        recommended_at=rec, gates=sorted(cards.GATES),
    )


# ---------------------------------------------------------------- cards


@router.get("/cards", response_model=list[CardRow])
def list_cards(column: str | None = None, closed: bool = False, session: Session = Depends(get_session)
               ) -> list[CardRow]:
    stmt = select(Card)
    if not closed:
        stmt = stmt.where(Card.closed_at.is_(None))
    if column:
        stmt = stmt.where(Card.column == column)
    rows = session.scalars(stmt.order_by(Card.updated_at.desc()).limit(500)).all()
    busy = _busy(session, [c.id for c in rows])
    return [_row(session, c, busy) for c in rows]


@router.post("/cards", response_model=CardDetail)
def create_card(body: CardCreate, actor: str = Depends(require_auth), session: Session = Depends(get_session)
                ) -> CardDetail:
    if not body.title.strip() and not body.link:
        raise HTTPException(status_code=400, detail="a title or a link is required")
    card = _guard(actions.file_post, session, title=body.title, text=body.body_md, link=body.link, column=body.column,
                  kind=body.kind, template="story-only" if body.kind == "story" else None, actor=actor)
    return detail(session, card)


@router.get("/cards/{card_id}", response_model=CardDetail)
def get_card(card_id: uuid.UUID, session: Session = Depends(get_session)) -> CardDetail:
    return detail(session, _card(session, card_id))


@router.post("/cards/{card_id}/move", response_model=CardDetail)
def move_card(card_id: uuid.UUID, body: CardMove, actor: str = Depends(require_auth),
              session: Session = Depends(get_session)) -> CardDetail:
    """The admin's move. Approved starts the maker (or sends a card with no
    brief back to the partner); Scheduled approves the drafts at `when`
    (the recommended time if empty); Planned is back to the partner (a
    rethink with a note); Making is a rethink of the made post with a note;
    In review sends the drafts to the editor; On hold holds; Closed drops."""
    card = _card(session, card_id)
    when: datetime | None = None
    if body.column == "Scheduled":
        when = _guard(posts.parse_when, body.when or "")
    _guard(transitions.move, session, card, body.column, actor=actor, note=body.note, when=when)
    session.refresh(card)
    return detail(session, card)


@router.post("/cards/{card_id}/comments", response_model=CardDetail)
def add_comment(card_id: uuid.UUID, body: CommentIn, session: Session = Depends(get_session)) -> CardDetail:
    """Your comment; while the card is in Planned or Brief ready the partner
    answers it."""
    card = _card(session, card_id)
    _guard(actions.comment, session, card, body.body_md)
    return detail(session, card)


@router.post("/cards/{card_id}/attachments", response_model=CardDetail)
async def add_attachment(card_id: uuid.UUID, file: UploadFile = File(...), note: str = Form(""),
                         actor: str = Depends(require_auth), session: Session = Depends(get_session)) -> CardDetail:
    """An image for the partner to judge, with a note on where it is from
    (its credit). It is stored with that note as its provenance."""
    card = _card(session, card_id)
    data = await file.read()
    _guard(actions.attach_image, session, card, data, filename=file.filename or "attachment", note=note,
           actor=actor)
    return detail(session, card)


@router.get("/cards/{card_id}/events", response_model=list[EventOut])
def card_events(card_id: uuid.UUID, session: Session = Depends(get_session)) -> list[EventOut]:
    card = _card(session, card_id)
    return [EventOut(id=str(e.id), from_column=e.from_column, to_column=e.to_column, actor=e.actor, reason=e.reason,
                     refused=e.refused, at=e.at) for e in cards.events(session, card)]


# ---------------------------------------------------------------- agents

NEEDS = {"partner": transitions.PARTNER_COLUMNS, "make": ("Approved",), "review": ("In review",)}


def _now(kind: str):
    def endpoint(card_id: uuid.UUID, session: Session = Depends(get_session)) -> CardDetail:
        card = _card(session, card_id)
        if card.closed_at is not None or card.column not in NEEDS[kind]:
            raise HTTPException(status_code=409, detail=f"the card is in {card.column}; a {kind} turn needs "
                                                        f"{' or '.join(NEEDS[kind])}")
        jobs.enqueue(session, kind, card.id, {"reason": "the admin asked for a turn now"})
        return detail(session, card)

    endpoint.__name__ = f"queue_{kind}"
    endpoint.__doc__ = f"Queue a {kind} turn on the card now (the worker runs it next)."
    return endpoint


for _kind in NEEDS:
    router.add_api_route(f"/cards/{{card_id}}/{_kind}", _now(_kind), methods=["POST"], response_model=CardDetail)


@router.get("/runs", response_model=RunsOut)
def list_runs(card_id: uuid.UUID | None = None, limit: int = 100, session: Session = Depends(get_session)) -> RunsOut:
    stmt = select(AgentRun)
    if card_id:
        stmt = stmt.where(AgentRun.card_id == card_id)
    rows = session.scalars(stmt.order_by(AgentRun.started_at.desc()).limit(min(max(limit, 1), 500))).all()
    return RunsOut(runs=[_run_out(r) for r in rows], month_usd=llm.month_spend(session),
                   ceiling_usd=config.llm.monthly_usd)


# ---------------------------------------------------------------- sources


@router.get("/sources", response_model=list[SourceOut])
def list_sources() -> list[SourceOut]:
    out = []
    for key, opts in (config.sources or {}).items():
        opts = dict(opts or {})
        out.append(SourceOut(key=key, type=str(opts.pop("type", key)), options=opts))
    return out


@router.post("/sources/{key}/scan", response_model=ScanOut)
def scan_source(key: str, days: int | None = None, session: Session = Depends(get_session)) -> ScanOut:
    """Run the scout over one source now."""
    r = _guard(scout.run, session, only=key, days=days, log=lambda _m: None)
    return ScanOut(filed=r.filed, seen=r.seen, deferred=r.deferred, errors=r.errors)


@router.post("/sources/manual", response_model=CardDetail)
def manual_source(body: ManualSource, actor: str = Depends(require_auth), session: Session = Depends(get_session)
                  ) -> CardDetail:
    """"Post about this": a title and text, or just a link (the page is
    fetched). The card goes to Planned and the partner starts."""
    if not body.title.strip() and not body.link:
        raise HTTPException(status_code=400, detail="a title and text, or a link, is required")
    if not body.link and not body.text.strip():
        raise HTTPException(status_code=400, detail="paste some text, or give a link")
    card = _guard(actions.file_post, session, title=body.title, text=body.text, link=body.link, column="Planned",
                  kind="story" if body.template == "story-only" else "post", template=body.template, actor=actor)
    return detail(session, card)

