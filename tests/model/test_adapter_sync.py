import json
import tarfile
from io import BytesIO
from pathlib import Path

import httpx
import pytest

from src.model import adapter_sync


def _cfg(tmp_path: Path) -> dict:
    return {
        "training": {
            "labeled_examples_path": str(tmp_path / "labeled_examples.jsonl"),
            "checkpoint_dir": str(tmp_path / "checkpoints"),
            "sync": {
                "enabled": True,
                "host": "http://training-host",
                "push_batch_size": 2,
                "poll_interval_s": 1,
            },
        }
    }


def _tarball(files: dict[str, str]) -> bytes:
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, BytesIO(data))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _redirect_state_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter_sync, "DEFAULT_SYNC_STATE_PATH", tmp_path / "sync_state.json")
    monkeypatch.setattr(adapter_sync, "DEFAULT_REGISTRY_PATH", tmp_path / "model_registry.json")


# --- push: pending examples ---

def test_pending_examples_returns_new_lines(tmp_path):
    path = tmp_path / "examples.jsonl"
    path.write_text("a\nb\nc\n")
    assert adapter_sync._pending_examples(path, 1) == ["b", "c"]


def test_pending_examples_missing_file_returns_empty(tmp_path):
    assert adapter_sync._pending_examples(tmp_path / "missing.jsonl", 0) == []


# --- push: batching + high-water mark ---

def test_push_if_ready_waits_for_batch_size(tmp_path):
    cfg = _cfg(tmp_path)
    Path(cfg["training"]["labeled_examples_path"]).write_text('{"a": 1}\n')  # batch_size is 2

    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(AssertionError("should not push below batch size"))
    ))
    assert adapter_sync.push_if_ready(cfg, client) == 0
    assert not adapter_sync.DEFAULT_SYNC_STATE_PATH.exists()


def test_push_if_ready_pushes_and_advances_state(tmp_path):
    cfg = _cfg(tmp_path)
    examples_path = Path(cfg["training"]["labeled_examples_path"])
    examples_path.write_text('{"a": 1}\n{"a": 2}\n{"a": 3}\n')
    received = []

    def handler(request):
        received.append(json.loads(request.content))
        return httpx.Response(202, json={"status": "accepted"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pushed = adapter_sync.push_if_ready(cfg, client)

    assert pushed == 3
    assert received == [{"examples": ['{"a": 1}', '{"a": 2}', '{"a": 3}']}]
    state = json.loads(adapter_sync.DEFAULT_SYNC_STATE_PATH.read_text())
    assert state["last_pushed_line"] == 3

    received.clear()
    assert adapter_sync.push_if_ready(cfg, client) == 0
    assert received == []


# --- pull: version comparison ---

def test_needs_pull():
    assert adapter_sync._needs_pull(None, "v1") is True
    assert adapter_sync._needs_pull("v1", "v1") is False
    assert adapter_sync._needs_pull("v1", "v2") is True
    assert adapter_sync._needs_pull("v1", None) is False


# --- pull: atomic swap ---

def test_swap_current_extracts_and_promotes(tmp_path):
    checkpoint_dir = tmp_path / "checkpoints"
    adapter_sync._swap_current(checkpoint_dir, "v1", _tarball({"adapter_config.json": '{"r": 8}'}))

    assert (checkpoint_dir / "v1" / "adapter_config.json").read_text() == '{"r": 8}'
    assert (checkpoint_dir / "current" / "adapter_config.json").read_text() == '{"r": 8}'
    assert not (checkpoint_dir / ".previous").exists()


def test_swap_current_replaces_and_archives_previous(tmp_path):
    checkpoint_dir = tmp_path / "checkpoints"
    adapter_sync._swap_current(checkpoint_dir, "v1", _tarball({"f.txt": "one"}))
    adapter_sync._swap_current(checkpoint_dir, "v2", _tarball({"f.txt": "two"}))

    assert (checkpoint_dir / "current" / "f.txt").read_text() == "two"
    assert (checkpoint_dir / "v1" / "f.txt").read_text() == "one"
    assert (checkpoint_dir / ".previous" / "f.txt").read_text() == "one"


# --- pull: end to end against a mocked training host ---

def test_pull_if_newer_downloads_and_installs(tmp_path):
    cfg = _cfg(tmp_path)
    tar_bytes = _tarball({"adapter_model.bin": "weights-v1"})

    def handler(request):
        if request.url.path == "/training/registry":
            return httpx.Response(200, json={"current_version": "v1", "checkpoints": []})
        if request.url.path == "/training/adapter/v1":
            return httpx.Response(200, content=tar_bytes)
        raise AssertionError(f"unexpected request: {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    version = adapter_sync.pull_if_newer(cfg, client)

    assert version == "v1"
    checkpoint_dir = Path(cfg["training"]["checkpoint_dir"])
    assert (checkpoint_dir / "current" / "adapter_model.bin").read_text() == "weights-v1"
    registry = json.loads(adapter_sync.DEFAULT_REGISTRY_PATH.read_text())
    assert registry["current_version"] == "v1"


def test_pull_if_newer_skips_when_up_to_date(tmp_path):
    cfg = _cfg(tmp_path)
    adapter_sync.DEFAULT_REGISTRY_PATH.write_text(
        json.dumps({"current_version": "v1", "checkpoints": []})
    )

    def handler(request):
        assert request.url.path == "/training/registry", "should not fetch the adapter when up to date"
        return httpx.Response(200, json={"current_version": "v1", "checkpoints": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert adapter_sync.pull_if_newer(cfg, client) is None
