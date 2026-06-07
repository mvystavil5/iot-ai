from datetime import datetime, timezone

import pytest

from src.events import bus
from src.ingestion.schema import KnowledgeChunk
from src.knowledge.builder import KnowledgeBuilder


@pytest.fixture(autouse=True)
def _clear_bus():
    bus.clear()
    yield
    bus.clear()


def _chunk() -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id="chunk-1",
        sensor_id="temp_01",
        timestamp=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
        text="Sensor temp_01 reported 22.4C",
        value=22.4,
        unit="C",
        outlier=False,
        chunk_type="single",
    )


class _FakeEmbedder:
    def __init__(self):
        self.embedded: list[str] = []

    def embed_one(self, text: str) -> list[float]:
        self.embedded.append(text)
        return [float(len(text))]


class _FakeStore:
    def __init__(self):
        self.upserts: list[tuple[list[KnowledgeChunk], list[list[float]]]] = []
        self.evict_calls = 0

    def upsert(self, chunks, embeddings):
        self.upserts.append((chunks, embeddings))

    def evict_to_limit(self, protected_ids=None):
        self.evict_calls += 1
        return 0


def test_handle_chunk_embeds_upserts_evicts_and_announces():
    embedder, store = _FakeEmbedder(), _FakeStore()
    builder = KnowledgeBuilder(store=store, embedder=embedder)
    received = []
    bus.subscribe("store_updated", received.append)

    chunk = _chunk()
    builder.handle_chunk(chunk)

    assert embedder.embedded == [chunk.text]
    assert len(store.upserts) == 1
    chunks, embeddings = store.upserts[0]
    assert chunks == [chunk]
    assert embeddings == [[float(len(chunk.text))]]
    assert store.evict_calls == 1
    assert received == [chunk]


def test_start_subscribes_to_knowledge_chunks_topic():
    embedder, store = _FakeEmbedder(), _FakeStore()
    builder = KnowledgeBuilder(store=store, embedder=embedder)
    builder.start()

    chunk = _chunk()
    bus.publish("knowledge_chunks", chunk)

    assert embedder.embedded == [chunk.text]
    assert len(store.upserts) == 1
