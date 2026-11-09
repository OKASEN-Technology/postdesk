from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["image", "carousel", "story", "reel"]
Status = Literal["draft", "approved", "publishing", "published", "failed", "rejected"]


class Slide(BaseModel):
    asset_id: str
    alt_text: str = ""
    url: str | None = None  # filled on read: the /media URL
    # A made slide's render spec, and the words on it a PATCH may change
    # (maker.SLIDE_COPY). Both None for an uploaded slide.
    spec: dict | None = None
    words: dict | None = None


class SlidePatch(BaseModel):
    """New words for one made slide: `fields` is copy field -> text; an
    empty string clears the field. The slide is re-rendered."""

    index: int = Field(ge=0)
    fields: dict[str, str]


class PostItem(BaseModel):
    id: str
    channel: str
    kind: Kind
    status: Status
    title: str
    caption: str
    slides: list[Slide]
    card_id: str | None
    source_ref: str | None
    publish_at: datetime | None
    approved_by: str | None
    approved_at: datetime | None
    published_at: datetime | None
    permalink: str | None
    error: str | None
    insights: dict | None
    insights_at: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class PostRow(BaseModel):
    """One line of the queue list: no caption or alt texts (`GET /{id}` has
    the full post)."""

    id: str
    channel: str
    kind: Kind
    status: Status
    title: str
    first_slide_url: str | None
    slide_count: int
    card_id: str | None
    source_ref: str | None
    publish_at: datetime | None
    published_at: datetime | None
    error_excerpt: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class PostCreate(BaseModel):
    kind: Kind
    title: str
    caption: str = ""
    slides: list[Slide] = Field(min_length=1, max_length=20)
    source_ref: str | None = None
    created_by: str = "admin"
    publish_at: datetime | None = None


class PostUpdate(BaseModel):
    title: str | None = None
    caption: str | None = None
    alt_texts: list[str] | None = None
    slides: list[SlidePatch] | None = None


class PostApprove(BaseModel):
    publish_at: datetime
    approved_by: str = "admin"


class CaptionLint(BaseModel):
    errors: list[str]
    warnings: list[str]
    hashtags: int


class AssetOut(BaseModel):
    id: str
    url: str
    public_url: str
    content_type: str
    byte_size: int
    width: int
    height: int
    storage: str


class Password(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class AuthStatus(BaseModel):
    configured: bool
    authenticated: bool


class ClientConfig(BaseModel):
    handle: str
    timezone: str
    feed_slot: str
    story_slot: str
    publisher_hours: list[str]
    max_hashtags: int
    public_base_url: str
    storage: str
    config_path: str | None


class WhoAmI(BaseModel):
    username: str
    ig_user_id: str
    page_name: str | None
    token_kind: str
    token_expires: str
    quota_used: int
    quota_total: int


# ---------------------------------------------------------------- the board


class CardCreate(BaseModel):
    """An idea for the board. `column` Inbox waits for you; Planned starts
    the partner. `link` alone fetches the page (the url source)."""

    title: str = ""
    body_md: str = ""
    link: str | None = None
    kind: Literal["post", "story"] = "post"
    column: Literal["Inbox", "Planned"] = "Inbox"


class ManualSource(BaseModel):
    """`POST /api/sources/manual`: a title and text, or just a link."""

    title: str = ""
    text: str = ""
    link: str | None = None
    template: str | None = None


class CardMove(BaseModel):
    column: str
    note: str | None = None
    # For Scheduled: "tomorrow 18:00", "fri 7:30", an ISO time; empty for the recommended time.
    when: str | None = None


class CommentIn(BaseModel):
    body_md: str = Field(min_length=1)


class CardRow(BaseModel):
    id: str
    kind: str
    column: str
    title: str
    source_ref: str | None
    thumbnail_url: str | None
    stuck_reason: str | None
    verdict: str | None
    busy: str | None  # the agent working on it now, or queued
    closed: bool
    created_by: str
    created_at: datetime
    updated_at: datetime


class CommentOut(BaseModel):
    id: str
    author: str
    body_md: str
    attachments: list[dict]
    created_at: datetime


class EventOut(BaseModel):
    id: str
    from_column: str | None
    to_column: str
    actor: str
    reason: str | None
    refused: bool
    at: datetime


class DraftOut(BaseModel):
    id: str
    kind: str
    status: str
    title: str
    caption: str
    slides: list[Slide]
    publish_at: datetime | None
    permalink: str | None
    error: str | None


class RunOut(BaseModel):
    id: str
    agent: str
    card_id: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    status: str
    error: str | None
    started_at: datetime


class JobOut(BaseModel):
    id: str
    kind: str
    card_id: str | None
    status: str
    attempts: int
    run_after: datetime
    error: str | None


class CardDetail(CardRow):
    body_md: str
    facts: dict | None
    brief_md: str | None
    guidance: str | None
    plan: dict
    images: list[dict]
    review: dict | None
    review_round: int
    thread: list[CommentOut]
    events: list[EventOut]
    drafts: list[DraftOut]
    runs: list[RunOut]
    spend_usd: float
    jobs: list[JobOut]
    recommended_at: datetime | None
    gates: list[str]


class RunsOut(BaseModel):
    runs: list[RunOut]
    month_usd: float
    ceiling_usd: float


class ScanOut(BaseModel):
    filed: list[str]
    seen: int
    deferred: int
    errors: list[str]


class SourceOut(BaseModel):
    key: str
    type: str
    options: dict


# ---------------------------------------------------------------- settings (M3)


class TelegramLink(BaseModel):
    code: str


class PermissionIn(BaseModel):
    holder: str
    holder_kind: Literal["person", "organisation", "photographer", "other"] = "person"
    instagram: str | None = None
    domain: str | None = None
    subject: str | None = None
    scope: str
    credit: str
    tag: str | None = None
    evidence: str
    expires_on: str | None = None  # YYYY-MM-DD


class PermissionOut(BaseModel):
    id: str
    holder: str
    holder_kind: str
    instagram: str | None
    domain: str | None
    subject: str | None
    scope: str
    credit: str
    tag: str | None
    evidence: str
    granted_on: str
    expires_on: str | None
    revoked_at: datetime | None
    recorded_by: str
    active: bool


class LookupIn(BaseModel):
    source: Literal["page", "wikimedia", "business_discovery"]
    subject: str = "hero"
    query: str = ""


class StatusOut(BaseModel):
    """What the header shows on every page: are the agents running."""

    paused: dict | None
    month_usd: float
    ceiling_usd: float
    over: bool
    stopped: str | None
    telegram: Literal["off", "unpaired", "paired"]
