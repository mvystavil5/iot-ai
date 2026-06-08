import json
from datetime import datetime, timezone

import pytest

from src.events import bus
from src.security import detector
from src.security.signature import build_signature
from src.ingestion.schema import TelemetryReading


@pytest.fixture(autouse=True)
def _clear_bus():
    bus.clear()
    yield
    bus.clear()


def _cfg():
    return {"security": {
        "anomaly_similarity_threshold": 0.6,
        "live_window_minutes": 30,
        "feature_weights": {"hourly_activity": 0.5, "presence_ratio": 0.3, "session_length": 0.2},
    }}


PEAKED = {"hourly_activity": [1.0] + [0.0] * 23, "presence_ratio": 0.8, "mean_session_length_min": 20.0}
FLAT_DIFFERENT = {"hourly_activity": [0.0] * 23 + [1.0], "presence_ratio": 0.1, "mean_session_length_min": 0.0}


def _baseline(baseline_id, signature):
    return {"baseline_id": baseline_id, "learned_at": "2026-01-01T00:00:00+00:00",
            "window": {"start": "2026-01-01T00:00:00+00:00", "end": "2026-01-01T01:00:00+00:00"},
            "signature": signature}


# -- score_similarity ----------------------------------------------------------

def test_score_similarity_identical_signatures_scores_one():
    assert detector.score_similarity(PEAKED, PEAKED, _cfg()) == 1.0


def test_score_similarity_divergent_signatures_scores_low():
    assert detector.score_similarity(PEAKED, FLAT_DIFFERENT, _cfg()) < 0.5


# -- detect ---------------------------------------------------------------------

def test_detect_reports_expected_when_live_resembles_baseline():
    result = detector.detect(PEAKED, _baseline("b1", PEAKED), _cfg())
    assert result == {"status": detector.STATUS_EXPECTED, "similarity": 1.0}


def test_detect_reports_anomalous_when_live_diverges_from_baseline():
    result = detector.detect(PEAKED, _baseline("b1", FLAT_DIFFERENT), _cfg())
    assert result["status"] == detector.STATUS_ANOMALOUS
    assert result["similarity"] < _cfg()["security"]["anomaly_similarity_threshold"]


def test_detect_reports_no_baseline_when_nothing_learned_yet():
    result = detector.detect(PEAKED, None, _cfg())
    assert result == {"status": detector.STATUS_NO_BASELINE, "similarity": 0.0}


# -- run_live_check -------------------------------------------------------------

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


REGISTRY = {"sensors": [{"id": "motion_01", "name": "Living room motion", "type": "motion", "location": "living_room"}]}


def test_run_live_check_matches_baseline_persists_and_publishes_occupancy_checked(tmp_path):
    chronological = [_r(9, 0, 1.0), _r(9, 5, 1.0), _r(14, 0, 0.0)]
    live_signature = build_signature(chronological)
    baseline = _baseline("occupancy_baseline_abc123", live_signature)

    alerts_path = tmp_path / "alerts.jsonl"
    received = []
    bus.subscribe("occupancy_checked", received.append)

    record = detector.run_live_check(
        ts_store=_FakeTsStore(list(reversed(chronological))),
        registry=REGISTRY,
        baseline=baseline,
        cfg=_cfg(),
        alerts_path=alerts_path,
    )

    assert record["status"] == detector.STATUS_EXPECTED
    assert record["similarity"] == 1.0
    assert received == [record]
    assert json.loads(alerts_path.read_text().strip())["status"] == detector.STATUS_EXPECTED


def test_run_live_check_flags_anomaly_and_publishes_anomaly_event(tmp_path):
    # An empty live window -> uniform/zeroed signature, far from a peaked baseline.
    baseline = _baseline("occupancy_baseline_abc123", PEAKED)
    alerts_path = tmp_path / "alerts.jsonl"

    checked = []
    anomalies = []
    bus.subscribe("occupancy_checked", checked.append)
    bus.subscribe("occupancy_anomaly_detected", anomalies.append)

    record = detector.run_live_check(
        ts_store=_FakeTsStore([]),
        registry=REGISTRY,
        baseline=baseline,
        cfg=_cfg(),
        alerts_path=alerts_path,
    )

    assert record["status"] == detector.STATUS_ANOMALOUS
    assert checked == [record]
    assert anomalies == [record]
    assert json.loads(alerts_path.read_text().strip())["status"] == detector.STATUS_ANOMALOUS


def test_run_live_check_reports_no_baseline_when_nothing_learned(tmp_path):
    alerts_path = tmp_path / "alerts.jsonl"

    record = detector.run_live_check(
        ts_store=_FakeTsStore([]),
        registry=REGISTRY,
        baseline=None,
        cfg=_cfg(),
        alerts_path=alerts_path,
    )

    assert record["status"] == detector.STATUS_NO_BASELINE
    assert record["similarity"] == 0.0


def test_run_live_check_handles_missing_motion_sensor(tmp_path):
    alerts_path = tmp_path / "alerts.jsonl"
    record = detector.run_live_check(
        ts_store=_FakeTsStore([]),
        registry={"sensors": []},
        baseline=None,
        cfg=_cfg(),
        alerts_path=alerts_path,
    )
    assert record["status"] == detector.STATUS_NO_BASELINE
    assert record["similarity"] == 0.0
