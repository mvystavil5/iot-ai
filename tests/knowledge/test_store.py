from datetime import datetime, timedelta, timezone

import pytest

from src.ingestion.schema import KnowledgeChunk
from src.knowledge.store import VectorStore


def _cfg(tmp_path) -> dict:
    return {
        "vector_store": {
            "backend": "chromadb",
            "path": str(tmp_path / "chroma"),
            "collection": "iot_knowledge",
            "max_chunks": 3,
            "eviction_policy": "oldest_not_referenced",
        }
    }


def _chunk(idx: int, minute: int) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=f"chunk-{idx}",
        sensor_id="temp_01",
        timestamp=datetime(2026, 6, 7, 12, minute, tzinfo=timezone.utc),
        text=f"Sensor temp_01 reported {idx}C",
        value=float(idx),
        unit="C",
        outlier=False,
        chunk_type="single",
    )


class _FakeCollection:
    """In-memory stand-in for a chromadb Collection, ordered insertion-stable."""

    def __init__(self):
        self._docs: dict[str, tuple[str, dict, list[float]]] = {}

    def upsert(self, ids, embeddings, documents, metadatas):
        for chunk_id, emb, doc, meta in zip(ids, embeddings, documents, metadatas):
            self._docs[chunk_id] = (doc, meta, emb)

    def count(self) -> int:
        return len(self._docs)

    def get(self, include=None):
        ids = list(self._docs)
        return {"ids": ids, "metadatas": [self._docs[i][1] for i in ids]}

    def delete(self, ids):
        for chunk_id in ids:
            self._docs.pop(chunk_id, None)

    def query(self, query_embeddings, n_results):
        ids = list(self._docs)[:n_results]
        return {
            "ids": [ids],
            "documents": [[self._docs[i][0] for i in ids]],
            "metadatas": [[self._docs[i][1] for i in ids]],
            "distances": [[0.1 * (idx + 1) for idx in range(len(ids))]],
        }


class _FakeClient:
    def __init__(self):
        self.collections: dict[str, _FakeCollection] = {}

    def get_or_create_collection(self, name):
        return self.collections.setdefault(name, _FakeCollection())


def _store(tmp_path) -> VectorStore:
    return VectorStore(_cfg(tmp_path), client=_FakeClient())


# --- upsert / query ---

def test_upsert_then_query_round_trips_chunk(tmp_path):
    store = _store(tmp_path)
    chunk = _chunk(1, 0)
    store.upsert([chunk], [[0.1, 0.2]])

    chunks, similarities = store.query([0.1, 0.2], top_k=8)

    assert len(chunks) == 1
    assert chunks[0].chunk_id == "chunk-1"
    assert chunks[0].sensor_id == "temp_01"
    assert chunks[0].text == chunk.text
    assert similarities[0] == pytest.approx(0.9)  # 1 - distance(0.1)


def test_upsert_rejects_mismatched_lengths(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.upsert([_chunk(1, 0), _chunk(2, 1)], [[0.1, 0.2]])


def test_upsert_empty_is_a_no_op(tmp_path):
    store = _store(tmp_path)
    store.upsert([], [])
    assert store.stats()["count"] == 0


# --- eviction ---

def test_evict_to_limit_drops_oldest_first(tmp_path):
    store = _store(tmp_path)
    for idx, minute in enumerate([10, 5, 0, 15], start=1):  # insertion order != age order
        store.upsert([_chunk(idx, minute)], [[0.0]])

    evicted = store.evict_to_limit()

    assert evicted == 1
    remaining = {c.chunk_id for c in store.query([0.0], top_k=10)[0]}
    assert "chunk-3" not in remaining  # minute=0 -> oldest -> evicted
    assert remaining == {"chunk-1", "chunk-2", "chunk-4"}


def test_evict_to_limit_skips_protected_ids(tmp_path):
    store = _store(tmp_path)
    for idx, minute in enumerate([0, 1, 2, 3], start=1):
        store.upsert([_chunk(idx, minute)], [[0.0]])

    evicted = store.evict_to_limit(protected_ids={"chunk-1"})

    assert evicted == 1
    remaining = {c.chunk_id for c in store.query([0.0], top_k=10)[0]}
    assert "chunk-1" in remaining       # protected despite being oldest
    assert "chunk-2" not in remaining   # next-oldest unprotected one evicted instead


def test_evict_to_limit_no_op_under_limit(tmp_path):
    store = _store(tmp_path)
    store.upsert([_chunk(1, 0)], [[0.0]])
    assert store.evict_to_limit() == 0


# --- stats ---

def test_stats_reports_collection_health(tmp_path):
    store = _store(tmp_path)
    store.upsert([_chunk(1, 0)], [[0.0]])

    stats = store.stats()
    assert stats["collection"] == "iot_knowledge"
    assert stats["count"] == 1
    assert stats["max_chunks"] == 3
