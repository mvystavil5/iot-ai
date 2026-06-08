from datetime import datetime, timezone

import pytest

from src.events import bus
from src.security import learner, store
from src.ingestion.schema import TelemetryReading

REGISTRY = {"sensors": [{"id": "motion_01", "name": "Living room motion", "type": "motion", "location": "living_room"}]}
NO_MOTION_REGISTRY = {"sensors": [{"id": "temp_01", "name": "Living room temperature", "type": "temperature", "location": "living_room"}]}


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


def _stub_baseline(baseline_id):
    return {
        "baseline_id": baseline_id,
        "learned_at": "2026-01-01T00:00:00+00:00",
        "window": {"start": "2026-01-01T00:00:00+00:00", "end": "2026-01-01T01:00:00+00:00"},
        "signature": {"presence_ratio": 0.5, "hourly_activity": [0.0] * 24, "mean_session_length_min": 0.0, "sample_size": 1},
    }


def _stub_alert(status):
    return {"status": status, "similarity": 0.9, "checked_at": "2026-01-01T00:30:00+00:00"}


# -- learn_baseline -------------------------------------------------------------

def test_learn_baseline_builds_persists_and_publishes(tmp_path):
    baseline_path = tmp_path / "baseline.jsonl"
    chronological = [_r(9, 0, 1.0), _r(9, 5, 1.0)]
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    received = []
    bus.subscribe("occupancy_baseline_learned", received.append)

    baseline = learner.learn_baseline(
        start, end,
        ts_store=_FakeTsStore(list(reversed(chronological))),
        registry=REGISTRY,
        baseline_path=baseline_path,
    )

    assert baseline["baseline_id"].startswith("occupancy_baseline_")
    assert baseline["signature"]["sample_size"] == 2
    assert "display_name" not in baseline
    assert "consent_at" not in baseline
    assert received == [baseline]
    assert store.load_baselines(baseline_path) == [baseline]


def test_learn_baseline_filters_readings_outside_window(tmp_path):
    baseline_path = tmp_path / "baseline.jsonl"
    in_window = _r(9, 0, 1.0)
    after_window = _r(11, 0, 1.0)
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    baseline = learner.learn_baseline(
        start, end,
        ts_store=_FakeTsStore([after_window, in_window]),  # most-recent-first
        registry=REGISTRY,
        baseline_path=baseline_path,
    )

    assert baseline["signature"]["sample_size"] == 1


def test_learn_baseline_raises_without_motion_sensor(tmp_path):
    with pytest.raises(ValueError, match="motion sensor"):
        learner.learn_baseline(
            datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 9, tzinfo=timezone.utc),
            ts_store=_FakeTsStore([]),
            registry=NO_MOTION_REGISTRY,
            baseline_path=tmp_path / "baseline.jsonl",
        )


def test_learn_baseline_relearning_makes_the_newest_one_active(tmp_path):
    baseline_path = tmp_path / "baseline.jsonl"
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    first = learner.learn_baseline(start, end, ts_store=_FakeTsStore([]), registry=REGISTRY, baseline_path=baseline_path)
    second = learner.learn_baseline(start, end, ts_store=_FakeTsStore([]), registry=REGISTRY, baseline_path=baseline_path)

    assert first["baseline_id"] != second["baseline_id"]
    assert learner.get_baseline(baseline_path=baseline_path)["baseline_id"] == second["baseline_id"]


# -- get_baseline ----------------------------------------------------------------

def test_get_baseline_returns_none_when_nothing_learned(tmp_path):
    assert learner.get_baseline(baseline_path=tmp_path / "baseline.jsonl") is None


# -- reset_baseline ----------------------------------------------------------------

def test_reset_baseline_hard_deletes_baseline_and_alert_history(tmp_path):
    baseline_path = tmp_path / "baseline.jsonl"
    alerts_path = tmp_path / "alerts.jsonl"
    store._append(baseline_path, _stub_baseline("occupancy_baseline_1"))
    store._append(baseline_path, _stub_baseline("occupancy_baseline_2"))
    store._append(alerts_path, _stub_alert("expected"))
    store._append(alerts_path, _stub_alert("anomalous"))

    received = []
    bus.subscribe("occupancy_baseline_reset", received.append)

    assert learner.reset_baseline(baseline_path=baseline_path, alerts_path=alerts_path) is True

    assert store.load_baselines(baseline_path) == []
    assert store.load_alerts(alerts_path) == []
    assert len(received) == 1
    assert "reset_at" in received[0]


def test_reset_baseline_returns_false_when_nothing_to_reset(tmp_path):
    received = []
    bus.subscribe("occupancy_baseline_reset", received.append)

    assert learner.reset_baseline(baseline_path=tmp_path / "baseline.jsonl", alerts_path=tmp_path / "alerts.jsonl") is False
    assert received == []
