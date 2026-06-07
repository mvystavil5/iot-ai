"""
Ingestion pipeline — the entry point for all sensor data (see
.claude/agents/ingestion.md). Validates a raw payload against the
TelemetryReading schema and the sensor registry, normalizes its unit,
persists it to the time-series store, and emits a KnowledgeChunk on the
`knowledge_chunks` topic for the Knowledge Builder.

  from src.ingestion.pipeline import IngestionPipeline
  IngestionPipeline().ingest({"sensor_id": "temp_01", "timestamp": "...", "value": 22.4, "unit": "C"})
"""

from __future__ import annotations

import logging
import uuid
from datetime import timezone
from typing import Any

from pydantic import ValidationError

from src.config import load_sensor_registry
from src.events import bus
from src.ingestion.normalizer import normalize
from src.ingestion.schema import KnowledgeChunk, SensorConfig, TelemetryReading
from src.ingestion.storage import DEFAULT_DB_PATH, TimeSeriesStore

log = logging.getLogger(__name__)


class IngestionError(ValueError):
    """Raised when a raw payload fails schema validation — the ingestion
    agent's "missing required fields -> reject with 422" rule (see
    .claude/agents/ingestion.md § Validation errors)."""


def _registry_by_id(registry: dict) -> dict[str, SensorConfig]:
    return {s["id"]: SensorConfig(**s) for s in registry.get("sensors", [])}


def _is_outlier(sensor: SensorConfig | None, value: float) -> bool:
    if sensor is None:
        return False
    lo, hi = sensor.expected_range
    return not (lo <= value <= hi)


def _chunk_text(reading: TelemetryReading, sensor: SensorConfig | None) -> str:
    """Human-readable representation for embedding — the exact template
    from .claude/agents/knowledge-builder.md § Core loop, step 2."""
    location = sensor.location if sensor else "unknown"
    tag_str = ", ".join(f"{k}={v}" for k, v in reading.tags.items()) or "none"
    return (
        f"Sensor {reading.sensor_id} reported {reading.value}{reading.unit} "
        f"at {reading.timestamp.isoformat()}. Location: {location}. Tags: {tag_str}."
    )


def to_chunk(reading: TelemetryReading, sensor: SensorConfig | None = None) -> KnowledgeChunk:
    """Build the KnowledgeChunk event the Knowledge Builder consumes."""
    return KnowledgeChunk(
        chunk_id=f"{reading.sensor_id}-{reading.timestamp.isoformat()}-{uuid.uuid4().hex[:8]}",
        sensor_id=reading.sensor_id,
        timestamp=reading.timestamp,
        text=_chunk_text(reading, sensor),
        value=reading.value,
        unit=reading.unit,
        outlier=reading.outlier,
        tags=reading.tags,
        chunk_type="single",
    )


class IngestionPipeline:
    """validate -> normalize -> store -> emit chunk.

    One instance per process — share it across the API layer, bridges, and
    simulator so they all write through the same store and registry (the
    Phase 1 single-board deployment runs them in-process together)."""

    def __init__(self, store: TimeSeriesStore | None = None, registry: dict | None = None) -> None:
        self.store = store or TimeSeriesStore(DEFAULT_DB_PATH)
        self._sensors = _registry_by_id(registry if registry is not None else load_sensor_registry())

    def ingest(self, raw: dict[str, Any]) -> TelemetryReading:
        """Run one raw payload through the full pipeline; returns the stored
        (normalized) TelemetryReading. Raises IngestionError on schema
        violations; unknown sensor IDs are accepted but logged."""
        try:
            reading = TelemetryReading(**raw)
        except ValidationError as exc:
            raise IngestionError(f"Invalid telemetry payload: {exc}") from exc

        if reading.timestamp.tzinfo is None:
            reading = reading.model_copy(update={"timestamp": reading.timestamp.replace(tzinfo=timezone.utc)})

        sensor = self._sensors.get(reading.sensor_id)
        if sensor is None:
            log.warning("Unknown sensor_id '%s' — not in config/sensors.yaml", reading.sensor_id)

        normalized_value, normalized_unit, unit_normalized = normalize(
            sensor.type if sensor else "", reading.value, reading.unit
        )
        outlier = reading.outlier or _is_outlier(sensor, normalized_value)
        reading = reading.model_copy(update={
            "value": normalized_value,
            "unit": normalized_unit,
            "unit_normalized": unit_normalized,
            "outlier": outlier,
        })

        is_new = self.store.insert(reading)
        if not is_new:
            log.debug("Duplicate reading %s@%s — stored once, no chunk re-emitted",
                      reading.sensor_id, reading.timestamp.isoformat())
            return reading

        bus.publish("knowledge_chunks", to_chunk(reading, sensor))
        log.info("Ingested %s=%s%s (outlier=%s)", reading.sensor_id, reading.value, reading.unit, reading.outlier)
        return reading
