"""One engine per process, built on first use from `settings.database_url`
(so tests and `postdesk init` can change it before anything connects)."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from postdesk.config import settings

_engine: Engine | None = None
_factory: sessionmaker | None = None


def engine() -> Engine:
    global _engine, _factory
    if _engine is None:
        _engine = create_engine(settings.database_url, pool_pre_ping=True)
        _factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Forget the engine, so the next session uses the current URL."""
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = _factory = None


def SessionLocal() -> Session:
    engine()
    assert _factory is not None
    return _factory()


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
