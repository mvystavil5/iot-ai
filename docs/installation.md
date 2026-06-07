# Installation & Operations Guide

This guide covers everything needed to go from a bare machine + Arduino UNO Q
to a running IoT World-Model instance: what to buy, how to set it up, how to
develop against it, and how to deploy it.

---

## 1. Bill of Materials

### 1.1 Reference sensor node — Arduino UNO Q

| # | Item | Spec | Qty | Notes |
|---|---|---|---|---|
| 1 | Arduino UNO Q | STM32U585 MCU + Qualcomm QRB2210 MPU, 2/4 GB LPDDR4x, 16/32 GB eMMC, Wi-Fi 5 (802.11ac), BT 5.1, onboard 12×8 LED matrix | 1 | **Phase 1 runs the entire stack on this one board** — firmware (MCU) + `wifi_bridge.py`, `led_matrix.py`, ingestion API, ChromaDB, and the LLM (MPU/Debian). No separate server needed. |
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

### 1.2 Host / server machine — **Phase 2 only**

Phase 1 needs **no separate machine**: the UNO Q's QRB2210 MPU hosts the
entire stack itself (see §4.1). Provision the machine below only when you
outgrow Phase 1 and migrate to the Phase 2 multi-room/server topology (§4.2).

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

The Arduino UNO Q ships data over **Wi-Fi only** (no Ethernet). In the
**Phase 1 on-device topology** (§4.1, the default), `wifi_bridge.py` simply
points back at the API running on the same board:

1. Power the UNO Q and confirm `wifi_bridge.py` is running on its Debian (MPU)
   side — it reads frames from the MCU over Arduino Bridge RPC, timestamps
   them (the MCU has no RTC), validates sensor IDs against
   `config/sensors.yaml`, and POSTs SenML JSON to `POST /telemetry`.
2. Point it at the local ingestion API (same board, Phase 1):
   ```bash
   python wifi_bridge.py --server http://127.0.0.1:8000
   ```
   For the Phase 2 separate-server topology (§4.2), point `--server` at that
   host's address instead.

For bench testing without the full wireless path, you can instead tether the
UNO Q over USB-C and run the serial bridge directly:

```bash
python -m src.ingestion.serial_bridge --port /dev/ttyACM0 --api http://127.0.0.1:8000
```

### 2.5 Start the load indicator

`led_matrix.py` drives the UNO Q's onboard 12×8 LED matrix as a live system
gauge — left bar = CPU %, right bar = memory %, both filling bottom-up —
sampled via `psutil`. Run it on the MPU alongside the bridge and API:

```bash
python -m src.ingestion.led_matrix --interval 2.0
```

If the vendor LED matrix binding isn't installed (e.g. on a dev machine), it
falls back to logging an ASCII rendering instead of failing.

### 2.6 Start the stack

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
5. Run `python -m src.ingestion.led_matrix --debug` to watch CPU/memory load
   sampling in real time (logs an ASCII rendering on dev machines without the
   physical matrix) — useful for confirming the indicator reflects load while
   you exercise the pipeline above.

### 3.5 Agents and skills

