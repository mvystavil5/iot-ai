# Installation & Operations Guide

This guide covers everything needed to go from a bare machine + Arduino UNO Q
to a running IoT World-Model instance: what to buy, how to set it up, how to
develop against it, and how to deploy it.

---

## 1. Bill of Materials

### 1.1 Reference sensor node — Arduino UNO Q

| # | Item | Spec | Qty | Notes |
|---|---|---|---|---|
| 1 | Arduino UNO Q | STM32U585 MCU + Qualcomm QRB2210 MPU, 2/4 GB LPDDR4x, 16/32 GB eMMC, Wi-Fi 5 (802.11ac), BT 5.1 | 1 | Primary sensor node — runs firmware (MCU) + `wifi_bridge.py` (MPU/Debian) |
| 2 | DHT22 temperature/humidity sensor | °C, %RH | 1 | Wired to MCU pin **D4**, needs 10 kΩ pull-up DATA→VCC |
| 3 | MQ-135 air-quality sensor | analog, ppm proxy (CO₂) | 1 | Wired to MCU pin **A0**; uncalibrated — see note below |
| 4 | HC-SR501 PIR motion sensor | digital bool | 1 | Wired to MCU pin **D7** |
| 5 | 10 kΩ resistor | pull-up for DHT22 DATA line | 1 | |
| 6 | Breadboard + jumper wires | — | 1 set | |
| 7 | USB-C cable | data-capable (not charge-only) | 1 | Powers the board and carries MCU↔host serial during development |
| 8 | 5 V / 3 A USB-C power supply | — | 1 | For standalone deployment (not powered from a dev laptop) |

**Wiring summary** (see `firmware/sensors/sensors.ino` header for full detail):

```
DHT22    VCC → 3.3 V   GND → GND   DATA → D4   (10 kΩ pull-up DATA→VCC)
MQ-135   VCC → 5 V     GND → GND   AOUT → A0
HC-SR501 VCC → 5 V     GND → GND   OUT  → D7
```

> **Calibration note:** The MQ-135 is an uncalibrated CO₂ proxy. For accurate
> PPM readings, replace it with an **MH-Z19B** NDIR CO₂ sensor (see
> `config/sensors.yaml` → `co2_01.hardware.note`).

### 1.2 Host / server machine

| Item | Minimum | Recommended | Notes |
|---|---|---|---|
| CPU | 4-core x86_64 or ARM64 | 8-core | Runs ingestion API, vector store, local LLM |
| RAM | 8 GB | 16 GB+ | `smollm2:135m` fits in ~4 GB; `phi3:mini` needs more |
| Disk | 20 GB free | 50 GB+ SSD | ChromaDB + SQLite + model weights + LoRA checkpoints |
| GPU | none (CPU-only works) | CUDA-capable (e.g. RTX 30xx+) | Only needed for LoRA fine-tuning at scale (`torch+cu121`) |
| OS | Linux / macOS / Windows + WSL2 | Linux (Debian/Ubuntu) | Matches the UNO Q MPU's Debian environment |
| Network | Wi-Fi 5 access point on the same LAN as the UNO Q | — | The board posts telemetry over `POST /telemetry` via Wi-Fi |

### 1.3 Software bill of materials

| Component | Version / Source | Purpose |
|---|---|---|
| Python | 3.11+ | All agents and API |
| `uv` | latest | Dependency management (`uv add`, `uv run`) |
| Ollama | latest | Serves local LLM (`smollm2:135m`) and embedding model (`nomic-embed-text`) |
| ChromaDB | `>=0.5` (via `requirements.txt`) | Local vector store, file-backed at `./data/chroma` |
| Arduino IDE / `arduino-cli` | latest | Flashing the STM32U585 MCU firmware |
| Arduino libraries | `DHT sensor library` (Adafruit) + `Adafruit Unified Sensor`, `ArduinoJson` v6, `Arduino Bridge` | Required by `firmware/sensors/sensors.ino` |
| Anthropic SDK (`anthropic`) | `>=0.30` | Optional cloud fallback for embeddings/LLM (`claude-haiku-4-5`) |

