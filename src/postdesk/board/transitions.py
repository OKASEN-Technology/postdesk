"""
The moves. One function per move: each checks the card's column (the
precondition) and who is asking (the actor), moves it through
`cards.set_column` (which keeps the gates and writes the `card_event`),
and queues the next agent's job.

The admin's moves (`actor` is `admin` or `api`): plan, approve, rethink,
back to the partner, schedule, unschedule, hold, send to the editor, back
to the inbox, drop. `move` maps a target column to the right one, for the
API's `POST /cards/{id}/move`.

The agents' moves: `brief` (the partner's turn), `make` (the maker), `review`
(the editor's verdict), `revise` (the revision turn), `stuck` (any of them,
or the worker, when it cannot go on). None of them can reach Approved or
Scheduled: `set_column` refuses and logs it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from postdesk import jobs, queue
from postdesk.board import cards, posts
from postdesk.board import facts as card_facts
from postdesk.board.cards import BoardError, set_column
from postdesk.config import config
from postdesk.db.models import Card, SocialPost
from postdesk.queue.times import local

PARTNER_COLUMNS = ("Planned", "Brief ready")
# What the revision turn may change: words only. A slide's image, source
# line or link, and anything about rights, needs a person.
EDITABLE = frozenset({"caption", "alt_text", "slide_text"})
MAKER = "maker"


def _when(dt: datetime | None) -> str:
    if dt is None:
        return "no time set"
    t = local(dt)
    return f"{t:%a} {t.day} {t:%b}, {t:%H:%M}"


# ---------------------------------------------------------------- stuck


def stuck(session: Session, card: Card, why: str, *, actor: str = "system") -> Card:
    """On hold with the reason, from wherever the card is (not once it is
    live). Approve it again, send it to the editor, or back to the partner."""
    session.refresh(card)
    if card.column in cards.DONE or card.closed_at is not None:
        cards.comment(session, card, "system", f"Not moved: {why}")
        return card
    set_column(session, card, "On hold", actor=actor, reason=why, commit=False)
    card.stuck_reason = why[:2000]
    cards.comment(session, card, "system",
                  f"On hold: {why}\n\nFix it in the post editor and send it to the editor, approve it again to "
                  "make it afresh, or send it back to the partner.", commit=False)
    session.commit()
    return card


# ---------------------------------------------------------------- the admin's moves


def plan(session: Session, card: Card, *, actor: str, note: str | None = None) -> Card:
    """Inbox -> Planned: the partner starts on it."""
    if note:
        cards.comment(session, card, "user", note, commit=False)
    set_column(session, card, "Planned", actor=actor, reason=note or "planned", allowed_from=("Inbox",))
    jobs.enqueue(session, "partner", card.id, {"reason": "the card was just planned: write its brief"})
    return card


def approve(session: Session, card: Card, *, actor: str, note: str | None = None) -> Card:
    """The first gate: the brief is right, make it. A card with no brief yet
    goes back to the partner first."""
    cards.check_move(session, card, "Approved", actor=actor, allowed_from=("Planned", "Brief ready", "On hold"),
                     reason=note)
    if note:
        cards.comment(session, card, "user", note, commit=False)
    if not card.brief_md and not card.plan.get("post") and not posts.drafts(session, card.id):
        set_column(session, card, "Planned", actor=actor, reason="approved before it had a brief")
        cards.comment(session, card, "system", "Not made yet: this card has no brief. The partner is writing it now; "
                                               "approve it again once it is at Brief ready.")
        jobs.enqueue(session, "partner", card.id,
                     {"reason": "the card was approved before it had a brief: write it"})
        return card
    set_column(session, card, "Approved", actor=actor, reason=note or "approved")
    jobs.enqueue(session, "make", card.id)
    return card


def rethink(session: Session, card: Card, *, actor: str, note: str) -> Card:
    """With a note: a brief goes back to the partner; a made post goes to
    the revision turn with the note as its instruction."""
    if not (note or "").strip():
        raise BoardError("say what to change")
    note = note.strip()
    if card.column in PARTNER_COLUMNS:
        cards.comment(session, card, "user", note, commit=False)
        set_column(session, card, "Planned", actor=actor, reason=f"rethink: {note}", allowed_from=PARTNER_COLUMNS)
        jobs.enqueue(session, "partner", card.id, {"reason": f"rethink: {note}"})
        return card
    if card.column in ("In review", "Awaiting approval", "On hold") and posts.drafts(session, card.id):
        cards.comment(session, card, "user", note, commit=False)
        set_column(session, card, "Making", actor=actor, reason=f"rethink: {note}")
        jobs.enqueue(session, "revise", card.id, {"note": note, "from": "user"})
        return card
    raise BoardError(f"the card is in {card.column}; rethink works on a brief (Planned, Brief ready) or on drafts "
                     "(In review, Awaiting approval, On hold)")


def back_to_partner(session: Session, card: Card, *, actor: str, note: str | None = None) -> Card:
    if note:
        cards.comment(session, card, "user", note, commit=False)
    set_column(session, card, "Planned", actor=actor, reason=note or "back to the partner",
               allowed_from=("Inbox", "Brief ready", "Approved", "Making", "In review", "Awaiting approval",
                             "On hold", "Planned"))
    jobs.enqueue(session, "partner", card.id, {"reason": note or "the admin sent the card back to you"})
    return card


def schedule(session: Session, card: Card, *, actor: str, when: datetime | None = None) -> list[SocialPost]:
    """The second gate: the card's drafts approved in the queue at `when`
    (else the recommended time), then the card to Scheduled."""
    cards.check_move(session, card, "Scheduled", actor=actor, allowed_from=("Awaiting approval",))
    try:
        approved = posts.schedule(session, card, when=when, approved_by=actor)
    except (queue.QueueError, LookupError) as e:
        session.rollback()
        raise BoardError(f"not scheduled: {e}") from e
    times = "; ".join(f"{p.kind} {_when(p.publish_at)}" for p in approved if p.status != "rejected")
    set_column(session, card, "Scheduled", actor=actor, reason=f"scheduled: {times}")
    cards.comment(session, card, "system", f"Scheduled: {times}. Unschedule to change the time.")
    return approved


def unschedule(session: Session, card: Card, *, actor: str) -> Card:
    cards.check_move(session, card, "Awaiting approval", actor=actor, allowed_from=("Scheduled",))
    for p in posts.for_card(session, card.id):
        if p.status == "approved":
            queue.unapprove(session, p.id)
    set_column(session, card, "Awaiting approval", actor=actor, reason="unscheduled")
    return card


def retry(session: Session, card: Card, *, actor: str) -> Card:
    """After a failed publish: the failed posts back to draft and the card
    to Awaiting approval, to be scheduled again. Check the account first: a
    post that failed late may be live after all."""
    failed = [p for p in posts.for_card(session, card.id) if p.status == "failed"]
    if not failed:
        raise BoardError("nothing on this card failed to publish")
    cards.check_move(session, card, "Awaiting approval", actor=actor, allowed_from=("On hold",))
    if not cards.is_human(actor, session):
        raise BoardError("only the admin sends a failed post back to draft")
    for p in failed:
        queue.back_to_draft(session, p.id)
    set_column(session, card, "Awaiting approval", actor=actor, reason="back to draft after a failed publish")
    return card


def hold(session: Session, card: Card, *, actor: str, note: str | None = None) -> Card:
    why = (note or "").strip() or f"held by {actor}"
    if card.column in cards.DONE:
        raise BoardError(f"the card is {card.column}")
    set_column(session, card, "On hold", actor=actor, reason=why, commit=False)
    card.stuck_reason = why
    session.commit()
    return card


def accept_review_hold(session: Session, card: Card, *, actor: str) -> Card:
    """A person accepts the editor's flagged draft; scheduling remains separate."""
    if not cards.is_human(actor, session):
        raise BoardError("only the admin can accept a held review")
    cards.check_move(session, card, "Awaiting approval", actor=actor, allowed_from=("On hold",))
    if (card.review or {}).get("column") != "On hold" or not posts.drafts(session, card.id):
        raise BoardError("there is no held review with drafts to accept")
    cards.comment(session, card, "user", "Accepted the editor's review warnings as is; scheduling remains separate.",
                  commit=False)
    return set_column(session, card, "Awaiting approval", actor=actor, reason="accepted held review as is")


