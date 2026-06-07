"""
Hypothesis scheduler — pop the highest-ranked pending hypothesis, dispatch
it to the matching experiment runner, and log the outcome (see
.claude/agents/explorer.md § Experiment scheduling and
.claude/skills/run-experiment.md).

  python -m src.exploration.scheduler --list
  python -m src.exploration.scheduler --run-next [--verbose]
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from src.exploration import experiments, outcomes
from src.exploration.hypothesis_generator import DEFAULT_QUEUE_PATH, load_queue

log = logging.getLogger(__name__)

# One runner per hypothesis_generator.generate "experiment_type" — see
# explorer.md § Experiment scheduling for the four kinds it documents
# (observation/alert/simulation/active query). Phase 1 has no actuation
# hardware, so "active query" has no runner yet.
RUNNERS = {
    "observation": experiments.run_observation_experiment,
    "alert": experiments.run_alert_experiment,
    "simulation": experiments.run_simulation_experiment,
}


def _rewrite_queue(hypotheses: list[dict], path: Path | None = None) -> None:
    path = path or DEFAULT_QUEUE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(h) + "\n" for h in hypotheses))


def list_queue(path: Path | None = None) -> list[dict]:
    """Pending hypotheses, highest-ranked first."""
    pending = [h for h in load_queue(path) if h.get("status") == "pending"]
    return sorted(pending, key=lambda h: h["score"], reverse=True)


def run_next(*, path: Path | None = None, runners: dict | None = None, record_outcome=None) -> dict | None:
    """Run the top-ranked pending hypothesis end-to-end: dispatch the
    matching experiment runner, log the outcome, and mark the hypothesis
    done. Returns None if nothing is pending."""
    runners = runners if runners is not None else RUNNERS
    record_outcome = record_outcome or outcomes.record_outcome

    queue = load_queue(path)
    pending = [h for h in queue if h.get("status") == "pending"]
    if not pending:
        return None

    hypothesis = max(pending, key=lambda h: h["score"])
    runner = runners.get(hypothesis["experiment_type"])
    if runner is None:
        raise ValueError(f"No experiment runner registered for type {hypothesis['experiment_type']!r}")

    result = runner(hypothesis)
    record = record_outcome(hypothesis, result)

    hypothesis["status"] = "done"
    _rewrite_queue(queue, path)

    log.info(
        "Ran %s (%s) -> %s (confidence_delta=%+.2f)",
        hypothesis["hypothesis_id"], hypothesis["experiment_type"], result["outcome"], result["confidence_delta"],
    )
    return {"hypothesis": hypothesis, "result": result, "outcome_record": record}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the Explorer's hypothesis scheduler")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Show the pending hypothesis queue, highest-ranked first")
    group.add_argument("--run-next", action="store_true", help="Run the top-ranked pending hypothesis")
    p.add_argument("--verbose", action="store_true", help="Print the experiment evidence alongside the verdict")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")

    if args.list:
        pending = list_queue()
        if not pending:
            print("Hypothesis queue is empty.")
        for h in pending:
            print(f"{h['score']:.2f}  {h['hypothesis_id']}  [{h['experiment_type']}]  {h['statement']}")
    else:
        outcome = run_next()
        if outcome is None:
            print("Hypothesis queue is empty — nothing to run.")
        else:
            h, result = outcome["hypothesis"], outcome["result"]
            print(f"Tested {h['hypothesis_id']}: {h['statement']}")
            print(f"Outcome: {result['outcome']} (confidence_delta={result['confidence_delta']:+.2f})")
            if args.verbose:
                print(f"Evidence: {result['evidence']}")
                print(f"Forwarded to Trainer: {outcome['outcome_record']['labeled_example'] is not None}")
