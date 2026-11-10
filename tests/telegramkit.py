"""
A fake Telegram Bot API (httpx.MockTransport, like conftest's FakeGraph):
it answers every method the bot uses, numbers the messages it "sends",
serves a photo for getFile, and records each call with its parameters
(JSON, or the fields of a multipart upload). Plus the updates Telegram
would send: a message, a reply, a photo, a button press.
"""

from __future__ import annotations

import io
import json
import re
from itertools import count

import httpx
from PIL import Image

from postdesk.telegram.api import Bot

TOKEN = "123:TEST"
CHAT = "4242"
OTHER = "9999"


def jpeg(colour=(180, 120, 60), size=(1080, 1350)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="JPEG")
    return buf.getvalue()


def _form(request: httpx.Request) -> tuple[dict, dict]:
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    fields, files = {}, {}
    for part in request.content.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, body = part.split(b"\r\n\r\n", 1)
        body = body.removesuffix(b"\r\n")
        m = re.search(rb'name="([^"]+)"', head)
        if not m:
            continue
        if b"filename=" in head:
            files[m.group(1).decode()] = body
        else:
            fields[m.group(1).decode()] = body.decode()
    for k in ("media", "reply_markup"):
        if k in fields:
            fields[k] = json.loads(fields[k])
    return fields, files


class FakeTelegram:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.updates: list[dict] = []
        self.ids = count(1000)
        self.photo = jpeg((40, 90, 160))
        self.fail: dict[str, str] = {}  # method -> description, once

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        path = request.url.path
        if path.startswith("/file/"):
            return httpx.Response(200, content=self.photo, headers={"content-type": "image/jpeg"})
        method = path.rsplit("/", 1)[1]
        assert f"/bot{TOKEN}/" in path
        if request.headers.get("content-type", "").startswith("multipart/"):
            params, files = _form(request)
            params["_files"] = sorted(files)
        else:
            params = json.loads(request.content or b"{}")
        self.calls.append((method, params))
        if method in self.fail:
            desc = self.fail.pop(method)
            return httpx.Response(400, json={"ok": False, "error_code": 400, "description": desc})
        if method == "getUpdates":
            ups, self.updates = self.updates, []
            return self._ok(ups)
        if method == "getMe":
            return self._ok({"id": 1, "is_bot": True, "username": "bakery_desk_bot"})
        if method == "getFile":
            return self._ok({"file_id": params["file_id"], "file_path": "photos/file_1.jpg"})
        if method in ("sendMessage", "sendPhoto"):
            return self._ok({"message_id": next(self.ids), "chat": {"id": int(params["chat_id"])},
                             "text": params.get("text")})
        if method == "sendMediaGroup":
            return self._ok([{"message_id": next(self.ids), "chat": {"id": int(params["chat_id"])}}
                             for _ in params["media"]])
        return self._ok(True)

    @staticmethod
    def _ok(result) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": result})

    # ------------------------------------------------------------ what was sent

    def of(self, method: str) -> list[dict]:
        return [p for m, p in self.calls if m == method]

    def texts(self, chat: str = CHAT) -> list[str]:
        return [p["text"] for p in self.of("sendMessage") if str(p["chat_id"]) == str(chat)]

    def last(self, chat: str = CHAT) -> dict:
        return [p for p in self.of("sendMessage") if str(p["chat_id"]) == str(chat)][-1]

    def buttons(self, params: dict) -> list[str]:
        markup = params.get("reply_markup") or {}
        return [b["callback_data"].split(":")[0] for row in markup.get("inline_keyboard") or [] for b in row
                if "callback_data" in b]

    def clear(self) -> None:
        self.calls.clear()


def bot(fake: FakeTelegram) -> Bot:
    return Bot(TOKEN, client=httpx.Client(transport=httpx.MockTransport(fake)))


_uid = count(1)
_mid = count(1)


def message(text: str, *, chat: str = CHAT, reply_to: int | None = None, **extra) -> dict:
    m = {"message_id": next(_mid), "chat": {"id": int(chat), "type": "private", "first_name": "Sam"},
         "date": 0, "text": text, **extra}
    if reply_to is not None:
        m["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": next(_uid), "message": m}


def photo(caption: str | None, *, chat: str = CHAT, reply_to: int | None = None) -> dict:
    u = message("", chat=chat, reply_to=reply_to)
    m = u["message"]
    m.pop("text")
    m["photo"] = [{"file_id": "small", "width": 90, "height": 90, "file_size": 900},
                  {"file_id": "big-photo-id", "width": 1080, "height": 1080, "file_size": 90000}]
    if caption is not None:
        m["caption"] = caption
    return u


def press(data: str, *, chat: str = CHAT, message_id: int = 1) -> dict:
    return {"update_id": next(_uid), "callback_query": {
        "id": f"cb{next(_uid)}", "data": data, "from": {"id": int(chat)},
        "message": {"message_id": message_id, "chat": {"id": int(chat)}}}}
