"""
`POST /api/telegram/webhook`: where Telegram posts updates when the bot runs
on a webhook (telegram/bot.py, a public https `PUBLIC_BASE_URL`). No login:
the request must carry the secret the bot registered with Telegram, and the
update then goes through the same checks as a polled one (only the paired
chat may act).
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from postdesk.config import settings
from postdesk.db.session import get_session

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request, session: Session = Depends(get_session),
                  secret: str | None = Header(None, alias="X-Telegram-Bot-Api-Secret-Token")) -> dict:
    from postdesk.telegram import bot, commands
    from postdesk.telegram.api import Bot

    if not settings.telegram_bot_token:
        raise HTTPException(status_code=404, detail="no bot")
    if not secret or not hmac.compare_digest(secret, bot.webhook_secret()):
        raise HTTPException(status_code=403, detail="bad secret")
    update = await request.json()
    try:
        commands.handle(session, Bot(), update)
    except Exception as e:  # Telegram retries a failed delivery; a bad update should not loop
        session.rollback()
        print(f"telegram: update {update.get('update_id')}: {type(e).__name__}: {e}")
    return {"ok": True}
