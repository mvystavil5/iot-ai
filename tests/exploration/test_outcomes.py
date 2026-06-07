import json
from pathlib import Path

import pytest

from src.events import bus
from src.exploration import outcomes

HYPOTHESIS = {"hypothesis_id": "hyp_abc123", "statement": "temp correlates with humidity"}


@pytest.fixture(autouse=True)
def _clear_bus():
    bus.clear()
    yield
    bus.clear()


def _cfg(tmp_path):
    return {"training": {"labeled_examples_path": str(tmp_path / "labeled_examples.jsonl")}}


def test_to_labeled_example_shape():
    result = {"outcome": "confirmed", "evidence": "trends aligned"}
    assert outcomes.to_labeled_example(HYPOTHESIS, result) == {
        "input": HYPOTHESIS["statement"], "output": "trends aligned", "label": "confirmed",
    }


def test_record_outcome_forwards_labeled_example_when_not_inconclusive(tmp_path):
    received = []
    bus.subscribe("labeled_examples", received.append)
    outcomes_path = tmp_path / "outcomes.jsonl"
    cfg = _cfg(tmp_path)
    result = {"outcome": "confirmed", "confidence_delta": 0.15, "evidence": "trends aligned", "new_chunks": []}

    record = outcomes.record_outcome(HYPOTHESIS, result, outcomes_path=outcomes_path, cfg=cfg)

    expected_example = {"input": HYPOTHESIS["statement"], "output": "trends aligned", "label": "confirmed"}
    assert record["labeled_example"] == expected_example
    assert received == [expected_example]
    assert json.loads(Path(cfg["training"]["labeled_examples_path"]).read_text().strip()) == expected_example
    assert json.loads(outcomes_path.read_text().strip())["hypothesis_id"] == "hyp_abc123"


def test_record_outcome_does_not_forward_inconclusive_results(tmp_path):
    received = []
    bus.subscribe("labeled_examples", received.append)
    outcomes_path = tmp_path / "outcomes.jsonl"
    labeled_path = tmp_path / "labeled.jsonl"
    result = {"outcome": "inconclusive", "confidence_delta": 0.0, "evidence": "no clear trend", "new_chunks": []}

    record = outcomes.record_outcome(
        HYPOTHESIS, result, outcomes_path=outcomes_path, labeled_examples_path=labeled_path, cfg=_cfg(tmp_path),
    )

    assert record["labeled_example"] is None
    assert received == []
    assert not labeled_path.exists()
    assert json.loads(outcomes_path.read_text().strip())["outcome"] == "inconclusive"
