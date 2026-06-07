import json

import pytest

from src.exploration import scheduler


def _hyp(hid, score, status="pending", experiment_type="observation"):
    return {
        "hypothesis_id": hid, "score": score, "status": status, "experiment_type": experiment_type,
        "statement": f"hypothesis {hid}", "required_sensor_data": ["temp_01"],
    }


def _write_queue(path, hypotheses):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(h) + "\n" for h in hypotheses))


def test_list_queue_returns_only_pending_sorted_by_score(tmp_path):
    path = tmp_path / "queue.jsonl"
    _write_queue(path, [_hyp("a", 1.0), _hyp("b", 3.0), _hyp("c", 2.0, status="done")])

    assert [h["hypothesis_id"] for h in scheduler.list_queue(path)] == ["b", "a"]


def test_run_next_returns_none_when_queue_empty(tmp_path):
    path = tmp_path / "queue.jsonl"
    _write_queue(path, [])
    assert scheduler.run_next(path=path) is None


def test_run_next_dispatches_top_hypothesis_records_outcome_and_marks_done(tmp_path):
    path = tmp_path / "queue.jsonl"
    _write_queue(path, [_hyp("a", 1.0), _hyp("b", 3.0)])

    calls = []

    def fake_runner(hypothesis):
        calls.append(hypothesis["hypothesis_id"])
        return {"outcome": "confirmed", "confidence_delta": 0.1, "evidence": "ev", "new_chunks": []}

    records = []

    def fake_record_outcome(hypothesis, result):
        record = {"hypothesis_id": hypothesis["hypothesis_id"], "outcome": result["outcome"], "labeled_example": {"input": "x"}}
        records.append(record)
        return record

    outcome = scheduler.run_next(path=path, runners={"observation": fake_runner}, record_outcome=fake_record_outcome)

    assert calls == ["b"]  # highest-scored hypothesis runs first
    assert outcome["hypothesis"]["hypothesis_id"] == "b"
    assert outcome["hypothesis"]["status"] == "done"
    assert outcome["result"]["outcome"] == "confirmed"
    assert outcome["outcome_record"] == records[0]

    queue = scheduler.load_queue(path)
    assert next(h for h in queue if h["hypothesis_id"] == "b")["status"] == "done"
    assert next(h for h in queue if h["hypothesis_id"] == "a")["status"] == "pending"


def test_run_next_raises_for_unknown_experiment_type(tmp_path):
    path = tmp_path / "queue.jsonl"
    _write_queue(path, [_hyp("a", 1.0, experiment_type="active_query")])

    with pytest.raises(ValueError, match="active_query"):
        scheduler.run_next(path=path)
