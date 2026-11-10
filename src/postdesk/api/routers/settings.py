"""
The settings screen's API: what the account is set to, and the edits to it
(`GET/PUT /api/settings`, kept in the `setting` table and laid over
postdesk.toml, store.py); Telegram pairing; the agents' pause; the spend;
the one-way notifiers' test; the permissions on file. Secrets are never
read or written here: they stay in `.env`.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from postdesk import control, images, llm, notify, store
from postdesk.api.auth import require_auth
from postdesk.api.schemas import PermissionIn, PermissionOut, StatusOut, TelegramLink
from postdesk.config import EDITABLE, config, settings
from postdesk.db.models import ImagePermission
from postdesk.db.session import get_session
from postdesk.images import rights
from postdesk.telegram import pairing

router = APIRouter(prefix="/api", tags=["settings"], dependencies=[Depends(require_auth)])

EFFORTS = ["low", "medium", "high"]


def _values() -> dict:
    from postdesk.maker.words import voice_text

    out: dict = {}
    for section, keys in EDITABLE.items():
        value = getattr(config, section)
        if keys is None:
            out[section] = value
        else:
            out[section] = {k: (v.model_dump() if hasattr(v, "model_dump") else v)
                            for k, v in ((k, getattr(value, k)) for k in keys)}
    out["voice"]["file"] = config.voice.file
    out["voice"]["text"] = voice_text() if not config.voice.text else config.voice.text
    out["voice"]["from_file"] = not config.voice.text
    out["telegram"]["mode"] = config.telegram.mode
    return out


def _telegram(session: Session) -> dict:
    from postdesk.telegram import bot

    return {"configured": bool(settings.telegram_bot_token), "linked": pairing.linked(session),
            "mode": bot.mode() if settings.telegram_bot_token else None}


def _notifiers() -> list[dict]:
    ready = notify.configured()
    out = []
    for name in notify.ONE_WAY:
        opts = (config.notify or {}).get(name)
        out.append({"name": name, "on": opts is not None and (opts or {}).get("enabled", True) is not False,
                    "ready": name in ready, "events": (opts or {}).get("events") or list(notify.KINDS)})
    return out


@router.get("/settings")
def get_settings(session: Session = Depends(get_session)) -> dict:
    from postdesk.maker import plans
    from postdesk.sources import available

    store.refresh_config(session)
    s = control.spend(session)
    overridden = {k: (sorted(v) if isinstance(v, dict) else True) for k, v in store.overrides(session).items()}
    return {
        "values": _values(),
        "overridden": overridden,
        "config_path": str(config.path) if config.path else None,
        "choices": {"models": sorted([*llm.PRICES, *llm.OPENAI_PRICES]), "efforts": EFFORTS,
                    "image_sources": sorted(images.available()),
                    "source_types": sorted(available()), "templates": sorted(plans.available())},
        "telegram": _telegram(session),
        "spend": {"month_usd": round(s.month_usd, 4), "ceiling_usd": s.ceiling_usd, "over": s.over},
        "paused": control.paused(session),
        "notifiers": _notifiers(),
    }


@router.put("/settings")
def put_settings(body: dict, session: Session = Depends(get_session)) -> dict:
    """Change some settings: `{section: {key: value}}`; a null value goes
    back to postdesk.toml's. Only the keys the screen shows can change."""
    try:
        store.save_overrides(session, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return get_settings(session)


@router.post("/settings/brand-assets/test")
def test_brand_assets(body: dict) -> dict:
    from postdesk import brand_assets
    from postdesk.config import Voice

    try:
        url = Voice(brand_assets_url=body.get("url")).brand_assets_url
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not url:
        raise HTTPException(status_code=400, detail="Enter a public brand assets URL first")
    assets, error = brand_assets.load(config_url=url)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {"count": len(assets)}


@router.get("/status", response_model=StatusOut)
def status(session: Session = Depends(get_session)) -> StatusOut:
    store.refresh_config(session)
    s = control.spend(session)
    linked = pairing.linked(session)
    return StatusOut(paused=control.paused(session), month_usd=round(s.month_usd, 4), ceiling_usd=s.ceiling_usd,
                     over=s.over, stopped=control.stopped(session),
                     telegram="off" if not settings.telegram_bot_token else ("paired" if linked else "unpaired"))


@router.post("/agents/pause", response_model=StatusOut)
def pause(actor: str = Depends(require_auth), session: Session = Depends(get_session)) -> StatusOut:
    control.pause(session, by=actor)
    return status(session)


@router.post("/agents/resume", response_model=StatusOut)
def resume(actor: str = Depends(require_auth), session: Session = Depends(get_session)) -> StatusOut:
    control.resume(session, by=actor)
    return status(session)


# ---------------------------------------------------------------- telegram


@router.post("/telegram/link")
def telegram_link(body: TelegramLink, actor: str = Depends(require_auth), session: Session = Depends(get_session)
                  ) -> dict:
    """Pair the chat that was given this code by /start."""
    try:
        linked = pairing.link(session, body.code, by=actor)
    except pairing.PairingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _hello(linked["chat_id"])
    return _telegram(session)


def _hello(chat_id: str) -> None:
    from postdesk.telegram.api import Bot, TelegramError
    from postdesk.telegram.commands import HELP

    if not settings.telegram_bot_token:
        return
    try:
        Bot().send_message(chat_id, "Paired. This chat runs postdesk from now on.\n\n" + HELP)
    except TelegramError:
        pass  # pairing stands; the test button shows what is wrong


@router.post("/telegram/unlink")
def telegram_unlink(session: Session = Depends(get_session)) -> dict:
    pairing.unlink(session)
    return _telegram(session)


@router.post("/telegram/test")
def telegram_test(session: Session = Depends(get_session)) -> dict:
    from postdesk.telegram.api import Bot, TelegramError

    linked = pairing.linked(session)
    if linked is None:
        raise HTTPException(status_code=409, detail="no chat is paired yet: send /start to the bot for a code")
    try:
        Bot().send_message(str(linked["chat_id"]), "A test from postdesk. This is the chat it talks to.")
    except TelegramError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"ok": True}


