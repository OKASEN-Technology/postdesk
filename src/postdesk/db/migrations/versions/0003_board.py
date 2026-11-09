"""M2, the board: card, card_comment, card_event, job; social_post.card_id
becomes a foreign key to card.

Revision ID: 0003_board
Revises: 0002_maker
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_board"
down_revision = "0002_maker"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "card",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("kind", sa.String, nullable=False, server_default="post"),
        sa.Column("column", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("body_md", sa.Text, nullable=False, server_default=""),
        sa.Column("source_ref", sa.String),
        sa.Column("fingerprint", sa.String, unique=True),
        sa.Column("facts", JSONB),
        sa.Column("brief_md", sa.Text),
        sa.Column("guidance", sa.Text),
        sa.Column("plan", JSONB, nullable=False, server_default="{}"),
        sa.Column("labels", JSONB, nullable=False, server_default="[]"),
        sa.Column("review_round", sa.Integer, nullable=False, server_default="0"),
        sa.Column("review", JSONB),
        sa.Column("stuck_reason", sa.Text),
        sa.Column("closed_at", TS),
        sa.Column("created_by", sa.String, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_card_column", "card", ["column"])
    op.create_index("ix_card_source_ref", "card", ["source_ref"])

    op.create_table(
        "card_comment",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("card_id", UUID, sa.ForeignKey("card.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author", sa.String, nullable=False),
        sa.Column("body_md", sa.Text, nullable=False, server_default=""),
        sa.Column("attachments", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_card_comment_card_id", "card_comment", ["card_id"])

    op.create_table(
        "card_event",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("card_id", UUID, sa.ForeignKey("card.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_column", sa.String),
        sa.Column("to_column", sa.String, nullable=False),
        sa.Column("actor", sa.String, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("refused", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("at", TS, nullable=False),
    )
    op.create_index("ix_card_event_card_id", "card_event", ["card_id"])
    op.create_index("ix_card_event_at", "card_event", ["at"])

    op.create_table(
        "job",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("card_id", UUID, sa.ForeignKey("card.id", ondelete="CASCADE")),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String, nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("run_after", TS, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
    )
    op.create_index("ix_job_card_id", "job", ["card_id"])
    op.create_index("ix_job_queue", "job", ["status", "run_after"])
    # One running job per card: the runner's concurrency rule, held by the database.
    op.create_index("uq_job_running_card", "job", ["card_id"], unique=True,
                    postgresql_where=sa.text("status = 'running'"))

    # Posts made before the board have card ids of nothing; clear any stray
    # value so the foreign key can be added.
    op.execute("UPDATE social_post SET card_id = NULL WHERE card_id IS NOT NULL")
    op.create_foreign_key("fk_social_post_card", "social_post", "card", ["card_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_social_post_card", "social_post", type_="foreignkey")
    for t in ("job", "card_event", "card_comment", "card"):
        op.drop_table(t)
