import math
from datetime import datetime, timedelta, timezone

import pytest

from src.ingestion.schema import KnowledgeChunk
from src.model.retriever import Retriever, _age_hours, recency_weighted_score

NOW = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)


def _chunk(chunk_id: str, age_hours: float) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id, sensor_id="temp_01",
        timestamp=NOW - timedelta(hours=age_hours),
        text=f"chunk {chunk_id}", value=22.0, unit="C", outlier=False,
    )


def _cfg(top_k: int = 8, decay_hours: float = 24.0) -> dict:
    return {"rag": {
        "top_k": top_k, "min_similarity": 0.3, "recency_decay_hours": decay_hours,
        "confidence_weights": {"coverage": 0.25, "similarity": 0.4, "recency": 0.2, "consistency": 0.15},
    }}


class _FakeEmbedder:
    def __init__(self):
        self.embedded: list[str] = []

    def embed_one(self, text: str) -> list[float]:
        self.embedded.append(text)
        return [0.0]


class _FakeStore:
    def __init__(self, chunks, similarities):
        self._chunks = chunks
        self._similarities = similarities
        self.queries: list[tuple] = []

    def query(self, embedding, top_k=8):
        self.queries.append((embedding, top_k))
        return list(self._chunks), list(self._similarities)


def test_age_hours_computes_difference():
    assert _age_hours(_chunk("c1", age_hours=2), NOW) == pytest.approx(2.0)


def test_recency_weighted_score_decays_with_age():
    fresh = recency_weighted_score(0.8, _chunk("c1", age_hours=0), NOW, decay_hours=24.0)
    old = recency_weighted_score(0.8, _chunk("c2", age_hours=48), NOW, decay_hours=24.0)
    assert fresh > old
    assert old == pytest.approx(0.8 * math.exp(-2.0))


def test_retrieve_embeds_query_and_passes_top_k_through():
    store = _FakeStore([_chunk("c1", 0)], [0.9])
    embedder = _FakeEmbedder()
    retriever = Retriever(store=store, embedder=embedder, cfg=_cfg(top_k=5))

    result = retriever.retrieve("how hot is it?", query_time=NOW)

    assert embedder.embedded == ["how hot is it?"]
    assert store.queries == [([0.0], 5)]
    assert result.query_time == NOW


def test_retrieve_reorders_by_recency_weighted_score():
    old_but_similar = _chunk("old", age_hours=48)
    new_but_less_similar = _chunk("new", age_hours=0)
    store = _FakeStore([old_but_similar, new_but_less_similar], [0.9, 0.85])
    retriever = Retriever(store=store, embedder=_FakeEmbedder(), cfg=_cfg(decay_hours=24.0))

    result = retriever.retrieve("q", query_time=NOW)

    assert [c.chunk_id for c in result.chunks] == ["new", "old"]
    assert result.similarities == [0.85, 0.9]


def test_retrieve_returns_empty_result_when_store_has_nothing():
    retriever = Retriever(store=_FakeStore([], []), embedder=_FakeEmbedder(), cfg=_cfg())

    result = retriever.retrieve("q", query_time=NOW)

    assert result.chunks == []
    assert result.similarities == []