def send_to_review(session: Session, card: Card, *, actor: str, note: str | None = None) -> Card:
    """The drafts as they stand (fixed by hand, say) to the editor again,
    with a fresh count of revision rounds."""
    cards.check_move(session, card, "In review", actor=actor,
                     allowed_from=("On hold", "Awaiting approval", "Making", "Approved"))
    if not posts.drafts(session, card.id):
        raise BoardError("the card has no drafts to review")
    if note:
        cards.comment(session, card, "user", note, commit=False)
    card.review_round = 0
    set_column(session, card, "In review", actor=actor, reason=note or "sent to the editor")
    jobs.enqueue(session, "review", card.id)
    return card


def to_inbox(session: Session, card: Card, *, actor: str, note: str | None = None) -> Card:
    return set_column(session, card, "Inbox", actor=actor, reason=note or "back to the inbox",
                      allowed_from=("Planned", "Brief ready", "On hold"))


def drop(session: Session, card: Card, *, actor: str, note: str | None = None) -> Card:
    """Off the board: its drafts rejected (an approved one unapproved first)."""
    if card.column in cards.DONE:
        raise BoardError(f"the card is {card.column}")
    for p in posts.for_card(session, card.id):
        if p.status in ("draft", "approved", "failed"):
            queue.reject(session, p.id)
    return cards.close(session, card, actor=actor, reason=note or "dropped")


