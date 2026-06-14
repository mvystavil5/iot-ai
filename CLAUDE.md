# IoT World-Model LLM — Project Guide

## What this project is

An incrementally-trainable language/world model that ingests IoT sensor telemetry, infers knowledge about the physical environment, and actively generates hypotheses to explore. It starts tiny (single sensor stream, local RAG store) and is designed to scale to many sensors, federated agents, and online fine-tuning.

## Architecture in one paragraph

Raw telemetry arrives via MQTT / HTTP POST → **Ingestion Agent** normalizes and stores time-series chunks. A **Knowledge Builder Agent** embeds those chunks and upserts them into a vector store (ChromaDB for local, Qdrant for scale). A **Reasoner Agent** wraps a small LLM (Ollama / Claude API) with RAG retrieval to answer queries and emit beliefs. An **Explorer Agent** generates falsifiable hypotheses and schedules sensor queries or simulated perturbations to test them. A **Trainer Agent** handles periodic LoRA fine-tuning checkpoints when a sufficient number of labeled examples accumulate.

```
IoT Sensors
    │ MQTT / HTTP
    ▼
[Ingestion Agent]──► time-series DB (SQLite → TimescaleDB)
    │ normalized chunks
    ▼
[Knowledge Builder Agent]──► Vector Store (ChromaDB → Qdrant)
    │ retrieval context
    ▼
[Reasoner Agent]──► LLM (Ollama local / Claude API)
    │ beliefs + uncertainty
    ▼
[Explorer Agent]──► Hypothesis queue ──► sensor commands / simulations
    │ labeled outcomes
    ▼
[Trainer Agent]──► LoRA checkpoints ──► updated base model
```

## Key design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Continuous learning method | RAG-first, LoRA fine-tune when labeled data accumulates | RAG is zero-latency to update; LoRA bakes in learned priors |
| Base model (tiny) | `smollm2-135m` or `phi-3-mini` via Ollama | Fits on laptop CPU, easy to swap |
| Vector store (tiny) | ChromaDB in-process | Zero infra, file-backed, upgrades to Qdrant with one config change |
| Telemetry format | OTLP / SenML JSON | Standards-aligned, sensor-agnostic |
| Agent runtime | Claude Code multi-agent via `.claude/agents/` | Agents share filesystem context, easy to extend |

## Agents

| Agent | File | Role |
|---|---|---|
| Ingestion | `.claude/agents/ingestion.md` | Receive, validate, normalize telemetry |
| Knowledge Builder | `.claude/agents/knowledge-builder.md` | Embed chunks, maintain vector store |
| Reasoner | `.claude/agents/reasoner.md` | RAG-augmented query answering, belief tracking |
| Explorer | `.claude/agents/explorer.md` | Hypothesis generation, active-learning scheduling |
| Trainer | `.claude/agents/trainer.md` | LoRA fine-tuning orchestration |

## Arduino App Lab app — the Phase 1 sensor node

The board's sensor node ships as an **Arduino App Lab app** at `apps/iot_node/`.
This is the **primary** deployment path: App Lab builds + flashes the MCU sketch
and runs the MPU Python half together over the **RouterBridge** RPC.

| Part | File | Role |
|---|---|---|
| MCU sketch | `apps/iot_node/sketch/sketch.ino` | `Bridge.provide` handlers (`read_temp/humidity/co2/motion`) + `set_matrix` |
| MPU loop | `apps/iot_node/python/main.py` | `Bridge.call` the handlers, timestamp, `POST /telemetry`, push LED frames |
| LED gauge | `apps/iot_node/python/led_gauge.py` | pure-python CPU/mem → 3×uint32 frame packer (unit-testable) |
| Manifest | `apps/iot_node/app.yaml` | App Lab manifest (name, version, bricks, ports) |

The App **replaces** the bench-only trio for production use:
`firmware/sensors/sensors.ino` → `sketch/sketch.ino` (RouterBridge instead of
serial JSON), `src/ingestion/serial_bridge.py` → `python/main.py`,
`src/ingestion/led_matrix.py` → `python/led_gauge.py` + the sketch's
`set_matrix` RPC. See `apps/iot_node/README.md`.

## Skills

| Skill | File | Trigger |
|---|---|---|
| ingest-telemetry | `.claude/skills/ingest-telemetry.md` | Manually push a batch of sensor readings |
| query-knowledge | `.claude/skills/query-knowledge.md` | Ask the model what it knows about X |
| run-experiment | `.claude/skills/run-experiment.md` | Trigger an active-exploration cycle |
| train-checkpoint | `.claude/skills/train-checkpoint.md` | Kick off a LoRA fine-tune run |

## Source layout

