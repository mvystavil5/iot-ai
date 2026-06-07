"""
Embedding pipeline — turns KnowledgeChunk text into vectors via the
configured embedding backend (Ollama nomic-embed-text by default; see
.claude/agents/knowledge-builder.md and config/model.yaml: embedding).

The Ollama client is imported lazily (and is injectable) so this module
stays importable and unit-testable on hosts without Ollama installed or
running — the same pattern src.model.trainer uses for its ML dependencies.
"""

from __future__ import annotations

import logging
from typing import Protocol

from src.config import load_model_config

log = logging.getLogger(__name__)


class _EmbeddingClient(Protocol):
    def embeddings(self, model: str, prompt: str) -> dict: ...


class Embedder:
    """Wraps the configured embedding backend. `client` is injectable —
    tests pass a fake exposing `.embeddings(model, prompt) -> {"embedding": [...]}`,
    matching the `ollama` Python client's response shape."""

    def __init__(self, cfg: dict | None = None, client: _EmbeddingClient | None = None) -> None:
        embed_cfg = (cfg or load_model_config())["embedding"]
        self.model = embed_cfg["model"]
        self.dim = embed_cfg["dim"]
        self._client = client

    def _get_client(self) -> _EmbeddingClient:
        if self._client is None:
            import ollama

            self._client = ollama
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts; returns one vector per input, in order."""
        client = self._get_client()
        vectors: list[list[float]] = []
        for text in texts:
            response = client.embeddings(model=self.model, prompt=text)
            vector = response["embedding"]
            if len(vector) != self.dim:
                log.warning(
                    "Embedding for model %s returned dim=%d, expected dim=%d (config/model.yaml: embedding.dim)",
                    self.model, len(vector), self.dim,
                )
            vectors.append(vector)
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