Agents (`.claude/agents/*.md`) and skills (`.claude/skills/*.md`) drive the
multi-agent runtime — see the table in [`../CLAUDE.md`](../CLAUDE.md#agents)
for the full mapping of agent → file → role.

---

## 4. Deployment

### 4.1 On-device deployment — Phase 1 target (~10 sensors, default)

**The entire stack runs on the UNO Q's QRB2210 MPU — no separate server.**
The board's Debian Linux side hosts the FastAPI ingestion/query API, SQLite,
file-backed ChromaDB (`./data/chroma`), and `smollm2:135m` via Ollama, all
within its 2–4 GB RAM / 16–32 GB eMMC envelope (sized for exactly this in
`config/model.yaml`). Run everything as supervised long-lived processes on
the board itself:

```bash
# On the UNO Q (Debian/MPU side)
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 1
python wifi_bridge.py --server http://127.0.0.1:8000
python -m src.ingestion.led_matrix --interval 2.0
```

> Use a single worker — the in-process event bus
> (`knowledge_chunks` / `store_updated` / `beliefs` topics, see
> `config/agents.yaml`) and the file-backed ChromaDB store are not safe to
> share across multiple processes.

`wifi_bridge.py` (or `serial_bridge.py` for tethered bench setups) and
`led_matrix.py` auto-reconnect / degrade gracefully on transport or hardware
issues (Wi-Fi drop, USB unplug, missing matrix binding) — supervise all three
alongside `uvicorn` (e.g. via `systemd` units or a process manager).

The 12×8 LED matrix doubles as your headroom gauge: watch it (left = CPU %,
right = memory %) to judge when the board is approaching the limits that
warrant moving to §4.2.

### 4.2 Migrating to a separate server — Phase 2+ (multi-room/building)

When sensor count, query load, or LLM quality needs outgrow the UNO Q's
on-device headroom (the LED matrix gauge running consistently near full is
your signal), migrate the knowledge/reasoning stack to a separate server —
**no application code changes required**, only config and a data copy:

1. Provision the host described in §1.2.
2. Copy `data/` (SQLite DB, `data/chroma/`, `beliefs.jsonl`,
   `labeled_examples.jsonl`) to the new host.
3. Start the API stack there (`uvicorn src.api.main:app ...`).
4. On the UNO Q, repoint the bridge at the new host and keep the LED matrix
   running locally as a board-health gauge:
   ```bash
   python wifi_bridge.py --server http://<server-host>:8000
   python -m src.ingestion.led_matrix --interval 2.0
   ```

The config layer is designed so backend swaps within that migration require
no code changes:

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

**LoRA fine-tuning never runs on the UNO Q** — its QRB2210 MPU has no
CUDA-class GPU and its 2–4 GB RAM is already committed to inference
(§4.1). Instead, the board only accumulates labeled examples and pulls back
whatever adapter a separate **training host** produces — see
`docs/architecture.md` § Training & adapter sync for the full data-flow
diagram and design rationale. The Trainer agent stays **disabled in
`config/agents.yaml`** (`trainer.enabled: false`); training is triggered
manually on the host via `src/model/trainer.py`, not by an on-board agent.

**1. Provision and run the training host** (the machine from §1.2 — GPU
recommended; CPU-only works but is slow):

```bash
# On the training host
uv pip install -r requirements.txt    # includes peft, transformers, datasets, torch
uvicorn src.model.training_service:app --host 0.0.0.0 --port 8100
```

This serves `POST /training/examples` (receives batches pushed from the
board), `GET /training/registry` (serves `data/model_registry.json`), and
`GET /training/adapter/{version}` (streams a checkpoint as a tarball) — the
three endpoints `adapter_sync.py` talks to.

**2. Enable the sync** in `config/model.yaml: training.sync` (off by default
in Phase 1):

```yaml
training:
  sync:
    enabled: true
    host: "http://<training-host>:8100"
    push_batch_size: 50      # examples per export batch
    poll_interval_s: 1800    # adapter-registry poll cadence
```

**3. Run the sync loop on the board**, alongside `wifi_bridge.py` and
`led_matrix.py`:

```bash
python -m src.model.adapter_sync              # continuous push+pull loop
python -m src.model.adapter_sync --once       # single cycle, e.g. for cron
```

It pushes a batch to `{host}/training/examples` whenever
`data/labeled_examples.jsonl` has accumulated `push_batch_size` new lines
(tracked via a high-water mark in `data/sync_state.json`), and polls
`{host}/training/registry` every `poll_interval_s` — when a newer
`current_version` appears, it downloads the adapter tarball, atomically
swaps it into `checkpoints/current/` (old version archived to
`checkpoints/.previous/`), and rewrites the local `data/model_registry.json`.

**4. Run training on the host** once enough examples have accumulated:

```bash
python -m src.model.trainer --check-readiness   # N examples vs. trigger_threshold (default 50)
python -m src.model.trainer --run --verbose      # full LoRA fine-tune → eval → promote
```

`--run` loads `data/labeled_examples.jsonl`, formats instruction-tuning
pairs, splits 80/10/10 (stratified by sensor type), fine-tunes via PEFT
(`config/model.yaml: lora`/`training`), evaluates on the held-out test set,
and promotes to `checkpoints/current/` only if it beats the best
`eval_score` on record — recording the run in `data/training_runs.jsonl`
and `data/model_registry.json` (keeping the last `keep_last_n` checkpoints).
For CPU-only hosts, leave `torch` at its default CPU build; for GPU hosts
install `torch+cu121`.

> `_evaluate` is the one piece of `trainer.py` that's still a stub — it
> needs `src/model/reasoner.py` (not yet implemented per `TODO.md`) to score
> a fine-tuned adapter against held-out test pairs. Wire it up once that
> module exists.

### 4.4 Operational checklist

- [ ] `data/chroma/`, `data/*.db`, `checkpoints/` excluded from git (`.gitignore`)
- [ ] Ollama service running and models pulled (`smollm2:135m`, `nomic-embed-text`)
- [ ] `wifi_bridge.py` (or `serial_bridge.py`) running as a supervised long-lived process
- [ ] `led_matrix.py` running and showing live CPU/memory bars (or logging ASCII frames if no physical matrix binding)
- [ ] `GET /health` returns OK and `GET /beliefs` shows recent entries
- [ ] Backups of `data/beliefs.jsonl`, `data/labeled_examples.jsonl`, and `checkpoints/`
- [ ] `config/agents.yaml: explorer.schedule_cron` matches your desired hypothesis-generation cadence
