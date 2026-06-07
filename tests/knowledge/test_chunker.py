from datetime import datetime, timedelta, timezone

import pytest

from src.ingestion.schema import SensorConfig, TelemetryReading
from src.knowledge import chunker

SENSOR = SensorConfig(
    id="temp_01", name="Living room temperature", type="temperature", unit="C",
    location="living_room", expected_range=(-10, 50), reporting_interval_s=30,
)
HF_SENSOR = SensorConfig(
    id="vib_01", name="Vibration", type="vibration", unit="g",
    location="workshop", expected_range=(0, 10), reporting_interval_s=0,
)


def _reading(value: float, minute: int, outlier: bool = False) -> TelemetryReading:
    return TelemetryReading(
        sensor_id="temp_01",
        timestamp=datetime(2026, 6, 7, 12, minute, tzinfo=timezone.utc),
        value=value,
        unit="C",
        outlier=outlier,
    )


def test_is_high_frequency():
    assert chunker.is_high_frequency(HF_SENSOR) is True
    assert chunker.is_high_frequency(SENSOR) is False


def test_aggregate_chunk_requires_readings():
    with pytest.raises(ValueError):
        chunker.aggregate_chunk(SENSOR, [])


def test_aggregate_chunk_computes_stats_and_trend():
    readings = [_reading(20.0, 0), _reading(21.0, 1), _reading(22.0, 2)]
    chunk = chunker.aggregate_chunk(SENSOR, readings, window_s=60)

    assert chunk.chunk_type == "aggregate"
    assert chunk.sensor_id == "temp_01"
    assert chunk.value == pytest.approx(21.0)
    assert chunk.tags["n"] == "3"
    assert chunk.tags["trend"] == "rising"
    assert "min=20.000" in chunk.text
    assert "max=22.000" in chunk.text
    assert "trend=rising" in chunk.text


def test_aggregate_chunk_flags_outlier_if_any_reading_is():
    readings = [_reading(20.0, 0), _reading(99.0, 1, outlier=True)]
    chunk = chunker.aggregate_chunk(SENSOR, readings)
    assert chunk.outlier is True


def test_trend_detection():
    assert chunker._trend([20.0, 20.1]) == "flat"
    assert chunker._trend([20.0, 25.0]) == "rising"
    assert chunker._trend([25.0, 20.0]) == "falling"
    assert chunker._trend([20.0]) == "flat"
