from fastapi.testclient import TestClient

from app.main import app
from datetime import datetime, timezone

from app.models import CollectionOutcome, CollectionRun, MetricState, ProfileSnapshot, Reel, ReelSnapshot, RunStatus, TrackedProfile


def signed_in_client(persona="Saba"):
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"password": "team-secret"}).status_code == 200
    assert client.post("/api/auth/persona", json={"persona": persona}).status_code == 200
    return client


def test_login_requires_valid_password_and_profile_choice():
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "team-secret"}).status_code == 200
    assert client.get("/api/profiles").status_code == 200
    assert client.post("/api/profiles", json={"url": "https://instagram.com/example"}).status_code == 428
    assert client.post("/api/auth/persona", json={"persona": "Unknown"}).status_code == 422
    assert client.post("/api/auth/persona", json={"persona": "Dachi"}).json()["persona"] == "Dachi"


def test_profile_duplicates_and_archiving_preserve_record():
    client = signed_in_client("Lui")
    created = client.post("/api/profiles", json={"url": "https://instagram.com/Example_Profile/"})
    assert created.status_code == 202
    profile = created.json()
    assert profile["username"] == "example_profile"
    assert profile["added_by"] == "Lui"
    assert client.post("/api/profiles", json={"url": "instagram.com/example_profile"}).status_code == 409
    assert client.delete(f"/api/profiles/{profile['id']}").status_code == 204
    assert client.get("/api/profiles").json()["total"] == 0
    restored = client.post("/api/profiles", json={"url": "https://instagram.com/example_profile"})
    assert restored.status_code == 202
    assert restored.json()["id"] == profile["id"]


def test_engagement_is_calculated_only_with_complete_inputs(db):
    profile = TrackedProfile(username="creator", instagram_url="https://instagram.com/creator/", added_by="Saba", followers_count=1000, followers_state=MetricState.available)
    db.add(profile)
    db.flush()
    reel = Reel(profile_id=profile.id, shortcode="abc", permalink="https://instagram.com/reel/abc/", likes_count=80, comments_count=20, views_count=4000, metrics_state=MetricState.available)
    db.add(reel)
    db.commit()
    client = signed_in_client()
    payload = client.get(f"/api/reels/{reel.id}").json()
    assert payload["engagement_rate"] == 10.0
    profile.followers_count = None
    db.commit()
    payload = client.get(f"/api/reels/{reel.id}").json()
    assert payload["engagement_rate"] is None


def test_history_date_ranges_require_chronological_order():
    client = signed_in_client()
    response = client.get("/api/dashboard?from=2026-09-13&to=2026-09-12")
    assert response.status_code == 422


def test_manual_collection_requires_a_persona_and_queues():
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"password": "team-secret"}).status_code == 200
    assert client.post("/api/collection/run").status_code == 428
    assert client.post("/api/auth/persona", json={"persona": "Dachi"}).status_code == 200
    response = client.post("/api/collection/run")
    assert response.status_code == 202
    assert response.json() == {"status": "queued"}


def test_collection_runs_include_copyable_per_profile_diagnostics(db):
    profile = TrackedProfile(username="diagnostic_creator", instagram_url="https://instagram.com/diagnostic_creator/", added_by="Saba")
    run = CollectionRun(schedule_key="manual:diagnostic", trigger="manual", status=RunStatus.failed, profiles_total=1, profiles_failed=1, error="Browser startup failed")
    db.add_all([profile, run])
    db.flush()
    db.add(CollectionOutcome(run_id=run.id, profile_id=profile.id, status=RunStatus.failed, reels_observed=0, message="Attempt 1/3 failed (TargetClosedError)"))
    db.commit()

    payload = signed_in_client().get("/api/collection/runs").json()
    assert payload[0]["error"] == "Browser startup failed"
    assert payload[0]["outcomes"] == [{
        "profile_id": profile.id,
        "username": "diagnostic_creator",
        "status": "failed",
        "reels_observed": 0,
        "message": "Attempt 1/3 failed (TargetClosedError)",
        "started_at": payload[0]["outcomes"][0]["started_at"],
        "completed_at": None,
    }]


def test_weekly_kpi_plan_is_shared_and_uses_snapshot_growth(db):
    profile = TrackedProfile(username="kpi_creator", instagram_url="https://instagram.com/kpi_creator/", added_by="Dachi", created_at=datetime(2026, 9, 8, tzinfo=timezone.utc))
    db.add(profile)
    db.flush()
    reel = Reel(profile_id=profile.id, shortcode="kpi_reel", permalink="https://instagram.com/reel/kpi_reel/", first_seen_at=datetime(2026, 9, 9, tzinfo=timezone.utc))
    db.add(reel)
    db.flush()
    first_run = CollectionRun(schedule_key="kpi:first", trigger="manual", status=RunStatus.succeeded)
    last_run = CollectionRun(schedule_key="kpi:last", trigger="manual", status=RunStatus.succeeded)
    db.add_all([first_run, last_run])
    db.flush()
    db.add_all([
        ProfileSnapshot(profile_id=profile.id, run_id=first_run.id, followers_count=100, state=MetricState.available, observed_at=datetime(2026, 9, 8, tzinfo=timezone.utc)),
        ProfileSnapshot(profile_id=profile.id, run_id=last_run.id, followers_count=125, state=MetricState.available, observed_at=datetime(2026, 9, 13, tzinfo=timezone.utc)),
        ReelSnapshot(reel_id=reel.id, run_id=first_run.id, views_count=1000, likes_count=10, comments_count=2, state=MetricState.available, observed_at=datetime(2026, 9, 9, tzinfo=timezone.utc)),
        ReelSnapshot(reel_id=reel.id, run_id=last_run.id, views_count=1600, likes_count=15, comments_count=3, state=MetricState.available, observed_at=datetime(2026, 9, 13, tzinfo=timezone.utc)),
    ])
    db.commit()

    dachi = signed_in_client("Dachi")
    response = dachi.put("/api/kpi/weekly?week_start=2026-09-09", json={"profiles_target": 2, "reels_target": 3, "views_growth_target": 1000, "followers_growth_target": 50, "focus": "  Add strong creators  "})
    assert response.status_code == 200
    payload = response.json()
    assert payload["week_start"] == "2026-09-07"
    plan = next(item for item in payload["managers"] if item["manager"] == "Dachi")
    assert plan["focus"] == "Add strong creators"
    actuals = {metric["key"]: metric["actual"] for metric in plan["metrics"]}
    assert actuals == {"profiles": 1, "reels": 1, "views_growth": 600, "followers_growth": 25}

    saba = signed_in_client("Saba")
    shared = saba.get("/api/kpi/weekly?week_start=2026-09-07").json()
    assert next(item for item in shared["managers"] if item["manager"] == "Dachi")["focus"] == "Add strong creators"
    assert saba.put("/api/kpi/weekly?week_start=2026-09-07", json={"profiles_target": -1, "reels_target": 0, "views_growth_target": 0, "followers_growth_target": 0}).status_code == 422