@router.post("/notify/{name}/test")
def notify_test(name: str) -> dict:
    try:
        notify.test(name)
    except notify.NotifyError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{name}: {e}") from e
    return {"ok": True}


# ---------------------------------------------------------------- permissions


def permission_out(p: ImagePermission) -> PermissionOut:
    return PermissionOut(id=str(p.id), holder=p.holder, holder_kind=p.holder_kind, instagram=p.instagram,
                         domain=p.domain, subject=p.subject, scope=p.scope, credit=p.credit, tag=p.tag,
                         evidence=p.evidence, granted_on=p.granted_on.isoformat(),
                         expires_on=p.expires_on.isoformat() if p.expires_on else None, revoked_at=p.revoked_at,
                         recorded_by=p.recorded_by, active=rights.active(p))


@router.get("/permissions", response_model=list[PermissionOut])
def list_permissions(revoked: bool = False, session: Session = Depends(get_session)) -> list[PermissionOut]:
    return [permission_out(p) for p in rights.listed(session, include_revoked=revoked)]


@router.post("/permissions", response_model=PermissionOut)
def add_permission(body: PermissionIn, actor: str = Depends(require_auth), session: Session = Depends(get_session)
                   ) -> PermissionOut:
    """Record a rights holder's yes (a person does this, never an agent)."""
    try:
        expires = date.fromisoformat(body.expires_on) if body.expires_on else None
        p = rights.add(session, holder=body.holder, holder_kind=body.holder_kind, scope=body.scope,
                       credit=body.credit, evidence=body.evidence, recorded_by=actor, instagram=body.instagram,
                       domain=body.domain, subject=body.subject, tag=body.tag, expires_on=expires)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return permission_out(p)


