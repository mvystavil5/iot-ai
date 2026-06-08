"""
Local JSONL storage for the learned occupancy baseline and anomaly-check
history — pure I/O, mirroring the read/append/rewrite helpers in
src/model/beliefs.py.

This data never leaves the board: data/*.jsonl is git-ignored wholesale (see
.gitignore), and src/model/adapter_sync.py's push path reads only
config/model.yaml: training.labeled_examples_path — never these files. Unlike
the identity profiles this module replaced, nothing here is tied to a named
individual — it's an aggregate statistical description of "normal occupancy
for this space" plus a log of how live activity scored against it.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_BASELINE_PATH = Path("./data/occupancy_baseline.jsonl")
DEFAULT_ALERTS_PATH = Path("./data/occupancy_alerts.jsonl")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _rewrite(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def load_baselines(path: Path | None = None) -> list[dict]:
    return _read_jsonl(path or DEFAULT_BASELINE_PATH)


def load_alerts(path: Path | None = None) -> list[dict]:
    return _read_jsonl(path or DEFAULT_ALERTS_PATH)
