"""
Occupancy-pattern signatures — pure aggregation over the ambient sensor
history the board already collects (PIR motion, DHT11 temp/humidity, MQ-135
CO2). No new hardware, no biometrics, no cameras/microphones.

A signature answers "what does occupancy of this space normally look like" —
when it's typically active, how active, how long active stretches run — never
"who is here". The same function builds both the long-window learned baseline
and the short-window live signature so the detector compares like with like
(see detector.score_similarity).
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone

from src.ingestion.schema import TelemetryReading

HOURS_IN_DAY = 24
ACTIVE_THRESHOLD = 0.5  # motion reading >= this counts as "active" for session detection
MAX_SESSION_GAP_MIN = 10.0  # readings more than this far apart never share a session


def _hour_of(reading: TelemetryReading) -> int:
    ts = reading.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.hour


def _hourly_activity(motion_readings: list[TelemetryReading]) -> list[float]:
    """24-bucket histogram (hour-of-day -> fraction of that hour's readings
    that were "active"), normalized so the buckets sum to 1.0. An empty or
    all-quiet history yields a flat (uniform) histogram — "no information",
    not "definitely inactive every hour"."""
    totals = [0] * HOURS_IN_DAY
    actives = [0] * HOURS_IN_DAY
    for r in motion_readings:
        hour = _hour_of(r)
        totals[hour] += 1
        if r.value >= ACTIVE_THRESHOLD:
            actives[hour] += 1

    raw = [actives[h] / totals[h] if totals[h] else 0.0 for h in range(HOURS_IN_DAY)]
    total = sum(raw)
    if total <= 0:
        return [1.0 / HOURS_IN_DAY] * HOURS_IN_DAY
    return [v / total for v in raw]


def _presence_ratio(motion_readings: list[TelemetryReading]) -> float:
    """Fraction of motion readings in the window that registered activity."""
    if not motion_readings:
        return 0.0
    active = sum(1 for r in motion_readings if r.value >= ACTIVE_THRESHOLD)
    return active / len(motion_readings)


def _session_lengths_min(motion_readings_chronological: list[TelemetryReading]) -> list[float]:
    """Lengths (in minutes) of contiguous active stretches — consecutive
    "active" readings no more than MAX_SESSION_GAP_MIN apart collapse into
    one session, mirroring how a single visit shows up as one continuous
    stretch of activity rather than a series of independent instants."""
    sessions: list[float] = []
    session_start: datetime | None = None
    last_active: datetime | None = None

    for r in motion_readings_chronological:
        ts = r.timestamp if r.timestamp.tzinfo else r.timestamp.replace(tzinfo=timezone.utc)
        if r.value >= ACTIVE_THRESHOLD:
            if session_start is None:
                session_start = ts
            elif last_active is not None and (ts - last_active).total_seconds() / 60.0 > MAX_SESSION_GAP_MIN:
                sessions.append((last_active - session_start).total_seconds() / 60.0)
                session_start = ts
            last_active = ts
        # inactive readings don't close a session by themselves — only a gap does

    if session_start is not None and last_active is not None:
        sessions.append((last_active - session_start).total_seconds() / 60.0)

    return sessions


def _mean_session_length_min(motion_readings_chronological: list[TelemetryReading]) -> float:
    sessions = _session_lengths_min(motion_readings_chronological)
    return statistics.mean(sessions) if sessions else 0.0


def build_signature(motion_readings: list[TelemetryReading]) -> dict:
    """Build a JSON-serializable occupancy-pattern feature set from one
    sensor's motion readings, in chronological order (oldest first) —
    callers querying TimeSeriesStore (which returns most-recent-first) must
    reverse before passing in here.

    Motion is the load-bearing signal — the only direct presence proxy among
    the board's sensors. The same function builds both the long-window
    learned baseline and the short-window live signature; the caller (which
    already needs the sensor registry to find the motion sensor's id) is
    responsible for selecting and windowing the stream."""
    return {
        "presence_ratio": round(_presence_ratio(motion_readings), 4),
        "hourly_activity": [round(v, 4) for v in _hourly_activity(motion_readings)],
        "mean_session_length_min": round(_mean_session_length_min(motion_readings), 2),
        "sample_size": len(motion_readings),
    }
