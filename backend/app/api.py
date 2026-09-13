from __future__ import annotations

import csv
import asyncio
import io
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from google.cloud import run_v2
from sqlalchemy import desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import PERSONAS, client_key, create_token, enforce_login_limit, read_token, record_login_attempt, require_persona, require_session, verify_password
from .config import get_settings
from .database import get_db
from .instagram import normalize_instagram_profile_url
from .models import CollectionRun, MetricState, ProfileSnapshot, Reel, ReelSnapshot, RunStatus, TrackedProfile, utcnow
from .schemas import DashboardResponse, LoginRequest, MetricSummary, PaginatedProfiles, PaginatedReels, PersonaRequest, ProfileCreate, ProfileOut, ReelOut, SessionResponse, SnapshotPoint

router = APIRouter(prefix="/api")


def _history_bounds(days: int, start_date: date | None, end_date: date | None) -> tuple[datetime, datetime]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="The start date must be before the end date")
    if start_date or end_date:
        local_tz = ZoneInfo(get_settings().app_timezone)
        today = datetime.now(local_tz).date()
        end = end_date or today
        start = start_date or end - timedelta(days=days)
        return (
            datetime.combine(start, time.min, tzinfo=local_tz).astimezone(timezone.utc),
            datetime.combine(end, time.max, tzinfo=local_tz).astimezone(timezone.utc),
        )
    now = utcnow()
    return now - timedelta(days=days), now


def _date_query_params(days: int, start_date: date | None, end_date: date | None) -> tuple[datetime, datetime]:
    return _history_bounds(days, start_date, end_date)


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(settings.session_cookie_name, token, max_age=settings.session_ttl_seconds, httponly=True, secure=settings.secure_cookies, samesite="lax", path="/")


def _reel_out(reel: Reel) -> ReelOut:
    followers = reel.profile.followers_count if reel.profile else None
    rate = None
    if followers and reel.likes_count is not None and reel.comments_count is not None:
        rate = round((reel.likes_count + reel.comments_count) / followers * 100, 2)
    return ReelOut.model_validate(reel).model_copy(update={"engagement_rate": rate, "profile_username": reel.profile.username if reel.profile else None})


def _change(current: int | None, previous: int | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / previous * 100, 2)


async def _trigger_collector_job(username: str | None = None) -> None:
    settings = get_settings()
    name = settings.collector_job_name
    if not name:
        if settings.local_collector_enabled:
            from .collector import run_collection

            await asyncio.to_thread(run_collection, "manual", username)
        return
    client = run_v2.JobsAsyncClient()
    request = run_v2.RunJobRequest(
        name=name,
        overrides=run_v2.RunJobRequest.Overrides(
            container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(args=["--trigger", "manual"])]
        ),
    )
    await client.run_job(request=request)


@router.post("/auth/login", response_model=SessionResponse)
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    key = client_key(request)
    enforce_login_limit(db, key)
    valid = verify_password(payload.password)
    record_login_attempt(db, key, valid)
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect team password")
    _set_session_cookie(response, create_token())
    return SessionResponse(authenticated=True)


@router.get("/auth/session", response_model=SessionResponse)
def session(request: Request):
    value = read_token(request.cookies.get(get_settings().session_cookie_name))
    return SessionResponse(authenticated=bool(value), persona=value.get("persona") if value else None)


@router.post("/auth/persona", response_model=SessionResponse)
def select_persona(payload: PersonaRequest, response: Response, _: dict = Depends(require_session)):
    if payload.persona not in PERSONAS:
        raise HTTPException(status_code=422, detail="Unknown team profile")
    _set_session_cookie(response, create_token(payload.persona))
    return SessionResponse(authenticated=True, persona=payload.persona)