def move(session: Session, card: Card, column: str, *, actor: str, note: str | None = None,
         when: datetime | None = None):
    """The admin's move to `column`, as the board's buttons and drags ask
    for it. Columns only the agents or the publisher set are refused."""
    note = (note or "").strip() or None
    if column == "Approved":
        return approve(session, card, actor=actor, note=note)
    if column == "Scheduled":
        return schedule(session, card, actor=actor, when=when)
    if not cards.is_human(actor, session):
        raise BoardError(f"{actor} moves cards through its own turn, not here")
    if column == "Planned":
        if card.column == "Inbox":
            return plan(session, card, actor=actor, note=note)
        if note and card.column in PARTNER_COLUMNS:
            return rethink(session, card, actor=actor, note=note)
        return back_to_partner(session, card, actor=actor, note=note)
    if column == "Making":
        return rethink(session, card, actor=actor, note=note or "")
    if column == "In review":
        return send_to_review(session, card, actor=actor, note=note)
    if column == "Awaiting approval":
        return unschedule(session, card, actor=actor)
    if column == "On hold":
        return hold(session, card, actor=actor, note=note)
    if column == "Inbox":
        return to_inbox(session, card, actor=actor, note=note)
    if column == "Closed":
        return drop(session, card, actor=actor, note=note)
    if column in cards.COLUMNS:
        raise BoardError(f"{column} is set by the agents or the publisher, not by a move")
    raise BoardError(f"no column {column!r}")


# ---------------------------------------------------------------- the partner


def brief(session: Session, card: Card, d: dict, *, candidates: list[dict], problems: list[str] | None = None
          ) -> Card:
    """The partner's turn applied: the brief, the guidance, which images it
    uses, a custom plan; the reply in the thread; Planned or Brief ready.
    A card that moved on while the partner worked is left alone. With
    `problems` (a figure not in the source, an image that is not the
    card's) nothing is saved and the card stays in Planned."""
    session.refresh(card)
    if card.column not in PARTNER_COLUMNS or card.closed_at is not None:
        return card
    column = d.get("column") if d.get("column") in PARTNER_COLUMNS else "Planned"
    problems = list(problems or [])
    images, image_problems = card_facts.resolve_images(candidates, list(d.get("use_images") or []),
                                                       list(d.get("attached_images") or []))
    problems += image_problems
    if d.get("reply"):
        cards.comment(session, card, "partner", d["reply"], commit=False)
    if problems:
        column = "Planned"
        cards.comment(session, card, "system", "Not ready to make:\n\n" + "\n".join(f"- {p}" for p in problems),
                      commit=False)
    else:
        if d.get("brief"):
            card.brief_md = d["brief"].strip()
        if d.get("guidance"):
            card.guidance = d["guidance"].strip()
        plan = dict(card.plan or {})
        plan["images"] = images
        from postdesk.images import attachment

        attachment.record_credit(session, images)
        if d.get("post"):
            plan["post"] = d["post"]
            card.kind = "story" if d["post"].get("kind") == "story" else "post"
        card.plan = plan
    if column == "Brief ready" and not card.brief_md:
        column = "Planned"  # no brief, no Brief ready
    if column == "Brief ready" and not card.facts and not card.plan.get("post") and not card.plan.get("template"):
        column = "Planned"
        cards.comment(session, card, "system", "Not ready to make: this card has no source and no slide plan yet.",
                      commit=False)
    was = card.column
    from postdesk import notify

    if column == "Brief ready":
        notify.emit(session, "brief_ready", card, reply=d.get("reply"))
    elif d.get("reply") or problems:
        notify.emit(session, "partner_reply", card, reply=d.get("reply"), problems=problems)
    set_column(session, card, column, actor="partner",
               reason="the brief is ready" if column == "Brief ready" else "the partner needs an answer")
    if column == "Brief ready" and was != "Brief ready":
        cards.comment(session, card, "system", "Brief ready. Approve it to make the post, or comment what to change.")
    return card


