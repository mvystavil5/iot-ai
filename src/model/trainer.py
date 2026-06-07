"""
LoRA fine-tuning orchestration — runs on the offline/cloud training host,
never on the board (see docs/architecture.md § Training & adapter sync; the
board only accumulates labeled examples and pulls back the resulting adapter
via src.model.adapter_sync).

Pipeline (per .claude/agents/trainer.md):
  1. load labeled examples from data/labeled_examples.jsonl
  2. format into instruction-tuning pairs
  3. split 80/10/10 train/val/test, stratified by sensor type
  4. LoRA fine-tune via PEFT (config/model.yaml: lora / training)
  5. evaluate on the held-out test set
  6. promote to checkpoints/current/ if it beats the current best score
  7. record the run in data/training_runs.jsonl and data/model_registry.json

  python -m src.model.trainer --check-readiness
  python -m src.model.trainer --run --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.config import load_model_config

log = logging.getLogger(__name__)

DEFAULT_REGISTRY_PATH = Path("./data/model_registry.json")
DEFAULT_RUNS_LOG = Path("./data/training_runs.jsonl")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _load_registry(path: Path | None = None) -> dict:
    path = path or DEFAULT_REGISTRY_PATH
    if not path.exists():
        return {"current_version": None, "checkpoints": []}
    return json.loads(path.read_text())


def _save_registry(registry: dict, path: Path | None = None) -> None:
    path = path or DEFAULT_REGISTRY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2))


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

def check_readiness(cfg: dict) -> dict:
    """Summarize whether enough labeled data has accumulated to warrant
    another training run — what `.claude/skills/train-checkpoint.md` reports
    before confirming a run with the user."""
    training_cfg = cfg["training"]
    examples = _read_jsonl(Path(training_cfg["labeled_examples_path"]))
    registry = _load_registry()
    runs = _read_jsonl(DEFAULT_RUNS_LOG)

    current = next(
        (c for c in registry["checkpoints"] if c["version"] == registry.get("current_version")),
        None,
    )
    return {
        "labeled_examples": len(examples),
        "trigger_threshold": training_cfg["trigger_threshold"],
        "ready": len(examples) >= training_cfg["trigger_threshold"],
        "current_version": registry.get("current_version"),
        "current_score": current["eval_score"] if current else None,
        "last_run_at": runs[-1]["finished_at"] if runs else None,
    }


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def _format_pairs(examples: list[dict]) -> list[dict]:
    """Map labeled examples into instruction-tuning pairs:
    {"instruction", "input": "<sensor context>", "output": "<answer w/ confidence>"}
    per the format documented in .claude/agents/trainer.md."""
    return [
        {
            "instruction": ex.get("instruction", ex.get("query", "")),
            "input": ex.get("context", ex.get("sensor_context", "")),
            "output": ex.get("output", ex.get("answer", "")),
            "sensor_type": ex.get("sensor_type", "unknown"),
        }
        for ex in examples
    ]


def _split(pairs: list[dict], seed: int = 0) -> tuple[list[dict], list[dict], list[dict]]:
    """80/10/10 train/val/test split, stratified by sensor_type so rare
    sensor types still appear in every split."""
    by_type: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        by_type[p["sensor_type"]].append(p)

    rng = random.Random(seed)
    train: list[dict] = []
    val: list[dict] = []
    test: list[dict] = []
    for group in by_type.values():
        rng.shuffle(group)
        n_train = round(len(group) * 0.8)
        n_val = round(len(group) * 0.1)
        train.extend(group[:n_train])
        val.extend(group[n_train:n_train + n_val])
        test.extend(group[n_train + n_val:])
    return train, val, test


def _to_dataset(pairs: list[dict], tokenizer):
    from datasets import Dataset

    def render(p: dict) -> str:
        return f"### Instruction:\n{p['instruction']}\n\n### Context:\n{p['input']}\n\n### Response:\n{p['output']}"

    texts = [render(p) for p in pairs]
    return Dataset.from_dict({"text": texts}).map(
        lambda batch: tokenizer(batch["text"], truncation=True, padding="max_length", max_length=512),
        batched=True,
    )


def _fine_tune(train: list[dict], val: list[dict], cfg: dict, output_dir: Path) -> None:
    """Run the LoRA fine-tune via PEFT + the HuggingFace Trainer, wired to
    the lora/training params in config/model.yaml.

    torch/transformers/peft/datasets are imported lazily here (rather than
    at module level) so this module — and --check-readiness in particular —
    stays importable and unit-testable on hosts without a full GPU training
    environment installed, matching the rest of the still-scaffolded
    src/model/ layer (see TODO.md)."""
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments

    lora_cfg = cfg["lora"]
    training_cfg = cfg["training"]
    base_model_name = cfg["llm"]["model"]

    tokenizer = AutoTokenizer.from_pretrained(base_model_name)
    model = get_peft_model(
        AutoModelForCausalLM.from_pretrained(base_model_name),
        LoraConfig(
            r=lora_cfg["rank"],
            lora_alpha=lora_cfg["alpha"],
            lora_dropout=lora_cfg["dropout"],
            target_modules=lora_cfg["target_modules"],
            task_type=TaskType[lora_cfg["task_type"]],
        ),
    )

    args = TrainingArguments(
        output_dir=str(output_dir),
        per_device_train_batch_size=training_cfg["batch_size"],
        gradient_accumulation_steps=training_cfg["gradient_accumulation"],
        learning_rate=training_cfg["learning_rate"],
        warmup_steps=training_cfg["warmup_steps"],
        max_steps=training_cfg["max_steps"],
        logging_steps=10,
        save_strategy="no",
        report_to=[],
    )
    Trainer(
        model=model,
        args=args,
        train_dataset=_to_dataset(train, tokenizer),
        eval_dataset=_to_dataset(val, tokenizer),
    ).train()
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)


def _evaluate(adapter_dir: Path, test: list[dict], cfg: dict) -> float:
    """Score the fine-tuned adapter on the held-out test set: answer accuracy
    weighted by confidence calibration, in [0, 1] — the same "eval_score"
    tracked in data/model_registry.json.

    Needs a loaded base+adapter model and a scoring harness to run against
    `test` pairs — left as the integration point for src.model.reasoner,
    which doesn't exist yet (see TODO.md). Wire it up there once it does."""
    raise NotImplementedError(
        "Evaluation requires src.model.reasoner (not yet implemented) to "
        "score the fine-tuned adapter against held-out test pairs."
    )


