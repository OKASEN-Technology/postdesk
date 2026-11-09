"""M0, the scheduler: content_asset, image_permission, social_post, metric, setting.

Revision ID: 0001_scheduler
Revises:
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_scheduler"
down_revision = None
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "content_asset",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("filename", sa.String, nullable=False),
        sa.Column("content_type", sa.String, nullable=False),
        sa.Column("byte_size", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String, nullable=False, unique=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("uploaded_by", sa.String),
        sa.Column("storage", sa.String, nullable=False, server_default="local"),
        sa.Column("provenance", JSON),
    )
    op.create_table(
        "image_permission",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("holder", sa.String, nullable=False),
        sa.Column("holder_kind", sa.String, nullable=False),
        sa.Column("instagram", sa.String),
        sa.Column("domain", sa.String),
        sa.Column("subject", sa.String),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("credit", sa.String, nullable=False),
        sa.Column("tag", sa.String),
        sa.Column("evidence", sa.Text, nullable=False),
        sa.Column("granted_on", sa.Date, nullable=False),
        sa.Column("expires_on", sa.Date),
        sa.Column("revoked_at", TS),
        sa.Column("recorded_by", sa.String, nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_table(
        "social_post",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("channel", sa.String, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("caption", sa.Text, nullable=False),
        sa.Column("slides", JSON, nullable=False),
        sa.Column("media", JSON),
        sa.Column("card_id", UUID),
        sa.Column("source_ref", sa.String),
        sa.Column("publish_at", TS),
        sa.Column("approved_by", sa.String),
        sa.Column("approved_at", TS),
        sa.Column("published_at", TS),
        sa.Column("ig_media_id", sa.String),
        sa.Column("permalink", sa.String),
        sa.Column("error", sa.Text),
        sa.Column("insights", JSON),
        sa.Column("insights_at", TS),
        sa.Column("created_by", sa.String, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_social_post_status", "social_post", ["status"])
    op.create_index("ix_social_post_card_id", "social_post", ["card_id"])
    op.create_table(
        "metric",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("metric", sa.String, nullable=False),
        sa.Column("dimension", sa.String, nullable=False),
        sa.Column("value", sa.Numeric, nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("recorded_at", TS, nullable=False),
        sa.UniqueConstraint("day", "source", "metric", "dimension", name="uq_metric"),
    )
    op.create_index("ix_metric_day", "metric", ["day"])
    op.create_table(
        "setting",
        sa.Column("key", sa.String, primary_key=True),
        sa.Column("value", JSON),
        sa.Column("updated_at", TS, nullable=False),
    )


def downgrade() -> None:
    for t in ("setting", "metric", "social_post", "image_permission", "content_asset"):
        op.drop_table(t)
