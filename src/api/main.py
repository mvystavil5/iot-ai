from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="IoT World-Model API", version="0.1.0")


class TelemetryPayload(BaseModel):
    sensor_id: str
    timestamp: str
    value: float
    unit: str
    tags: dict[str, str] = {}


@app.post("/telemetry", status_code=202)
async def ingest_telemetry(payload: TelemetryPayload):
    # TODO: route to ingestion pipeline
    return {"status": "accepted", "sensor_id": payload.sensor_id}


@app.get("/query")
async def query_knowledge(q: str):
    # TODO: route to reasoner
    return {"query": q, "answer": "not implemented", "confidence": 0.0}


@app.get("/beliefs")
async def get_beliefs():
    # TODO: load from data/beliefs.jsonl
    return {"beliefs": []}


@app.get("/hypotheses")
async def get_hypotheses():
    # TODO: load from data/hypothesis_queue.jsonl
    return {"hypotheses": []}


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
