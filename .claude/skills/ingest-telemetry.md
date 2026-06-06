---
name: ingest-telemetry
description: Push a batch of sensor readings into the system. Accepts a file path or inline JSON. Validates, normalizes, embeds, and stores the data end-to-end.
---

When this skill is invoked:

1. Ask the user for the data source if not provided:
   - A file path to a JSONL / CSV file of readings
   - Or inline JSON: `[{"sensor_id": "temp_01", "timestamp": "...", "value": 22.4, "unit": "C"}]`

2. Run validation:
   ```bash
   python -m src.ingestion.validator --input {source}
   ```
   Report any schema errors to the user before proceeding.

3. Run the ingestion pipeline:
   ```bash
   python -m src.ingestion.pipeline --input {source} --verbose
   ```

4. Report back:
   - N readings ingested
   - N outliers flagged
   - N chunks added to the vector store
   - Any sensor_ids that were not in the registry (offer to add them to `config/sensors.yaml`)