# ---------------------------------------------------------------------------
# Promotion & bookkeeping
# ---------------------------------------------------------------------------

def _promote(
    version: str,
    adapter_dir: Path,
    eval_score: float,
    base_model: str,
    n_examples: int,
    registry: dict,
    cfg: dict,
) -> tuple[dict, bool]:
    """Add a checkpoint entry to the registry, promoting it to `current`
    only if it beats the best eval_score seen so far — "never overwrite
    checkpoints/current/ without passing eval" per .claude/agents/trainer.md."""
    best_score = max((c["eval_score"] for c in registry["checkpoints"]), default=-1.0)
    promoted = eval_score > best_score

    registry["checkpoints"].append({
        "version": version,
        "adapter_path": str(adapter_dir),
        "base_model": base_model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "eval_score": eval_score,
        "promoted": promoted,
        "training_examples": n_examples,
    })
    if promoted:
        registry["current_version"] = version

    keep_last_n = cfg["training"]["keep_last_n"]
    registry["checkpoints"] = registry["checkpoints"][-keep_last_n:]
    return registry, promoted


# ---------------------------------------------------------------------------
# Top-level run
# ---------------------------------------------------------------------------

def run(cfg: dict, verbose: bool = False) -> dict:
    """Execute the full training pipeline; returns the run record appended
    to data/training_runs.jsonl."""
    training_cfg = cfg["training"]
    examples = _read_jsonl(Path(training_cfg["labeled_examples_path"]))
    if len(examples) < training_cfg["trigger_threshold"]:
        raise RuntimeError(
            f"Only {len(examples)} labeled examples available "
            f"(need {training_cfg['trigger_threshold']}) — run --check-readiness for details"
        )

    train, val, test = _split(_format_pairs(examples))
    if verbose:
        log.info("Split: train=%d val=%d test=%d", len(train), len(val), len(test))

    version = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = Path(training_cfg["checkpoint_dir"]) / version
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc).isoformat()
    _fine_tune(train, val, cfg, output_dir)
    eval_score = _evaluate(output_dir, test, cfg)
    finished_at = datetime.now(timezone.utc).isoformat()

    registry, promoted = _promote(
        version, output_dir, eval_score, cfg["llm"]["model"], len(examples), _load_registry(), cfg,
    )
    _save_registry(registry)

    record = {
        "version": version,
        "started_at": started_at,
        "finished_at": finished_at,
        "n_examples": len(examples),
        "eval_score": eval_score,
        "promoted": promoted,
    }
    _append_jsonl(DEFAULT_RUNS_LOG, record)
    log.info("Run %s finished — score=%.3f promoted=%s", version, eval_score, promoted)
    return record


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LoRA fine-tuning orchestration (training host only)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check-readiness", action="store_true", help="Report training readiness and exit")
    g.add_argument("--run", action="store_true", help="Run the full fine-tuning pipeline")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    config = load_model_config()
    if args.check_readiness:
        print(json.dumps(check_readiness(config), indent=2))
    else:
        run(config, verbose=args.verbose)
