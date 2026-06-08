"""
Local JSONL storage for daily activity summaries and trend checks — pure
I/O, mirroring the read/append/rewrite helpers in src/model/beliefs.py and
src/security/store.py.

This is the most personal data the board ever derives — it describes one
specific person's movement, by their own choice, for their own experiment —
so it gets the *same* hard structural guarantees as the security module's
occupancy data, not weaker ones: data/*.jsonl is git-ignored wholesale (see
.gitignore), and src/model/adapter_sync.py's push path reads only
config/model.yaml: training.labeled_examples_path — never these files. It
never leaves the board.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_DAILY_PATH = Path("./data/wellness_daily.jsonl")
DEFAULT_TRENDS_PATH = Path("./data/wellness_trends.jsonl")


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


def load_daily_summaries(path: Path | None = None) -> list[dict]:
    return _read_jsonl(path or DEFAULT_DAILY_PATH)


def load_trend_checks(path: Path | None = None) -> list[dict]:
    return _read_jsonl(path or DEFAULT_TRENDS_PATH)
