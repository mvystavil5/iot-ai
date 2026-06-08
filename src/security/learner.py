"""
Baseline learning — calibrate "what normal occupancy of this space looks
like" by aggregating an observation window of motion history into one
aggregate occupancy signature (see docs/architecture.md § Security & anomaly
detection, plan "Occupancy-Baseline Anomaly Detection").

This is deliberately *not* a per-person registration system: it never asks
"who is this", never stores a name, and produces exactly one baseline for the
space — an aggregate description of when the space is normally active, how
active, and for how long. Re-running `learn_baseline` (e.g., after household
routines change) appends a fresh baseline that becomes the new active one.

`reset_baseline` is a hard purge of the baseline AND the entire alert history
that was scored against it — a clean slate for relearning, not a soft flag
left sitting next to live data.

  python -m src.security.learner --learn --duration 3600
  python -m src.security.learner --reset
  python -m src.security.learner --show
"""

from __future__ import annotations

import argparse
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import load_sensor_registry
from src.events import bus
from src.security import store
from src.security.signature import build_signature
from src.ingestion.storage import TimeSeriesStore

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _baseline_id() -> str:
    return f"occupancy_baseline_{uuid.uuid4().hex[:8]}"


def _motion_sensor_id(registry: dict) -> str | None:
    for sensor in registry.get("sensors", []):
        if sensor.get("type") == "motion":
            return sensor["id"]
    return None


def learn_baseline(
    window_start: datetime,
    window_end: datetime,
    *,
    ts_store: TimeSeriesStore | None = None,
    registry: dict | None = None,
    baseline_path: Path | None = None,
) -> dict:
    """Aggregate the (already-elapsed) [window_start, window_end) motion
    history into an occupancy-baseline signature and persist it. The most
    recently learned baseline is the active one (see get_baseline)."""
    registry = registry if registry is not None else load_sensor_registry()
    ts_store = ts_store or TimeSeriesStore()

    sensor_id = _motion_sensor_id(registry)
    if sensor_id is None:
        raise ValueError("No motion sensor in the registry — cannot build an occupancy baseline")

    readings = ts_store.query(sensor_id, since=window_start.isoformat(), limit=100_000)
    in_window = [r for r in readings if r.timestamp <= window_end]
    chronological = list(reversed(in_window))  # TimeSeriesStore.query is most-recent-first

    baseline = {
        "baseline_id": _baseline_id(),
        "learned_at": _now_iso(),
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "signature": build_signature(chronological),
    }

    store._append(baseline_path or store.DEFAULT_BASELINE_PATH, baseline)
    bus.publish("occupancy_baseline_learned", baseline)
    log.info(
        "Learned occupancy baseline %s from %d motion reading(s)",
        baseline["baseline_id"], baseline["signature"]["sample_size"],
    )
    return baseline


def get_baseline(*, baseline_path: Path | None = None) -> dict | None:
    """The active baseline — the most recently learned one — or None if the
    space hasn't been calibrated yet."""
    baselines = store.load_baselines(baseline_path)
    return baselines[-1] if baselines else None


def reset_baseline(
    *,
    baseline_path: Path | None = None,
    alerts_path: Path | None = None,
) -> bool:
    """Hard-delete every learned baseline AND every alert ever scored
    against one — a clean slate for relearning, e.g. when who normally
    occupies the space changes. Returns False if there was nothing to
    reset."""
    baseline_path = baseline_path or store.DEFAULT_BASELINE_PATH
    alerts_path = alerts_path or store.DEFAULT_ALERTS_PATH

    baselines = store.load_baselines(baseline_path)
    alerts = store.load_alerts(alerts_path)
    if not baselines and not alerts:
        return False

    store._rewrite(baseline_path, [])
    store._rewrite(alerts_path, [])

    bus.publish("occupancy_baseline_reset", {"reset_at": _now_iso()})
    log.info(
        "Reset occupancy baseline — purged %d baseline(s) and %d alert record(s)",
        len(baselines), len(alerts),
    )
    return True


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Learn / reset / show the occupancy baseline used for anomaly detection")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--learn", action="store_true", help="Observe the space and learn a fresh baseline")
    group.add_argument("--reset", action="store_true", help="Hard-delete the baseline and all alert history")
    group.add_argument("--show", action="store_true", help="Print the active baseline")
    p.add_argument("--duration", type=int, default=3600, help="Learning window length in seconds (with --learn)")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")

    if args.learn:
        start, end = _now(), _now() + timedelta(seconds=args.duration)
        print(f"Learning window open: now until {args.duration}s from now.")
        print("Let the space run its normal routine — recording starts immediately and stops automatically.")
        try:
            time.sleep(args.duration)
        except KeyboardInterrupt:
            end = _now()
            print("\nWindow ended early — learning from the partial window.")
        baseline = learn_baseline(start, end)
        print(f"Learned baseline {baseline['baseline_id']} "
              f"({baseline['signature']['sample_size']} motion reading(s) observed).")
    elif args.reset:
        if reset_baseline():
            print("Reset — baseline and all alert history purged. Run --learn to recalibrate.")
        else:
            print("Nothing to reset — no baseline or alert history on file.")
    else:
        baseline = get_baseline()
        if baseline is None:
            print("No baseline learned yet — run --learn to calibrate.")
        else:
            sig = baseline["signature"]
            print(f"{baseline['baseline_id']}  (learned {baseline['learned_at']}, "
                  f"{sig['sample_size']} reading(s), presence_ratio={sig['presence_ratio']})")
