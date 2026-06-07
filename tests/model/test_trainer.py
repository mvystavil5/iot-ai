import json
from pathlib import Path

import pytest

from src.model import trainer

CFG = {
    "training": {
        "labeled_examples_path": "",  # filled in per-test via _cfg
        "trigger_threshold": 3,
        "checkpoint_dir": "",
        "keep_last_n": 2,
    },
    "lora": {"rank": 8, "alpha": 16, "dropout": 0.05, "target_modules": ["q_proj"], "task_type": "CAUSAL_LM"},
    "llm": {"model": "smollm2:135m"},
}


def _cfg(tmp_path: Path) -> dict:
    cfg = json.loads(json.dumps(CFG))
    cfg["training"]["labeled_examples_path"] = str(tmp_path / "labeled_examples.jsonl")
    cfg["training"]["checkpoint_dir"] = str(tmp_path / "checkpoints")
    return cfg


@pytest.fixture(autouse=True)
def _redirect_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(trainer, "DEFAULT_REGISTRY_PATH", tmp_path / "model_registry.json")
    monkeypatch.setattr(trainer, "DEFAULT_RUNS_LOG", tmp_path / "training_runs.jsonl")


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


# --- check_readiness ---

def test_check_readiness_not_ready_below_threshold(tmp_path):
    cfg = _cfg(tmp_path)
    _write_jsonl(Path(cfg["training"]["labeled_examples_path"]), [{"query": "q1"}, {"query": "q2"}])

    report = trainer.check_readiness(cfg)
    assert report["labeled_examples"] == 2
    assert report["trigger_threshold"] == 3
    assert report["ready"] is False
    assert report["current_version"] is None
    assert report["current_score"] is None
    assert report["last_run_at"] is None


def test_check_readiness_ready_and_reports_current_checkpoint(tmp_path):
    cfg = _cfg(tmp_path)
    _write_jsonl(
        Path(cfg["training"]["labeled_examples_path"]),
        [{"query": f"q{i}"} for i in range(5)],
    )
    trainer.DEFAULT_REGISTRY_PATH.write_text(json.dumps({
        "current_version": "20260101_000000",
        "checkpoints": [
            {"version": "20260101_000000", "eval_score": 0.8, "promoted": True},
        ],
    }))
    trainer._append_jsonl(trainer.DEFAULT_RUNS_LOG, {"version": "20260101_000000", "finished_at": "2026-01-01T00:05:00Z"})

    report = trainer.check_readiness(cfg)
    assert report["ready"] is True
    assert report["current_version"] == "20260101_000000"
    assert report["current_score"] == 0.8
    assert report["last_run_at"] == "2026-01-01T00:05:00Z"


# --- _format_pairs / _split ---

def test_format_pairs_maps_known_fields():
    pairs = trainer._format_pairs([
        {"query": "what is the temp?", "context": "temp_01=22C", "answer": "22C (0.9)", "sensor_type": "temperature"},
    ])
    assert pairs == [{
        "instruction": "what is the temp?",
        "input": "temp_01=22C",
        "output": "22C (0.9)",
        "sensor_type": "temperature",
    }]


def test_format_pairs_defaults_unknown_sensor_type():
    assert trainer._format_pairs([{"query": "q", "context": "c", "answer": "a"}])[0]["sensor_type"] == "unknown"


def test_split_is_80_10_10_and_covers_all_pairs():
    pairs = [{"sensor_type": "temperature", "instruction": str(i)} for i in range(20)]
    train, val, test = trainer._split(pairs)

    assert len(train) == 16
    assert len(val) == 2
    assert len(test) == 2
    assert sorted(p["instruction"] for p in train + val + test) == sorted(p["instruction"] for p in pairs)


def test_split_stratifies_by_sensor_type():
    pairs = (
        [{"sensor_type": "temperature", "instruction": f"t{i}"} for i in range(10)]
        + [{"sensor_type": "motion", "instruction": f"m{i}"} for i in range(10)]
    )
    train, val, test = trainer._split(pairs)

    for split in (train, val, test):
        types = {p["sensor_type"] for p in split}
        assert types == {"temperature", "motion"}, "every split should contain both sensor types"


# --- _promote ---

def test_promote_first_checkpoint_is_always_promoted(tmp_path):
    cfg = _cfg(tmp_path)
    registry, promoted = trainer._promote(
        "v1", Path("checkpoints/v1"), eval_score=0.5, base_model="smollm2:135m",
        n_examples=10, registry={"current_version": None, "checkpoints": []}, cfg=cfg,
    )
    assert promoted is True
    assert registry["current_version"] == "v1"
    assert registry["checkpoints"][0]["eval_score"] == 0.5


def test_promote_only_when_better_than_best(tmp_path):
    cfg = _cfg(tmp_path)
    registry = {
        "current_version": "v1",
        "checkpoints": [{"version": "v1", "eval_score": 0.8, "promoted": True}],
    }

    registry, promoted = trainer._promote(
        "v2", Path("checkpoints/v2"), eval_score=0.6, base_model="smollm2:135m",
        n_examples=10, registry=registry, cfg=cfg,
    )
    assert promoted is False
    assert registry["current_version"] == "v1"  # unchanged — v2 didn't beat the best


def test_promote_prunes_to_keep_last_n(tmp_path):
    cfg = _cfg(tmp_path)  # keep_last_n = 2
    registry = {"current_version": None, "checkpoints": []}
    for i, score in enumerate([0.5, 0.6, 0.7]):
        registry, _ = trainer._promote(
            f"v{i}", Path(f"checkpoints/v{i}"), eval_score=score, base_model="smollm2:135m",
            n_examples=10, registry=registry, cfg=cfg,
        )

    assert [c["version"] for c in registry["checkpoints"]] == ["v1", "v2"]


# --- run() guards ---

def test_run_raises_when_not_enough_examples(tmp_path):
    cfg = _cfg(tmp_path)
    _write_jsonl(Path(cfg["training"]["labeled_examples_path"]), [{"query": "q1"}])

    with pytest.raises(RuntimeError, match="Only 1 labeled example"):
        trainer.run(cfg)
