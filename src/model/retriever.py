"""
Top-k retrieval with recency weighting (.claude/agents/reasoner.md § RAG
pipeline, steps 1-2): embed the query, search the vector store, then
re-rank hits by `similarity * exp(-age_h / recency_decay_hours)` so fresh
readings outrank stale-but-similar ones (config/model.yaml: rag).

Returns a `RetrievalResult` ready for src.model.rag_confidence.compute_rag_confidence.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from src.config import load_model_config
from src.ingestion.schema import KnowledgeChunk
from src.knowledge.embedder import Embedder
from src.knowledge.store import VectorStore
from src.model.rag_confidence import RetrievalResult

log = logging.getLogger(__name__)


def _age_hours(chunk: KnowledgeChunk, now: datetime) -> float:
    ts = chunk.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 3600.0)


def recency_weighted_score(similarity: float, chunk: KnowledgeChunk, now: datetime, decay_hours: float) -> float:
    """`score = similarity * exp(-age_h / decay_hours)` — the formula named
    in TODO.md's retriever bullet."""
    return similarity * math.exp(-_age_hours(chunk, now) / decay_hours)


class Retriever:
    """`store`/`embedder` are injectable so retrieval can be tested without
    ChromaDB or Ollama running, mirroring the rest of src.knowledge/src.model."""

    def __init__(self, store: VectorStore | None = None, embedder: Embedder | None = None, cfg: dict | None = None) -> None:
        cfg = cfg or load_model_config()
        self._rag_cfg = cfg["rag"]
        self.store = store or VectorStore(cfg)
        self.embedder = embedder or Embedder(cfg)

    def retrieve(self, query: str, top_k: int | None = None, query_time: datetime | None = None) -> RetrievalResult:
        """Embed `query`, fetch the top-k chunks, then re-sort them by
        recency-weighted score (similarity decays with chunk age)."""
        top_k = top_k or self._rag_cfg["top_k"]
        query_time = query_time or datetime.now(timezone.utc)
        decay_hours = self._rag_cfg["recency_decay_hours"]

        embedding = self.embedder.embed_one(query)
        chunks, similarities = self.store.query(embedding, top_k=top_k)
        if not chunks:
            return RetrievalResult(chunks=[], similarities=[], query_time=query_time)

        ranked = sorted(
            zip(chunks, similarities),
            key=lambda pair: recency_weighted_score(pair[1], pair[0], query_time, decay_hours),
            reverse=True,
        )
        ranked_chunks, ranked_similarities = (list(t) for t in zip(*ranked))
        return RetrievalResult(chunks=ranked_chunks, similarities=ranked_similarities, query_time=query_time)