All Python dependencies are pinned in [`requirements.txt`](../requirements.txt).

---

## 2. Setup

### 2.1 Flash the MCU firmware

1. Install the Arduino IDE (or `arduino-cli`) and add the UNO Q board package.
2. Install required libraries via Library Manager: `DHT sensor library`,
   `Adafruit Unified Sensor`, `ArduinoJson` (v6), `Arduino Bridge`.
3. Wire the sensors per §1.1.
4. Open `firmware/sensors/sensors.ino`, select the UNO Q board/port, and upload.
5. Confirm the MCU is emitting newline-delimited JSON batches at 115200 baud
   (e.g. `[{"sensor_id":"temp_01","value":22.4,"unit":"C"}, ...]`).

### 2.2 Prepare the host machine

```bash
# Clone and enter the repo
git clone <repo-url> iot-ai && cd iot-ai

# Install Python dependencies (uv-managed; requirements.txt is the source of truth)
uv venv
uv pip install -r requirements.txt

# Install and start Ollama, then pull the local models
ollama pull smollm2:135m
ollama pull nomic-embed-text
```

Create the local data directories (excluded from git via `.gitignore`):

```bash
mkdir -p data/chroma checkpoints
```

### 2.3 Configure

All runtime configuration is YAML under `config/`, loaded once at startup via
`src/config.py` — no code changes needed to swap backends:

| File | Controls |
|---|---|
| `config/sensors.yaml` | Sensor registry (id, type, unit, expected range, hardware wiring, transport interface) |
| `config/model.yaml` | Embedding model, vector store backend/path, LLM backend, RAG params, belief/training thresholds |
| `config/agents.yaml` | Agent enable flags, trigger topics, escalation thresholds, schedules |

Review `config/sensors.yaml` and confirm the sensor IDs match what your
firmware sends (`temp_01`, `humid_01`, `co2_01`, `motion_01` by default).

### 2.4 Connect the sensor node

The Arduino UNO Q ships data over **Wi-Fi only** (no Ethernet):

1. Power the UNO Q and confirm `wifi_bridge.py` is running on its Debian (MPU)
   side — it reads frames from the MCU over Arduino Bridge RPC, timestamps
   them (the MCU has no RTC), validates sensor IDs against
   `config/sensors.yaml`, and POSTs SenML JSON to `POST /telemetry`.
2. Point it at your host's ingestion API:
   ```bash
   python wifi_bridge.py --server http://<host>:8000
   ```

For bench testing without the full wireless path, you can instead tether the
UNO Q over USB-C and run the serial bridge directly on the host:

```bash
python -m src.ingestion.serial_bridge --port /dev/ttyACM0 --api http://127.0.0.1:8000
```

### 2.5 Start the stack

```bash
# Ingestion + query API
uvicorn src.api.main:app --reload

# Sanity check with a synthetic reading
python -m src.ingestion.simulator --sensor temp_01 --value 22.4

# Ask the reasoner
python -m src.model.cli "What is the current temperature trend?"
```

Verify health at `GET /health` and confirm telemetry is landing by checking
`GET /beliefs` after a few minutes of data collection.

---

## 3. Development

### 3.1 Project layout

```
src/
  ingestion/   # telemetry receivers, normalizers, time-series storage
  knowledge/   # embedding pipeline, vector store client, chunking
  model/       # LLM wrapper, RAG chain, belief tracker
  exploration/ # hypothesis generator, active-learning scheduler
  api/         # FastAPI HTTP layer for sensors + UI
firmware/      # Arduino UNO Q MCU sketches
config/        # sensors.yaml, model.yaml, agents.yaml
docs/          # architecture, LLM design, this guide
tests/         # mirrors src/ layout (tests/ingestion/, tests/knowledge/, ...)
```

See [`architecture.md`](architecture.md) for the full data-flow diagram and
[`llm-design.md`](llm-design.md) for model/RAG design rationale.

### 3.2 Conventions (enforced in review)

