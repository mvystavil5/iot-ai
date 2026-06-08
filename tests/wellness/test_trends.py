import pytest

from src.events import bus
from src.wellness import store, trends

REGISTRY = {"sensors": [{"id": "motion_01", "name": "Living room motion", "type": "motion", "location": "living_room"}]}


@pytest.fixture(autouse=True)
def _clear_bus():
    bus.clear()
    yield
    bus.clear()


def _cfg(**overrides):
    wellness = {
        "recent_window_days": 7,
        "baseline_window_days": 21,
        "min_days_per_window": 3,
        "trend_alert_minutes": 30.0,
    }
    wellness.update(overrides)
    return {"wellness": wellness}


def _day(day, active, sedentary, streak=600.0):
    return {"day": day, "active_minutes": active, "sedentary_minutes": sedentary,
            "longest_sedentary_streak_min": streak, "activity_sessions": 3,
            "mean_session_length_min": 20.0, "sample_size": 50}


# -- score_trend ----------------------------------------------------------------

def test_score_trend_reports_signed_average_deltas():
    recent = [_day("d4", 100.0, 1340.0), _day("d5", 100.0, 1340.0)]
    baseline = [_day("d1", 160.0, 1280.0), _day("d2", 160.0, 1280.0)]

    deltas = trends.score_trend(recent, baseline)

    assert deltas["active_minutes_delta"] == -60.0
    assert deltas["sedentary_minutes_delta"] == 60.0


def test_score_trend_handles_empty_inputs():
    assert trends.score_trend([], []) == {
        "active_minutes_delta": 0.0,
        "sedentary_minutes_delta": 0.0,
        "longest_sedentary_streak_delta": 0.0,
    }


# -- detect_trend ----------------------------------------------------------------

def test_detect_trend_reports_more_sedentary_when_still_time_grows():
    recent = [_day(f"r{i}", 60.0, 1380.0, streak=900.0) for i in range(7)]
    baseline = [_day(f"b{i}", 160.0, 1280.0, streak=500.0) for i in range(21)]

    result = trends.detect_trend(recent, baseline, _cfg())

    assert result["status"] == trends.STATUS_MORE_SEDENTARY
    assert result["sedentary_minutes_delta"] > 0


def test_detect_trend_reports_more_active_when_still_time_shrinks():
    recent = [_day(f"r{i}", 200.0, 1240.0, streak=300.0) for i in range(7)]
    baseline = [_day(f"b{i}", 100.0, 1340.0, streak=600.0) for i in range(21)]

    result = trends.detect_trend(recent, baseline, _cfg())

    assert result["status"] == trends.STATUS_MORE_ACTIVE


def test_detect_trend_reports_stable_when_within_threshold():
    recent = [_day(f"r{i}", 110.0, 1330.0, streak=600.0) for i in range(7)]
    baseline = [_day(f"b{i}", 100.0, 1340.0, streak=600.0) for i in range(21)]

    result = trends.detect_trend(recent, baseline, _cfg())

    assert result["status"] == trends.STATUS_STABLE


def test_detect_trend_reports_insufficient_data_with_too_few_days():
    recent = [_day("r0", 100.0, 1340.0)]
    baseline = [_day(f"b{i}", 100.0, 1340.0) for i in range(21)]

    result = trends.detect_trend(recent, baseline, _cfg())

    assert result == {"status": trends.STATUS_INSUFFICIENT_DATA, "active_minutes_delta": 0.0,
                      "sedentary_minutes_delta": 0.0, "longest_sedentary_streak_delta": 0.0}


# -- run_trend_check -------------------------------------------------------------

def test_run_trend_check_persists_and_publishes_checked_and_risk_events(tmp_path):
    daily_path = tmp_path / "daily.jsonl"
    trends_path = tmp_path / "trends.jsonl"
    for i in range(21):
        store._append(daily_path, _day(f"b{i}", 160.0, 1280.0, streak=500.0))
    for i in range(7):
        store._append(daily_path, _day(f"r{i}", 60.0, 1380.0, streak=900.0))

    checked = []
    flagged = []
    bus.subscribe("wellness_trend_checked", checked.append)
    bus.subscribe("wellness_risk_flagged", flagged.append)

    record = trends.run_trend_check(daily_path=daily_path, trends_path=trends_path, cfg=_cfg())

    assert record["status"] == trends.STATUS_MORE_SEDENTARY
    assert checked == [record]
    assert flagged == [record]
    assert store.load_trend_checks(trends_path) == [record]


def test_run_trend_check_does_not_publish_risk_event_when_stable(tmp_path):
    daily_path = tmp_path / "daily.jsonl"
    trends_path = tmp_path / "trends.jsonl"
    for i in range(28):
        store._append(daily_path, _day(f"d{i}", 100.0, 1340.0, streak=600.0))

    flagged = []
    bus.subscribe("wellness_risk_flagged", flagged.append)

    record = trends.run_trend_check(daily_path=daily_path, trends_path=trends_path, cfg=_cfg())

    assert record["status"] == trends.STATUS_STABLE
    assert flagged == []


def test_run_trend_check_reports_insufficient_data_when_history_is_short(tmp_path):
    daily_path = tmp_path / "daily.jsonl"
    trends_path = tmp_path / "trends.jsonl"
    store._append(daily_path, _day("d0", 100.0, 1340.0))

    record = trends.run_trend_check(daily_path=daily_path, trends_path=trends_path, cfg=_cfg())

    assert record["status"] == trends.STATUS_INSUFFICIENT_DATA
