"""
The publisher's heartbeat: publish what is due, then collect insights. Run
as `postdesk cron` (one pass, for a system cron or a hosted cron job) or as
the loop inside `postdesk serve`, every 15 minutes within
`[schedule].publisher_hours`.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from postdesk import instagram as ig
from postdesk import queue
from postdesk.db.session import SessionLocal
from postdesk.instagram import insights
from postdesk.queue.times import next_publisher_window, now_utc, within_publisher_hours

INTERVAL_S = 15 * 60


@dataclass
class RunResult:
    published: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped_hours: bool = False
    insights: insights.Result | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return not self.failed and self.error is None and (self.insights is None or self.insights.error is None)


def run_once(*, force: bool = False, log=print, graph: ig.Graph | None = None) -> RunResult:
    """One pass. Outside publisher hours nothing is published (unless
    `force`); insights are collected either way. One step failing does not
    skip the next."""
    result = RunResult()
    graph = graph or ig.Graph()
    with SessionLocal() as session:
        if not force and not within_publisher_hours():
            result.skipped_hours = True
            if queue.due(session):
                log(f"publish: outside publisher hours; next window opens {next_publisher_window():%Y-%m-%d %H:%M} UTC")
        else:
            try:
                attempted = queue.publish_due(session, graph, log=log)
            except ig.InstagramError as e:
                result.error = str(e)
                log(f"publish: {e}")
            except Exception as e:
                session.rollback()
                log(f"Instagram publisher: {type(e).__name__}")
                result.error = "Instagram publisher failed. Check the service logs."
            else:
                result.published = [str(p.id) for p in attempted if p.status == "published"]
                result.failed = [str(p.id) for p in attempted if p.status == "failed"]
                if not attempted:
                    log("publish: nothing due")
            try:
                from postdesk.x import publisher as x_publisher

                x_attempted = x_publisher.publish_due(session, log=log)
                result.published.extend(str(p.id) for p in x_attempted if p.status == "published")
                result.failed.extend(str(p.id) for p in x_attempted if p.status == "failed")
            except Exception as e:
                log(f"X publisher: {type(e).__name__}")
                result.error = result.error or "X publisher failed. Check the service logs."
            try:
                from postdesk.facebook import publisher as facebook_publisher

                fb_attempted = facebook_publisher.publish_due(session, log=log, graph=graph)
                result.published.extend(str(p.id) for p in fb_attempted if p.status == "published")
                result.failed.extend(str(p.id) for p in fb_attempted if p.status == "failed")
            except Exception as e:
                session.rollback()
                log(f"Facebook publisher: {type(e).__name__}")
                result.error = result.error or "Facebook publisher failed. Check the service logs."
        try:
            result.insights = insights.collect(session, graph, log=log)
        except Exception as e:
            result.insights = insights.Result(error=str(e))
            log(f"insights: {e}")
    return result


def loop(stop: threading.Event, *, interval: float = INTERVAL_S, log=print) -> None:
    """`postdesk serve`'s background loop. Runs straight away, then every
    `interval` seconds until `stop` is set."""
    log(f"scheduler: every {int(interval // 60)} minutes within publisher hours")
    while not stop.is_set():
        started = time.monotonic()
        try:
            run_once(log=lambda m: log(f"[{now_utc():%H:%M}] {m}"))
        except Exception as e:
            log(f"scheduler: {e}")
        stop.wait(max(0.0, interval - (time.monotonic() - started)))
