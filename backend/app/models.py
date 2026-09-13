from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class MetricState(str, enum.Enum):
    available = "available"
    unavailable = "unavailable"
    stale = "stale"
    failed = "failed"


class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    partial = "partial"
    failed = "failed"
    skipped = "skipped"


class TrackedProfile(Base):
    __tablename__ = "tracked_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    instagram_url: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    biography: Mapped[Optional[str]] = mapped_column(Text)
    profile_picture_url: Mapped[Optional[str]] = mapped_column(Text)
    followers_count: Mapped[Optional[int]] = mapped_column(Integer)
    followers_state: Mapped[MetricState] = mapped_column(Enum(MetricState), default=MetricState.unavailable)
    followers_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    added_by: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    fetch_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_collected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    reels: Mapped[list["Reel"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    snapshots: Mapped[list["ProfileSnapshot"]] = relationship(back_populates="profile", cascade="all, delete-orphan")


class Reel(Base):
    __tablename__ = "reels"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(ForeignKey("tracked_profiles.id", ondelete="CASCADE"), index=True)
    shortcode: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    permalink: Mapped[str] = mapped_column(Text)
    caption: Mapped[Optional[str]] = mapped_column(Text)
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    views_count: Mapped[Optional[int]] = mapped_column(Integer)
    likes_count: Mapped[Optional[int]] = mapped_column(Integer)
    comments_count: Mapped[Optional[int]] = mapped_column(Integer)
    metrics_state: Mapped[MetricState] = mapped_column(Enum(MetricState), default=MetricState.unavailable)
    metrics_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    views_source: Mapped[Optional[str]] = mapped_column(String(80))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    profile: Mapped[TrackedProfile] = relationship(back_populates="reels")
    snapshots: Mapped[list["ReelSnapshot"]] = relationship(back_populates="reel", cascade="all, delete-orphan")


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_key: Mapped[str] = mapped_column(String(80), unique=True)
    trigger: Mapped[str] = mapped_column(String(32), default="scheduled")
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.queued)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    profiles_total: Mapped[int] = mapped_column(Integer, default=0)
    profiles_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    profiles_failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)
    outcomes: Mapped[list["CollectionOutcome"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class ProfileSnapshot(Base):
    __tablename__ = "profile_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(ForeignKey("tracked_profiles.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("collection_runs.id", ondelete="CASCADE"), index=True)
    followers_count: Mapped[Optional[int]] = mapped_column(Integer)
    state: Mapped[MetricState] = mapped_column(Enum(MetricState))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(80), default="instagram_public_web")
    profile: Mapped[TrackedProfile] = relationship(back_populates="snapshots")
    __table_args__ = (UniqueConstraint("profile_id", "run_id", name="uq_profile_snapshot_run"),)


class ReelSnapshot(Base):
    __tablename__ = "reel_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    reel_id: Mapped[str] = mapped_column(ForeignKey("reels.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("collection_runs.id", ondelete="CASCADE"), index=True)
    views_count: Mapped[Optional[int]] = mapped_column(Integer)
    likes_count: Mapped[Optional[int]] = mapped_column(Integer)
    comments_count: Mapped[Optional[int]] = mapped_column(Integer)
    state: Mapped[MetricState] = mapped_column(Enum(MetricState))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    views_source: Mapped[Optional[str]] = mapped_column(String(80))
    reel: Mapped[Reel] = relationship(back_populates="snapshots")
    __table_args__ = (UniqueConstraint("reel_id", "run_id", name="uq_reel_snapshot_run"),)


class CollectionOutcome(Base):
    __tablename__ = "collection_outcomes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("collection_runs.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("tracked_profiles.id", ondelete="CASCADE"), index=True)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus))
    reels_observed: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    run: Mapped[CollectionRun] = relationship(back_populates="outcomes")
    __table_args__ = (UniqueConstraint("run_id", "profile_id", name="uq_outcome_run_profile"),)


class AuthAttempt(Base):
    __tablename__ = "auth_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_key: Mapped[str] = mapped_column(String(64), index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    __table_args__ = (Index("ix_auth_attempt_client_time", "client_key", "attempted_at"),)


class WeeklyKpiPlan(Base):
    __tablename__ = "weekly_kpi_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    week_start: Mapped[date] = mapped_column(Date, index=True)
    manager: Mapped[str] = mapped_column(String(16), index=True)
    profiles_target: Mapped[int] = mapped_column(Integer, default=0)
    reels_target: Mapped[int] = mapped_column(Integer, default=0)
    views_growth_target: Mapped[int] = mapped_column(Integer, default=0)
    followers_growth_target: Mapped[int] = mapped_column(Integer, default=0)
    focus: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("week_start", "manager", name="uq_kpi_plan_week_manager"),)


class WeeklyKpiItem(Base):
    __tablename__ = "weekly_kpi_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    week_start: Mapped[date] = mapped_column(Date, index=True)
    manager: Mapped[str] = mapped_column(String(16), index=True)
    text: Mapped[str] = mapped_column(String(500))
    completed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (Index("ix_kpi_item_week_manager", "week_start", "manager"),)
