from datetime import datetime, timezone

from src.ingestion.schema import SensorConfig, TelemetryReading
from src.knowledge import event_chunk

SENSOR = SensorConfig(
    id="temp_01", name="Living room temperature", type="temperature", unit="C",
    location="living_room", expected_range=(-10, 50), reporting_interval_s=30,
)


def _reading(outlier: bool) -> TelemetryReading:
    return TelemetryReading(
        sensor_id="temp_01",
        timestamp=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
        value=99.0 if outlier else 22.0,
        unit="C",
        outlier=outlier,
    )


def test_detect_crossing_returns_none_without_previous():
    assert event_chunk.detect_crossing(None, _reading(outlier=True)) is None


def test_detect_crossing_returns_none_when_state_unchanged():
    assert event_chunk.detect_crossing(_reading(outlier=False), _reading(outlier=False)) is None


def test_detect_crossing_into_outlier_range():
    assert event_chunk.detect_crossing(_reading(outlier=False), _reading(outlier=True)) == "entered_outlier_range"


def test_detect_crossing_back_to_normal():
    assert event_chunk.detect_crossing(_reading(outlier=True), _reading(outlier=False)) == "returned_to_normal"


def test_to_event_chunk_carries_weight_and_event_type_in_tags():
    chunk = event_chunk.to_event_chunk(_reading(outlier=True), "entered_outlier_range", SENSOR)
    assert chunk.chunk_type == "event"
    assert chunk.tags["event_type"] == "entered_outlier_range"
    assert chunk.tags["weight"] == event_chunk.EVENT_WEIGHT
    assert "EVENT (entered_outlier_range)" in chunk.text
    assert "living_room" in chunk.text
