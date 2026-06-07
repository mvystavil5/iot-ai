import pytest

from src.events import bus
from src.model import beliefs


@pytest.fixture(autouse=True)
def _clear_bus():
    bus.clear()
    yield
    bus.clear()


def _store(tmp_path, threshold: float = 0.7) -> beliefs.BeliefStore:
    return beliefs.BeliefStore(path=tmp_path / "beliefs.jsonl", cfg={"beliefs": {"invalidation_threshold": threshold}})


def test_query_hash_is_case_and_whitespace_insensitive():
    assert beliefs.query_hash("  What's the temp?  ") == beliefs.query_hash("what's the temp?")


def test_active_belief_returns_none_when_never_asked(tmp_path):
    assert _store(tmp_path).active_belief("never asked") is None


def test_record_first_belief_is_active_and_not_invalidated(tmp_path):
    store = _store(tmp_path)
    belief = store.record("what's the temp?", "22C", 0.8, ["c1"])

    assert belief["invalidated_at"] is None
    assert belief["query_hash"] == beliefs.query_hash("what's the temp?")
    assert store.active_belief("what's the temp?")["answer"] == "22C"


def test_contradiction_above_threshold_invalidates_previous_and_emits(tmp_path):
    store = _store(tmp_path, threshold=0.7)
    received = []
    bus.subscribe("belief_invalidated", received.append)

    store.record("q", "22C", 0.8, ["c1"])
    store.record("q", "30C", 0.9, ["c2"])

    assert store.active_belief("q")["answer"] == "30C"
    invalidated = next(b for b in store.all() if b["answer"] == "22C")
    assert invalidated["invalidated_at"] is not None
    assert received and received[0]["answer"] == "22C"


def test_contradiction_below_threshold_does_not_invalidate(tmp_path):
    store = _store(tmp_path, threshold=0.7)
    store.record("q", "22C", 0.8, ["c1"])
    store.record("q", "30C", 0.5, ["c2"])  # below threshold — recorded, but doesn't invalidate

    assert all(b["invalidated_at"] is None for b in store.all())
    assert len(store.all()) == 2


def test_same_answer_does_not_invalidate(tmp_path):
    store = _store(tmp_path, threshold=0.7)
    store.record("q", "22C", 0.8, ["c1"])
    store.record("q", "22C", 0.95, ["c2"])

    assert all(b["invalidated_at"] is None for b in store.all())
    assert len(store.all()) == 2


def test_beliefs_persist_across_instances(tmp_path):
    path = tmp_path / "beliefs.jsonl"
    cfg = {"beliefs": {"invalidation_threshold": 0.7}}
    beliefs.BeliefStore(path=path, cfg=cfg).record("q", "22C", 0.8, ["c1"])

    assert len(beliefs.BeliefStore(path=path, cfg=cfg).all()) == 1
