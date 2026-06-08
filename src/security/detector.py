"""
Occupancy-anomaly detection — compare a live, short-window occupancy
signature against the learned baseline (see learner.py) and report how
closely it resembles "normal" for this space. A live pattern that doesn't
resemble the baseline is flagged **anomalous** — a potential intruder, a
schedule change, anything that looks like "not the usual routine here".

This is a small statistical comparator, not an LLM call: time-series
similarity is exactly the kind of task RAG+LLM is the wrong tool for (see
src/model/rag_confidence.py's "no LLM introspection" precedent — the same
reasoning applies here, just one level lower in the stack). The system never
claims to know *who* is present — only whether the current pattern looks like
the space's established normal.

  python -m src.security.detector --check
  python -m src.security.detector --watch [--interval 300]
"""

from __future__ import annotations

import argparse
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import load_model_config, load_sensor_registry
from src.events import bus
from src.security import store
from src.security.learner import _motion_sensor_id, get_baseline
from src.security.signature import build_signature
from src.ingestion.storage import TimeSeriesStore

log = logging.getLogger(__name__)

STATUS_EXPECTED = "expected"
STATUS_ANOMALOUS = "anomalous"
STATUS_NO_BASELINE = "no_baseline"

DEFAULT_INTERVAL_S = 300.0

_DEFAULT_FEATURE_WEIGHTS = {"hourly_activity": 0.5, "presence_ratio": 0.3, "session_length": 0.2}
_SESSION_LENGTH_SCALE_MIN = 30.0  # difference-decay time constant for session-length closeness

_UNSET = object()  # distinguishes "caller didn't pass a baseline" (load active one) from "explicitly None" (no baseline)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _closeness(a: float, b: float, scale: float) -> float:
    """1.0 when identical, decaying exponentially as the gap grows —
    same shape as rag_confidence's recency decay, applied to feature gaps
    instead of chunk age."""
    return math.exp(-abs(a - b) / scale)


def score_similarity(live: dict, baseline: dict, cfg: dict | None = None) -> float:
    """Weighted similarity in [0, 1] between a live occupancy signature and
    the learned baseline — pure, metadata-only, config-driven weights.
    Mirrors compute_rag_confidence's shape: components in, weighted sum out,
    no I/O.

    Components (weights configurable in config/model.yaml: security.feature_weights):
      hourly_activity — cosine similarity of the 24-bucket activity histograms
      presence_ratio  — closeness of overall active-fraction
      session_length  — closeness of mean contiguous-activity duration
    """
    cfg = cfg or load_model_config()
    weights: dict = cfg.get("security", {}).get("feature_weights", _DEFAULT_FEATURE_WEIGHTS)

    hourly = _cosine_similarity(live["hourly_activity"], baseline["hourly_activity"])
    presence = _closeness(live["presence_ratio"], baseline["presence_ratio"], scale=1.0)
    session = _closeness(live["mean_session_length_min"], baseline["mean_session_length_min"], scale=_SESSION_LENGTH_SCALE_MIN)

    score = (
        weights.get("hourly_activity", _DEFAULT_FEATURE_WEIGHTS["hourly_activity"]) * hourly
        + weights.get("presence_ratio", _DEFAULT_FEATURE_WEIGHTS["presence_ratio"]) * presence
        + weights.get("session_length", _DEFAULT_FEATURE_WEIGHTS["session_length"]) * session
    )
    return round(min(max(score, 0.0), 1.0), 4)


def detect(live: dict, baseline: dict | None, cfg: dict | None = None) -> dict:
    """Classify a live occupancy signature as `expected` (resembles the
    learned baseline), `anomalous` (doesn't — possible intruder or routine
    change), or `no_baseline` (nothing learned yet to compare against).
    Below `security.anomaly_similarity_threshold`, the honest read is
    "this doesn't look like the usual pattern here" — never a forced guess
    at who it might be."""
    cfg = cfg or load_model_config()
    threshold = cfg.get("security", {}).get("anomaly_similarity_threshold", 0.6)

    if baseline is None:
        return {"status": STATUS_NO_BASELINE, "similarity": 0.0}

    similarity = score_similarity(live, baseline["signature"], cfg)
    status = STATUS_EXPECTED if similarity >= threshold else STATUS_ANOMALOUS
    return {"status": status, "similarity": similarity}


def run_live_check(
    *,
    ts_store: TimeSeriesStore | None = None,
    registry: dict | None = None,
    baseline=_UNSET,
    cfg: dict | None = None,
    alerts_path: Path | None = None,
) -> dict:
    """I/O glue: pull the recent motion window, build a live signature,
    compare it against the active baseline, persist the result, and publish
    `occupancy_checked` (always) plus `occupancy_anomaly_detected` (only when
    the live pattern doesn't resemble the baseline). Returns the check dict —
    always similarity-scored, never presented as a verdict about a person
    (see /security/check in src/api/main.py)."""
    cfg = cfg or load_model_config()
    registry = registry if registry is not None else load_sensor_registry()
    ts_store = ts_store or TimeSeriesStore()
    active_baseline = get_baseline() if baseline is _UNSET else baseline

    sensor_id = _motion_sensor_id(registry)
    if sensor_id is None:
        result = {"status": STATUS_NO_BASELINE, "similarity": 0.0}
        log.warning("No motion sensor in the registry — cannot build a live occupancy signature")
    else:
        window_min = cfg.get("security", {}).get("live_window_minutes", 30)
        since = (datetime.now(timezone.utc) - timedelta(minutes=window_min)).isoformat()
        readings = ts_store.query(sensor_id, since=since, limit=10_000)
        live_signature = build_signature(list(reversed(readings)))  # chronological order for build_signature
        result = detect(live_signature, active_baseline, cfg)

    record = {**result, "checked_at": _now_iso()}
    store._append(alerts_path or store.DEFAULT_ALERTS_PATH, record)
    bus.publish("occupancy_checked", record)
    if record["status"] == STATUS_ANOMALOUS:
        bus.publish("occupancy_anomaly_detected", record)
    log.info("Occupancy check: %s (similarity=%.2f)", record["status"], record["similarity"])
    return record


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Check live occupancy against the learned baseline and flag anomalies")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Run a single live check and print the result")
    group.add_argument("--watch", action="store_true", help="Continuously check on an interval")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S, help="Seconds between checks in --watch mode")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


def _print_result(record: dict) -> None:
    if record["status"] == STATUS_NO_BASELINE:
        print("no baseline learned yet — run `python -m src.security.learner --learn` to calibrate")
    elif record["status"] == STATUS_ANOMALOUS:
        print(f"ANOMALOUS — doesn't match the learned baseline (similarity={record['similarity']:.2f})")
    else:
        print(f"expected — matches the learned baseline (similarity={record['similarity']:.2f})")


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")

    if args.check:
        _print_result(run_live_check())
    else:
        try:
            while True:
                _print_result(run_live_check())
                time.sleep(args.interval)
        except KeyboardInterrupt:
            log.info("Occupancy detector stopped.")
