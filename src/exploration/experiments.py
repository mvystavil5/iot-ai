"""
Experiment runners dispatched by the scheduler — see .claude/agents/explorer.md
§ Experiment scheduling. Each runner takes a hypothesis dict (as produced by
hypothesis_generator.generate) and returns a result ready for
outcomes.record_outcome():

  {"outcome": "confirmed" | "refuted" | "inconclusive",
   "confidence_delta": float, "evidence": str, "new_chunks": [...]}

Phase 1 has no actuation hardware (no "active query" capability yet — see
explorer.md § Experiment scheduling), so all three runners work from data
already on the board: recent time-series history, the sensor registry's
expected_range, and cheap synthetic random-walk simulation.
"""

from __future__ import annotations

import logging
import random

from src.config import load_sensor_registry
from src.ingestion.simulator import random_walk
from src.ingestion.storage import TimeSeriesStore

log = logging.getLogger(__name__)

OBSERVATION_WINDOW = 50    # readings per sensor pulled for trend comparison
MIN_READINGS_FOR_TREND = 2
SIMULATION_STEPS = 20
_FLAT_THRESHOLD = 0.02


def _trend(values: list[float]) -> str:
    if len(values) < MIN_READINGS_FOR_TREND:
        return "flat"
    delta = values[-1] - values[0]
    span = max(abs(v) for v in values) or 1.0
    if abs(delta) / span < _FLAT_THRESHOLD:
        return "flat"
    return "rising" if delta > 0 else "falling"


def _result(outcome: str, confidence_delta: float, evidence: str) -> dict:
    return {"outcome": outcome, "confidence_delta": confidence_delta, "evidence": evidence, "new_chunks": []}


def run_observation_experiment(hypothesis: dict, *, store: TimeSeriesStore | None = None) -> dict:
    """Re-query existing history for the hypothesis's sensors and check
    whether their recent trends move together — the cheapest, always-on
    experiment type and the only one hypothesis_generator currently emits."""
    store = store or TimeSeriesStore()
    sensor_ids = hypothesis["required_sensor_data"]

    trends: dict[str, str] = {}
    sample_sizes: dict[str, int] = {}
    for sensor_id in sensor_ids:
        readings = store.query(sensor_id, limit=OBSERVATION_WINDOW)
        sample_sizes[sensor_id] = len(readings)
        trends[sensor_id] = _trend([r.value for r in reversed(readings)])  # chronological order for trend

    if any(n < MIN_READINGS_FOR_TREND for n in sample_sizes.values()):
        return _result("inconclusive", 0.0, f"Insufficient history to compare trends: {sample_sizes}")

    distinct = set(trends.values())
    if len(distinct) == 1 and "flat" not in distinct:
        return _result("confirmed", 0.15, f"Trends moved together: {trends}")
    if distinct == {"rising", "falling"}:
        return _result("refuted", -0.1, f"Trends diverged: {trends}")
    return _result("inconclusive", 0.0, f"No clear shared trend: {trends}")


def run_alert_experiment(hypothesis: dict, *, store: TimeSeriesStore | None = None, registry: dict | None = None) -> dict:
    """Check whether the hypothesis's primary sensor is currently outside
    its configured expected_range — Phase 1's stand-in for a persistent
    threshold-alert watcher (no actuation hardware to host one on yet)."""
    store = store or TimeSeriesStore()
    registry = registry or load_sensor_registry()
    sensor_id = hypothesis["required_sensor_data"][0]
    sensor = next((s for s in registry.get("sensors", []) if s["id"] == sensor_id), None)
    latest = store.query(sensor_id, limit=1)

    if sensor is None or not latest:
        return _result("inconclusive", 0.0, f"No recent data available for {sensor_id}.")

    lo, hi = sensor["expected_range"]
    value = latest[0].value
    if lo <= value <= hi:
        return _result("refuted", -0.05, f"{sensor_id}={value} is within expected range [{lo}, {hi}].")
    return _result("confirmed", 0.15, f"{sensor_id}={value} is outside expected range [{lo}, {hi}].")


def run_simulation_experiment(
    hypothesis: dict,
    *,
    registry: dict | None = None,
    walk_fn=random_walk,
    steps: int = SIMULATION_STEPS,
    rng=random,
) -> dict:
    """Generate cheap synthetic random-walk series for the hypothesis's
    sensors and check whether their simulated trends align — a stand-in for
    physical perturbation experiments while the board has no actuators."""
    registry = registry or load_sensor_registry()
    sensors = {s["id"]: s for s in registry.get("sensors", [])}

    trends: dict[str, str] = {}
    for sensor_id in hypothesis["required_sensor_data"]:
        sensor = sensors.get(sensor_id)
        if sensor is None:
            return _result("inconclusive", 0.0, f"Sensor {sensor_id} not found in registry.")
        lo, hi = sensor["expected_range"]
        value = rng.uniform(lo, hi)
        series = [value]
        for _ in range(steps):
            value = walk_fn(value, lo, hi)
            series.append(value)
        trends[sensor_id] = _trend(series)

    distinct = set(trends.values())
    if len(distinct) == 1 and "flat" not in distinct:
        return _result("confirmed", 0.05, f"Simulated trends moved together: {trends}")
    return _result("inconclusive", 0.0, f"Simulated trends did not align: {trends}")
