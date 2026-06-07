"""
Batch validator — checks a JSONL file of raw telemetry payloads against the
TelemetryReading schema and config/sensors.yaml without ingesting anything,
so a replay file can be vetted before it's pushed for real:

  python -m src.ingestion.validator --input readings.jsonl
  python -m src.ingestion.validator --input readings.jsonl --ingest
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from src.config import load_sensor_registry
from src.ingestion.pipeline import IngestionPipeline
from src.ingestion.schema import TelemetryReading

log = logging.getLogger(__name__)


def _known_sensor_ids(registry: dict) -> set[str]:
    return {s["id"] for s in registry.get("sensors", [])}


def validate_file(path: Path, registry: dict | None = None) -> dict:
    """Validate every non-blank line of a JSONL file.

    Returns a report:
      {"total", "valid", "invalid", "unknown_sensor", "errors": [...],
       "valid_payloads": [...]}
    `errors` entries are {"line": <1-based lineno>, "error": <message>}.
    """
    registry = registry if registry is not None else load_sensor_registry()
    known_ids = _known_sensor_ids(registry)

    total = valid = invalid = unknown_sensor = 0
    errors: list[dict] = []
    valid_payloads: list[dict] = []

    for lineno, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        total += 1
        try:
            payload = json.loads(line)
            reading = TelemetryReading(**payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            invalid += 1
            errors.append({"line": lineno, "error": str(exc)})
            continue

        if reading.sensor_id not in known_ids:
            unknown_sensor += 1
            errors.append({"line": lineno, "error": f"unknown sensor_id '{reading.sensor_id}'"})
            continue

        valid += 1
        valid_payloads.append(payload)

    return {
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "unknown_sensor": unknown_sensor,
        "errors": errors,
        "valid_payloads": valid_payloads,
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate a JSONL batch of telemetry payloads")
    p.add_argument("--input", required=True, type=Path, help="Path to a newline-delimited JSON file")
    p.add_argument("--ingest", action="store_true", help="Also run valid readings through the pipeline")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    report = validate_file(args.input)
    valid_payloads = report.pop("valid_payloads")
    print(json.dumps(report, indent=2))

    if args.ingest and valid_payloads:
        pipeline = IngestionPipeline()
        for payload in valid_payloads:
            pipeline.ingest(payload)
        log.info("Ingested %d valid reading(s) from %s", len(valid_payloads), args.input)
