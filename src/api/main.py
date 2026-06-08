from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.exploration import scheduler
from src.security import learner
from src.security.detector import run_live_check
from src.wellness import tracker
from src.wellness.trends import run_trend_check
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


class LearnBaselinePayload(BaseModel):
    duration_s: int = 3600


@app.post("/security/baseline/learn")
async def learn_security_baseline(payload: LearnBaselinePayload):
    """Learn a fresh occupancy baseline over the trailing `duration_s` of
    already-collected history — a deliberate adaptation of learner.py's CLI
    flow (which blocks until a *future* window elapses): an HTTP handler
    can't hold a request open for up to an hour. The result is one aggregate
    "what does normal occupancy look like" signature for the space — never a
    profile of any specific person."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=payload.duration_s)
    baseline = learner.learn_baseline(start, end)
    return {
        "baseline_id": baseline["baseline_id"],
        "learned_at": baseline["learned_at"],
        "sample_size": baseline["signature"]["sample_size"],
    }


@app.post("/security/baseline/reset")
async def reset_security_baseline():
    if not learner.reset_baseline():
        raise HTTPException(status_code=404, detail="No baseline or alert history to reset")
    return {"status": "reset"}


@app.get("/security/baseline")
async def get_security_baseline():
    baseline = learner.get_baseline()
    if baseline is None:
        return {"baseline": None}
    return {"baseline": {
        "baseline_id": baseline["baseline_id"],
        "learned_at": baseline["learned_at"],
        "sample_size": baseline["signature"]["sample_size"],
    }}


@app.get("/security/check")
async def get_security_check():
    """Always returns a similarity score alongside the status — never
    presented as a verdict about who is present (see detector.detect's
    honest `expected` / `anomalous` / `no_baseline` statuses)."""
    return run_live_check()


class RecordWellnessDayPayload(BaseModel):
    day: Optional[str] = None  # ISO date "YYYY-MM-DD"; defaults to yesterday (UTC)


@app.post("/wellness/day/record")
async def record_wellness_day(payload: RecordWellnessDayPayload):
    """Aggregate one already-elapsed UTC calendar day of motion history into
    a personal activity summary — strictly opt-in, single-person, run by you
    on yourself (see src/wellness/tracker.py). Defaults to yesterday since a
    day "in progress" can't yet be summarized honestly."""
    target_day = date.fromisoformat(payload.day) if payload.day else (datetime.now(timezone.utc) - timedelta(days=1)).date()
    try:
        summary = tracker.record_day(target_day)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return summary


@app.get("/wellness/days")
async def get_wellness_days(limit: int = 30):
    return {"days": tracker.get_recent_days(limit)}


@app.get("/wellness/trend")
async def get_wellness_trend():
    """Compares your recent recorded days to the days before them — an
    informational signal about *your own* movement over time, never a
    diagnosis (see src/wellness/trends.py: `more_sedentary` / `more_active` /
    `stable` / `insufficient_data`)."""
    return run_trend_check()


@app.post("/wellness/reset")
async def reset_wellness_history():
    if not tracker.reset_history():
        raise HTTPException(status_code=404, detail="No wellness history to reset")
    return {"status": "reset"}


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
