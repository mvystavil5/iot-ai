from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.exploration import scheduler
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


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
