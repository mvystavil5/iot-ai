from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator


class TelemetryReading(BaseModel):
    sensor_id: str
    timestamp: datetime
    value: float
    unit: str
    outlier: bool = False
    unit_normalized: bool = True
    tags: dict[str, str] = Field(default_factory=dict)


class KnowledgeChunk(BaseModel):
    chunk_id: str
    sensor_id: str
    timestamp: datetime
    text: str                     # human-readable representation for embedding
    value: float
    unit: str
    outlier: bool
    tags: dict[str, str] = Field(default_factory=dict)
    chunk_type: Literal["single", "aggregate", "event"] = "single"


class SensorConfig(BaseModel):
    id: str
    name: str
    type: str
    unit: str
    location: str
    expected_range: tuple[float, float]
    reporting_interval_s: int
