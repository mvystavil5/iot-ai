"""
LED matrix system-load indicator: renders CPU and memory utilization as bar
graphs on the Arduino UNO Q's onboard 12x8 LED matrix.

Runs on the QRB2210 MPU (Debian Linux side), alongside wifi_bridge.py, so a
Phase-1 single-board deployment has an at-a-glance view of its own headroom
without a separate monitoring server.

Layout (12 columns x 8 rows, bars fill bottom-up):
  columns 0-5  -> CPU utilization
  columns 6-11 -> memory utilization

Run on the Arduino UNO Q Linux side:
  python -m src.ingestion.led_matrix
  python -m src.ingestion.led_matrix --interval 2.0
"""

import argparse
import logging
import time
from typing import Protocol

import psutil

log = logging.getLogger(__name__)

MATRIX_COLS = 12
MATRIX_ROWS = 8
_BAR_COLS = MATRIX_COLS // 2

DEFAULT_INTERVAL_S = 2.0

Frame = list[list[bool]]


class _MatrixBackend(Protocol):
    def draw(self, frame: Frame) -> None: ...
    def clear(self) -> None: ...


class _SimulatedMatrix:
    """Fallback for dev machines / boards without the vendor binding installed.
    Logs an ASCII rendering instead of driving physical LEDs."""

    def draw(self, frame: Frame) -> None:
        rows = ("".join("#" if lit else "." for lit in row) for row in frame)
        log.info("LED matrix:\n%s", "\n".join(rows))

    def clear(self) -> None:
        log.debug("LED matrix cleared (simulated)")


def _open_matrix() -> _MatrixBackend:
    # The vendor Linux binding's package name/API may differ across UNO Q SDK
    # versions — fall back to a logged ASCII rendering if it isn't present so
    # this still runs (and is testable) on dev machines without the board.
    try:
        from arduino_uno_q.led_matrix import LedMatrix  # type: ignore[import-not-found]

        return LedMatrix(cols=MATRIX_COLS, rows=MATRIX_ROWS)
    except ImportError:
        log.warning("Vendor LED matrix binding not found — using simulated (ASCII log) backend")
        return _SimulatedMatrix()


def _bar_height(pct: float, rows: int = MATRIX_ROWS) -> int:
    """Convert a 0-100 percentage into a 0..rows lit-row count."""
    return round(max(0.0, min(100.0, pct)) / 100.0 * rows)


def render_frame(cpu_pct: float, mem_pct: float) -> Frame:
    """Build an 8x12 frame: left half = CPU bar, right half = memory bar."""
    cpu_h = _bar_height(cpu_pct)
    mem_h = _bar_height(mem_pct)
    frame = [[False] * MATRIX_COLS for _ in range(MATRIX_ROWS)]
    for row in range(MATRIX_ROWS):
        lit_from_bottom = MATRIX_ROWS - row
        if lit_from_bottom <= cpu_h:
            for col in range(_BAR_COLS):
                frame[row][col] = True
        if lit_from_bottom <= mem_h:
            for col in range(_BAR_COLS, MATRIX_COLS):
                frame[row][col] = True
    return frame


def run(interval_s: float) -> None:
    matrix = _open_matrix()
    log.info("LED matrix load indicator started — interval=%.1fs backend=%s", interval_s, type(matrix).__name__)
    try:
        while True:
            cpu_pct = psutil.cpu_percent(interval=None)
            mem_pct = psutil.virtual_memory().percent
            matrix.draw(render_frame(cpu_pct, mem_pct))
            log.debug("cpu=%.1f%% mem=%.1f%%", cpu_pct, mem_pct)
            time.sleep(interval_s)
    except KeyboardInterrupt:
        log.info("LED matrix indicator stopped.")
    finally:
        matrix.clear()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CPU/memory load indicator on the UNO Q LED matrix")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S, help="Refresh interval in seconds")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    run(args.interval)
