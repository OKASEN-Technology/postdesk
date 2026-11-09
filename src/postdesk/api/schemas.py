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
