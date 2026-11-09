"""M1, the maker: agent_run.

Revision ID: 0002_maker
Revises: 0001_scheduler
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_maker"
down_revision = "0001_scheduler"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "agent_run",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("agent", sa.String, nullable=False),
        sa.Column("post_id", UUID),
        sa.Column("card_id", UUID),
        sa.Column("ref", sa.String),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("effort", sa.String),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
        sa.Column("status", sa.String, nullable=False, server_default="ok"),
        sa.Column("error", sa.Text),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS),
    )
    op.create_index("ix_agent_run_post_id", "agent_run", ["post_id"])
    op.create_index("ix_agent_run_card_id", "agent_run", ["card_id"])
    op.create_index("ix_agent_run_started_at", "agent_run", ["started_at"])


def downgrade() -> None:
    op.drop_table("agent_run")