@router.post("/auth/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(get_settings().session_cookie_name, path="/")


@router.get("/profiles", response_model=PaginatedProfiles)
def list_profiles(search: str = "", include_archived: bool = False, sort: Literal["newest", "followers", "username", "last_collected"] = "newest", last_scraped_after: date | None = Query(None), last_scraped_before: date | None = Query(None), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), _: dict = Depends(require_session), db: Session = Depends(get_db)):
    conditions = []
    if not include_archived:
        conditions.append(TrackedProfile.is_active.is_(True))
    if search:
        token = f"%{search.strip()}%"
        conditions.append(or_(TrackedProfile.username.ilike(token), TrackedProfile.display_name.ilike(token)))
    local_tz = ZoneInfo(get_settings().app_timezone)
    if last_scraped_after:
        after_at = datetime.combine(last_scraped_after, time.min, tzinfo=local_tz).astimezone(timezone.utc)
        conditions.append(TrackedProfile.last_collected_at >= after_at)
    if last_scraped_before:
        before_at = datetime.combine(last_scraped_before + timedelta(days=1), time.min, tzinfo=local_tz).astimezone(timezone.utc)
        conditions.append(TrackedProfile.last_collected_at < before_at)
    query = select(TrackedProfile).where(*conditions)
    order = {"newest": desc(TrackedProfile.created_at), "followers": desc(TrackedProfile.followers_count), "username": TrackedProfile.username, "last_collected": desc(TrackedProfile.last_collected_at).nullslast()}[sort]
    total = db.scalar(select(func.count(TrackedProfile.id)).where(*conditions)) or 0
    items = db.scalars(query.order_by(order).limit(limit).offset(offset)).all()
    return PaginatedProfiles(items=list(items), total=total)


@router.post("/profiles", response_model=ProfileOut, status_code=202)
async def add_profile(payload: ProfileCreate, background_tasks: BackgroundTasks, persona: str = Depends(require_persona), db: Session = Depends(get_db)):
    try:
        username, canonical_url = normalize_instagram_profile_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    active_count = db.scalar(select(func.count(TrackedProfile.id)).where(TrackedProfile.is_active.is_(True))) or 0
    if active_count >= get_settings().max_profiles:
        raise HTTPException(status_code=409, detail="The 100-profile tracking limit has been reached")
    existing = db.scalar(select(TrackedProfile).where(TrackedProfile.username == username))
    if existing:
        if existing.is_active:
            raise HTTPException(status_code=409, detail="This Instagram profile is already tracked")
        existing.is_active = True
        existing.fetch_requested_at = utcnow()
        existing.added_by = persona
        db.commit()
        db.refresh(existing)
        background_tasks.add_task(_trigger_collector_job, username)
        return existing
    profile = TrackedProfile(username=username, instagram_url=canonical_url, added_by=persona, fetch_requested_at=utcnow())
    db.add(profile)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This Instagram profile is already tracked") from exc
    db.refresh(profile)
    background_tasks.add_task(_trigger_collector_job, username)
    return profile


