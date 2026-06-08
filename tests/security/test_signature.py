from datetime import datetime, timezone

from src.security import signature as sig
from src.ingestion.schema import TelemetryReading


def _r(hour, minute, value):
    return TelemetryReading(
        sensor_id="motion_01",
        timestamp=datetime(2026, 1, 1, hour, minute, tzinfo=timezone.utc),
        value=value,
        unit="bool",
    )


def test_build_signature_empty_history_yields_uniform_flat_signature():
    result = sig.build_signature([])

    assert result["presence_ratio"] == 0.0
    assert result["mean_session_length_min"] == 0.0
    assert result["sample_size"] == 0
    # "no information" is represented as a flat histogram, not "definitely inactive"
    assert result["hourly_activity"] == [round(1.0 / 24, 4)] * 24


def test_presence_ratio_reflects_active_fraction():
    readings = [_r(9, 0, 1.0), _r(9, 5, 1.0), _r(9, 10, 0.0), _r(9, 15, 0.0)]
    result = sig.build_signature(readings)
    assert result["presence_ratio"] == 0.5


def test_hourly_activity_concentrates_on_active_hours():
    readings = [_r(9, 0, 1.0), _r(9, 5, 1.0), _r(14, 0, 0.0)]
    result = sig.build_signature(readings)

    histogram = result["hourly_activity"]
    assert histogram[9] == max(histogram)
    assert histogram[9] > 0
    assert histogram[14] == 0.0
    assert round(sum(histogram), 4) == 1.0


def test_session_length_collapses_contiguous_readings_and_splits_on_gaps():
    # 9:00 -> 9:08 is one contiguous session (gaps <= 10 min); 9:25 starts a
    # new, single-reading session after a 17-minute gap.
    readings = [_r(9, 0, 1.0), _r(9, 5, 1.0), _r(9, 8, 1.0), _r(9, 25, 1.0)]
    result = sig.build_signature(readings)

    # sessions: [8 minutes, 0 minutes] -> mean 4.0
    assert result["mean_session_length_min"] == 4.0
    assert result["sample_size"] == 4
