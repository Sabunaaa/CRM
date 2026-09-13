from __future__ import annotations

import argparse
import logging
import random
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from .config import get_settings
from .database import Base, SessionLocal, engine
from .instagram import CollectionThrottled, CollectionUnavailable, create_instagram_adapter
from .models import CollectionOutcome, CollectionRun, MetricState, ProfileSnapshot, Reel, ReelSnapshot, RunStatus, TrackedProfile, utcnow

logger = logging.getLogger("instatrack.collector")
LOCK_ID = 68741023


def schedule_key(now: datetime, trigger: str) -> str:
    if trigger == "manual":
        return f"manual:{now.isoformat()}"
    local = now.astimezone(ZoneInfo(get_settings().app_timezone))
    bucket = 0 if local.hour < 12 else 12
    return f"scheduled:{local.date().isoformat()}T{bucket:02d}:00:00[{get_settings().app_timezone}]"


def _try_lock(db) -> bool:
    if db.bind and db.bind.dialect.name == "postgresql":
        return bool(db.scalar(text("SELECT pg_try_advisory_lock(:id)"), {"id": LOCK_ID}))
    return True


def _unlock(db) -> None:
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})


def _outcome_log(outcome: CollectionOutcome, message: str) -> None:
    timestamp = utcnow().isoformat(timespec="seconds")
    lines = (outcome.message or "").splitlines()
    lines.append(f"{timestamp}  {message}")
    outcome.message = "\n".join(lines[-50:])


def _upsert_profile_data(db, tracked: TrackedProfile, data, run: CollectionRun) -> int:
    observed_at = utcnow()
    tracked.display_name = data.display_name
    tracked.biography = data.biography
    tracked.profile_picture_url = data.profile_picture_url
    tracked.followers_count = data.followers_count
    tracked.followers_state = MetricState.available if data.followers_count is not None else MetricState.unavailable
    tracked.followers_observed_at = observed_at if data.followers_count is not None else tracked.followers_observed_at
    tracked.last_collected_at = observed_at
    tracked.last_error = None
    tracked.fetch_requested_at = None
    db.add(ProfileSnapshot(profile_id=tracked.id, run_id=run.id, followers_count=data.followers_count, state=tracked.followers_state, observed_at=observed_at))
    for item in data.reels:
        reel = db.scalar(select(Reel).where(Reel.shortcode == item.shortcode))
        if reel is None:
            reel = Reel(profile_id=tracked.id, shortcode=item.shortcode, permalink=item.permalink)
            db.add(reel)
            db.flush()
        reel.caption = item.caption
        reel.hashtags = item.hashtags
        reel.thumbnail_url = item.thumbnail_url
        reel.published_at = item.published_at
        reel.views_count = item.views_count
        reel.likes_count = item.likes_count
        reel.comments_count = item.comments_count
        reel.metrics_state = MetricState.available if any(value is not None for value in (item.views_count, item.likes_count, item.comments_count)) else MetricState.unavailable
        reel.metrics_observed_at = observed_at if reel.metrics_state == MetricState.available else reel.metrics_observed_at
        reel.views_source = item.views_source
        db.add(ReelSnapshot(reel_id=reel.id, run_id=run.id, views_count=item.views_count, likes_count=item.likes_count, comments_count=item.comments_count, state=reel.metrics_state, observed_at=observed_at, views_source=item.views_source))
    return len(data.reels)


