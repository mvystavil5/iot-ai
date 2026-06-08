"""
Activity-trend comparison — compare a recent window of daily summaries
against the window before it and report whether the pattern is shifting (see
tracker.py for how summaries accumulate). The personal-health counterpart to
src/security/detector.py: same shape (pure scorer + I/O-glue runner that
persists and publishes), entirely different question — "is *my* movement
trending differently than it was", never "does this match someone else's
profile".

This is a small statistical comparator, not an LLM call and not a medical
one: see src/model/rag_confidence.py's "no LLM introspection" precedent —
the same reasoning applies here, one level lower in the stack, for the same
reason (trend arithmetic over two small lists of numbers is not a task that
benefits from an LLM's involvement, and dressing it up as one would only
invite false authority). `more_sedentary` / `more_active` are observations
about *minutes*, not a diagnosis — the experiment this module exists to run
is "can simple sensors say anything informative at all", not "what does it
mean", and the honest answer to the second question is "ask a professional,
this system can't tell you".

  python -m src.wellness.trends --check
"""

from __future__ import annotations

import argparse
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path

from src.config import load_model_config
from src.events import bus
from src.wellness import store

log = logging.getLogger(__name__)

STATUS_STABLE = "stable"
STATUS_MORE_SEDENTARY = "more_sedentary"
STATUS_MORE_ACTIVE = "more_active"
STATUS_INSUFFICIENT_DATA = "insufficient_data"

_DELTA_KEYS = ("active_minutes_delta", "sedentary_minutes_delta", "longest_sedentary_streak_delta")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _avg(summaries: list[dict], key: str) -> float:
    values = [s[key] for s in summaries]
    return statistics.mean(values) if values else 0.0


def score_trend(recent: list[dict], baseline: list[dict]) -> dict:
    """Signed average-per-day deltas (recent minus baseline) in minutes —
    pure, metadata-only, no I/O. Mirrors detector.score_similarity's shape:
    components in, numbers out. Positive `sedentary_minutes_delta` means
    "more still time per day lately than before"."""
    return {
        "active_minutes_delta": round(_avg(recent, "active_minutes") - _avg(baseline, "active_minutes"), 2),
        "sedentary_minutes_delta": round(_avg(recent, "sedentary_minutes") - _avg(baseline, "sedentary_minutes"), 2),
        "longest_sedentary_streak_delta": round(
            _avg(recent, "longest_sedentary_streak_min") - _avg(baseline, "longest_sedentary_streak_min"), 2
        ),
    }


def detect_trend(recent: list[dict], baseline: list[dict], cfg: dict | None = None) -> dict:
    """Classify how `recent` days compare to the `baseline` days before them:
    `more_sedentary` (notably more still time / longer still streaks lately),
    `more_active` (notably less), `stable` (within the configured threshold),
    or `insufficient_data` (too few days on either side to say anything
    honest). Below the threshold, "nothing notable" is the honest read —
    never a forced verdict from too little history."""
    cfg = cfg or load_model_config()
    wcfg = cfg.get("wellness", {})
    min_days = wcfg.get("min_days_per_window", 3)
    threshold = wcfg.get("trend_alert_minutes", 30.0)

    if len(recent) < min_days or len(baseline) < min_days:
        return {"status": STATUS_INSUFFICIENT_DATA, **{k: 0.0 for k in _DELTA_KEYS}}

    deltas = score_trend(recent, baseline)
    if deltas["sedentary_minutes_delta"] >= threshold or deltas["longest_sedentary_streak_delta"] >= threshold:
        status = STATUS_MORE_SEDENTARY
    elif deltas["sedentary_minutes_delta"] <= -threshold:
        status = STATUS_MORE_ACTIVE
    else:
        status = STATUS_STABLE
    return {"status": status, **deltas}


def run_trend_check(*, daily_path: Path | None = None, trends_path: Path | None = None, cfg: dict | None = None) -> dict:
    """I/O glue: split the recorded history into a recent window and the
    baseline window immediately before it, classify the shift, persist the
    result, and publish `wellness_trend_checked` (always) plus
    `wellness_risk_flagged` (only on `more_sedentary` — "might be worth a
    look", framed as exactly that and nothing stronger)."""
    cfg = cfg or load_model_config()
    wcfg = cfg.get("wellness", {})
    recent_days = wcfg.get("recent_window_days", 7)
    baseline_days = wcfg.get("baseline_window_days", 21)

    summaries = store.load_daily_summaries(daily_path)
    recent = summaries[-recent_days:] if recent_days > 0 else []
    rest = summaries[: len(summaries) - len(recent)]
    baseline = rest[-baseline_days:] if baseline_days > 0 else []

    result = detect_trend(recent, baseline, cfg)
    record = {**result, "checked_at": _now_iso(), "recent_days": len(recent), "baseline_days": len(baseline)}

    store._append(trends_path or store.DEFAULT_TRENDS_PATH, record)
    bus.publish("wellness_trend_checked", record)
    if record["status"] == STATUS_MORE_SEDENTARY:
        bus.publish("wellness_risk_flagged", record)
    log.info("Wellness trend check: %s (recent=%d baseline=%d day(s))", record["status"], record["recent_days"], record["baseline_days"])
    return record


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare your recent activity to the period before it")
    p.add_argument("--check", action="store_true", required=True, help="Run a trend check and print the result")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


def _print_result(record: dict) -> None:
    if record["status"] == STATUS_INSUFFICIENT_DATA:
        print(f"not enough recorded days yet to compare (recent={record['recent_days']}, baseline={record['baseline_days']}) "
              f"— keep running `python -m src.wellness.tracker --record`")
        return
    label = {
        STATUS_STABLE: "stable — no notable shift",
        STATUS_MORE_SEDENTARY: "more sedentary lately — might be worth a look (not a diagnosis)",
        STATUS_MORE_ACTIVE: "more active lately",
    }[record["status"]]
    print(f"{label}\n  active_minutes/day:            {record['active_minutes_delta']:+.1f}\n"
          f"  sedentary_minutes/day:         {record['sedentary_minutes_delta']:+.1f}\n"
          f"  longest_sedentary_streak/day:  {record['longest_sedentary_streak_delta']:+.1f}")


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    _print_result(run_trend_check())
