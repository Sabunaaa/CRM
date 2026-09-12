from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class PersonaRequest(BaseModel):
    persona: str


class SessionResponse(BaseModel):
    authenticated: bool
    persona: str | None = None


class ProfileCreate(BaseModel):
    url: str = Field(min_length=1, max_length=500)


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    instagram_url: str
    display_name: str | None
    biography: str | None
    profile_picture_url: str | None
    followers_count: int | None
    followers_state: str
    followers_observed_at: datetime | None
    added_by: str
    is_active: bool
    last_collected_at: datetime | None
    last_error: str | None
    created_at: datetime


class ReelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    profile_id: str
    shortcode: str
    permalink: str
    caption: str | None
    hashtags: list[str]
    thumbnail_url: str | None
    published_at: datetime | None
    views_count: int | None
    likes_count: int | None
    comments_count: int | None
    metrics_state: str
    metrics_observed_at: datetime | None
    views_source: str | None
    engagement_rate: float | None = None
    profile_username: str | None = None


class SnapshotPoint(BaseModel):
    observed_at: datetime
    value: int | None
    state: str


class PaginatedProfiles(BaseModel):
    items: list[ProfileOut]
    total: int


class PaginatedReels(BaseModel):
    items: list[ReelOut]
    total: int


class MetricSummary(BaseModel):
    value: int | None
    previous_value: int | None
    change_percent: float | None
    observed_at: datetime | None
    state: str


class DashboardResponse(BaseModel):
    views: MetricSummary
    likes: MetricSummary
    comments: MetricSummary
    followers: MetricSummary
    views_series: list[dict]
    top_reels: list[ReelOut]
    recent_reels: list[ReelOut]