```
src/
  ingestion/     # telemetry receivers, normalizers, time-series storage
  knowledge/     # embedding pipeline, vector store client, chunking
  model/         # LLM wrapper, RAG chain, belief tracker
  exploration/   # hypothesis generator, active-learning scheduler
  api/           # FastAPI HTTP layer for sensors + UI
apps/
  iot_node/      # Arduino App Lab app — primary Phase 1 sensor node (MCU sketch + MPU python, RouterBridge)
firmware/
  sensors/       # bench-only USB-serial MCU sketch (fallback to the App Lab app)
config/
  sensors.yaml   # sensor registry (id, type, units, expected range)
  model.yaml     # LLM backend, embedding model, RAG params
  agents.yaml    # agent routing and capability flags
docs/
  architecture.md
  llm-design.md
  scaling.md
tests/
```

## Running locally (quickstart)

```bash
# 1. install deps
pip install -r requirements.txt

# 2. start local LLM
ollama pull smollm2:135m

# 3. start ingestion API
uvicorn src.api.main:app --reload

# 4. push a test reading
python -m src.ingestion.simulator --sensor temp_01 --value 22.4

# 5. ask the reasoner
python -m src.model.cli "What is the current temperature trend?"
```

## Reference hardware — Arduino UNO Q

The primary sensor node for this project. Key specs that affect firmware and bridge code:

| Property | Value |
|---|---|
| MCU | STM32U585, ARM Cortex-M33, 160 MHz, 2 MB Flash, 786 kB SRAM |
| MPU | Qualcomm Dragonwing QRB2210, quad-core Cortex-A53 @ 2.0 GHz, Adreno GPU |
| MPU RAM | 2 GB or 4 GB LPDDR4x |
| MPU Storage | 16 GB or 32 GB eMMC |
| Wi-Fi | Wi-Fi 5 (802.11ac) dual-band 2.4 / 5 GHz — WCBN3536A module, onboard antenna |
| Bluetooth | Bluetooth 5.1, onboard antenna |
| USB | USB-C |
| MCU OS | Zephyr RTOS via Arduino Core |
| MPU OS | Debian Linux (upstream support) |
| MCU ↔ MPU | Arduino Bridge RPC over internal USB CDC |
| Onboard display | 12×8 monochrome LED matrix (assumed parity with UNO R4 WiFi's matrix — confirm against your unit) |
| Expansion | Qwiic / Modulino, MIPI-CSI (2× camera up to 25 MP), MIPI-DSI (display) |
| Form factor | Standard UNO shield-compatible |

**MCU↔MPU over RouterBridge; telemetry over HTTP.** The MCU (STM32U585) reads GPIO sensors and exposes them as RouterBridge RPC handlers; the MPU half (`apps/iot_node/python/main.py`) calls those handlers, timestamps the readings (the MCU has no RTC), and posts to `POST /telemetry`. In Phase 1 the ingestion API runs on the same board, so the POST is to `127.0.0.1:8000`; in Phase 2 you point `API_BASE` in `main.py` at the separate server and it travels over Wi-Fi 5 (no Ethernet — WiFi only). The standalone `wifi_bridge.py` originally planned for this role is superseded by the App Lab Python half.

**Phase 1 runs entirely on the UNO Q.** The QRB2210 MPU (Debian Linux) is sized to host the whole Phase-1 stack — ingestion API, SQLite, file-backed ChromaDB, and `smollm2:135m` via Ollama — alongside the `apps/iot_node/` App Lab app, with no separate server. The onboard 12×8 LED matrix is driven as a live CPU/memory load gauge (left bar = CPU %, right bar = memory %, bottom-up fill) so the board's own headroom is visible at a glance — under App Lab the MPU packs the frame (`python/led_gauge.py`) and the MCU sketch renders it via the `set_matrix` RPC; the standalone `src/ingestion/led_matrix.py` does the same job for the bench/USB fallback. Phase 2 (multi-room/multi-building, see `TODO.md`) migrates the knowledge/reasoning stack to a separate server — see `docs/installation.md` § Deployment for the migration path.

Sensor wiring (reference build):

| Sensor | Model | Measures | MCU pin | Vcc |
|---|---|---|---|---|
| Temperature + humidity | DHT11 | °C, %RH | D4 | 3.3 V |
| Air quality (CO₂ proxy) | MQ-135 | ppm (rel.) | A0 | 5 V |
| Motion | HC-SR501 PIR | bool | D7 | 5 V |

Primary deployment is the App Lab app at `apps/iot_node/` (sketch + MPU python). The bench-only USB-serial fallback lives in `firmware/sensors/sensors.ino` (MCU) + `src/ingestion/serial_bridge.py` (MPU).

## Conventions

- Python 3.11+, type hints everywhere, no `Any` unless truly unavoidable.
- All sensor data flows through Pydantic models defined in `src/ingestion/schema.py`.
- Agent prompts live in `.claude/agents/*.md`; never hard-code prompts in Python.
- Config is YAML in `config/`; loaded once at startup via `src/config.py`.
- Tests mirror source layout: `tests/ingestion/`, `tests/knowledge/`, etc.
- Use `uv` for dependency management (`uv add`, `uv run`).
