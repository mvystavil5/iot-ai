"""
Event chunk detection — wraps a reading that crosses into or out of its
expected range into a dedicated, higher-weight KnowledgeChunk
(chunk_type="event"), per .claude/agents/knowledge-builder.md § Chunking
strategy ("create a dedicated event chunk with higher retrieval weight").

KnowledgeChunk has no native weight field, so the weight is carried in
`tags["weight"]` (tags is a dict[str, str]) for the retriever to read.
"""

from __future__ import annotations

import uuid

from src.ingestion.schema import KnowledgeChunk, SensorConfig, TelemetryReading

EVENT_WEIGHT = "2.0"


def detect_crossing(previous: TelemetryReading | None, current: TelemetryReading) -> str | None:
    """Return an event label when `current.outlier` differs from
    `previous.outlier`, else None. The first reading for a sensor
    (`previous is None`) is never an event — there's nothing to compare to."""
    if previous is None or previous.outlier == current.outlier:
        return None
    return "entered_outlier_range" if current.outlier else "returned_to_normal"


def to_event_chunk(reading: TelemetryReading, event_type: str, sensor: SensorConfig | None = None) -> KnowledgeChunk:
    location = sensor.location if sensor else "unknown"
    direction = "now outside" if reading.outlier else "back within"
    text = (
        f"EVENT ({event_type}): sensor {reading.sensor_id} reported {reading.value}{reading.unit} "
        f"at {reading.timestamp.isoformat()}, location {location} — {direction} its expected range."
    )
    return KnowledgeChunk(
        chunk_id=f"{reading.sensor_id}-event-{reading.timestamp.isoformat()}-{uuid.uuid4().hex[:8]}",
        sensor_id=reading.sensor_id,
        timestamp=reading.timestamp,
        text=text,
        value=reading.value,
        unit=reading.unit,
        outlier=reading.outlier,
        tags={**reading.tags, "event_type": event_type, "weight": EVENT_WEIGHT},
        chunk_type="event",
    )
