from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.exploration import scheduler
from src.identity import registration
from src.identity.matcher import run_live_match
from src.ingestion.pipeline import IngestionError, IngestionPipeline
from src.model.beliefs import BeliefStore
from src.model.reasoner import Reasoner

app = FastAPI(title="IoT World-Model API", version="0.1.0")
_pipeline = IngestionPipeline()
_reasoner = Reasoner()
_beliefs = BeliefStore()


class TelemetryPayload(BaseModel):
    sensor_id: str
    timestamp: str
    value: float
    unit: str
    tags: dict[str, str] = {}


@app.post("/telemetry", status_code=202)
async def ingest_telemetry(payload: TelemetryPayload):
    try:
        reading = _pipeline.ingest(payload.model_dump())
    except IngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "accepted", "sensor_id": reading.sensor_id, "outlier": reading.outlier}


@app.get("/query")
async def query_knowledge(q: str):
    result = _reasoner.answer(q)
    return {
        "query": q,
        "answer": result["answer"],
        "confidence": result["confidence"],
        "supporting_sensors": result["supporting_sensors"],
        "caveats": result["caveats"],
    }


@app.get("/beliefs")
async def get_beliefs():
    return {"beliefs": _beliefs.all()}


@app.get("/hypotheses")
async def get_hypotheses():
    return {"hypotheses": scheduler.list_queue()}


@app.post("/experiment/run")
async def run_experiment():
    outcome = scheduler.run_next()
    if outcome is None:
        return {"status": "empty", "message": "Hypothesis queue is empty."}
    return {
        "status": "completed",
        "hypothesis_id": outcome["hypothesis"]["hypothesis_id"],
        "outcome": outcome["result"]["outcome"],
        "confidence_delta": outcome["result"]["confidence_delta"],
        "evidence": outcome["result"]["evidence"],
    }


class IdentityRegisterPayload(BaseModel):
    display_name: str
    duration_s: int = 3600


@app.post("/identity/register")
async def register_identity(payload: IdentityRegisterPayload):
    """Register a routine signature over the trailing `duration_s` of
    already-collected history — a deliberate adaptation of
    registration.py's CLI flow (which blocks until a *future* window
    elapses): an HTTP handler can't hold a request open for up to an hour.
    Consent is still explicit and immediate — the caller chooses, right now,
    to have their recent routine become their profile."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=payload.duration_s)
    profile = registration.register(payload.display_name, start, end)
    return {
        "profile_id": profile["profile_id"],
        "display_name": profile["display_name"],
        "consent_at": profile["consent_at"],
        "sample_size": profile["signature"]["sample_size"],
    }


@app.post("/identity/revoke/{profile_id}")
async def revoke_identity(profile_id: str):
    if not registration.revoke(profile_id):
        raise HTTPException(status_code=404, detail=f"No active profile with id {profile_id!r}")
    return {"status": "revoked", "profile_id": profile_id}


@app.get("/identity/profiles")
async def list_identity_profiles():
    return {"profiles": registration.list_profiles()}


@app.get("/identity/match")
async def get_identity_match():
    """Always returns a confidence score alongside the guess — never
    presented as fact (see matcher.match's honest "unknown" fallback)."""
    return run_live_match()


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