# ---------------------------------------------------------------- the maker


def make(session: Session, card: Card, *, call=None, renderer=None, log=None) -> list[SocialPost]:
    """Approved -> Making -> In review: the post made from the approved
    brief (its guidance, its images), the old drafts replaced, the editor
    queued. Drafts made by hand and already on the card go straight to the
    editor instead."""
    from postdesk import maker

    session.refresh(card)
    if card.column not in ("Approved", "Making") or card.closed_at is not None:
        return []
    linked = posts.drafts(session, card.id)
    if any(p.created_by != MAKER for p in linked):
        set_column(session, card, "In review", actor=MAKER,
                   reason="the drafts were made by hand; the editor reads them")
        card.review_round = 0
        session.commit()
        jobs.enqueue(session, "review", card.id)
        return linked
    set_column(session, card, "Making", actor=MAKER, reason="making the post from the approved brief")
    f = card_facts.facts_for_make(card)
    if card.plan.get("post"):
        template = card_facts.custom_template(card.plan["post"], card.title)
    else:
        template = card.plan.get("template") or f.template or "article"
    made = maker.make(session, f, template, guidance=card.guidance, card_id=card.id,
                      source_ref=card.source_ref or f"card:{card.id}", call=call, renderer=renderer, log=log)
    posts.replace_drafts(session, card, made)
    card.review_round = 0
    lines = [f"- {p.kind}: {len(p.slides)} {'slide' if len(p.slides) == 1 else 'slides'}, recommended "
             f"{_when(p.publish_at)}" for p in made]
    cards.comment(session, card, MAKER, "Made:\n\n" + "\n".join(lines) + "\n\nThe editor reads it next.", commit=False)
    set_column(session, card, "In review", actor=MAKER, reason="made; the editor reads it")
    jobs.enqueue(session, "review", card.id)
    return made


# ---------------------------------------------------------------- the editor


def review_outcome(d: dict, rnd: int, max_rounds: int) -> tuple[str, bool, str]:
    """(column, stuck, why) for the editor's verdict on review round `rnd`."""
    if d["verdict"] == "pass":
        return "Awaiting approval", False, "passed review"
    unfixable = sorted({n["target"] for n in d.get("notes") or [] if n["target"] not in EDITABLE})
    if d["verdict"] == "hold" or unfixable:
        why = f"needs a person ({', '.join(unfixable)})" if unfixable else "the editor held it"
        return "On hold", True, why
    if not d.get("notes"):
        return "On hold", True, "the editor asked for a revision without saying what"
    if rnd > max_rounds:
        return "On hold", True, f"still not right after {max_rounds} revisions"
    return "Making", False, "back to the maker for a revision"


def review_comment(d: dict, rnd: int, column: str, why: str) -> str:
    lines = [f"### Review, round {rnd}: {d['verdict']}", "", d.get("summary", "").strip(), "",
             "| Check | | Note |", "|---|---|---|"]
    lines += [f"| {c['check']} | {'pass' if c['ok'] else 'FAIL'} | {str(c.get('note', '')).replace('|', '/')} |"
              for c in d.get("checks") or []]
    if d.get("notes"):
        lines += ["", "**To change**", ""]
        for n in d["notes"]:
            where = n["target"] + (f" (slide {n['slide']})" if n.get("slide") else "")
            lines.append(f"- **{where}**: {n['problem']}" + (f" Suggested: {n['fix']}" if n.get("fix") else ""))
    lines += ["", f"-> **{column}**: {why}."]
    return "\n".join(lines)


