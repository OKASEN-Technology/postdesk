"""Alembic, driven from the package (`postdesk db upgrade`, container start,
the test suite) so no alembic.ini is needed."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def alembic_config(url: str) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str) -> None:
    command.downgrade(alembic_config(url), revision)


def current_and_head(engine: Engine) -> tuple[str | None, str | None]:
    """(what the database is at, what the code expects). Equal when the
    migrations are current."""
    script = ScriptDirectory.from_config(alembic_config(str(engine.url.render_as_string(hide_password=False))))
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    return current, script.get_current_head()
