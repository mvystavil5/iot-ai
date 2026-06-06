"""
Serial bridge: reads JSON sensor batches from the STM32 co-processor over
USB CDC serial and forwards each reading to the local ingestion API.

The STM32 sends newline-delimited JSON arrays, e.g.:
  [{"sensor_id":"temp_01","value":22.4,"unit":"C"}, ...]

This bridge adds UTC timestamps (the MCU has no RTC or NTP access) and
reconnects automatically if the serial port disappears (e.g. USB unplug).

Run on the Arduino UNO Q Linux side:
  python -m src.ingestion.serial_bridge
  python -m src.ingestion.serial_bridge --port /dev/ttyACM1 --api http://127.0.0.1:8000
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone

import httpx
import serial

from src.config import load_sensor_registry

log = logging.getLogger(__name__)

DEFAULT_PORT    = "/dev/ttyACM0"
DEFAULT_BAUD    = 115200
DEFAULT_API     = "http://127.0.0.1:8000"
RECONNECT_DELAY = 5   # seconds between serial reconnect attempts
HTTP_TIMEOUT    = 5.0


def _known_sensor_ids(registry: dict) -> set[str]:
    return {s["id"] for s in registry.get("sensors", [])}


def _post(client: httpx.Client, api_base: str, sensor_id: str, value: float, unit: str, ts: str) -> None:
    payload = {
        "sensor_id": sensor_id,
        "timestamp": ts,
        "value": value,
        "unit": unit,
        "tags": {"source": "stm32", "interface": "serial"},
    }
    try:
        r = client.post(f"{api_base}/telemetry", json=payload, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        log.debug("POST %s=%.2f → %s", sensor_id, value, r.status_code)
    except httpx.HTTPError as exc:
        log.warning("POST failed for %s: %s", sensor_id, exc)


def run(port: str, baud: int, api_base: str) -> None:
    registry    = load_sensor_registry()
    known_ids   = _known_sensor_ids(registry)
    serial_cfg  = registry.get("interfaces", {}).get("serial", {})
    effective_port = port or serial_cfg.get("port", DEFAULT_PORT)
    effective_baud = baud or serial_cfg.get("baud", DEFAULT_BAUD)

    log.info("Serial bridge starting — port=%s baud=%d api=%s", effective_port, effective_baud, api_base)
    log.info("Known sensor IDs: %s", known_ids)

    with httpx.Client() as client:
        while True:
            try:
                with serial.Serial(effective_port, effective_baud, timeout=60) as ser:
                    log.info("Connected to STM32 on %s", effective_port)
                    for raw_line in ser:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        try:
                            readings = json.loads(line)
                        except json.JSONDecodeError:
                            log.debug("Non-JSON from MCU: %s", line)
                            continue

                        if not isinstance(readings, list):
                            log.warning("Expected JSON array, got: %s", type(readings).__name__)
                            continue

                        ts = datetime.now(timezone.utc).isoformat()
                        forwarded = 0
                        for r in readings:
                            sid = r.get("sensor_id")
                            if sid not in known_ids:
                                log.warning("Unknown sensor_id '%s' — add to config/sensors.yaml", sid)
                                continue
                            _post(client, api_base, sid, float(r["value"]), r["unit"], ts)
                            forwarded += 1
                        log.info("Forwarded %d/%d readings at %s", forwarded, len(readings), ts)

            except serial.SerialException as exc:
                log.warning("Serial error on %s (%s) — reconnecting in %ds", effective_port, exc, RECONNECT_DELAY)
                time.sleep(RECONNECT_DELAY)
            except KeyboardInterrupt:
                log.info("Bridge stopped.")
                break


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="STM32 → ingestion API serial bridge")
    p.add_argument("--port", default="", help="Serial port (default: from config/sensors.yaml)")
    p.add_argument("--baud", type=int, default=0,  help="Baud rate (default: from config/sensors.yaml)")
    p.add_argument("--api",  default=DEFAULT_API,  help="Ingestion API base URL")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    run(args.port, args.baud, args.api)
