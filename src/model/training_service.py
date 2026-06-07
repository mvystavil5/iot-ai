"""
Training service: a small standalone API for the offline/cloud training host
(separate from src/api/main.py, which serves the board's telemetry/query API).

Endpoints:
  POST /training/examples         — append a labeled-example batch pushed by
                                     the board's adapter_sync
  GET  /training/registry         — serve data/model_registry.json so the
                                     board can detect a newer adapter version
  GET  /training/adapter/{version}— stream checkpoints/{version} as a tarball
                                     for the board to download and install

Run on the training host:
  uvicorn src.model.training_service:app --host 0.0.0.0 --port 8100
"""

import io
import json
import tarfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.config import load_model_config

app = FastAPI(title="IoT World-Model Training Service", version="0.1.0")


class ExamplesBatch(BaseModel):
    examples: list[str]


def _model_cfg() -> dict:
    return load_model_config()["training"]


def _labeled_examples_path() -> Path:
    return Path(_model_cfg()["labeled_examples_path"])


def _registry_path() -> Path:
    return Path("./data/model_registry.json")


def _checkpoint_dir() -> Path:
    return Path(_model_cfg()["checkpoint_dir"])


@app.post("/training/examples", status_code=202)
async def receive_examples(batch: ExamplesBatch):
    path = _labeled_examples_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for line in batch.examples:
            f.write(line.rstrip("\n") + "\n")
    return {"status": "accepted", "received": len(batch.examples)}


@app.get("/training/registry")
async def get_registry():
    path = _registry_path()
    if not path.exists():
        return {"current_version": None, "checkpoints": []}
    return json.loads(path.read_text())


@app.get("/training/adapter/{version}")
async def get_adapter(version: str):
    adapter_dir = _checkpoint_dir() / version
    if not adapter_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"No checkpoint for version '{version}'")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(adapter_dir, arcname=version)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{version}.tar.gz"'},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
