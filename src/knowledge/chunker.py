"""
Chunking strategies beyond the single-reading chunks the ingestion pipeline
already emits (see .claude/agents/knowledge-builder.md § Chunking strategy):
60-second statistical aggregates for high-frequency (>1 Hz) sensors —
min/max/mean/stddev/trend — so a burst of readings collapses into one
retrievable chunk instead of flooding the store.
"""

from __future__ import annotations

import statistics
import uuid

from src.ingestion.schema import KnowledgeChunk, SensorConfig, TelemetryReading

HIGH_FREQUENCY_THRESHOLD_S = 1.0  # sensors reporting faster than 1 Hz get aggregated
DEFAULT_WINDOW_S = 60
_FLAT_THRESHOLD = 0.02  # relative change below this counts as "flat"


def is_high_frequency(sensor: SensorConfig) -> bool:
    return sensor.reporting_interval_s < HIGH_FREQUENCY_THRESHOLD_S


def _trend(values: list[float]) -> str:
    if len(values) < 2:
        return "flat"
    delta = values[-1] - values[0]
    span = max(abs(v) for v in values) or 1.0
    if abs(delta) / span < _FLAT_THRESHOLD:
        return "flat"
    return "rising" if delta > 0 else "falling"


def aggregate_chunk(sensor: SensorConfig, readings: list[TelemetryReading], window_s: int = DEFAULT_WINDOW_S) -> KnowledgeChunk:
    """Collapse a burst of same-sensor readings (assumed chronologically
    ordered, oldest first) into one aggregate chunk per the representation
    knowledge-builder.md prescribes for high-frequency sensors."""
    if not readings:
        raise ValueError("aggregate_chunk requires at least one reading")

    values = [r.value for r in readings]
    lo, hi, mean = min(values), max(values), statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    trend = _trend(values)
    last = readings[-1]

    text = (
        f"Sensor {sensor.id} over the last {window_s}s ({len(readings)} readings): "
        f"min={lo:.3f}{last.unit}, max={hi:.3f}{last.unit}, mean={mean:.3f}{last.unit}, "
        f"stddev={stdev:.3f}, trend={trend}. Location: {sensor.location}."
    )
    return KnowledgeChunk(
        chunk_id=f"{sensor.id}-agg-{last.timestamp.isoformat()}-{uuid.uuid4().hex[:8]}",
        sensor_id=sensor.id,
        timestamp=last.timestamp,
        text=text,
        value=mean,
        unit=last.unit,
        outlier=any(r.outlier for r in readings),
        tags={"window_s": str(window_s), "n": str(len(readings)), "trend": trend},
        chunk_type="aggregate",
    )