- Python 3.11+, type hints everywhere — avoid `Any` unless truly unavoidable.
- All sensor data flows through the Pydantic models in `src/ingestion/schema.py`.
- Agent prompts live in `.claude/agents/*.md` — never hard-code prompts in Python.
- Config is YAML in `config/`, loaded once at startup via `src/config.py`.
- Dependency management via `uv` (`uv add <pkg>`, `uv run <cmd>`).

### 3.3 Running tests

```bash
uv run pytest                       # full suite
uv run pytest tests/ingestion/      # mirror src/ layout per area
```

### 3.4 Local iteration loop

1. Push synthetic readings with `python -m src.ingestion.simulator --sensor <id> --value <v>`
   to exercise the pipeline without hardware.
2. Inspect vector store health with `python -m src.knowledge.cli stats`.
3. Query the reasoner with `python -m src.model.cli "<query>" --show-context --show-beliefs`
   to see retrieved chunks and confidence scoring alongside the answer.
4. Use `python -m src.exploration.scheduler --list` / `--run-next --verbose`
   to drive an active-exploration cycle manually (mirrors the
   `run-experiment` skill).

### 3.5 Agents and skills

Agents (`.claude/agents/*.md`) and skills (`.claude/skills/*.md`) drive the
multi-agent runtime — see the table in [`../CLAUDE.md`](../CLAUDE.md#agents)
for the full mapping of agent → file → role.

---

## 4. Deployment

### 4.1 Single-machine deployment (Phase 1 target: ~10 sensors)

This is the default mode described above: SQLite for time-series storage,
ChromaDB (file-backed at `./data/chroma`) for the vector store, and
`smollm2:135m` served locally via Ollama. Run the API under a process
supervisor for resilience:

```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 1
```

> Use a single worker — the in-process event bus
> (`knowledge_chunks` / `store_updated` / `beliefs` topics, see
> `config/agents.yaml`) and the file-backed ChromaDB store are not safe to
> share across multiple processes.

Run `wifi_bridge.py` (or `serial_bridge.py` for tethered setups) as a
long-lived service alongside the API — both auto-reconnect on transport
failure (Wi-Fi drop / USB unplug).

### 4.2 Scaling out (Phase 2+)

The config layer is designed so backend swaps require no code changes:

| Swap | From → To | Where |
|---|---|---|
| Time-series DB | SQLite → TimescaleDB | `src/ingestion/storage.py` |
| Vector store | ChromaDB → Qdrant | `config/model.yaml: vector_store.backend`, `src/knowledge/store.py` |
| LLM | `smollm2:135m` (Ollama) → `phi3:mini` or Claude API | `config/model.yaml: llm.backend` / `llm.model` |
| Embeddings | `nomic-embed-text` → `text-embedding-3-small` | `config/model.yaml: embedding.model` |
| Telemetry transport | HTTP POST → MQTT broker (Mosquitto) | `config/agents.yaml: ingestion.trigger`, `src/ingestion/mqtt_bridge.py` |

At Phase 2 scale, run one Reasoner instance per room/zone sharing a single
Explorer, and stand up a dashboard polling `/health` and `/beliefs`.

### 4.3 Fine-tuning / training deployment

The Trainer agent is **disabled by default** (`config/agents.yaml: trainer.enabled: false`)
— enable it once you have a GPU and `training.trigger_threshold` (default 50)
labeled examples have accumulated in `data/labeled_examples.jsonl`. LoRA
checkpoints are written to `training.checkpoint_dir` (`./checkpoints`,
keeping the last `keep_last_n` versions). For CPU-only hosts, leave
`torch` at its default CPU build; for GPU hosts install `torch+cu121`.

### 4.4 Operational checklist

- [ ] `data/chroma/`, `data/*.db`, `checkpoints/` excluded from git (`.gitignore`)
- [ ] Ollama service running and models pulled (`smollm2:135m`, `nomic-embed-text`)
- [ ] `wifi_bridge.py` (or `serial_bridge.py`) running as a supervised long-lived process
- [ ] `GET /health` returns OK and `GET /beliefs` shows recent entries
- [ ] Backups of `data/beliefs.jsonl`, `data/labeled_examples.jsonl`, and `checkpoints/`
- [ ] `config/agents.yaml: explorer.schedule_cron` matches your desired hypothesis-generation cadence