@router.get("/profiles/{profile_id}", response_model=ProfileOut)
def get_profile(profile_id: str, _: dict = Depends(require_session), db: Session = Depends(get_db)):
    profile = db.get(TrackedProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.get("/profiles/{profile_id}/history", response_model=list[SnapshotPoint])
def profile_history(profile_id: str, days: int = Query(30, ge=1, le=365), start_date: date | None = Query(None, alias="from"), end_date: date | None = Query(None, alias="to"), _: dict = Depends(require_session), db: Session = Depends(get_db)):
    start_at, end_at = _date_query_params(days, start_date, end_date)
    rows = db.scalars(select(ProfileSnapshot).where(ProfileSnapshot.profile_id == profile_id, ProfileSnapshot.observed_at >= start_at, ProfileSnapshot.observed_at <= end_at).order_by(ProfileSnapshot.observed_at)).all()
    return [SnapshotPoint(observed_at=row.observed_at, value=row.followers_count, state=row.state.value) for row in rows]


@router.delete("/profiles/{profile_id}", status_code=204)
def archive_profile(profile_id: str, _: str = Depends(require_persona), db: Session = Depends(get_db)):
    profile = db.get(TrackedProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    profile.is_active = False
    profile.fetch_requested_at = None
    db.commit()


@router.get("/reels", response_model=PaginatedReels)
def list_reels(search: str = "", profile_id: str | None = None, sort: Literal["newest", "views", "engagement"] = "newest", observed_after: date | None = Query(None), observed_before: date | None = Query(None), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), _: dict = Depends(require_session), db: Session = Depends(get_db)):
    conditions = [TrackedProfile.is_active.is_(True)]
    if profile_id:
        conditions.append(Reel.profile_id == profile_id)
    if search:
        token = f"%{search.strip()}%"
        conditions.append(or_(Reel.caption.ilike(token), TrackedProfile.username.ilike(token)))
    if observed_after and observed_before and observed_after > observed_before:
        raise HTTPException(status_code=422, detail="The scrape start date must be before the end date")
    local_tz = ZoneInfo(get_settings().app_timezone)
    if observed_after:
        after_at = datetime.combine(observed_after, time.min, tzinfo=local_tz).astimezone(timezone.utc)
        conditions.append(Reel.metrics_observed_at >= after_at)
    if observed_before:
        before_at = datetime.combine(observed_before + timedelta(days=1), time.min, tzinfo=local_tz).astimezone(timezone.utc)
        conditions.append(Reel.metrics_observed_at < before_at)
    base = select(Reel).join(TrackedProfile).where(*conditions)
    if sort == "views":
        order = desc(Reel.views_count)
    else:
        order = desc(Reel.published_at)
    rows = db.scalars(base.order_by(order).limit(limit).offset(offset)).all()
    items = [_reel_out(row) for row in rows]
    if sort == "engagement":
        items.sort(key=lambda item: item.engagement_rate if item.engagement_rate is not None else -1, reverse=True)
    total = db.scalar(select(func.count(Reel.id)).join(TrackedProfile).where(*conditions)) or 0
    return PaginatedReels(items=items, total=total)


@router.get("/reels/{reel_id}", response_model=ReelOut)
def get_reel(reel_id: str, _: dict = Depends(require_session), db: Session = Depends(get_db)):
    reel = db.get(Reel, reel_id)
    if not reel:
        raise HTTPException(status_code=404, detail="Reel not found")
    return _reel_out(reel)


@router.get("/reels/{reel_id}/history")
def reel_history(reel_id: str, days: int = Query(30, ge=1, le=365), start_date: date | None = Query(None, alias="from"), end_date: date | None = Query(None, alias="to"), _: dict = Depends(require_session), db: Session = Depends(get_db)):
    start_at, end_at = _date_query_params(days, start_date, end_date)
    rows = db.scalars(select(ReelSnapshot).where(ReelSnapshot.reel_id == reel_id, ReelSnapshot.observed_at >= start_at, ReelSnapshot.observed_at <= end_at).order_by(ReelSnapshot.observed_at)).all()
    return [{"observed_at": row.observed_at, "views": row.views_count, "likes": row.likes_count, "comments": row.comments_count, "state": row.state.value, "views_source": row.views_source} for row in rows]


def _metric_summary(current: int | None, previous: int | None, observed_at: datetime | None) -> MetricSummary:
    return MetricSummary(value=current, previous_value=previous, change_percent=_change(current, previous), observed_at=observed_at, state="available" if current is not None else "unavailable")


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(days: int = Query(30, ge=1, le=365), start_date: date | None = Query(None, alias="from"), end_date: date | None = Query(None, alias="to"), _: dict = Depends(require_session), db: Session = Depends(get_db)):
    start_at, end_at = _date_query_params(days, start_date, end_date)
    reels = db.scalars(select(Reel).join(TrackedProfile).where(TrackedProfile.is_active.is_(True))).all()
    profiles = db.scalars(select(TrackedProfile).where(TrackedProfile.is_active.is_(True))).all()
    current = {
        "views": sum(r.views_count or 0 for r in reels) if any(r.views_count is not None for r in reels) else None,
        "likes": sum(r.likes_count or 0 for r in reels) if any(r.likes_count is not None for r in reels) else None,
        "comments": sum(r.comments_count or 0 for r in reels) if any(r.comments_count is not None for r in reels) else None,
        "followers": sum(p.followers_count or 0 for p in profiles) if any(p.followers_count is not None for p in profiles) else None,
    }
    observed_at = max([value for value in [*(r.metrics_observed_at for r in reels), *(p.followers_observed_at for p in profiles)] if value], default=None)
    snapshots = db.execute(
        select(func.max(ReelSnapshot.observed_at), func.sum(ReelSnapshot.views_count))
        .join(Reel, Reel.id == ReelSnapshot.reel_id)
        .join(TrackedProfile, TrackedProfile.id == Reel.profile_id)
        .where(ReelSnapshot.observed_at >= start_at, ReelSnapshot.observed_at <= end_at, TrackedProfile.is_active.is_(True))
        .group_by(ReelSnapshot.run_id)
        .order_by(func.max(ReelSnapshot.observed_at))
    ).all()
    views_series = [{"observed_at": row[0], "views": int(row[1]) if row[1] is not None else None} for row in snapshots]
    run_ids = db.scalars(select(CollectionRun.id).where(CollectionRun.status.in_([RunStatus.succeeded, RunStatus.partial])).order_by(desc(CollectionRun.started_at)).limit(2)).all()
    previous_totals = {"views": None, "likes": None, "comments": None, "followers": None}
    if len(run_ids) > 1:
        previous_run = run_ids[1]
        reel_totals = db.execute(select(func.sum(ReelSnapshot.views_count), func.sum(ReelSnapshot.likes_count), func.sum(ReelSnapshot.comments_count)).where(ReelSnapshot.run_id == previous_run)).one()
        previous_totals.update({"views": reel_totals[0], "likes": reel_totals[1], "comments": reel_totals[2]})
        previous_totals["followers"] = db.scalar(select(func.sum(ProfileSnapshot.followers_count)).where(ProfileSnapshot.run_id == previous_run))
    top = sorted(reels, key=lambda r: r.views_count if r.views_count is not None else -1, reverse=True)[:5]
    recent = sorted(reels, key=lambda r: r.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:10]
    return DashboardResponse(
        views=_metric_summary(current["views"], previous_totals["views"], observed_at),
        likes=_metric_summary(current["likes"], previous_totals["likes"], observed_at),
        comments=_metric_summary(current["comments"], previous_totals["comments"], observed_at),
        followers=_metric_summary(current["followers"], previous_totals["followers"], observed_at),
        views_series=views_series,
        top_reels=[_reel_out(item) for item in top],
        recent_reels=[_reel_out(item) for item in recent],
    )


@router.get("/collection/runs")
def collection_runs(limit: int = Query(20, ge=1, le=100), _: dict = Depends(require_session), db: Session = Depends(get_db)):
    rows = db.scalars(select(CollectionRun).order_by(desc(CollectionRun.started_at)).limit(limit)).all()
    return [{"id": r.id, "status": r.status.value, "trigger": r.trigger, "started_at": r.started_at, "completed_at": r.completed_at, "profiles_total": r.profiles_total, "profiles_succeeded": r.profiles_succeeded, "profiles_failed": r.profiles_failed, "error": r.error} for r in rows]


@router.post("/collection/run", status_code=status.HTTP_202_ACCEPTED)
async def start_manual_collection(background_tasks: BackgroundTasks, _: str = Depends(require_persona)):
    background_tasks.add_task(_trigger_collector_job)
    return {"status": "queued"}


@router.get("/export/profiles.csv")
def export_profiles(_: dict = Depends(require_session), db: Session = Depends(get_db)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["username", "profile_url", "display_name", "followers", "metric_state", "observed_at", "added_by", "active"])
    for p in db.scalars(select(TrackedProfile).order_by(TrackedProfile.username)):
        writer.writerow([p.username, p.instagram_url, p.display_name or "", p.followers_count if p.followers_count is not None else "", p.followers_state.value, p.followers_observed_at or "", p.added_by, p.is_active])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=instagram-profiles.csv"})


@router.get("/export/reels.csv")
def export_reels(_: dict = Depends(require_session), db: Session = Depends(get_db)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["creator", "reel_url", "published_at", "views", "likes", "comments", "engagement_rate", "metric_state", "observed_at", "views_source", "caption", "hashtags"])
    for reel in db.scalars(select(Reel).join(TrackedProfile).where(TrackedProfile.is_active.is_(True)).order_by(desc(Reel.published_at))):
        item = _reel_out(reel)
        writer.writerow([item.profile_username, item.permalink, item.published_at or "", item.views_count if item.views_count is not None else "", item.likes_count if item.likes_count is not None else "", item.comments_count if item.comments_count is not None else "", item.engagement_rate if item.engagement_rate is not None else "", item.metrics_state, item.metrics_observed_at or "", item.views_source or "", item.caption or "", " ".join(f"#{tag}" for tag in item.hashtags)])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=instagram-reels.csv"})
