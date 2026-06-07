"""
Vector store client wrapper — upsert, query, evict, stats over the
configured backend (ChromaDB in-process for Phase 1; swap to Qdrant for
scale per config/model.yaml: vector_store, no code changes needed beyond
this module — see .claude/agents/knowledge-builder.md).

The ChromaDB client is imported lazily (and is injectable) so this module
stays importable and unit-testable without ChromaDB installed, mirroring
src.model.adapter_sync / src.model.trainer's lazy-import conventions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

from src.config import load_model_config
from src.ingestion.schema import KnowledgeChunk

log = logging.getLogger(__name__)

DEFAULT_TOP_K = 8


class _Collection(Protocol):
    def upsert(self, ids, embeddings, documents, metadatas) -> None: ...
    def query(self, query_embeddings, n_results) -> dict: ...
    def get(self, include=None) -> dict: ...
    def delete(self, ids) -> None: ...
    def count(self) -> int: ...


class _Client(Protocol):
    def get_or_create_collection(self, name: str) -> _Collection: ...


def _chunk_metadata(chunk: KnowledgeChunk) -> dict:
    """Flatten a KnowledgeChunk into ChromaDB-compatible scalar metadata
    (Chroma metadata values must be str/int/float/bool — `tags` is JSON-encoded)."""
    return {
        "sensor_id": chunk.sensor_id,
        "timestamp": chunk.timestamp.isoformat(),
        "value": chunk.value,
        "unit": chunk.unit,
        "outlier": chunk.outlier,
        "tags": json.dumps(chunk.tags),
        "chunk_type": chunk.chunk_type,
    }


def _chunk_from_record(chunk_id: str, document: str, metadata: dict) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        sensor_id=metadata["sensor_id"],
        timestamp=metadata["timestamp"],
        text=document,
        value=metadata["value"],
        unit=metadata["unit"],
        outlier=bool(metadata["outlier"]),
        tags=json.loads(metadata.get("tags", "{}")),
        chunk_type=metadata.get("chunk_type", "single"),
    )


class VectorStore:
    """ChromaDB-backed semantic memory. `client` is injectable — tests pass
    a fake exposing the minimal `_Client`/`_Collection` protocol above."""

    def __init__(self, cfg: dict | None = None, client: _Client | None = None) -> None:
        vs_cfg = (cfg or load_model_config())["vector_store"]
        self.path = Path(vs_cfg["path"])
        self.collection_name = vs_cfg["collection"]
        self.max_chunks = vs_cfg["max_chunks"]
        self._client = client
        self._collection: _Collection | None = None

    def _get_collection(self) -> _Collection:
        if self._collection is None:
            client = self._client
            if client is None:
                import chromadb

                self.path.mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(path=str(self.path))
                self._client = client
            self._collection = client.get_or_create_collection(self.collection_name)
        return self._collection

    # -- upsert ------------------------------------------------------------

    def upsert(self, chunks: list[KnowledgeChunk], embeddings: list[list[float]]) -> None:
        """Embed-and-index a batch of chunks. `embeddings` must be parallel
        to `chunks` (the Knowledge Builder owns calling the embedder —
        keeping this wrapper backend-only, per its single responsibility)."""
        if not chunks:
            return
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")

        self._get_collection().upsert(
            ids=[c.chunk_id for c in chunks],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[_chunk_metadata(c) for c in chunks],
        )
        log.debug("Upserted %d chunk(s) into %s", len(chunks), self.collection_name)

    # -- query --------------------------------------------------------------

    def query(self, query_embedding: list[float], top_k: int = DEFAULT_TOP_K) -> tuple[list[KnowledgeChunk], list[float]]:
        """Top-k semantic search. Returns (chunks, similarities) — cosine
        similarity derived as `1 - distance` (Chroma's default cosine space)."""
        result = self._get_collection().query(query_embeddings=[query_embedding], n_results=top_k)
        ids = result["ids"][0]
        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        distances = result["distances"][0]

        chunks = [_chunk_from_record(i, d, m) for i, d, m in zip(ids, documents, metadatas)]
        similarities = [1.0 - dist for dist in distances]
        return chunks, similarities

    # -- maintenance ---------------------------------------------------------

    def evict_to_limit(self, protected_ids: set[str] | None = None) -> int:
        """Sliding-window eviction: drop the oldest chunks once the store
        exceeds `max_chunks`, skipping any in `protected_ids` (e.g. chunks
        referenced by active beliefs — knowledge-builder.md § Core loop, step 5).
        Returns the number of chunks evicted."""
        protected_ids = protected_ids or set()
        collection = self._get_collection()
        overflow = collection.count() - self.max_chunks
        if overflow <= 0:
            return 0

        record = collection.get(include=["metadatas"])
        candidates = sorted(
            (
                (chunk_id, meta.get("timestamp", ""))
                for chunk_id, meta in zip(record["ids"], record["metadatas"])
                if chunk_id not in protected_ids
            ),
            key=lambda pair: pair[1],
        )
        to_evict = [chunk_id for chunk_id, _ in candidates[:overflow]]
        if to_evict:
            collection.delete(ids=to_evict)
            log.info("Evicted %d chunk(s) (oldest-first, max_chunks=%d)", len(to_evict), self.max_chunks)
        return len(to_evict)

    def stats(self) -> dict:
        collection = self._get_collection()
        return {
            "collection": self.collection_name,
            "path": str(self.path),
            "count": collection.count(),
            "max_chunks": self.max_chunks,
        }
