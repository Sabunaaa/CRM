from datetime import datetime, timezone

from app.collector import schedule_key


def test_schedule_key_uses_tbilisi_day_and_half_day():
    assert schedule_key(datetime(2026, 9, 12, 20, 0, tzinfo=timezone.utc), "scheduled").startswith("scheduled:2026-09-13T00")
    assert schedule_key(datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc), "scheduled").startswith("scheduled:2026-09-13T12")
    assert schedule_key(datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc), "manual").startswith("manual:")
