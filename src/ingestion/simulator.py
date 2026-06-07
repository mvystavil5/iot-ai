"""
Sensor reading simulator — generates synthetic telemetry for local testing
without hardware (CLAUDE.md quickstart step 4):

  python -m src.ingestion.simulator --sensor temp_01 --value 22.4

Two modes:
  --sensor ID --value V   one-shot reading for a single sensor
  (no --sensor)           continuous: random-walk readings for every sensor
                          in config/sensors.yaml, looping at --interval

By default posts to the local ingestion API (matching how real bridges
deliver data); pass --pipeline to bypass HTTP and call IngestionPipeline
in-process instead — handy for offline runs with no API server up.
"""

from __future__ import annotations

import argparse
import logging
import random
import time
from datetime import datetime, timezone

import httpx

from src.config import load_sensor_registry
from src.ingestion.pipeline import IngestionPipeline

log = logging.getLogger(__name__)

DEFAULT_API = "http://127.0.0.1:8000"
DEFAULT_INTERVAL_S = 30.0
HTTP_TIMEOUT = 5.0


def _payload(sensor_id: str, value: float, unit: str) -> dict:
    return {
        "sensor_id": sensor_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "value": value,
        "unit": unit,
        "tags": {"source": "simulator"},
    }


def _post(client: httpx.Client, api_base: str, payload: dict) -> None:
    try:
        r = client.post(f"{api_base}/telemetry", json=payload, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        log.info("POST %s=%s%s -> %s", payload["sensor_id"], payload["value"], payload["unit"], r.status_code)
    except httpx.HTTPError as exc:
        log.warning("POST failed for %s: %s", payload["sensor_id"], exc)


def random_walk(value: float, lo: float, hi: float, step_frac: float = 0.02) -> float:
    """Nudge `value` by up to step_frac of the sensor's expected range,
    clamped to stay inside it — keeps continuous-mode readings plausible."""
    span = hi - lo
    return min(hi, max(lo, value + random.uniform(-step_frac, step_frac) * span))


def run_once(sensor_id: str, value: float, unit: str, *, api_base: str, use_pipeline: bool) -> None:
    payload = _payload(sensor_id, value, unit)
    if use_pipeline:
        reading = IngestionPipeline().ingest(payload)
        log.info("Ingested %s=%s%s (outlier=%s)", reading.sensor_id, reading.value, reading.unit, reading.outlier)
    else:
        with httpx.Client() as client:
            _post(client, api_base, payload)


def run_continuous(*, api_base: str, use_pipeline: bool, interval_s: float) -> None:
    sensors = load_sensor_registry().get("sensors", [])
    if not sensors:
        log.warning("No sensors in config/sensors.yaml — nothing to simulate")
        return

    state = {s["id"]: random.uniform(*s["expected_range"]) for s in sensors}
    pipeline = IngestionPipeline() if use_pipeline else None
    log.info("Simulator started — %d sensor(s), interval=%.1fs, mode=%s",
             len(sensors), interval_s, "pipeline" if use_pipeline else "http")

    try:
        with httpx.Client() as client:
            while True:
                for s in sensors:
                    lo, hi = s["expected_range"]
                    state[s["id"]] = random_walk(state[s["id"]], lo, hi)
                    payload = _payload(s["id"], round(state[s["id"]], 2), s["unit"])
                    if pipeline is not None:
                        reading = pipeline.ingest(payload)
                        log.debug("Ingested %s=%s%s", reading.sensor_id, reading.value, reading.unit)
                    else:
                        _post(client, api_base, payload)
                time.sleep(interval_s)
    except KeyboardInterrupt:
        log.info("Simulator stopped.")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate synthetic sensor readings for local testing")
    p.add_argument("--sensor", help="Sensor ID for a one-shot reading, e.g. temp_01")
    p.add_argument("--value", type=float, help="Reading value for --sensor (one-shot mode)")
    p.add_argument("--unit", help="Override unit for --sensor (default: from config/sensors.yaml)")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S, help="Seconds between rounds in continuous mode")
    p.add_argument("--api", default=DEFAULT_API, help="Ingestion API base URL")
    p.add_argument("--pipeline", action="store_true", help="Bypass HTTP — call the pipeline in-process")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    if args.sensor:
        if args.value is None:
            raise SystemExit("--sensor requires --value")
        sensor_cfg = next((s for s in load_sensor_registry().get("sensors", []) if s["id"] == args.sensor), None)
        unit = args.unit or (sensor_cfg["unit"] if sensor_cfg else "")
        run_once(args.sensor, args.value, unit, api_base=args.api, use_pipeline=args.pipeline)
    else:
        run_continuous(api_base=args.api, use_pipeline=args.pipeline, interval_s=args.interval)
