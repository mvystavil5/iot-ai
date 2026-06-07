"""
Hypothesis generation — turn low-confidence or recently-invalidated beliefs
into ranked, falsifiable hypotheses about relationships between co-located
sensors (see .claude/agents/explorer.md § Hypothesis generation and
§ Active learning strategy: "start simple — temperature -> humidity
correlation — escalate to multi-sensor causal graphs").

  python -m src.exploration.hypothesis_generator [--run] [--top-n N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config import load_sensor_registry
from src.ingestion.storage import TimeSeriesStore
from src.model.beliefs import BeliefStore

log = logging.getLogger(__name__)

DEFAULT_QUEUE_PATH = Path("./data/hypothesis_queue.jsonl")
LOW_CONFIDENCE = 0.5
MIN_HISTORY_FOR_FEASIBILITY = 10
EXPERIMENT_COST = 0.2  # all Phase-1 hypotheses are observation experiments — cheap, no actuation

# Sensor-type pairs worth probing first, in priority order — "start simple
# (temperature -> humidity correlation), escalate to multi-sensor causal
# graphs" (explorer.md § Active learning strategy).
CANDIDATE_RELATIONSHIPS: list[tuple[str, str]] = [
    ("temperature", "humidity"),
    ("motion", "co2"),
    ("motion", "temperature"),
    ("temperature", "co2"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hypothesis_id(sensor_a_id: str, sensor_b_id: str) -> str:
    digest = hashlib.sha256(f"{sensor_a_id}:{sensor_b_id}".encode("utf-8")).hexdigest()
    return f"hyp_{digest[:12]}"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _trigger_beliefs(beliefs: list[dict]) -> list[dict]:
    """Beliefs worth exploring further — explorer.md step 2: "Identify
    beliefs with confidence < 0.5 or that were recently invalidated"."""
    return [b for b in beliefs if b.get("confidence", 1.0) < LOW_CONFIDENCE or b.get("invalidated_at") is not None]


def _sensors_by_type(registry: dict) -> dict[str, dict]:
    by_type: dict[str, dict] = {}
    for sensor in registry.get("sensors", []):
        by_type.setdefault(sensor["type"], sensor)
    return by_type


def _information_gain(trigger_count: int) -> float:
    """More open questions about the environment -> confirming or refuting
    a relationship teaches the model more. Capped at 1.0."""
    return min(1.0, 0.3 + 0.1 * trigger_count)


def _feasibility(sensor_a: dict, sensor_b: dict, store: TimeSeriesStore) -> float:
    """A relationship is only testable if both sensors have enough history
    to compare trends against (see experiments.run_observation_experiment)."""
    history = min(store.count(sensor_a["id"]), store.count(sensor_b["id"]))
    if history == 0:
        return 0.1
    return 1.0 if history >= MIN_HISTORY_FOR_FEASIBILITY else 0.5


def _build_hypothesis(sensor_a: dict, sensor_b: dict, *, trigger_count: int, store: TimeSeriesStore) -> dict:
    gain = _information_gain(trigger_count)
    feasibility = _feasibility(sensor_a, sensor_b, store)
    score = (gain * feasibility) / EXPERIMENT_COST
    return {
        "hypothesis_id": hypothesis_id(sensor_a["id"], sensor_b["id"]),
        "created_at": _now_iso(),
        "statement": (
            f"Given that {sensor_a['name']} and {sensor_b['name']} are co-located "
            f"in {sensor_a['location']}, I hypothesize that {sensor_a['type']} "
            f"changes correlate with {sensor_b['type']} changes."
        ),
        "falsification_condition": (
            f"{sensor_a['id']} and {sensor_b['id']} readings show no aligned trend "
            f"over a sustained observation window."
        ),
        "required_sensor_data": [sensor_a["id"], sensor_b["id"]],
        "experiment_type": "observation",
        "information_gain": gain,
        "feasibility": feasibility,
        "cost": EXPERIMENT_COST,
        "score": score,
        "status": "pending",
    }


def generate(beliefs=None, registry=None, store=None, cfg=None) -> list[dict]:
    """Rank candidate hypotheses by (information_gain x feasibility) / cost
    — explorer.md step 4. Returns [] when no belief is currently uncertain
    enough to warrant exploration, or when the registry has no sensor pair
    matching CANDIDATE_RELATIONSHIPS."""
    beliefs = BeliefStore(cfg=cfg).all() if beliefs is None else beliefs
    registry = load_sensor_registry() if registry is None else registry
    store = TimeSeriesStore() if store is None else store

    triggers = _trigger_beliefs(beliefs)
    if not triggers:
        return []

    by_type = _sensors_by_type(registry)
    hypotheses = []
    for type_a, type_b in CANDIDATE_RELATIONSHIPS:
        sensor_a, sensor_b = by_type.get(type_a), by_type.get(type_b)
        if sensor_a is None or sensor_b is None or sensor_a["id"] == sensor_b["id"]:
            continue
        hypotheses.append(_build_hypothesis(sensor_a, sensor_b, trigger_count=len(triggers), store=store))

    hypotheses.sort(key=lambda h: h["score"], reverse=True)
    return hypotheses


def load_queue(path: Path | None = None) -> list[dict]:
    return _read_jsonl(path or DEFAULT_QUEUE_PATH)


def _append_to_queue(hypotheses: list[dict], path: Path | None = None) -> None:
    path = path or DEFAULT_QUEUE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for h in hypotheses:
            f.write(json.dumps(h) + "\n")


def run(top_n: int = 1, *, path: Path | None = None, **kwargs) -> list[dict]:
    """Generate hypotheses and append the `top_n` highest-ranked to the
    queue — explorer.md step 5: "Store the top hypothesis in
    data/hypothesis_queue.jsonl"."""
    top = generate(**kwargs)[:top_n]
    if top:
        _append_to_queue(top, path)
        log.info("Queued %d hypothesis(es); top: %s", len(top), top[0]["hypothesis_id"])
    return top


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate ranked hypotheses from the current belief state")
    p.add_argument("--run", action="store_true", help="Generate and queue the top-ranked hypothesis(es)")
    p.add_argument("--top-n", type=int, default=1, help="How many ranked hypotheses to queue")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    if args.run:
        queued = run(top_n=args.top_n)
        if not queued:
            print("No hypotheses generated — nothing currently uncertain enough to explore.")
        for h in queued:
            print(f"Queued {h['hypothesis_id']} (score={h['score']:.2f}): {h['statement']}")
    else:
        for h in generate():
            print(f"{h['score']:.2f}  {h['hypothesis_id']}  {h['statement']}")
