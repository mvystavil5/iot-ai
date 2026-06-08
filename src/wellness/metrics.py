"""
Personal activity metrics — pure aggregation over the same ambient motion
history the board already collects, reframed for a different question than
src/security/signature.py asks.

Where the security module deliberately stays *aggregate* ("what does normal
occupancy of this space look like, never tied to a person"), this module is
the opposite kind of opt-in: it is *for* the one person running it, *on*
themselves, *by* their own choice — a personal experiment in whether simple
ambient sensors can say anything useful about movement, stillness, and how
those change over time. It still infers nothing about identity, location
beyond "this room", or any diagnosable condition; it only counts minutes.

A daily summary answers "how much did the space see me moving today, and in
what shape" — active stretches, still stretches, how long the longest one
ran — never "who", never "why", never a medical read. See trends.py for how
summaries accumulate into something worth noticing.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone

from src.ingestion.schema import TelemetryReading

ACTIVE_THRESHOLD = 0.5  # motion reading >= this counts as "active" for session detection
MAX_SESSION_GAP_MIN = 10.0  # readings more than this far apart never share a session


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def _active_sessions(motion_readings_chronological: list[TelemetryReading]) -> list[tuple[datetime, datetime]]:
    """Contiguous active stretches as (start, end) pairs — consecutive
    "active" readings no more than MAX_SESSION_GAP_MIN apart collapse into
    one session, identical grouping rule to security/signature.py's, applied
    here to get at *durations* rather than a histogram."""
    sessions: list[tuple[datetime, datetime]] = []
    session_start: datetime | None = None
    last_active: datetime | None = None

    for r in motion_readings_chronological:
        ts = _aware(r.timestamp)
        if r.value >= ACTIVE_THRESHOLD:
            if session_start is None:
                session_start = ts
            elif last_active is not None and (ts - last_active).total_seconds() / 60.0 > MAX_SESSION_GAP_MIN:
                sessions.append((session_start, last_active))
                session_start = ts
            last_active = ts

    if session_start is not None and last_active is not None:
        sessions.append((session_start, last_active))

    return sessions


def _still_streaks_min(
    sessions: list[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
) -> list[float]:
    """Gaps (in minutes) between active sessions, plus the lead-in before the
    first and the tail-out after the last — the stretches where the sensor
    saw no movement at all. Note this is a proxy for "still", not "asleep" or
    "away" or anything else: the sensor cannot and does not distinguish those,
    and this module never claims to."""
    streaks: list[float] = []
    cursor = window_start
    for start, end in sessions:
        gap = (start - cursor).total_seconds() / 60.0
        if gap > 0:
            streaks.append(gap)
        cursor = end
    tail = (window_end - cursor).total_seconds() / 60.0
    if tail > 0:
        streaks.append(tail)
    return streaks


def build_daily_summary(
    motion_readings_chronological: list[TelemetryReading],
    window_start: datetime,
    window_end: datetime,
) -> dict:
    """Build a JSON-serializable activity summary for one [window_start,
    window_end) span (typically one calendar day) from motion readings in
    chronological order (oldest first) — callers querying TimeSeriesStore
    (which returns most-recent-first) must reverse before passing in here.

    `active_minutes` + `sedentary_minutes` always sum to the window length;
    "sedentary" here means "no movement seen", not a judgment about what the
    person was doing — lying down, sitting still, or simply out of range of
    the one PIR sensor all look identical to it."""
    sessions = _active_sessions(motion_readings_chronological)
    active_minutes = sum((end - start).total_seconds() / 60.0 for start, end in sessions)
    window_minutes = max((window_end - window_start).total_seconds() / 60.0, 0.0)
    sedentary_minutes = max(window_minutes - active_minutes, 0.0)

    streaks = _still_streaks_min(sessions, window_start, window_end)
    session_lengths = [(end - start).total_seconds() / 60.0 for start, end in sessions]

    return {
        "day": window_start.date().isoformat(),
        "active_minutes": round(active_minutes, 2),
        "sedentary_minutes": round(sedentary_minutes, 2),
        "longest_sedentary_streak_min": round(max(streaks), 2) if streaks else round(window_minutes, 2),
        "activity_sessions": len(sessions),
        "mean_session_length_min": round(statistics.mean(session_lengths), 2) if session_lengths else 0.0,
        "sample_size": len(motion_readings_chronological),
    }
