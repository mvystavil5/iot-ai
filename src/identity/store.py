"""
Local JSONL storage for opt-in identity profiles and match records — pure
I/O, mirroring the read/append/rewrite helpers in src/model/beliefs.py.

This data is the most sensitive the board holds (a person's consciously
registered behavioral routine, plus the live match log) and never leaves
the board: data/*.jsonl is git-ignored wholesale (see .gitignore), and
src/model/adapter_sync.py's push path reads only
config/model.yaml: training.labeled_examples_path — never these files.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_PROFILES_PATH = Path("./data/identity_profiles.jsonl")
DEFAULT_MATCHES_PATH = Path("./data/identity_matches.jsonl")


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


def load_profiles(path: Path | None = None) -> list[dict]:
    return _read_jsonl(path or DEFAULT_PROFILES_PATH)


def load_matches(path: Path | None = None) -> list[dict]:
    return _read_jsonl(path or DEFAULT_MATCHES_PATH)
