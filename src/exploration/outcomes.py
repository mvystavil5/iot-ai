"""
Outcome logging — turn experiment results into the labeled-example format
the Trainer consumes (see .claude/agents/explorer.md § Outcome logging):

  {"hypothesis_id": ..., "outcome": "confirmed|refuted|inconclusive",
   "confidence_delta": ..., "new_chunks": [...],
   "labeled_example": {"input": ..., "output": ..., "label": ...}}

Examples whose outcome != "inconclusive" are appended to
training.labeled_examples_path and a `labeled_examples` event fires so the
Trainer can track accumulation toward training.trigger_threshold
(config/agents.yaml: trainer.trigger includes labeled_examples_threshold).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config import load_model_config
from src.events import bus

log = logging.getLogger(__name__)

DEFAULT_OUTCOMES_PATH = Path("./data/experiment_outcomes.jsonl")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_labeled_example(hypothesis: dict, result: dict) -> dict:
    """{input, output, label} training pair — input is the hypothesis under
    test, output is the gathered evidence, label is the verdict."""
    return {"input": hypothesis["statement"], "output": result["evidence"], "label": result["outcome"]}


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def record_outcome(
    hypothesis: dict,
    result: dict,
    *,
    outcomes_path: Path | None = None,
    labeled_examples_path: Path | None = None,
    cfg: dict | None = None,
) -> dict:
    """Persist the experiment outcome and, unless inconclusive, forward a
    labeled example to the Trainer's queue (explorer.md: "Labeled examples
    with outcome != inconclusive are forwarded to Trainer")."""
    cfg = cfg or load_model_config()
    outcomes_path = Path(outcomes_path) if outcomes_path else DEFAULT_OUTCOMES_PATH
    labeled_examples_path = (
        Path(labeled_examples_path) if labeled_examples_path else Path(cfg["training"]["labeled_examples_path"])
    )

    record = {
        "hypothesis_id": hypothesis["hypothesis_id"],
        "outcome": result["outcome"],
        "confidence_delta": result["confidence_delta"],
        "evidence": result.get("evidence", ""),
        "new_chunks": result.get("new_chunks", []),
        "recorded_at": _now_iso(),
        "labeled_example": None,
    }

    if result["outcome"] != "inconclusive":
        labeled_example = to_labeled_example(hypothesis, result)
        record["labeled_example"] = labeled_example
        _append_jsonl(labeled_examples_path, labeled_example)
        bus.publish("labeled_examples", labeled_example)
        log.info(
            "Outcome %s for %s -> labeled example forwarded to Trainer",
            result["outcome"], hypothesis["hypothesis_id"],
        )

    _append_jsonl(outcomes_path, record)
    return record