@router.delete("/permissions/{permission_id}", response_model=PermissionOut)
def revoke_permission(permission_id: uuid.UUID, session: Session = Depends(get_session)) -> PermissionOut:
    try:
        return permission_out(rights.revoke(session, permission_id))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

# Connection checks do not expose credentials. Configured means present, not verified.
@router.get("/connections")
def connections(session: Session = Depends(get_session)) -> dict:
    store.refresh_config(session)
    selected = [config.llm.partner.model, config.llm.editor.model, config.llm.maker.model, config.llm.pick.model]
    openai_selected = any(m.startswith(("gpt-", "o1", "o3", "o4")) for m in selected)
    claude_selected = any(not m.startswith(("gpt-", "o1", "o3", "o4")) for m in selected)
    return {
        "openai": {"configured": bool(settings.openai_api_key), "selected": openai_selected},
        "anthropic": {"configured": bool(settings.anthropic_api_key), "selected": claude_selected},
        "instagram": {"configured": bool(settings.instagram_access_token or
                                          (settings.instagram_page_token and settings.instagram_user_id))},
        "telegram": {"configured": bool(settings.telegram_bot_token), "paired": bool(pairing.linked(session))},
        "storage": {"configured": bool(settings.s3_enabled() or settings.public_base_url),
                    "kind": "S3" if settings.s3_enabled() else "local"},
        "renderer": {"configured": bool(settings.renderer_url)},
        "credential_storage": bool(settings.postdesk_credentials_key),
    }


@router.post("/connections/{name}/test")
def test_connection(name: str, session: Session = Depends(get_session)) -> dict:
    from postdesk import credentials, doctor

    store.refresh_config(session)
    if name in credentials.FIELDS:
        key = getattr(settings, credentials.FIELDS[name])
        if not key:
            raise HTTPException(status_code=409, detail="No API key is configured for this provider.")
        try:
            credentials.validate(name, key)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "detail": "The provider accepted the key. Model access and billing are not checked."}
    checks = {"instagram": doctor.check_instagram, "storage": doctor.check_storage,
              "renderer": doctor.check_renderer, "telegram": doctor.check_telegram}
    if name not in checks:
        raise HTTPException(status_code=404, detail="Unknown connection.")
    try:
        result = checks[name]()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="The connection test failed. Check the service configuration.") \
            from exc
    # Exception details can include credentials or signed URLs; do not return them.
    if not result.ok:
        return {"ok": False, "detail": "Test failed. Check this service's configuration and try again."}
    if name == "instagram":
        return {"ok": True, "detail": result.detail}
    return {"ok": True, "detail": "Test passed."}


@router.put("/connections/{name}/key")
def save_connection_key(name: str, body: dict, session: Session = Depends(get_session)) -> dict:
    from postdesk import credentials

    key = body.get("key")
    if not isinstance(key, str):
        raise HTTPException(status_code=400, detail="Enter an API key.")
    try:
        credentials.save(session, name, key.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}

@router.get("/connections/x")
def x_connection(session: Session = Depends(get_session)) -> dict:
    from postdesk.x import credentials as x_credentials

    try:
        return {"configured": x_credentials.read(session) is not None,
                "credential_storage": bool(settings.postdesk_credentials_key)}
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/connections/x")
def save_x_connection(body: dict, session: Session = Depends(get_session)) -> dict:
    from postdesk.x import credentials as x_credentials
    from postdesk.x.client import XError

    try:
        return {"account": x_credentials.save(session, body)}
    except (ValueError, XError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/connections/x/verify")
def verify_x_connection(session: Session = Depends(get_session)) -> dict:
    from postdesk.x import credentials as x_credentials
    from postdesk.x.client import XClient, XError

    try:
        saved = x_credentials.read(session)
        if saved is None:
            raise HTTPException(status_code=409, detail="Connect X first.")
        return {"account": XClient(saved).whoami()}
    except (ValueError, XError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
