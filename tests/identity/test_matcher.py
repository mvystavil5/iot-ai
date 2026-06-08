import json
from datetime import datetime, timezone

import pytest

from src.events import bus
from src.identity import matcher
from src.identity.signature import build_signature
from src.ingestion.schema import TelemetryReading


@pytest.fixture(autouse=True)
def _clear_bus():
    bus.clear()
    yield
    bus.clear()


def _cfg():
    return {"identity": {
        "match_confidence_threshold": 0.6,
        "live_window_minutes": 30,
        "feature_weights": {"hourly_activity": 0.5, "presence_ratio": 0.3, "session_length": 0.2},
    }}


PEAKED = {"hourly_activity": [1.0] + [0.0] * 23, "presence_ratio": 0.8, "mean_session_length_min": 20.0}
FLAT_DIFFERENT = {"hourly_activity": [0.0] * 23 + [1.0], "presence_ratio": 0.1, "mean_session_length_min": 0.0}


def _profile(profile_id, name, signature, revoked_at=None):
    return {"profile_id": profile_id, "display_name": name, "signature": signature, "revoked_at": revoked_at}


# -- score_match --------------------------------------------------------------

def test_score_match_identical_signatures_scores_one():
    assert matcher.score_match(PEAKED, PEAKED, _cfg()) == 1.0


def test_score_match_divergent_signatures_scores_low():
    assert matcher.score_match(PEAKED, FLAT_DIFFERENT, _cfg()) < 0.5


# -- match ---------------------------------------------------------------------

def test_match_picks_best_profile_above_threshold():
    profiles = [_profile("a", "A", FLAT_DIFFERENT), _profile("b", "B", PEAKED)]
    result = matcher.match(PEAKED, profiles, _cfg())
    assert result == {"profile_id": "b", "display_name": "B", "confidence": 1.0}


def test_match_reports_unknown_below_threshold():
    profiles = [_profile("a", "A", FLAT_DIFFERENT)]
    result = matcher.match(PEAKED, profiles, _cfg())
    assert result["profile_id"] == matcher.UNKNOWN_PROFILE_ID
    assert result["confidence"] < _cfg()["identity"]["match_confidence_threshold"]


def test_match_excludes_revoked_profiles():
    profiles = [_profile("b", "B", PEAKED, revoked_at="2026-01-01T00:00:00+00:00")]
    result = matcher.match(PEAKED, profiles, _cfg())
    assert result["profile_id"] == matcher.UNKNOWN_PROFILE_ID


def test_match_reports_unknown_with_no_profiles():
    assert matcher.match(PEAKED, [], _cfg()) == {"profile_id": "unknown", "display_name": None, "confidence": 0.0}


# -- run_live_match ------------------------------------------------------------

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


def test_run_live_match_matches_registered_profile_persists_and_publishes(tmp_path):
    chronological = [_r(9, 0, 1.0), _r(9, 5, 1.0), _r(14, 0, 0.0)]
    live_signature = build_signature(chronological)
    profile = _profile("person_a_abc123", "Person A", live_signature)

    matches_path = tmp_path / "matches.jsonl"
    received = []
    bus.subscribe("identity_matched", received.append)

    record = matcher.run_live_match(
        ts_store=_FakeTsStore(list(reversed(chronological))),
        registry=REGISTRY,
        profiles=[profile],
        cfg=_cfg(),
        matches_path=matches_path,
    )

    assert record["profile_id"] == "person_a_abc123"
    assert record["confidence"] == 1.0
    assert received == [record]
    assert json.loads(matches_path.read_text().strip())["profile_id"] == "person_a_abc123"


def test_run_live_match_reports_unknown_when_pattern_does_not_resemble_any_profile(tmp_path):
    # An empty live window -> uniform/zeroed signature, far from a peaked profile.
    profile = _profile("person_a_abc123", "Person A", PEAKED)
    matches_path = tmp_path / "matches.jsonl"

    record = matcher.run_live_match(
        ts_store=_FakeTsStore([]),
        registry=REGISTRY,
        profiles=[profile],
        cfg=_cfg(),
        matches_path=matches_path,
    )

    assert record["profile_id"] == matcher.UNKNOWN_PROFILE_ID
    assert json.loads(matches_path.read_text().strip())["profile_id"] == matcher.UNKNOWN_PROFILE_ID


def test_run_live_match_handles_missing_motion_sensor(tmp_path):
    matches_path = tmp_path / "matches.jsonl"
    record = matcher.run_live_match(
        ts_store=_FakeTsStore([]),
        registry={"sensors": []},
        profiles=[],
        cfg=_cfg(),
        matches_path=matches_path,
    )
    assert record["profile_id"] == matcher.UNKNOWN_PROFILE_ID
    assert record["confidence"] == 0.0
