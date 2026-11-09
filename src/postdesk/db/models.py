"""
The tables (docs/roadmap.md): M0's queue and storage, M1's `agent_run`.
Later milestones add `card`, `card_comment`, `card_event` and `job`.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from postdesk.db.base import Base, JSONType


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class ContentAsset(Base):
    """A stored file: a slide image, later a video. Content-addressed by
    `sha256`, so the same bytes uploaded twice reuse one row. `provenance`
    says where a picture came from and on what terms; every image that goes
    out carries one (README "What it will never do")."""

    __tablename__ = "content_asset"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)  # "image/jpeg", ...
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uploaded_by: Mapped[str | None] = mapped_column(String)
    # "local" (the app's disk, served at /media/<id>) | "s3" (the bucket).
    storage: Mapped[str] = mapped_column(String, nullable=False, default="local", server_default="local")
    # {source, permalink, licence, licence_url, author, credit, permission_id,
    # generated, derived_from, retrieved_at}. For a hand-made slide:
    # {"source": "upload", "credit": None, ...}.
    provenance: Mapped[dict | None] = mapped_column(JSONType)


class ImagePermission(Base):
    """A standing yes from whoever holds an image's rights: a person, a
    gallery, a brand, a photographer. Recorded by a person, never by an
    agent. `subject` narrows it to one subject's images; null covers
    everything the holder has."""

    __tablename__ = "image_permission"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    holder: Mapped[str] = mapped_column(String, nullable=False)
    holder_kind: Mapped[str] = mapped_column(String, nullable=False)  # person | organisation | photographer | other
    instagram: Mapped[str | None] = mapped_column(String)  # their account, no "@"
    domain: Mapped[str | None] = mapped_column(String)
    subject: Mapped[str | None] = mapped_column(String)
    scope: Mapped[str] = mapped_column(Text, nullable=False)  # what they agreed to
    credit: Mapped[str] = mapped_column(String, nullable=False)  # on the image
    tag: Mapped[str | None] = mapped_column(String)  # the caption's credit, "@handle"
    evidence: Mapped[str] = mapped_column(Text, nullable=False)  # where the yes is
    granted_on: Mapped[date] = mapped_column(Date, nullable=False)
    expires_on: Mapped[date | None] = mapped_column(Date)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SocialPost(Base):
    """One post or Story in the queue (queue/queue.py). `status`: draft ->
    approved (a person set `publish_at`) -> publishing -> published | failed;
    or rejected. Only `approved` rows whose time has passed are published,
    so nothing reaches Instagram without a person approving it."""

    __tablename__ = "social_post"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    channel: Mapped[str] = mapped_column(String, nullable=False, default="instagram")
    kind: Mapped[str] = mapped_column(String, nullable=False)  # image | carousel | story | reel
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft", index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)  # internal label, never posted
    caption: Mapped[str] = mapped_column(Text, nullable=False, default="")
    slides: Mapped[list] = mapped_column(JSONType, nullable=False)  # [{"asset_id", "alt_text", "spec"?}]
    media: Mapped[dict | None] = mapped_column(JSONType)  # a reel's video record (not in M0)
    # The card this post belongs to (M2). Null for posts made by hand.
    card_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    source_ref: Mapped[str | None] = mapped_column(String)  # "rss:<guid>", "manual:intro", ...
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ig_media_id: Mapped[str | None] = mapped_column(String)
    permalink: Mapped[str | None] = mapped_column(String)
    error: Mapped[str | None] = mapped_column(Text)
    insights: Mapped[dict | None] = mapped_column(JSONType)  # latest per-post numbers (instagram/insights.py)
    insights_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Metric(Base):
    """One daily number: `(day, source, metric, dimension) -> value`. The
    insights collector upserts, so re-running a day replaces its numbers."""

    __tablename__ = "metric"
    __table_args__ = (UniqueConstraint("day", "source", "metric", "dimension", name="uq_metric"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)  # "instagram"
    metric: Mapped[str] = mapped_column(String, nullable=False)
    dimension: Mapped[str] = mapped_column(String, nullable=False, default="")
    value: Mapped[float] = mapped_column(Numeric, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Setting(Base):
    """key -> json: what the wizard and the settings screen write (the admin
    password's hash, later the theme and model choices). Secrets from `.env`
    never go here."""

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONType)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRun(Base):
    """One Claude call (llm.py): which agent, for which post or card, the
    model, tokens, cost in USD and how it ended. `postdesk.llm` sums a
    month's `cost_usd` against `[llm].monthly_usd` before every call."""

    __tablename__ = "agent_run"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent: Mapped[str] = mapped_column(String, nullable=False)  # maker | partner | editor | ...
    post_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    card_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    ref: Mapped[str | None] = mapped_column(String)  # the source ref the run was about
    model: Mapped[str] = mapped_column(String, nullable=False)
    effort: Mapped[str | None] = mapped_column(String)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6))  # None for a model with no price on file
    status: Mapped[str] = mapped_column(String, nullable=False, default="ok")  # ok | failed | refused
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
