# IoT World-Model LLM

An incrementally-trainable language/world model that ingests IoT sensor
telemetry, infers knowledge about the physical environment, and actively
generates hypotheses to explore. It starts tiny — a single sensor stream and a
local RAG store on one board — and is designed to scale to many sensors,
federated agents, and online fine-tuning.

**Reference hardware: the [Arduino UNO Q](https://docs.arduino.cc/), deployed
as an [Arduino App Lab](https://docs.arduino.cc/software/app-lab/) app.** Phase 1
runs the *entire* stack — sensor node, ingestion API, vector store, and a small
local LLM — on the one board, with no separate server.

## How it works

```
IoT sensors (DHT11 / MQ-135 / HC-SR501)
    │  read on the STM32U585 MCU
    ▼
[Sensor node]  apps/iot_node/  ── RouterBridge RPC (MCU ↔ MPU) ──► POST /telemetry
    ▼
[Ingestion]    validate → normalize → SQLite time-series → emit chunk
    ▼
[Knowledge]    embed chunks (nomic-embed-text) → ChromaDB vector store
    ▼
[Reasoner]     RAG retrieval + smollm2:135m (Ollama) → beliefs + confidence
    ▼
[Explorer]     falsifiable hypotheses → scheduled experiments → labeled outcomes
    ▼
[Trainer]      LoRA fine-tune (off-board) → adapter pulled back to the board
```

Each stage runs as a Claude Code agent (`.claude/agents/`). Continuous learning
is **RAG-first** (zero-latency to update) with **LoRA** fine-tuning when enough
labeled examples accumulate. Two opt-in modules ride on the same sensors: an
**occupancy-baseline anomaly detector** (intrusion detection without biometrics)
and a single-person **wellness self-experiment** — both keep their data on the
board and support a hard purge.

## The sensor node — Arduino App Lab app

The board's sensor node is packaged as the App Lab app at
[`apps/iot_node/`](apps/iot_node/README.md). App Lab builds + flashes the MCU
sketch and runs the MPU Python half together over the **RouterBridge** RPC:

- **MCU** (`sketch/sketch.ino`) — exposes `read_temp` / `read_humidity` /
  `read_co2` / `read_motion` RPC handlers and a `set_matrix` handler that drives
  the onboard 12×8 LED matrix.
- **MPU** (`python/main.py`) — calls those handlers, timestamps each reading,
  POSTs to the on-board ingestion API, and pushes a CPU/memory load frame to the
  LED matrix every 2 s (left bar = CPU %, right bar = memory %).

A USB-serial firmware (`firmware/sensors/sensors.ino`) plus
`src/ingestion/serial_bridge.py` and `src/ingestion/led_matrix.py` remain as a
**bench-only fallback** for testing the pipeline over a tether without App Lab.

## Quickstart (dev machine)

```bash
# 1. install deps
pip install -r requirements.txt

# 2. start the local LLM + embeddings
ollama pull smollm2:135m
ollama pull nomic-embed-text

# 3. start the ingestion/query API
uvicorn src.api.main:app --reload

# 4. push a synthetic reading
python -m src.ingestion.simulator --sensor temp_01 --value 22.4

# 5. ask the reasoner
python -m src.model.cli "What is the current temperature trend?"
```

To deploy on real hardware, see [`docs/installation.md`](docs/installation.md)
and [`apps/iot_node/README.md`](apps/iot_node/README.md).

## Repository layout

```
apps/iot_node/   # Arduino App Lab app — primary Phase 1 sensor node (sketch + python)
firmware/        # bench-only USB-serial MCU sketch (fallback)
src/
  ingestion/     # telemetry receivers, normalizers, time-series storage
  knowledge/     # embedding pipeline, vector store client, chunking
  model/         # LLM wrapper, RAG chain, belief tracker, LoRA trainer/sync
  exploration/   # hypothesis generator, active-learning scheduler
  security/      # occupancy-baseline anomaly detection (opt-in)
  wellness/      # personal activity self-experiment (opt-in, single-person)
  api/           # FastAPI HTTP layer
config/          # sensors.yaml, model.yaml, agents.yaml
docs/            # architecture, installation, llm-design, whitepaper
tests/           # mirror the source layout
```

## Documentation

| Doc | What's in it |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Project guide for agents — architecture, conventions, hardware |
| [`docs/whitepaper.md`](docs/whitepaper.md) | Plain-language overview of the whole system |
| [`docs/architecture.md`](docs/architecture.md) | Data-flow diagrams, API reference, deployment topology |
| [`docs/llm-design.md`](docs/llm-design.md) | Model design for engineers (RAG, confidence, LoRA) |
| [`docs/installation.md`](docs/installation.md) | Bill of materials, on-device deploy, Phase 2 migration |
| [`TODO.md`](TODO.md) | Living task list, phase by phase |
