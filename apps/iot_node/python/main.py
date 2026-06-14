"""
IoT World-Model sensor node — Arduino App Lab (MPU / Python half).

Runs on the UNO Q's Debian MPU. Pulls sensor readings from the MCU over the
RouterBridge RPC (functions registered by ``sketch/sketch.ino``), timestamps
them (the MCU has no RTC), and POSTs each reading to the on-board ingestion
API's ``POST /telemetry`` endpoint.

This is the App Lab replacement for ``src/ingestion/serial_bridge.py``: the
Bridge handles MCU<->MPU transport, so there is no serial port or JSON framing
to manage here. The ingestion API / ChromaDB / Ollama stack still runs as
separate supervised processes (see ``docs/installation.md`` section 4.1) — this
app is only the sensor producer that feeds it.
"""

from arduino.app_utils import *  # noqa: F401,F403  (App, Bridge)

import math
import time
from datetime import datetime, timezone

import psutil
import requests

import led_gauge

API_BASE = "http://127.0.0.1:8000"
HTTP_TIMEOUT = 5.0
BATCH_INTERVAL_S = 30      # periodic full-batch cadence (matches reporting_interval_s)
MOTION_POLL_S = 1.0        # fast poll so PIR state changes post immediately
GAUGE_INTERVAL_S = 2.0     # LED-matrix load-gauge refresh cadence

_last_batch = 0.0
_last_gauge = 0.0
_last_motion: int | None = None


def _post(sensor_id: str, value: float, unit: str, ts: str) -> None:
    """Forward a single reading to the ingestion API; never raise on failure."""
    payload = {
        "sensor_id": sensor_id,
        "timestamp": ts,
        "value": value,
        "unit": unit,
        "tags": {"source": "stm32", "interface": "bridge"},
    }
    try:
        r = requests.post(f"{API_BASE}/telemetry", json=payload, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"POST failed for {sensor_id}: {exc}")


def _post_environment(ts: str) -> None:
    """Read + forward the slow-changing environment sensors (temp/humid/co2)."""
    temp = Bridge.call("read_temp")        # noqa: F405  (Bridge from app_utils)
    humid = Bridge.call("read_humidity")   # noqa: F405
    co2 = Bridge.call("read_co2")          # noqa: F405

    if temp is not None and not math.isnan(temp):
        _post("temp_01", round(float(temp), 1), "C", ts)
    if humid is not None and not math.isnan(humid):
        _post("humid_01", round(float(humid), 1), "%RH", ts)
    if co2 is not None:
        _post("co2_01", int(co2), "ppm", ts)


def _update_gauge() -> None:
    """Push a CPU/memory load frame to the MCU-owned LED matrix over the Bridge."""
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory().percent
    w0h, w0l, w1h, w1l, w2h, w2l = led_gauge.gauge_words(cpu, mem)
    try:
        Bridge.call("set_matrix", w0h, w0l, w1h, w1l, w2h, w2l)   # noqa: F405
    except Exception as exc:  # noqa: BLE001  — never let the gauge break ingestion
        print(f"LED gauge update failed: {exc}")


def loop() -> None:
    global _last_batch, _last_gauge, _last_motion
    now = time.monotonic()
    ts = datetime.now(timezone.utc).isoformat()

    # LED-matrix load gauge (left = CPU %, right = memory %), independent cadence.
    if now - _last_gauge >= GAUGE_INTERVAL_S:
        _last_gauge = now
        _update_gauge()

    # Immediate post on PIR state change (mirrors the old firmware behaviour).
    motion = Bridge.call("read_motion")    # noqa: F405
    if motion is not None and int(motion) != _last_motion:
        _last_motion = int(motion)
        _post("motion_01", _last_motion, "bool", ts)

    # Periodic full environment batch (plus current motion for a heartbeat).
    if now - _last_batch >= BATCH_INTERVAL_S:
        _last_batch = now
        _post_environment(ts)
        if motion is not None:
            _post("motion_01", int(motion), "bool", ts)

    time.sleep(MOTION_POLL_S)


App.run(user_loop=loop)  # noqa: F405  (App from app_utils)
