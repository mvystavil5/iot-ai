"""
Belief store — read/write `data/beliefs.jsonl` and the invalidation rule
from .claude/agents/reasoner.md § Belief tracking:

  A belief is {query_hash, answer, confidence, supporting_chunk_ids,
  timestamp, invalidated_at}. When new data contradicts an existing belief
  (same query_hash, different answer, confidence > invalidation_threshold),
  the old belief is marked invalidated_at=now and `belief_invalidated`
  fires for the Explorer.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config import load_model_config
from src.events import bus

log = logging.getLogger(__name__)

DEFAULT_BELIEFS_PATH = Path("./data/beliefs.jsonl")


def query_hash(query: str) -> str:
    """Stable identifier grouping repeated/rephrased askings of "the same"
    question — case/whitespace-insensitive so "What's the temp?" and
    " what's the temp? " collapse to one belief lineage."""
    return hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()[:16]


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class BeliefStore:
    def __init__(self, path: Path | None = None, cfg: dict | None = None) -> None:
        self.path = path or DEFAULT_BELIEFS_PATH
        self._invalidation_threshold = (cfg or load_model_config())["beliefs"]["invalidation_threshold"]

    def all(self) -> list[dict]:
        return _read_jsonl(self.path)

    def active_belief(self, query: str) -> dict | None:
        """Most recent non-invalidated belief for this query's lineage, or
        None if it has never been asked (or every answer was invalidated)."""
        qh = query_hash(query)
        active = [b for b in self.all() if b["query_hash"] == qh and b.get("invalidated_at") is None]
        return active[-1] if active else None

    def record(self, query: str, answer: str, confidence: float, supporting_chunk_ids: list[str]) -> dict:
        """Append a new belief for `query`. If it contradicts the current
        active belief for the same query lineage with confidence above
        `beliefs.invalidation_threshold`, the old belief is marked
        invalidated and a `belief_invalidated` event is published."""
        beliefs = self.all()
        qh = query_hash(query)
        previous = next(
            (b for b in reversed(beliefs) if b["query_hash"] == qh and b.get("invalidated_at") is None),
            None,
        )

        belief = {
            "query_hash": qh,
            "query": query,
            "answer": answer,
            "confidence": confidence,
            "supporting_chunk_ids": list(supporting_chunk_ids),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "invalidated_at": None,
        }

        if previous is not None and previous["answer"] != answer and confidence > self._invalidation_threshold:
            previous["invalidated_at"] = belief["timestamp"]
            self._rewrite(beliefs)
            bus.publish("belief_invalidated", previous)
            log.info(
                "Belief for %r invalidated — was %r, now %r (confidence=%.2f > threshold=%.2f)",
                query, previous["answer"], answer, confidence, self._invalidation_threshold,
            )

        self._append(belief)
        return belief

    # -- I/O ----------------------------------------------------------------

    def _append(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(record) + "\n")

    def _rewrite(self, beliefs: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(json.dumps(b) + "\n" for b in beliefs))
