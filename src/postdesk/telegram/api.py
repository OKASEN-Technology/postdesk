"""The Telegram Bot API (https://core.telegram.org/bots/api) over httpx.
Only what the bot uses; every method returns the API's `result`."""

from __future__ import annotations

import json

import httpx

from postdesk.config import settings

BASE = "https://api.telegram.org"


class TelegramError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


def default_client() -> httpx.Client:
    return httpx.Client(timeout=60)


class Bot:
    def __init__(self, token: str | None = None, *, client: httpx.Client | None = None, base: str = BASE):
        self.token = token or settings.telegram_bot_token
        if not self.token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set in .env (from @BotFather: /newbot)")
        self.client = client or default_client()
        self.base = base.rstrip("/")

    def call(self, method: str, *, files: dict | None = None, http_timeout: float | None = None, **params):
        params = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base}/bot{self.token}/{method}"
        kw = {"timeout": http_timeout} if http_timeout else {}
        try:
            if files:
                data = {k: json.dumps(v) if isinstance(v, dict | list) else str(v) for k, v in params.items()}
                resp = self.client.post(url, data=data, files=files, **kw)
            else:
                resp = self.client.post(url, json=params, **kw)
        except httpx.HTTPError as e:
            raise TelegramError(f"Telegram {method}: {type(e).__name__}: {e}") from e
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if not body.get("ok"):
            desc = body.get("description") or resp.text[:200]
            retry = (body.get("parameters") or {}).get("retry_after")
            raise TelegramError(f"Telegram {method} failed ({resp.status_code}): {desc}", code=resp.status_code,
                                retry_after=retry)
        return body.get("result")

    # ------------------------------------------------------------ reading

    def get_me(self) -> dict:
        return self.call("getMe")

    def get_updates(self, offset: int | None, *, timeout: int = 20) -> list[dict]:
        """Long polling: waits up to `timeout` seconds for something new."""
        return self.call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message", "callback_query"],
                         http_timeout=timeout + 15) or []

    def get_file(self, file_id: str) -> bytes:
        info = self.call("getFile", file_id=file_id)
        try:
            resp = self.client.get(f"{self.base}/file/bot{self.token}/{info['file_path']}")
        except httpx.HTTPError as e:
            raise TelegramError(f"Telegram file download: {e}") from e
        if resp.status_code != 200:
            raise TelegramError(f"Telegram file download failed ({resp.status_code})")
        return resp.content

    # ------------------------------------------------------------ sending

    def send_message(self, chat_id: str, text: str, *, reply_markup: dict | None = None,
                     reply_to: int | None = None) -> dict:
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=reply_markup,
                         link_preview_options={"is_disabled": True},
                         reply_parameters={"message_id": reply_to, "allow_sending_without_reply": True}
                         if reply_to else None)

    def send_photo(self, chat_id: str, photo: bytes | str, *, caption: str | None = None,
                   reply_markup: dict | None = None) -> dict:
        if isinstance(photo, bytes):
            return self.call("sendPhoto", chat_id=chat_id, caption=caption, parse_mode="HTML",
                             reply_markup=reply_markup, files={"photo": ("photo.jpg", photo, "image/jpeg")})
        return self.call("sendPhoto", chat_id=chat_id, photo=photo, caption=caption, parse_mode="HTML",
                         reply_markup=reply_markup)

    def send_media_group(self, chat_id: str, photos: list[tuple[bytes | str, str | None]]) -> list[dict]:
        """2 to 10 photos as one album; each (bytes or URL, caption)."""
        media, files = [], {}
        for i, (photo, caption) in enumerate(photos):
            item = {"type": "photo", "caption": caption, "parse_mode": "HTML"} if caption else {"type": "photo"}
            if isinstance(photo, bytes):
                files[f"p{i}"] = (f"p{i}.jpg", photo, "image/jpeg")
                item["media"] = f"attach://p{i}"
            else:
                item["media"] = photo
            media.append(item)
        return self.call("sendMediaGroup", chat_id=chat_id, media=media, files=files or None)

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=(text or None) and text[:200])

    def clear_buttons(self, chat_id: str, message_id: int) -> None:
        self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                  reply_markup={"inline_keyboard": []})

    def set_webhook(self, url: str, secret: str) -> None:
        self.call("setWebhook", url=url, secret_token=secret, allowed_updates=["message", "callback_query"])

    def delete_webhook(self) -> None:
        self.call("deleteWebhook")
