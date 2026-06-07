from pathlib import Path

import pytest

from src.events import bus
from src.ingestion import normalizer
from src.ingestion.pipeline import IngestionError, IngestionPipeline, to_chunk
from src.ingestion.schema import KnowledgeChunk, TelemetryReading
from src.ingestion.storage import TimeSeriesStore

REGISTRY = {
    "sensors": [
        {"id": "temp_01", "name": "Living room temperature", "type": "temperature", "unit": "C",
         "location": "living_room", "expected_range": [-10, 50], "reporting_interval_s": 30},
        {"id": "co2_01", "name": "Living room CO2", "type": "co2", "unit": "ppm",
         "location": "living_room", "expected_range": [300, 5000], "reporting_interval_s": 30},
    ]
}


def _pipeline(tmp_path: Path) -> IngestionPipeline:
    return IngestionPipeline(store=TimeSeriesStore(tmp_path / "ts.db"), registry=REGISTRY)


def _payload(**overrides) -> dict:
    payload = {
        "sensor_id": "temp_01",
        "timestamp": "2026-06-07T12:00:00+00:00",
        "value": 22.4,
        "unit": "C",
        "tags": {},
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _clear_bus():
    bus.clear()
    yield
    bus.clear()


# --- normalizer ---

def test_normalize_fahrenheit_to_celsius():
    value, unit, ok = normalizer.normalize("temperature", 212.0, "F")
    assert unit == "C"
    assert ok is True
    assert value == pytest.approx(100.0)


def test_normalize_passthrough_for_canonical_unit():
    assert normalizer.normalize("temperature", 22.4, "C") == (22.4, "C", True)


def test_normalize_unknown_unit_passes_through_flagged():
    value, unit, ok = normalizer.normalize("temperature", 1.0, "furlongs_per_fortnight")
    assert (value, unit) == (1.0, "furlongs_per_fortnight")
    assert ok is False


# --- pipeline.ingest ---

def test_ingest_stores_and_emits_chunk(tmp_path):
    pipeline = _pipeline(tmp_path)
    received: list[KnowledgeChunk] = []
    bus.subscribe("knowledge_chunks", received.append)

    reading = pipeline.ingest(_payload())

    assert isinstance(reading, TelemetryReading)
    assert pipeline.store.count("temp_01") == 1
    assert len(received) == 1
    assert received[0].sensor_id == "temp_01"


def test_ingest_converts_units_and_normalizes(tmp_path):
    pipeline = _pipeline(tmp_path)
    reading = pipeline.ingest(_payload(value=212.0, unit="F"))
    assert reading.unit == "C"
    assert reading.value == pytest.approx(100.0)
    assert reading.unit_normalized is True


def test_ingest_flags_out_of_range_as_outlier(tmp_path):
    pipeline = _pipeline(tmp_path)
    reading = pipeline.ingest(_payload(value=999.0))
    assert reading.outlier is True


def test_ingest_in_range_is_not_an_outlier(tmp_path):
    pipeline = _pipeline(tmp_path)
    reading = pipeline.ingest(_payload(value=22.4))
    assert reading.outlier is False


def test_ingest_deduplicates_silently(tmp_path):
    pipeline = _pipeline(tmp_path)
    received: list[KnowledgeChunk] = []
    bus.subscribe("knowledge_chunks", received.append)

    pipeline.ingest(_payload())
    pipeline.ingest(_payload())  # same sensor_id + timestamp

    assert pipeline.store.count("temp_01") == 1
    assert len(received) == 1  # second ingest stores once, does not re-emit


def test_ingest_rejects_invalid_payload(tmp_path):
    pipeline = _pipeline(tmp_path)
    with pytest.raises(IngestionError):
        pipeline.ingest({"sensor_id": "temp_01"})  # missing timestamp/value/unit


def test_ingest_accepts_unknown_sensor_without_outlier_check(tmp_path):
    pipeline = _pipeline(tmp_path)
    reading = pipeline.ingest(_payload(sensor_id="mystery_01", value=1e9))
    assert reading.sensor_id == "mystery_01"
    assert reading.outlier is False  # no expected_range to compare against


# --- to_chunk ---

def test_to_chunk_builds_human_readable_text():
    reading = TelemetryReading(**_payload())
    chunk = to_chunk(reading)
    assert "temp_01" in chunk.text
    assert "22.4" in chunk.text
    assert chunk.chunk_type == "single"
