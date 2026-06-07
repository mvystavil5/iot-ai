"""
SQLite time-series storage for validated telemetry readings (see
docs/architecture.md § Storage layout — data/timeseries.db; Phase 2 swaps
this for TimescaleDB per TODO.md without changing the call surface).

Schema mirrors src.ingestion.schema.TelemetryReading. (sensor_id, timestamp)
is the primary key, so re-ingesting the same reading is a silent no-op —
the "duplicate -> deduplicate silently" rule in .claude/agents/ingestion.md.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.ingestion.schema import TelemetryReading

DEFAULT_DB_PATH = Path("./data/timeseries.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    sensor_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    outlier INTEGER NOT NULL DEFAULT 0,
    tags TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (sensor_id, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_readings_sensor_ts ON readings (sensor_id, timestamp);
"""


class TimeSeriesStore:
    """Thin wrapper around a SQLite connection — opened with
    check_same_thread=False so the same instance can be shared between the
    FastAPI event loop, bridges, and the simulator (all running in-process
    in the Phase 1 single-board deployment)."""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def insert(self, reading: TelemetryReading) -> bool:
        """Persist a reading; returns False (no-op) when (sensor_id,
        timestamp) already exists."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO readings (sensor_id, timestamp, value, unit, outlier, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                reading.sensor_id,
                reading.timestamp.isoformat(),
                reading.value,
                reading.unit,
                int(reading.outlier),
                json.dumps(reading.tags),
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def query(self, sensor_id: str, *, since: str | None = None, limit: int = 100) -> list[TelemetryReading]:
        """Most-recent-first readings for a sensor, optionally bounded by an
        ISO-8601 `since` timestamp (inclusive)."""
        sql = "SELECT sensor_id, timestamp, value, unit, outlier, tags FROM readings WHERE sensor_id = ?"
        params: list = [sensor_id]
        if since is not None:
            sql += " AND timestamp >= ?"
            params.append(since)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [
            TelemetryReading(
                sensor_id=row[0],
                timestamp=row[1],
                value=row[2],
                unit=row[3],
                outlier=bool(row[4]),
                tags=json.loads(row[5]),
            )
            for row in rows
        ]

    def count(self, sensor_id: str | None = None) -> int:
        if sensor_id is None:
            return self._conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        return self._conn.execute(
            "SELECT COUNT(*) FROM readings WHERE sensor_id = ?", (sensor_id,)
        ).fetchone()[0]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "TimeSeriesStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
