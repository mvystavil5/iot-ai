"""
Adapter sync: keeps the board's LoRA adapter in step with an offline/cloud
training host, without requiring inbound connectivity to the board (it may
sit behind NAT — the same constraint that shapes wifi_bridge.py).

Two outbound-only HTTP flows, configured under config/model.yaml: training.sync:

  push — batches new lines appended to data/labeled_examples.jsonl and POSTs
         them to {host}/training/examples once push_batch_size accumulate

  pull — polls GET {host}/training/registry; when its current_version differs
         from ours, downloads the adapter tarball from
         GET {host}/training/adapter/{version}, extracts it, and atomically
         swaps it into checkpoints/current/

Run on the Arduino UNO Q Linux side:
  python -m src.model.adapter_sync
  python -m src.model.adapter_sync --once --debug
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import tarfile
import time
from io import BytesIO
from pathlib import Path

import httpx

from src.config import load_model_config

log = logging.getLogger(__name__)

DEFAULT_SYNC_STATE_PATH = Path("./data/sync_state.json")
DEFAULT_REGISTRY_PATH = Path("./data/model_registry.json")
HTTP_TIMEOUT = 60.0


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Push: labeled examples → training host
# ---------------------------------------------------------------------------

def _pending_examples(examples_path: Path, last_pushed_line: int) -> list[str]:
    """Lines appended to the labeled-examples JSONL since the last push."""
    if not examples_path.exists():
        return []
    return examples_path.read_text().splitlines()[last_pushed_line:]


def _push_examples(client: httpx.Client, host: str, lines: list[str]) -> None:
    r = client.post(f"{host}/training/examples", json={"examples": lines}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()


def push_if_ready(cfg: dict, client: httpx.Client) -> int:
    """Push a batch of new labeled examples once enough have accumulated.
    Returns the number of lines pushed (0 if below the batch threshold)."""
    sync_cfg = cfg["training"]["sync"]
    examples_path = Path(cfg["training"]["labeled_examples_path"])
    state = _load_json(DEFAULT_SYNC_STATE_PATH, {"last_pushed_line": 0})
    pending = _pending_examples(examples_path, state["last_pushed_line"])

    batch_size = sync_cfg["push_batch_size"]
    if len(pending) < batch_size:
        log.debug("Push: %d pending example(s), waiting for %d", len(pending), batch_size)
        return 0

    _push_examples(client, sync_cfg["host"], pending)
    state["last_pushed_line"] += len(pending)
    _save_json(DEFAULT_SYNC_STATE_PATH, state)
    log.info("Pushed %d labeled example(s) to %s", len(pending), sync_cfg["host"])
    return len(pending)


# ---------------------------------------------------------------------------
# Pull: LoRA adapter ← training host
# ---------------------------------------------------------------------------

def _fetch_registry(client: httpx.Client, host: str) -> dict:
    r = client.get(f"{host}/training/registry", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _fetch_adapter(client: httpx.Client, host: str, version: str) -> bytes:
    r = client.get(f"{host}/training/adapter/{version}", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.content


def _needs_pull(local_version: str | None, remote_version: str | None) -> bool:
    return remote_version is not None and remote_version != local_version


def _swap_current(checkpoint_dir: Path, version: str, tar_bytes: bytes) -> None:
    """Extract a downloaded adapter tarball to checkpoint_dir/{version}, then
    atomically replace checkpoint_dir/current with it.

    Stages into a uniquely-named directory and renames into place so a crash
    mid-extraction can never leave checkpoints/current/ partially written —
    the Reasoner must always find either the old or the new adapter there."""
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    versioned_dir = checkpoint_dir / version
    current_dir = checkpoint_dir / "current"
    previous_dir = checkpoint_dir / ".previous"
    staging_dir = checkpoint_dir / f".staging-{version}"

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir()
    with tarfile.open(fileobj=BytesIO(tar_bytes)) as tar:
        tar.extractall(staging_dir)

    if versioned_dir.exists():
        shutil.rmtree(versioned_dir)
    staging_dir.rename(versioned_dir)

    if previous_dir.exists():
        shutil.rmtree(previous_dir)
    if current_dir.exists():
        current_dir.rename(previous_dir)
    shutil.copytree(versioned_dir, current_dir)


def pull_if_newer(cfg: dict, client: httpx.Client) -> str | None:
    """Check the training host's registry and pull a newer adapter if found.
    Returns the new version string, or None if already up to date."""
    sync_cfg = cfg["training"]["sync"]
    checkpoint_dir = Path(cfg["training"]["checkpoint_dir"])
    registry = _load_json(DEFAULT_REGISTRY_PATH, {"current_version": None, "checkpoints": []})

    remote_registry = _fetch_registry(client, sync_cfg["host"])
    remote_version = remote_registry.get("current_version")
    if not _needs_pull(registry.get("current_version"), remote_version):
        log.debug("Pull: up to date (version=%s)", registry.get("current_version"))
        return None

    log.info("New adapter version available: %s (have %s)", remote_version, registry.get("current_version"))
    tar_bytes = _fetch_adapter(client, sync_cfg["host"], remote_version)
    _swap_current(checkpoint_dir, remote_version, tar_bytes)

    registry["current_version"] = remote_version
    registry["checkpoints"] = remote_registry.get("checkpoints", registry["checkpoints"])
    _save_json(DEFAULT_REGISTRY_PATH, registry)
    log.info("Adapter %s pulled and promoted to checkpoints/current/", remote_version)
    return remote_version


# ---------------------------------------------------------------------------
# Run loop
# ---------------------------------------------------------------------------

def run_once(cfg: dict) -> None:
    try:
        with httpx.Client() as client:
            push_if_ready(cfg, client)
            pull_if_newer(cfg, client)
    except httpx.HTTPError as exc:
        log.warning("Sync round failed (%s) — will retry next interval", exc)


def run(cfg: dict) -> None:
    sync_cfg = cfg["training"]["sync"]
    interval = sync_cfg["poll_interval_s"]
    log.info(
        "Adapter sync started — host=%s push_batch=%d poll_interval=%ds",
        sync_cfg["host"], sync_cfg["push_batch_size"], interval,
    )
    try:
        while True:
            run_once(cfg)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Adapter sync stopped.")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync labeled examples / LoRA adapter with the training host")
    p.add_argument("--once", action="store_true", help="Run a single push+pull cycle and exit")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    config = load_model_config()
    if not config.get("training", {}).get("sync", {}).get("enabled", False):
        log.info("training.sync.enabled is false in config/model.yaml — nothing to do")
    elif args.once:
        run_once(config)
    else:
        run(config)
