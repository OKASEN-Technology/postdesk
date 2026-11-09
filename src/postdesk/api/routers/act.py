"""
`/act/<token>`: a gate action from a one-way notice's signed link
(notify/links.py). GET shows what the link will do and a button; only the
POST acts, so a link preview fetching it changes nothing. The move is the
admin's (`actor = link`) and goes through the same gate code as the board.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from postdesk import queue
from postdesk.board import cards, posts, transitions
from postdesk.board.cards import BoardError
from postdesk.db.session import get_session
from postdesk.notify import links

router = APIRouter(prefix="/act", tags=["act"], include_in_schema=False)

PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>postdesk</title><body style="font: 16px/1.5 system-ui; max-width: 32rem; margin: 3rem auto; padding: 0 1rem">
<h1 style="font-size: 1.25rem">{title}</h1><p>{text}</p>{form}
<p style="color: #666; font-size: .875rem"><a href="{card}">Open the card</a></p></body>"""


def _page(title: str, text: str, *, form: str = "", card: str = "/", status: int = 200) -> HTMLResponse:
    return HTMLResponse(PAGE.format(title=html.escape(title), text=text, form=form, card=html.escape(card)),
                        status_code=status)


def _load(session: Session, token: str):
    card_id, action = links.verify(session, token)
    return cards.get(session, card_id), action


@router.get("/{token}")
def confirm(token: str, session: Session = Depends(get_session)) -> HTMLResponse:
    try:
        card, action = _load(session, token)
    except (links.LinkError, LookupError) as e:
        return _page("This link does not work", html.escape(str(e)), status=400)
    if action == "approve":
        what, label = "Approve the brief; the maker starts on it.", "Approve"
    else:
        what = f"Schedule it for {html.escape(transitions._when(posts.recommended_time(session, card)))}."
        label = "Schedule"
    form = (f'<form method="post"><button style="font: inherit; padding: .5rem 1.25rem" type="submit">{label}'
            "</button></form>")
    return _page(card.title, f"Now in {html.escape(card.column)}. {what}", form=form, card=f"/cards/{card.id}")


@router.post("/{token}")
def act(token: str, session: Session = Depends(get_session)) -> HTMLResponse:
    try:
        card, action = _load(session, token)
        if action == "approve":
            transitions.approve(session, card, actor=links.ACTOR, note="approved from a notifier link")
            done = "Approved. The maker is on it." if card.column == "Approved" else \
                "It had no brief yet; the partner is writing it."
        else:
            transitions.schedule(session, card, actor=links.ACTOR)
            at = [p.publish_at for p in posts.for_card(session, card.id) if p.status == "approved"]
            done = f"Scheduled for {html.escape(transitions._when(min(at)) if at else 'its time')}."
    except (links.LinkError, LookupError) as e:
        return _page("This link does not work", html.escape(str(e)), status=400)
    except (BoardError, queue.QueueError) as e:
        session.rollback()
        return _page("Not done", html.escape(str(e)), status=409)
    return _page(card.title, done, card=f"/cards/{card.id}")
