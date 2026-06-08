from datetime import datetime, timedelta, timezone

import pytest

from src.events import bus
from src.identity import registration, store
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


def _stub_profile(profile_id, display_name, revoked_at=None):
    return {
        "profile_id": profile_id,
        "display_name": display_name,
        "consent_at": "2026-01-01T00:00:00+00:00",
        "window": {"start": "2026-01-01T00:00:00+00:00", "end": "2026-01-01T01:00:00+00:00"},
        "signature": {"presence_ratio": 0.5, "hourly_activity": [0.0] * 24, "mean_session_length_min": 0.0, "sample_size": 1},
        "revoked_at": revoked_at,
    }


def _stub_match(profile_id):
    return {"profile_id": profile_id, "display_name": "Whoever", "confidence": 0.9, "matched_at": "2026-01-01T00:30:00+00:00"}


# -- register ------------------------------------------------------------------

def test_register_builds_persists_and_publishes_profile(tmp_path):
    profiles_path = tmp_path / "profiles.jsonl"
    chronological = [_r(9, 0, 1.0), _r(9, 5, 1.0)]
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    received = []
    bus.subscribe("identity_registered", received.append)

    profile = registration.register(
        "Person A", start, end,
        ts_store=_FakeTsStore(list(reversed(chronological))),
        registry=REGISTRY,
        profiles_path=profiles_path,
    )

    assert profile["display_name"] == "Person A"
    assert profile["profile_id"].startswith("person_a_")
    assert profile["revoked_at"] is None
    assert profile["signature"]["sample_size"] == 2
    assert received == [profile]
    assert store.load_profiles(profiles_path) == [profile]


def test_register_filters_readings_outside_window(tmp_path):
    profiles_path = tmp_path / "profiles.jsonl"
    in_window = _r(9, 0, 1.0)
    after_window = _r(11, 0, 1.0)
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    profile = registration.register(
        "Person A", start, end,
        ts_store=_FakeTsStore([after_window, in_window]),  # most-recent-first
        registry=REGISTRY,
        profiles_path=profiles_path,
    )

    assert profile["signature"]["sample_size"] == 1


def test_register_raises_without_motion_sensor(tmp_path):
    with pytest.raises(ValueError, match="motion sensor"):
        registration.register(
            "Person A",
            datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 9, tzinfo=timezone.utc),
            ts_store=_FakeTsStore([]),
            registry=NO_MOTION_REGISTRY,
            profiles_path=tmp_path / "profiles.jsonl",
        )


# -- list_profiles --------------------------------------------------------------

def test_list_profiles_excludes_revoked_and_omits_signature(tmp_path):
    profiles_path = tmp_path / "profiles.jsonl"
    store._append(profiles_path, _stub_profile("person_a_1", "Person A"))
    store._append(profiles_path, _stub_profile("person_b_2", "Person B", revoked_at="2026-02-01T00:00:00+00:00"))

    listed = registration.list_profiles(profiles_path=profiles_path)

    assert len(listed) == 1
    assert listed[0]["profile_id"] == "person_a_1"
    assert "signature" not in listed[0]


# -- revoke ----------------------------------------------------------------------

def test_revoke_hard_deletes_profile_and_purges_matching_records(tmp_path):
    profiles_path = tmp_path / "profiles.jsonl"
    matches_path = tmp_path / "matches.jsonl"
    store._append(profiles_path, _stub_profile("person_a_1", "Person A"))
    store._append(profiles_path, _stub_profile("person_b_2", "Person B"))
    store._append(matches_path, _stub_match("person_a_1"))
    store._append(matches_path, _stub_match("person_b_2"))
    store._append(matches_path, _stub_match("unknown"))

    received = []
    bus.subscribe("identity_revoked", received.append)

    assert registration.revoke("person_a_1", profiles_path=profiles_path, matches_path=matches_path) is True

    remaining_profiles = store.load_profiles(profiles_path)
    assert [p["profile_id"] for p in remaining_profiles] == ["person_b_2"]

    remaining_matches = store.load_matches(matches_path)
    assert [m["profile_id"] for m in remaining_matches] == ["person_b_2", "unknown"]
    assert not any(m["profile_id"] == "person_a_1" for m in remaining_matches)

    assert received == [{"profile_id": "person_a_1", "revoked_at": received[0]["revoked_at"]}]


def test_revoke_returns_false_for_unknown_profile(tmp_path):
    profiles_path = tmp_path / "profiles.jsonl"
    store._append(profiles_path, _stub_profile("person_a_1", "Person A"))

    received = []
    bus.subscribe("identity_revoked", received.append)

    assert registration.revoke("does_not_exist", profiles_path=profiles_path, matches_path=tmp_path / "matches.jsonl") is False
    assert received == []
    assert len(store.load_profiles(profiles_path)) == 1
