from src.exploration import hypothesis_generator as hg

REGISTRY = {"sensors": [
    {"id": "temp_01", "name": "Living room temperature", "type": "temperature", "location": "living_room", "expected_range": [-10, 50]},
    {"id": "humid_01", "name": "Living room humidity", "type": "humidity", "location": "living_room", "expected_range": [0, 100]},
    {"id": "co2_01", "name": "Living room CO2", "type": "co2", "location": "living_room", "expected_range": [300, 5000]},
    {"id": "motion_01", "name": "Living room motion", "type": "motion", "location": "living_room", "expected_range": [0, 1]},
]}


class _FakeStore:
    def __init__(self, counts):
        self._counts = counts

    def count(self, sensor_id):
        return self._counts.get(sensor_id, 0)


def test_trigger_beliefs_selects_low_confidence_and_invalidated():
    beliefs = [
        {"confidence": 0.3, "invalidated_at": None},
        {"confidence": 0.9, "invalidated_at": "2026-01-01T00:00:00+00:00"},
        {"confidence": 0.8, "invalidated_at": None},
    ]
    assert len(hg._trigger_beliefs(beliefs)) == 2


def test_generate_returns_empty_when_nothing_uncertain():
    confident_beliefs = [{"confidence": 0.9, "invalidated_at": None}]
    assert hg.generate(beliefs=confident_beliefs, registry=REGISTRY, store=_FakeStore({})) == []


def test_generate_ranks_by_score_and_marks_pending():
    beliefs = [{"confidence": 0.2, "invalidated_at": None}]
    store = _FakeStore({"temp_01": 20, "humid_01": 20, "motion_01": 0, "co2_01": 5})

    hypotheses = hg.generate(beliefs=beliefs, registry=REGISTRY, store=store)

    assert hypotheses
    assert all(h["status"] == "pending" for h in hypotheses)
    assert all(h["experiment_type"] == "observation" for h in hypotheses)
    scores = [h["score"] for h in hypotheses]
    assert scores == sorted(scores, reverse=True)
    # temp<->humidity has the most history on both sides -> highest feasibility -> top score
    assert set(hypotheses[0]["required_sensor_data"]) == {"temp_01", "humid_01"}


def test_feasibility_reflects_available_history():
    sensor_a, sensor_b = REGISTRY["sensors"][0], REGISTRY["sensors"][1]
    assert hg._feasibility(sensor_a, sensor_b, _FakeStore({})) == 0.1
    assert hg._feasibility(sensor_a, sensor_b, _FakeStore({"temp_01": 3, "humid_01": 3})) == 0.5
    assert hg._feasibility(sensor_a, sensor_b, _FakeStore({"temp_01": 20, "humid_01": 20})) == 1.0


def test_run_appends_top_n_to_queue_and_round_trips(tmp_path):
    path = tmp_path / "hypothesis_queue.jsonl"
    beliefs = [{"confidence": 0.1, "invalidated_at": None}]
    store = _FakeStore({"temp_01": 20, "humid_01": 20, "motion_01": 20, "co2_01": 20})

    queued = hg.run(top_n=2, path=path, beliefs=beliefs, registry=REGISTRY, store=store)

    assert len(queued) == 2
    assert hg.load_queue(path) == queued


def test_run_does_not_touch_queue_when_nothing_generated(tmp_path):
    path = tmp_path / "hypothesis_queue.jsonl"
    confident_beliefs = [{"confidence": 0.9, "invalidated_at": None}]

    queued = hg.run(top_n=1, path=path, beliefs=confident_beliefs, registry=REGISTRY, store=_FakeStore({}))

    assert queued == []
    assert not path.exists()
