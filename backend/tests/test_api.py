from fastapi.testclient import TestClient

from app.main import app
from app.models import MetricState, Reel, TrackedProfile


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