def review(session: Session, card: Card, d: dict) -> Card:
    """The editor's verdict applied: pass -> Awaiting approval; revise ->
    Making with the notes (On hold past `[review].max_rounds`); hold -> On
    hold with the reason."""
    session.refresh(card)
    if card.column != "In review" or card.closed_at is not None:
        return card
    rnd = (card.review_round or 0) + 1
    column, is_stuck, why = review_outcome(d, rnd, config.review.max_rounds)
    card.review_round = rnd
    card.review = {**d, "round": rnd, "column": column, "why": why}
    cards.comment(session, card, "editor", review_comment(d, rnd, column, why), commit=False)
    if is_stuck:
        session.commit()
        return stuck(session, card, why if why != "the editor held it" else f"the editor held it: {d.get('summary')}",
                     actor="editor")
    set_column(session, card, column, actor="editor", reason=why)
    if column == "Making":
        jobs.enqueue(session, "revise", card.id, {"notes": d.get("notes") or [], "from": "editor"})
    return card


# ---------------------------------------------------------------- the revision


def valid_revision(d: dict, drafts: list[SocialPost]) -> tuple[list[dict], list[str]]:
    """([{id, caption?, alt_texts?, slides?}] to apply, [problems]). A slide
    patch is {index (0-based), fields: word field -> text}, each field one
    the slide's type may change."""
    from postdesk.maker.words import SLIDE_COPY

    by_id = {str(p.id): p for p in drafts}
    out, problems = [], []
    for r in d.get("posts") or []:
        p = by_id.get(str(r.get("id")))
        if p is None:
            problems.append(f"{r.get('id')}: not a draft on this card")
            continue
        patch: dict = {}
        if r.get("caption") is not None:
            if p.kind == "story":
                problems.append(f"{p.id}: a Story has no caption")
            else:
                patch["caption"] = r["caption"]
        if r.get("alt_texts") is not None:
            if len(r["alt_texts"]) != len(p.slides):
                problems.append(f"{p.id}: {len(r['alt_texts'])} alt texts for {len(p.slides)} slides")
            else:
                patch["alt_texts"] = r["alt_texts"]
        slides = []
        for s in r.get("slides") or []:
            i = s.get("index")
            fields = {k: v for k, v in (s.get("fields") or {}).items() if v is not None}
            if not isinstance(i, int) or not 0 <= i < len(p.slides):
                problems.append(f"{p.id}: slide index {i} is out of range (0–{len(p.slides) - 1})")
                continue
            spec = p.slides[i].get("spec") or {}
            editable = tuple(f for f in SLIDE_COPY.get(spec.get("type"), ()) if f != "source")
            bad = [f for f in fields if f not in editable]
            if not spec:
                problems.append(f"{p.id}: slide {i + 1} was uploaded, not made; its words cannot change")
            elif not fields:
                problems.append(f"{p.id}: slide {i + 1} has no words to change")
            elif bad:
                problems.append(f"{p.id}: slide {i + 1} can't change {', '.join(bad)} "
                                f"(its words are {', '.join(editable)})")
            else:
                slides.append({"index": i, "fields": fields})
        if slides:
            patch["slides"] = slides
        if patch:
            out.append({"id": str(p.id), **patch})
    return out, problems


def revise(session: Session, card: Card, d: dict, *, renderer=None) -> Card:
    """The revision applied (caption, alt text, a slide's words re-rendered
    from its spec; never an image or a new figure), then back to review."""
    from postdesk import maker

    session.refresh(card)
    if card.column != "Making" or card.closed_at is not None:
        return card
    drafts = posts.drafts(session, card.id)
    patches, problems = valid_revision(d, drafts)
    if not patches:
        why = (d.get("reply") or "").strip() or "no change it could make"
        return stuck(session, card, f"the revision could not fix this with words alone: {why}"
                     + (f" ({'; '.join(problems)})" if problems else ""), actor=MAKER)
    applied = 0
    for p in patches:
        try:
            maker.revise(session, uuid.UUID(p["id"]), caption=p.get("caption"), alt_texts=p.get("alt_texts"),
                         slides=p.get("slides"), renderer=renderer, by=MAKER)
            applied += 1
        except (maker.MakerError, queue.QueueError, LookupError) as e:
            session.rollback()
            problems.append(str(e))
    session.refresh(card)
    if not applied:
        return stuck(session, card, "the revision was refused: " + "; ".join(problems), actor=MAKER)
    text = f"Revised: {(d.get('reply') or '').strip()}"
    if problems:
        text += "\n\nNot applied: " + "; ".join(problems)
    cards.comment(session, card, MAKER, text[:4000], commit=False)
    set_column(session, card, "In review", actor=MAKER, reason="revised; the editor reads it again")
    jobs.enqueue(session, "review", card.id)
    return card
