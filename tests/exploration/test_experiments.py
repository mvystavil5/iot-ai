from types import SimpleNamespace

from src.exploration import experiments

HYPOTHESIS = {"required_sensor_data": ["temp_01", "humid_01"]}
ALERT_REGISTRY = {"sensors": [{"id": "co2_01", "expected_range": [300, 5000]}]}
SIM_REGISTRY = {"sensors": [
    {"id": "temp_01", "expected_range": [-10, 50]},
    {"id": "humid_01", "expected_range": [0, 100]},
]}


def _readings(chronological_values):
    """TimeSeriesStore.query returns most-recent-first — reverse to match."""
    return [SimpleNamespace(value=v) for v in reversed(chronological_values)]


class _FakeStore:
    def __init__(self, series):
        self._series = series  # {sensor_id: [chronological values]}

    def query(self, sensor_id, limit=100):
        return _readings(self._series.get(sensor_id, []))[:limit]


def _const_walk(delta):
    def walk(value, lo, hi):
        return min(hi, max(lo, value + delta))
    return walk


class _FixedRng:
    def __init__(self, value):
        self._value = value

    def uniform(self, lo, hi):
        return self._value


# -- observation ------------------------------------------------------------

def test_observation_confirms_when_trends_align():
    store = _FakeStore({"temp_01": [20, 21, 22, 23], "humid_01": [40, 41, 42, 43]})
    result = experiments.run_observation_experiment(HYPOTHESIS, store=store)
    assert result["outcome"] == "confirmed"
    assert result["confidence_delta"] > 0


def test_observation_refutes_when_trends_diverge():
    store = _FakeStore({"temp_01": [20, 21, 22, 23], "humid_01": [50, 48, 46, 44]})
    result = experiments.run_observation_experiment(HYPOTHESIS, store=store)
    assert result["outcome"] == "refuted"
    assert result["confidence_delta"] < 0


def test_observation_inconclusive_with_insufficient_history():
    store = _FakeStore({"temp_01": [22], "humid_01": [40, 41]})
    result = experiments.run_observation_experiment(HYPOTHESIS, store=store)
    assert result["outcome"] == "inconclusive"
    assert result["confidence_delta"] == 0.0


def test_observation_inconclusive_when_one_trend_is_flat():
    store = _FakeStore({"temp_01": [20, 20, 20, 20], "humid_01": [40, 41, 42, 43]})
    result = experiments.run_observation_experiment(HYPOTHESIS, store=store)
    assert result["outcome"] == "inconclusive"


# -- alert -------------------------------------------------------------------

def test_alert_confirms_when_value_outside_expected_range():
    store = _FakeStore({"co2_01": [6000]})
    result = experiments.run_alert_experiment({"required_sensor_data": ["co2_01"]}, store=store, registry=ALERT_REGISTRY)
    assert result["outcome"] == "confirmed"
    assert result["confidence_delta"] > 0


def test_alert_refutes_when_value_within_expected_range():
    store = _FakeStore({"co2_01": [800]})
    result = experiments.run_alert_experiment({"required_sensor_data": ["co2_01"]}, store=store, registry=ALERT_REGISTRY)
    assert result["outcome"] == "refuted"
    assert result["confidence_delta"] < 0


def test_alert_inconclusive_when_no_data():
    result = experiments.run_alert_experiment({"required_sensor_data": ["co2_01"]}, store=_FakeStore({}), registry=ALERT_REGISTRY)
    assert result["outcome"] == "inconclusive"


# -- simulation ---------------------------------------------------------------

def test_simulation_confirms_when_simulated_trends_align():
    result = experiments.run_simulation_experiment(
        {"required_sensor_data": ["temp_01", "humid_01"]},
        registry=SIM_REGISTRY, walk_fn=_const_walk(1.0), rng=_FixedRng(10.0), steps=5,
    )
    assert result["outcome"] == "confirmed"


def test_simulation_inconclusive_when_sensor_missing_from_registry():
    result = experiments.run_simulation_experiment(
        {"required_sensor_data": ["temp_01", "missing_01"]},
        registry=SIM_REGISTRY, walk_fn=_const_walk(1.0), rng=_FixedRng(10.0), steps=5,
    )
    assert result["outcome"] == "inconclusive"
    assert "missing_01" in result["evidence"]
