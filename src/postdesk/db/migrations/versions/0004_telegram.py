"""M3, Telegram and notifiers: `notice` (the outbox every channel sends
from) and `telegram_message` (which card a message in the chat is about).

Revision ID: 0004_telegram
Revises: 0003_board
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_telegram"
down_revision = "0003_board"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "notice",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("channel", sa.String, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("card_id", UUID, sa.ForeignKey("card.id", ondelete="CASCADE")),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("sent_at", TS),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_notice_pending", "notice", ["channel", "sent_at", "created_at"])
    op.create_index("ix_notice_card_id", "notice", ["card_id"])

    op.create_table(
        "telegram_message",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("chat_id", sa.String, nullable=False),
        sa.Column("message_id", sa.BigInteger, nullable=False),
        sa.Column("card_id", UUID, sa.ForeignKey("card.id", ondelete="CASCADE")),
        sa.Column("purpose", sa.String, nullable=False, server_default="card"),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_telegram_message"),
    )
    op.create_index("ix_telegram_message_card_id", "telegram_message", ["card_id"])


def downgrade() -> None:
    op.drop_table("telegram_message")
    op.drop_table("notice")
