"""
Knowledge Builder orchestration — wires the embedding and vector-store
pieces into the event-driven core loop from .claude/agents/knowledge-builder.md:
receive a KnowledgeChunk -> embed its text -> upsert into the vector store ->
maintain the sliding-window eviction -> announce `store_updated` for the
Reasoner's cache invalidation.

  from src.knowledge.builder import KnowledgeBuilder
  KnowledgeBuilder().start()   # subscribes to the `knowledge_chunks` topic
"""

from __future__ import annotations

import logging

from src.config import load_model_config
from src.events import bus
from src.ingestion.schema import KnowledgeChunk
from src.knowledge.embedder import Embedder
from src.knowledge.store import VectorStore

log = logging.getLogger(__name__)


class KnowledgeBuilder:
    """One instance per process — `store`/`embedder` are injectable so the
    orchestration logic can be tested without ChromaDB or Ollama running."""

    def __init__(self, store: VectorStore | None = None, embedder: Embedder | None = None, cfg: dict | None = None) -> None:
        cfg = cfg or load_model_config()
        self.store = store or VectorStore(cfg)
        self.embedder = embedder or Embedder(cfg)

    def handle_chunk(self, chunk: KnowledgeChunk) -> None:
        """Embed, index, evict, and announce — the full per-chunk loop."""
        embedding = self.embedder.embed_one(chunk.text)
        self.store.upsert([chunk], [embedding])
        self.store.evict_to_limit()
        bus.publish("store_updated", chunk)
        log.debug("Indexed chunk %s (sensor=%s, type=%s)", chunk.chunk_id, chunk.sensor_id, chunk.chunk_type)

    def start(self) -> None:
        bus.subscribe("knowledge_chunks", self.handle_chunk)
        log.info("Knowledge Builder subscribed to 'knowledge_chunks'")
