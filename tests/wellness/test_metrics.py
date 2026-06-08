from datetime import datetime, timezone

from src.wellness import metrics
from src.ingestion.schema import TelemetryReading

WINDOW_START = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)


def _r(hour, minute, value):
    return TelemetryReading(
        sensor_id="motion_01",
        timestamp=datetime(2026, 1, 1, hour, minute, tzinfo=timezone.utc),
        value=value,
        unit="bool",
    )


def test_empty_day_is_entirely_sedentary():
    summary = metrics.build_daily_summary([], WINDOW_START, WINDOW_END)

    assert summary["day"] == "2026-01-01"
    assert summary["active_minutes"] == 0.0
    assert summary["sedentary_minutes"] == 24 * 60
    assert summary["longest_sedentary_streak_min"] == 24 * 60
    assert summary["activity_sessions"] == 0
    assert summary["mean_session_length_min"] == 0.0
    assert summary["sample_size"] == 0


def test_active_and_sedentary_minutes_sum_to_window_length():
    readings = [_r(9, 0, 1.0), _r(9, 5, 1.0), _r(9, 10, 1.0), _r(14, 0, 0.0), _r(14, 5, 0.0)]
    summary = metrics.build_daily_summary(readings, WINDOW_START, WINDOW_END)

    assert round(summary["active_minutes"] + summary["sedentary_minutes"], 4) == 24 * 60


def test_single_active_session_is_counted_and_timed():
    readings = [_r(9, 0, 1.0), _r(9, 5, 1.0), _r(9, 10, 1.0)]
    summary = metrics.build_daily_summary(readings, WINDOW_START, WINDOW_END)

    assert summary["activity_sessions"] == 1
    assert summary["active_minutes"] == 10.0  # 09:00 -> 09:10
    assert summary["mean_session_length_min"] == 10.0


def test_gap_beyond_session_threshold_splits_into_separate_sessions():
    readings = [_r(9, 0, 1.0), _r(9, 5, 1.0), _r(14, 0, 1.0), _r(14, 5, 1.0)]
    summary = metrics.build_daily_summary(readings, WINDOW_START, WINDOW_END)

    assert summary["activity_sessions"] == 2


def test_longest_sedentary_streak_is_the_largest_gap_between_active_stretches():
    # active 09:00-09:05, then quiet until 20:00 (the longest gap), brief activity, then quiet to midnight
    readings = [_r(9, 0, 1.0), _r(9, 5, 1.0), _r(20, 0, 1.0), _r(20, 5, 1.0)]
    summary = metrics.build_daily_summary(readings, WINDOW_START, WINDOW_END)

    # 09:05 -> 20:00 is ~655 minutes, the largest still stretch in the day
    assert summary["longest_sedentary_streak_min"] == 655.0


def test_sample_size_reflects_total_readings_observed():
    readings = [_r(9, 0, 1.0), _r(9, 5, 0.0), _r(9, 10, 1.0)]
    summary = metrics.build_daily_summary(readings, WINDOW_START, WINDOW_END)

    assert summary["sample_size"] == 3
