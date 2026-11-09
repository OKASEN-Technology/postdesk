"""The FastAPI app: the API under /api, /media, and the built web UI at /."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from postdesk import __version__
from postdesk.api.routers import account, act, assets, auth, cards, media, posts, settings, telegram

STATIC = Path(__file__).resolve().parent / "static"

UNBUILT = """<!doctype html><meta charset="utf-8"><title>postdesk</title>
<body style="font: 16px/1.5 system-ui; max-width: 40rem; margin: 4rem auto; padding: 0 1rem">
<h1>postdesk</h1>
<p>The API is running, but the web UI has not been built into this install.</p>
<p>From a checkout: <code>cd web &amp;&amp; npm ci &amp;&amp; npm run build</code>, then restart.
The Docker image has it built in.</p>
<p>The API is at <a href="/api/docs">/api/docs</a>.</p>
</body>"""


def create_app(*, scheduler: bool = False, worker: bool = False, telegram_bot: bool = False) -> FastAPI:
    """`scheduler=True` (what `postdesk serve` passes) runs the publish loop
    in a background thread for the life of the app; `worker=True` runs the
    agents' job runner (and the scout and the board sync) beside it;
    `telegram_bot=True` runs the Telegram bot when TELEGRAM_BOT_TOKEN is set."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        threads = []
        try:
            from postdesk import store
            from postdesk.db.session import SessionLocal

            with SessionLocal() as session:
                store.refresh_config(session, log=print)
        except Exception as e:  # no database yet: the settings screen's edits wait for it
            print(f"settings: not loaded ({type(e).__name__})")
        if telegram_bot:
            from postdesk.config import settings as env
            from postdesk.telegram.bot import loop as bot_loop

            if env.telegram_bot_token:
                threads.append(threading.Thread(target=bot_loop, args=(stop,), name="postdesk-telegram",
                                                daemon=True))
        if scheduler:
            from postdesk.scheduler import loop

            threads.append(threading.Thread(target=loop, args=(stop,), name="postdesk-scheduler", daemon=True))
        if worker:
            from postdesk.jobs import loop as work

            threads.append(threading.Thread(target=work, args=(stop,), name="postdesk-worker", daemon=True))
        for t in threads:
            t.start()
        yield
        stop.set()
        for t in threads:
            t.join(timeout=5)

    app = FastAPI(title="postdesk", version=__version__, lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.include_router(auth.router)
    app.include_router(posts.router)
    app.include_router(assets.router)
    app.include_router(account.router)
    app.include_router(media.router)
    app.include_router(cards.router)
    app.include_router(settings.router)
    app.include_router(telegram.router)
    app.include_router(act.router)

    @app.get("/api/health", include_in_schema=False)
    def health() -> dict:
        return {"ok": True, "version": __version__}

    index = STATIC / "index.html"
    if (STATIC / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request):
        # The web app owns every other path (it routes client-side); a file
        # that exists in static/ (favicon, manifest) is served as is.
        if path and (STATIC / path).is_file():
            return FileResponse(STATIC / path)
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse(UNBUILT, status_code=200)

    return app


app = create_app()
