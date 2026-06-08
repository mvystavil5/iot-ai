"""
Daily activity tracking — turn one calendar day of motion history into a
persisted activity summary (see metrics.build_daily_summary). This is the
personal-health counterpart to src/security/learner.py: same shape (build →
persist → publish, plus a hard-delete reset), entirely different purpose —
there is no "baseline" here to compare *against* a person, only a per-day
record of *one's own* movement, kept for *their own* trend-spotting.

Nothing here is shared, matched, or compared between people. It is a running
diary of one sensor's view of one person's day, owned by that person.

`reset_history` is a hard purge of every recorded day AND every trend check
ever computed from them — a real "delete my data and start over", not a soft
flag (same standard as src/security/learner.reset_baseline).

  python -m src.wellness.tracker --record [--day 2026-06-08]
  python -m src.wellness.tracker --reset
  python -m src.wellness.tracker --show [--days 7]
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.config import load_sensor_registry
from src.events import bus
from src.wellness import store
from src.wellness.metrics import build_daily_summary
from src.ingestion.storage import TimeSeriesStore

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _motion_sensor_id(registry: dict) -> str | None:
    for sensor in registry.get("sensors", []):
        if sensor.get("type") == "motion":
            return sensor["id"]
    return None


def record_day(
    day: date,
    *,
    ts_store: TimeSeriesStore | None = None,
    registry: dict | None = None,
    daily_path: Path | None = None,
) -> dict:
    """Aggregate one UTC calendar day [00:00, 24:00) of motion history into
    an activity summary and persist it. Re-running for the same day appends
    a fresh record — load_daily_summaries returns them in recorded order, so
    the latest stands as the day's record (mirrors learner.learn_baseline's
    "most recent wins" convention)."""
    registry = registry if registry is not None else load_sensor_registry()
    ts_store = ts_store or TimeSeriesStore()

    sensor_id = _motion_sensor_id(registry)
    if sensor_id is None:
        raise ValueError("No motion sensor in the registry — cannot build a wellness summary")

    window_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    window_end = window_start + timedelta(days=1)

    readings = ts_store.query(sensor_id, since=window_start.isoformat(), limit=100_000)
    in_window = [r for r in readings if r.timestamp < window_end]
    chronological = list(reversed(in_window))  # TimeSeriesStore.query is most-recent-first

    summary = build_daily_summary(chronological, window_start, window_end)
    store._append(daily_path or store.DEFAULT_DAILY_PATH, summary)
    bus.publish("wellness_day_recorded", summary)
    log.info(
        "Recorded wellness summary for %s — active=%.0fmin sedentary=%.0fmin sessions=%d",
        summary["day"], summary["active_minutes"], summary["sedentary_minutes"], summary["activity_sessions"],
    )
    return summary


def get_recent_days(n: int = 30, *, daily_path: Path | None = None) -> list[dict]:
    """The most recently recorded `n` daily summaries, oldest first."""
    summaries = store.load_daily_summaries(daily_path)
    return summaries[-n:] if n > 0 else summaries


def reset_history(
    *,
    daily_path: Path | None = None,
    trends_path: Path | None = None,
) -> bool:
    """Hard-delete every recorded day AND every trend check ever computed
    from them — a real "delete my data and start over". Returns False if
    there was nothing to reset."""
    daily_path = daily_path or store.DEFAULT_DAILY_PATH
    trends_path = trends_path or store.DEFAULT_TRENDS_PATH

    daily = store.load_daily_summaries(daily_path)
    trends = store.load_trend_checks(trends_path)
    if not daily and not trends:
        return False

    store._rewrite(daily_path, [])
    store._rewrite(trends_path, [])

    bus.publish("wellness_history_reset", {"reset_at": _now_iso()})
    log.info("Reset wellness history — purged %d day(s) and %d trend check(s)", len(daily), len(trends))
    return True


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Record / reset / show your personal daily activity history")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--record", action="store_true", help="Aggregate one day of motion history into a summary")
    group.add_argument("--reset", action="store_true", help="Hard-delete all recorded days and trend checks")
    group.add_argument("--show", action="store_true", help="Print recent daily summaries")
    p.add_argument("--day", type=str, default=None, help="ISO date (YYYY-MM-DD) to record; defaults to yesterday (UTC)")
    p.add_argument("--days", type=int, default=7, help="How many recent days to print (with --show)")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")

    if args.record:
        target_day = date.fromisoformat(args.day) if args.day else (_now() - timedelta(days=1)).date()
        summary = record_day(target_day)
        print(f"{summary['day']}: active={summary['active_minutes']:.0f}min "
              f"sedentary={summary['sedentary_minutes']:.0f}min "
              f"longest_still_streak={summary['longest_sedentary_streak_min']:.0f}min "
              f"sessions={summary['activity_sessions']}")
    elif args.reset:
        if reset_history():
            print("Reset — all recorded days and trend checks purged.")
        else:
            print("Nothing to reset — no wellness history on file.")
    else:
        for summary in get_recent_days(args.days):
            print(f"{summary['day']}: active={summary['active_minutes']:.0f}min "
                  f"sedentary={summary['sedentary_minutes']:.0f}min "
                  f"longest_still_streak={summary['longest_sedentary_streak_min']:.0f}min "
                  f"sessions={summary['activity_sessions']}")
