from datetime import date, datetime, timezone

import pytest

from src.events import bus
from src.wellness import store, tracker
from src.ingestion.schema import TelemetryReading

REGISTRY = {"sensors": [{"id": "motion_01", "name": "Living room motion", "type": "motion", "location": "living_room"}]}
NO_MOTION_REGISTRY = {"sensors": [{"id": "temp_01", "name": "Living room temperature", "type": "temperature", "location": "living_room"}]}

DAY = date(2026, 1, 1)


@pytest.fixture(autouse=True)
def _clear_bus():
    bus.clear()
    yield
    bus.clear()


def _r(hour, minute, value):
    return TelemetryReading(
        sensor_id="motion_01",
        timestamp=datetime(2026, 1, 1, hour, minute, tzinfo=timezone.utc),
        value=value,
        unit="bool",
    )


class _FakeTsStore:
    def __init__(self, most_recent_first):
        self._readings = most_recent_first

    def query(self, sensor_id, since=None, limit=100):
        return self._readings[:limit]


def _stub_summary(day):
    return {"day": day, "active_minutes": 60.0, "sedentary_minutes": 1380.0,
            "longest_sedentary_streak_min": 600.0, "activity_sessions": 2,
            "mean_session_length_min": 30.0, "sample_size": 10}


def _stub_trend(status):
    return {"status": status, "active_minutes_delta": 0.0, "sedentary_minutes_delta": 0.0,
            "longest_sedentary_streak_delta": 0.0, "checked_at": "2026-01-01T00:00:00+00:00",
            "recent_days": 7, "baseline_days": 21}


# -- record_day -------------------------------------------------------------

def test_record_day_builds_persists_and_publishes(tmp_path):
    daily_path = tmp_path / "daily.jsonl"
    chronological = [_r(9, 0, 1.0), _r(9, 5, 1.0)]

    received = []
    bus.subscribe("wellness_day_recorded", received.append)

    summary = tracker.record_day(
        DAY,
        ts_store=_FakeTsStore(list(reversed(chronological))),
        registry=REGISTRY,
        daily_path=daily_path,
    )

    assert summary["day"] == "2026-01-01"
    assert summary["sample_size"] == 2
    assert "display_name" not in summary
    assert "person" not in summary
    assert received == [summary]
    assert store.load_daily_summaries(daily_path) == [summary]


def test_record_day_filters_readings_outside_window(tmp_path):
    daily_path = tmp_path / "daily.jsonl"
    in_window = _r(9, 0, 1.0)
    next_day = TelemetryReading(sensor_id="motion_01", timestamp=datetime(2026, 1, 2, 1, 0, tzinfo=timezone.utc), value=1.0, unit="bool")

    summary = tracker.record_day(
        DAY,
        ts_store=_FakeTsStore([next_day, in_window]),  # most-recent-first
        registry=REGISTRY,
        daily_path=daily_path,
    )

    assert summary["sample_size"] == 1


def test_record_day_raises_without_motion_sensor(tmp_path):
    with pytest.raises(ValueError, match="motion sensor"):
        tracker.record_day(
            DAY,
            ts_store=_FakeTsStore([]),
            registry=NO_MOTION_REGISTRY,
            daily_path=tmp_path / "daily.jsonl",
        )


# -- get_recent_days ---------------------------------------------------------

def test_get_recent_days_returns_most_recent_n(tmp_path):
    daily_path = tmp_path / "daily.jsonl"
    for d in ("2026-01-01", "2026-01-02", "2026-01-03"):
        store._append(daily_path, _stub_summary(d))

    recent = tracker.get_recent_days(2, daily_path=daily_path)

    assert [s["day"] for s in recent] == ["2026-01-02", "2026-01-03"]


def test_get_recent_days_returns_empty_when_nothing_recorded(tmp_path):
    assert tracker.get_recent_days(7, daily_path=tmp_path / "daily.jsonl") == []


# -- reset_history ------------------------------------------------------------

def test_reset_history_hard_deletes_days_and_trend_checks(tmp_path):
    daily_path = tmp_path / "daily.jsonl"
    trends_path = tmp_path / "trends.jsonl"
    store._append(daily_path, _stub_summary("2026-01-01"))
    store._append(daily_path, _stub_summary("2026-01-02"))
    store._append(trends_path, _stub_trend("stable"))
    store._append(trends_path, _stub_trend("more_sedentary"))

    received = []
    bus.subscribe("wellness_history_reset", received.append)

    assert tracker.reset_history(daily_path=daily_path, trends_path=trends_path) is True

    assert store.load_daily_summaries(daily_path) == []
    assert store.load_trend_checks(trends_path) == []
    assert len(received) == 1
    assert "reset_at" in received[0]


def test_reset_history_returns_false_when_nothing_to_reset(tmp_path):
    received = []
    bus.subscribe("wellness_history_reset", received.append)

    assert tracker.reset_history(daily_path=tmp_path / "daily.jsonl", trends_path=tmp_path / "trends.jsonl") is False
    assert received == []
