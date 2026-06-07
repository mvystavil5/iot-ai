from datetime import datetime, timezone

import pytest

from src.events import bus
from src.ingestion.schema import KnowledgeChunk
from src.model import reasoner as reasoner_module
from src.model.rag_confidence import RetrievalResult
from src.model.reasoner import Reasoner

CFG = {"rag": {
    "top_k": 8, "min_similarity": 0.3, "recency_decay_hours": 24.0,
    "confidence_weights": {"coverage": 0.25, "similarity": 0.4, "recency": 0.2, "consistency": 0.15},
}}


def _chunk(sensor_id: str = "temp_01", chunk_id: str = "c1") -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id, sensor_id=sensor_id,
        timestamp=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
        text=f"Sensor {sensor_id} reported 22C", value=22.0, unit="C", outlier=False,
    )


class _FakeRetriever:
    def __init__(self, result: RetrievalResult):
        self.result = result
        self.queries: list[str] = []

    def retrieve(self, query, **kwargs):
        self.queries.append(query)
        return self.result


class _FakeLLM:
    def __init__(self, text: str = "the answer", error: bool = False):
        self.text = text
        self.error = error
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.error:
            raise RuntimeError("boom")
        return self.text


class _FakeBeliefs:
    def __init__(self):
        self.records: list[dict] = []

    def record(self, query, answer, confidence, supporting_chunk_ids):
        record = {
            "query": query, "answer": answer, "confidence": confidence,
            "supporting_chunk_ids": list(supporting_chunk_ids), "invalidated_at": None,
        }
        self.records.append(record)
        return record


@pytest.fixture(autouse=True)
def _clear_bus():
    bus.clear()
    yield
    bus.clear()


def _make_reasoner(monkeypatch, *, result, confidence, llm=None, beliefs=None):
    monkeypatch.setattr(reasoner_module, "compute_rag_confidence", lambda r, cfg: confidence)
    return Reasoner(retriever=_FakeRetriever(result), llm=llm or _FakeLLM(), beliefs=beliefs or _FakeBeliefs(), cfg=CFG)


def test_answer_runs_full_chain_and_returns_expected_shape(monkeypatch):
    result = RetrievalResult(chunks=[_chunk("temp_01", "c1"), _chunk("co2_01", "c2")], similarities=[0.9, 0.8])
    llm = _FakeLLM("22 degrees, confidence 0.9")
    beliefs = _FakeBeliefs()
    reasoner = _make_reasoner(monkeypatch, result=result, confidence=0.85, llm=llm, beliefs=beliefs)

    out = reasoner.answer("what's the temperature?")

    assert out["answer"] == "22 degrees, confidence 0.9"
    assert out["confidence"] == 0.85
    assert out["supporting_sensors"] == ["co2_01", "temp_01"]
    assert out["supporting_chunk_ids"] == ["c1", "c2"]
    assert out["caveats"] == []
    assert beliefs.records[0]["answer"] == out["answer"]
    assert "Sensor temp_01 reported 22C" in llm.prompts[0]
    assert "what's the temperature?" in llm.prompts[0]


def test_answer_publishes_low_confidence_event(monkeypatch):
    result = RetrievalResult(chunks=[_chunk()], similarities=[0.5])
    received = []
    bus.subscribe("low_confidence", received.append)
    reasoner = _make_reasoner(monkeypatch, result=result, confidence=0.2)

    out = reasoner.answer("q")

    assert out["caveats"] == ["Low confidence — limited or stale supporting sensor data."]
    assert len(received) == 1
    assert received[0] == {"query": "q", "confidence": 0.2, "belief": out["belief"]}


def test_answer_does_not_publish_low_confidence_when_plausible(monkeypatch):
    result = RetrievalResult(chunks=[_chunk()], similarities=[0.9])
    received = []
    bus.subscribe("low_confidence", received.append)
    reasoner = _make_reasoner(monkeypatch, result=result, confidence=0.6)

    out = reasoner.answer("q")

    assert out["caveats"] == ["Moderate confidence — answer is plausible but not strongly supported."]
    assert received == []


def test_answer_falls_back_to_context_when_llm_fails(monkeypatch):
    result = RetrievalResult(chunks=[_chunk()], similarities=[0.9])
    llm = _FakeLLM(error=True)
    beliefs = _FakeBeliefs()
    reasoner = _make_reasoner(monkeypatch, result=result, confidence=0.9, llm=llm, beliefs=beliefs)

    out = reasoner.answer("q")

    assert "Sensor temp_01 reported 22C" in out["answer"]
    assert beliefs.records[0]["answer"] == out["answer"]


def test_answer_with_no_chunks_has_no_data_caveat(monkeypatch):
    result = RetrievalResult(chunks=[], similarities=[])
    reasoner = _make_reasoner(monkeypatch, result=result, confidence=0.0)

    out = reasoner.answer("q")

    assert out["supporting_sensors"] == []
    assert out["caveats"] == ["No matching sensor data was retrieved for this query."]
