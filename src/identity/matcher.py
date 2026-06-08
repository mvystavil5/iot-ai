"""
Occupancy attribution — match a live, short-window behavioral signature
against consciously-registered routine signatures (see registration.py) and
report a confidence-scored guess at "whose routine does this look like".

This is a small statistical pattern-matcher, not an LLM call: time-series
classification is exactly the kind of task RAG+LLM is the wrong tool for
(see src/model/rag_confidence.py's "no LLM introspection" precedent — the
same reasoning applies here, just one level lower in the stack). Below
`identity.match_confidence_threshold` the honest answer is "unknown" — a
new/unrecognized occupant — never a forced guess.

  python -m src.identity.matcher --match
  python -m src.identity.matcher --watch [--interval 300]
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
from src.identity import store
from src.identity.signature import build_signature
from src.ingestion.storage import TimeSeriesStore

log = logging.getLogger(__name__)

UNKNOWN_PROFILE_ID = "unknown"
DEFAULT_INTERVAL_S = 300.0

_DEFAULT_FEATURE_WEIGHTS = {"hourly_activity": 0.5, "presence_ratio": 0.3, "session_length": 0.2}
_SESSION_LENGTH_SCALE_MIN = 30.0  # difference-decay time constant for session-length closeness


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


def score_match(live: dict, registered: dict, cfg: dict | None = None) -> float:
    """Weighted similarity in [0, 1] between a live signature and a
    registered one — pure, metadata-only, config-driven weights. Mirrors
    compute_rag_confidence's shape: components in, weighted sum out, no I/O.

    Components (weights configurable in config/model.yaml: identity.feature_weights):
      hourly_activity — cosine similarity of the 24-bucket activity histograms
      presence_ratio  — closeness of overall active-fraction
      session_length  — closeness of mean contiguous-presence duration
    """
    cfg = cfg or load_model_config()
    weights: dict = cfg.get("identity", {}).get("feature_weights", _DEFAULT_FEATURE_WEIGHTS)

    hourly = _cosine_similarity(live["hourly_activity"], registered["hourly_activity"])
    presence = _closeness(live["presence_ratio"], registered["presence_ratio"], scale=1.0)
    session = _closeness(live["mean_session_length_min"], registered["mean_session_length_min"], scale=_SESSION_LENGTH_SCALE_MIN)

    score = (
        weights.get("hourly_activity", _DEFAULT_FEATURE_WEIGHTS["hourly_activity"]) * hourly
        + weights.get("presence_ratio", _DEFAULT_FEATURE_WEIGHTS["presence_ratio"]) * presence
        + weights.get("session_length", _DEFAULT_FEATURE_WEIGHTS["session_length"]) * session
    )
    return round(min(max(score, 0.0), 1.0), 4)


def match(live: dict, profiles: list[dict], cfg: dict | None = None) -> dict:
    """Best-match a live signature against registered (non-revoked)
    profiles. Below `identity.match_confidence_threshold`, honestly report
    "unknown" — this is the "detect a new/unrecognized occupant" capability,
    not a failure mode to hide."""
    cfg = cfg or load_model_config()
    threshold = cfg.get("identity", {}).get("match_confidence_threshold", 0.6)

    candidates = [p for p in profiles if p.get("revoked_at") is None]
    if not candidates:
        return {"profile_id": UNKNOWN_PROFILE_ID, "display_name": None, "confidence": 0.0}

    scored = [(p, score_match(live, p["signature"], cfg)) for p in candidates]
    best_profile, best_score = max(scored, key=lambda ps: ps[1])

    if best_score >= threshold:
        return {"profile_id": best_profile["profile_id"], "display_name": best_profile["display_name"], "confidence": best_score}
    return {"profile_id": UNKNOWN_PROFILE_ID, "display_name": None, "confidence": best_score}


def _motion_sensor_id(registry: dict) -> str | None:
    for sensor in registry.get("sensors", []):
        if sensor.get("type") == "motion":
            return sensor["id"]
    return None


def run_live_match(
    *,
    ts_store: TimeSeriesStore | None = None,
    registry: dict | None = None,
    profiles: list[dict] | None = None,
    cfg: dict | None = None,
    matches_path: Path | None = None,
) -> dict:
    """I/O glue: pull the recent motion window, build a live signature,
    match it against registered profiles, persist the result, and publish
    `identity_matched`. Returns the match dict (always confidence-scored —
    never presented as fact, see /identity/match in src/api/main.py)."""
    cfg = cfg or load_model_config()
    registry = registry if registry is not None else load_sensor_registry()
    profiles = profiles if profiles is not None else store.load_profiles()
    ts_store = ts_store or TimeSeriesStore()

    sensor_id = _motion_sensor_id(registry)
    if sensor_id is None:
        result = {"profile_id": UNKNOWN_PROFILE_ID, "display_name": None, "confidence": 0.0}
        log.warning("No motion sensor in the registry — cannot build a live signature")
    else:
        window_min = cfg.get("identity", {}).get("live_window_minutes", 30)
        since = (datetime.now(timezone.utc) - timedelta(minutes=window_min)).isoformat()
        readings = ts_store.query(sensor_id, since=since, limit=10_000)
        live_signature = build_signature(list(reversed(readings)))  # chronological order for build_signature
        result = match(live_signature, profiles, cfg)

    record = {**result, "matched_at": _now_iso()}
    store._append(matches_path or store.DEFAULT_MATCHES_PATH, record)
    bus.publish("identity_matched", record)
    log.info("Live match: %s (confidence=%.2f)", record["profile_id"], record["confidence"])
    return record


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Match live occupancy against registered identity profiles")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--match", action="store_true", help="Run a single live match and print the result")
    group.add_argument("--watch", action="store_true", help="Continuously match on an interval")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S, help="Seconds between matches in --watch mode")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


def _print_match(record: dict) -> None:
    if record["profile_id"] == UNKNOWN_PROFILE_ID:
        print(f"unknown occupant (best confidence={record['confidence']:.2f}, below threshold)")
    else:
        print(f"{record['display_name']} ({record['profile_id']}) — confidence={record['confidence']:.2f}")


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")

    if args.match:
        _print_match(run_live_match())
    else:
        try:
            while True:
                _print_match(run_live_match())
                time.sleep(args.interval)
        except KeyboardInterrupt:
            log.info("Identity matcher stopped.")
