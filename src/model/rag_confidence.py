"""
RAG-derived confidence scoring — no LLM introspection required.

Confidence is computed entirely from retrieval metadata: how many chunks
were found, how similar they are, how fresh they are, and how consistent
values are within each sensor stream.
"""

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.ingestion.schema import KnowledgeChunk


@dataclass
class RetrievalResult:
    """Output from the vector store for a single query."""

    chunks: list[KnowledgeChunk]
    similarities: list[float]  # cosine similarity per chunk, parallel to chunks
    query_time: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if len(self.chunks) != len(self.similarities):
            raise ValueError("chunks and similarities must have the same length")


def compute_rag_confidence(result: RetrievalResult, cfg: dict) -> float:
    """
    Return a confidence score in [0, 1] derived from retrieval quality.

    Four components (weights configurable in config/model.yaml rag.confidence_weights):

    coverage    — fraction of requested top_k slots that were filled.
                  A query returning 2 of 8 chunks signals sparse knowledge.

    similarity  — mean cosine similarity of chunks that exceed min_similarity.
                  Low similarity = the vector store is guessing.

    recency     — exponential decay by chunk age using recency_decay_hours as
                  the time constant. Staleness matters for sensor readings.

    consistency — within each sensor stream, low coefficient of variation
                  means the chunks agree on a stable value. High spread
                  signals noise or a fast-changing state.
    """
    if not result.chunks:
        return 0.0

    rag_cfg = cfg.get("rag", {})
    top_k: int = rag_cfg.get("top_k", 8)
    min_sim: float = rag_cfg.get("min_similarity", 0.3)
    decay_hours: float = rag_cfg.get("recency_decay_hours", 24.0)
    weights: dict = rag_cfg.get(
        "confidence_weights",
        {"coverage": 0.25, "similarity": 0.40, "recency": 0.20, "consistency": 0.15},
    )

    # --- 1. Coverage ---
    coverage = min(len(result.chunks) / top_k, 1.0)

    # --- 2. Similarity ---
    valid_sims = [s for s in result.similarities if s >= min_sim]
    similarity = statistics.mean(valid_sims) if valid_sims else 0.0

    # --- 3. Recency ---
    now = result.query_time
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    def _age_hours(chunk: KnowledgeChunk) -> float:
        ts = chunk.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (now - ts).total_seconds() / 3600.0)

    recency = statistics.mean(
        math.exp(-_age_hours(c) / decay_hours) for c in result.chunks
    )

    # --- 4. Consistency ---
    # Group values by sensor; a single-reading stream is perfectly consistent.
    by_sensor: dict[str, list[float]] = {}
    for chunk in result.chunks:
        by_sensor.setdefault(chunk.sensor_id, []).append(chunk.value)

    sensor_scores: list[float] = []
    for values in by_sensor.values():
        if len(values) < 2:
            sensor_scores.append(1.0)
            continue
        mean = statistics.mean(values)
        stdev = statistics.stdev(values)
        # Coefficient of variation, anchored to avoid div-by-zero near zero mean.
        cv = stdev / (abs(mean) + 1e-6)
        # exp(-cv): cv=0 → 1.0, cv=1 → 0.37, cv=2 → 0.14
        sensor_scores.append(math.exp(-cv))

    consistency = statistics.mean(sensor_scores) if sensor_scores else 1.0

    confidence = (
        weights.get("coverage", 0.25) * coverage
        + weights.get("similarity", 0.40) * similarity
        + weights.get("recency", 0.20) * recency
        + weights.get("consistency", 0.15) * consistency
    )

    return round(min(max(confidence, 0.0), 1.0), 3)
