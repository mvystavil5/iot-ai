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
| Expansion | Qwiic / Modulino, MIPI-CSI (2× camera up to 25 MP), MIPI-DSI (display) |
| Form factor | Standard UNO shield-compatible |

**Transport: WiFi only — no Ethernet.** All telemetry leaves the board as HTTP POST over Wi-Fi 5 from `wifi_bridge.py` running on the MPU (Debian side). The MCU (STM32U585) reads GPIO sensors and hands data to the MPU via `Bridge.put()` / `Bridge.get()` RPC; the MPU timestamps and posts to `POST /telemetry`.

Sensor wiring (reference build):

| Sensor | Model | Measures | MCU pin | Vcc |
|---|---|---|---|---|
| Temperature + humidity | DHT22 | °C, %RH | D4 | 3.3 V |
| Air quality (CO₂ proxy) | MQ-135 | ppm (rel.) | A0 | 5 V |
| Motion | HC-SR501 PIR | bool | D7 | 5 V |

Firmware lives in `firmware/arduino_uno_q/`; bridge lives in `src/ingestion/wifi_bridge.py`.

## Conventions

- Python 3.11+, type hints everywhere, no `Any` unless truly unavoidable.
- All sensor data flows through Pydantic models defined in `src/ingestion/schema.py`.
- Agent prompts live in `.claude/agents/*.md`; never hard-code prompts in Python.
- Config is YAML in `config/`; loaded once at startup via `src/config.py`.
- Tests mirror source layout: `tests/ingestion/`, `tests/knowledge/`, etc.
- Use `uv` for dependency management (`uv add`, `uv run`).
