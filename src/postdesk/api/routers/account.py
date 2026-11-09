"""The Instagram account behind the token, and the bits of config the web
UI needs (timezone, slots, publisher hours)."""

from fastapi import APIRouter, Depends, HTTPException

from postdesk import instagram as ig
from postdesk import storage
from postdesk.api.auth import require_auth
from postdesk.api.schemas import ClientConfig, WhoAmI
from postdesk.config import config, settings
from postdesk.instagram.setup import expiry, whoami

router = APIRouter(prefix="/api", tags=["account"], dependencies=[Depends(require_auth)])


@router.get("/config", response_model=ClientConfig)
def client_config() -> ClientConfig:
    return ClientConfig(
        handle=config.account.handle,
        timezone=config.account.timezone,
        feed_slot=config.schedule.feed_slot,
        story_slot=config.schedule.story_slot,
        publisher_hours=config.schedule.publisher_hours,
        max_hashtags=config.voice.max_hashtags,
        public_base_url=settings.public_base_url,
        storage=storage.backend(),
        config_path=str(config.path) if config.path else None,
    )


@router.get("/instagram/whoami", response_model=WhoAmI)
def instagram_whoami() -> WhoAmI:
    try:
        acct, info, used, total = whoami(ig.Graph())
    except ig.InstagramError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return WhoAmI(username=acct.username, ig_user_id=acct.ig_user_id, page_name=acct.page_name,
                  token_kind=acct.token_kind, token_expires=expiry(info), quota_used=used, quota_total=total)
