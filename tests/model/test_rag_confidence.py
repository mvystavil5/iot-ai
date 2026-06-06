from datetime import datetime, timedelta, timezone

import pytest

from src.ingestion.schema import KnowledgeChunk
from src.model.rag_confidence import RetrievalResult, compute_rag_confidence

DEFAULT_CFG = {
    "rag": {
        "top_k": 8,
        "min_similarity": 0.3,
        "recency_decay_hours": 24.0,
        "confidence_weights": {
            "coverage": 0.25,
            "similarity": 0.40,
            "recency": 0.20,
            "consistency": 0.15,
        },
    }
}


def _chunk(sensor_id: str, value: float, age_hours: float = 0.0) -> KnowledgeChunk:
    ts = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return KnowledgeChunk(
        chunk_id=f"{sensor_id}-{age_hours}-{value}",
        sensor_id=sensor_id,
        timestamp=ts,
        text=f"{sensor_id} reads {value}",
        value=value,
        unit="C",
        outlier=False,
    )


def _result(chunks: list[KnowledgeChunk], sims: list[float]) -> RetrievalResult:
    return RetrievalResult(
        chunks=chunks,
        similarities=sims,
        query_time=datetime.now(timezone.utc),
    )


# --- edge cases ---

def test_empty_retrieval_returns_zero():
    assert compute_rag_confidence(_result([], []), DEFAULT_CFG) == 0.0


def test_mismatched_lengths_raises():
    with pytest.raises(ValueError):
        RetrievalResult(chunks=[_chunk("t", 1.0)], similarities=[0.9, 0.8])


def test_confidence_clamped_to_0_1():
    chunks = [_chunk("temp_01", 22.0) for _ in range(20)]  # exceeds top_k
    score = compute_rag_confidence(_result(chunks, [1.0] * 20), DEFAULT_CFG)
    assert 0.0 <= score <= 1.0


# --- coverage ---

def test_partial_coverage_lower_than_full():
    full = [_chunk("t", 22.0) for _ in range(8)]
    partial = [_chunk("t", 22.0) for _ in range(2)]
    sims_full = [0.9] * 8
    sims_partial = [0.9] * 2
    assert (
        compute_rag_confidence(_result(full, sims_full), DEFAULT_CFG)
        > compute_rag_confidence(_result(partial, sims_partial), DEFAULT_CFG)
    )


# --- similarity ---

def test_high_similarity_beats_low():
    chunks = [_chunk("t", 22.0) for _ in range(4)]
    high = compute_rag_confidence(_result(chunks, [0.95] * 4), DEFAULT_CFG)
    low = compute_rag_confidence(_result(chunks, [0.35] * 4), DEFAULT_CFG)
    assert high > low


def test_sims_below_threshold_treated_as_zero_similarity():
    # Only one chunk, similarity below min_similarity — similarity component = 0
    score = compute_rag_confidence(_result([_chunk("t", 22.0)], [0.1]), DEFAULT_CFG)
    # similarity contributes 0; coverage + recency + consistency still give some score
    # but it should be well below 0.5
    assert score < 0.5


# --- recency ---

def test_fresh_chunks_score_higher_than_stale():
    fresh = compute_rag_confidence(_result([_chunk("t", 22.0, 0)], [0.9]), DEFAULT_CFG)
    stale = compute_rag_confidence(_result([_chunk("t", 22.0, 72)], [0.9]), DEFAULT_CFG)
    assert fresh > stale


def test_very_old_chunk_near_zero_recency():
    # 10× the decay constant — recency ≈ exp(-10) ≈ 0.00005
    # Use moderate similarity (0.5) so recency's collapse is visible in the total.
    # Expected: 0.25*0.125 + 0.40*0.5 + 0.20*~0 + 0.15*1.0 ≈ 0.38
    score = compute_rag_confidence(
        _result([_chunk("t", 22.0, age_hours=240)], [0.5]), DEFAULT_CFG
    )
    assert score < 0.5


# --- consistency ---

def test_consistent_values_score_higher_than_spread():
    consistent = [_chunk("t", 22.0 + i * 0.1, 0) for i in range(4)]
    spread = [_chunk("t", v, 0) for v in [10.0, 30.0, 50.0, 70.0]]
    sims = [0.9] * 4
    c_score = compute_rag_confidence(_result(consistent, sims), DEFAULT_CFG)
    s_score = compute_rag_confidence(_result(spread, sims), DEFAULT_CFG)
    assert c_score > s_score


def test_single_chunk_per_sensor_is_fully_consistent():
    # One reading per sensor — no variance possible, consistency = 1.0
    chunks = [_chunk("temp_01", 22.0, 0), _chunk("humid_01", 55.0, 0)]
    score = compute_rag_confidence(_result(chunks, [0.9, 0.9]), DEFAULT_CFG)
    # consistency = 1.0 contributes its full weight
    assert score > 0.5


# --- combined ---

def test_ideal_retrieval_scores_high():
    # 8 fresh, high-similarity, consistent chunks
    chunks = [_chunk("temp_01", 22.0 + i * 0.05, 0) for i in range(8)]
    score = compute_rag_confidence(_result(chunks, [0.95] * 8), DEFAULT_CFG)
    assert score > 0.8


def test_worst_case_sparse_old_dissimilar_inconsistent():
    chunks = [_chunk("t", v, age_hours=200) for v in [5.0, 80.0]]
    score = compute_rag_confidence(_result(chunks, [0.31, 0.31]), DEFAULT_CFG)
    assert score < 0.35