def run_collection(trigger: str = "scheduled", username: str | None = None) -> int:
    settings = get_settings()
    Base.metadata.create_all(bind=engine)
    now = utcnow()
    db = SessionLocal()
    if not _try_lock(db):
        logger.info("collection_skipped reason=lock_held")
        db.close()
        return 0
    try:
        run = CollectionRun(schedule_key=schedule_key(now, trigger), trigger=trigger, status=RunStatus.running)
        db.add(run)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            logger.info("collection_skipped reason=duplicate_schedule")
            return 0
        query = select(TrackedProfile).where(TrackedProfile.is_active.is_(True))
        if username:
            query = query.where(TrackedProfile.username == username)
        profiles = db.scalars(query.order_by(TrackedProfile.fetch_requested_at.desc().nullslast(), TrackedProfile.username)).all()
        run.profiles_total = len(profiles)
        db.commit()
        adapter = create_instagram_adapter(
            settings.collector_adapter,
            timeout_ms=settings.scrapling_timeout_ms,
            reel_delay_seconds=settings.scrapling_reel_delay_seconds,
        )
        for index, profile in enumerate(profiles):
            outcome = CollectionOutcome(run_id=run.id, profile_id=profile.id, status=RunStatus.running)
            db.add(outcome)
            _outcome_log(outcome, f"Starting @{profile.username} with {settings.collector_adapter}.")
            db.commit()
            error: Exception | None = None
            for attempt in range(settings.collection_max_retries + 1):
                attempt_number = attempt + 1
                attempts_total = settings.collection_max_retries + 1
                _outcome_log(outcome, f"Attempt {attempt_number}/{attempts_total}: requesting the public profile and up to {settings.max_reels_per_profile} reels.")
                db.commit()
                try:
                    data = adapter.fetch_profile(profile.username, settings.max_reels_per_profile)
                    reels_observed = _upsert_profile_data(db, profile, data, run)
                    views_found = sum(item.views_count is not None for item in data.reels)
                    likes_found = sum(item.likes_count is not None for item in data.reels)
                    comments_found = sum(item.comments_count is not None for item in data.reels)
                    followers_result = f"{data.followers_count:,}" if data.followers_count is not None else "unavailable"
                    _outcome_log(outcome, f"Public page fetched. Followers: {followers_result}; reels discovered: {reels_observed}.")
                    _outcome_log(outcome, f"Reel metrics available — views: {views_found}/{reels_observed}, likes: {likes_found}/{reels_observed}, comments: {comments_found}/{reels_observed}.")
                    _outcome_log(outcome, "Saved the new observations successfully.")
                    outcome.status = RunStatus.succeeded
                    outcome.reels_observed = reels_observed
                    outcome.completed_at = utcnow()
                    run.profiles_succeeded += 1
                    db.commit()
                    error = None
                    break
                except CollectionThrottled as exc:
                    error = exc
                    _outcome_log(outcome, f"Stopped by Instagram throttling ({type(exc).__name__}): {exc}")
                    db.commit()
                    break
                except CollectionUnavailable as exc:
                    error = exc
                    _outcome_log(outcome, f"Public data was unavailable ({type(exc).__name__}): {exc}")
                    db.commit()
                    break
                except Exception as exc:
                    error = exc
                    _outcome_log(outcome, f"Attempt {attempt_number}/{attempts_total} failed ({type(exc).__name__}): {exc}")
                    db.commit()
                    if attempt < settings.collection_max_retries:
                        retry_delay = (2 ** attempt) + random.random()
                        _outcome_log(outcome, f"Retrying after {retry_delay:.1f} seconds.")
                        db.commit()
                        time.sleep(retry_delay)
            if error:
                profile.last_error = str(error)
                profile.followers_state = MetricState.stale if profile.followers_count is not None else MetricState.failed
                for reel in db.scalars(select(Reel).where(Reel.profile_id == profile.id)):
                    reel.metrics_state = MetricState.stale if any(value is not None for value in (reel.views_count, reel.likes_count, reel.comments_count)) else MetricState.failed
                outcome.status = RunStatus.failed
                _outcome_log(outcome, "Collection failed. Previously successful values were preserved.")
                outcome.completed_at = utcnow()
                run.profiles_failed += 1
                db.commit()
                logger.warning("profile_collection_failed profile=%s error=%s", profile.username, error)
                if isinstance(error, CollectionThrottled):
                    logger.warning("collection_stopped reason=throttled")
                    break
            if index < len(profiles) - 1:
                time.sleep(settings.collection_delay_seconds)
        run.completed_at = utcnow()
        run.status = RunStatus.succeeded if run.profiles_failed == 0 else (RunStatus.failed if run.profiles_succeeded == 0 else RunStatus.partial)
        db.commit()
        logger.info("collection_complete status=%s adapter=%s succeeded=%s failed=%s", run.status.value, settings.collector_adapter, run.profiles_succeeded, run.profiles_failed)
        return 0 if run.status in {RunStatus.succeeded, RunStatus.partial} else 1
    except Exception as exc:
        if "run" in locals():
            run.status = RunStatus.failed
            run.error = f"{type(exc).__name__}: {exc}"
            run.completed_at = utcnow()
            db.commit()
        logger.exception("collection_failed error=%s", exc)
        return 1
    finally:
        if "adapter" in locals():
            adapter.close()
        _unlock(db)
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trigger", choices=["scheduled", "manual"], default="scheduled")
    parser.add_argument("--username")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    raise SystemExit(run_collection(args.trigger, args.username))


if __name__ == "__main__":
    main()
