# System Architecture

## Deployment topology

**Phase 1 (current target, ~10 sensors) runs the entire stack on the UNO Q
itself** — no separate server. The QRB2210 MPU's Debian Linux side hosts the
`apps/iot_node/` **Arduino App Lab app** (MCU sketch + MPU sensor/LED-gauge
loop over RouterBridge), the FastAPI ingestion/query API, SQLite, file-backed
ChromaDB, and `smollm2:135m` served by Ollama, all within its 2–4 GB RAM /
16–32 GB eMMC envelope. This collapses the "Sensors → MCU → MPU → [HTTP over
Wi-Fi] → server" hop in the diagram below into a single board: the MPU *is*
the ingestion/knowledge/reasoning host, and the App's Python half POSTs
telemetry to `http://127.0.0.1:8000`.

**Phase 2 (multi-room/multi-building, see `TODO.md`)** migrates the
Knowledge Builder, Reasoner, Explorer, and Trainer agents to a separate
server — the App's `python/main.py` then points `API_BASE` at that host
instead of `127.0.0.1`. Because every backend in `config/model.yaml` is swappable
without code changes (SQLite→TimescaleDB, ChromaDB→Qdrant), migrating means
copying `data/` to the new host and repointing the bridge — see
`docs/installation.md` § Deployment for the full migration checklist.

## Training & adapter sync

The QRB2210 MPU has no CUDA-class GPU and a 2–4 GB RAM budget already spent
on inference, so **LoRA fine-tuning never runs on the board** — it runs on a
separate offline/cloud **training host** (a GPU box, or Modal/RunPod per the
Phase 4 notes in `TODO.md`). The board's job in Phase 1 is just to
*accumulate* labeled examples and *pull back* whatever adapter the host
produces. Both legs of that exchange are **outbound-only HTTP from the
board** — the same "may sit behind NAT" assumption that shapes the App's
telemetry POST — so the board never needs to be reachable:

```
UNO Q (board)                                   Training host (GPU/cloud)
─────────────                                   ─────────────────────────
data/labeled_examples.jsonl                     data/labeled_examples.jsonl
        │ src/model/adapter_sync.py                     ▲
        │ batch POST once                               │ src/model/training_service.py
        │ training.sync.push_batch_size                 │ POST /training/examples
        │ new lines accumulate                          │ appends batch
        └──────── POST /training/examples ──────────────┘

        ┌──────── GET  /training/registry ───────────────┐
        │ poll every                                     │ GET /training/registry
        │ training.sync.poll_interval_s,                 │ serves data/model_registry.json
        │ compare current_version                        ▼
        │                                       src/model/trainer.py
        │  if newer:                              --check-readiness
        │  GET /training/adapter/{version} ◄──────  --run → PEFT LoRA fine-tune
        ▼  (tarball, streamed by training_service)       → _evaluate → _promote
checkpoints/{version}/ → atomic swap → checkpoints/current/
data/model_registry.json rewritten locally
(Reasoner compares its loaded version to the registry's
current_version on each query and hot-reloads — no event bus needed)
```

Why pull-based for the adapter and push-based for examples: the board
initiates both, but a *poll* lets it discover a new adapter whenever the
host finishes a run (the host has no way to reach the board to announce
one), while a *push*, gated on `training.sync.push_batch_size`, keeps
example-export decoupled from the host's own `trigger_threshold` — the host
accumulates across many small pushes and `trainer.py --check-readiness`
decides when a run is warranted. `_swap_current` in `adapter_sync.py` stages
the extracted adapter and renames it into place atomically (old version
archived to `checkpoints/.previous/`) so a crash mid-pull can never leave
`checkpoints/current/` partially written. See
`docs/installation.md` § 4.3 for running the training service and enabling
the sync.

## Data flow

```
┌─────────────────────────────────────────────────────┐
│                   IoT Sensors                        │
│  DHT11 (D4)    MQ-135 (A0)    HC-SR501 PIR (D7)     │
└───────────┬─────────────────────────────────────────┘
            │ GPIO — analog / digital
            ▼
┌─────────────────────────────────────────────────────┐
│   STM32U585 MCU  (Cortex-M33, 160 MHz)  on UNO Q   │
│  • apps/iot_node/sketch/sketch.ino                  │
│  • Exposes read_temp/humidity/co2/motion RPCs       │
│  • set_matrix RPC renders the LED load frame        │
│    (Arduino RouterBridge → internal USB CDC)        │
└───────────┬─────────────────────────────────────────┘
            │ RouterBridge RPC (internal USB CDC, on-board)
            ▼
┌─────────────────────────────────────────────────────┐
│  QRB2210 MPU  (quad Cortex-A53, 2 GHz)  on UNO Q   │
│  Running: Debian Linux + apps/iot_node/python/main.py│
│  • Bridge.call's the MCU RPC handlers every 30 s    │
│    (+ immediate post on PIR state change)           │
│  • Adds UTC timestamps (MCU has no RTC)             │
│  • Validates sensor IDs against config/sensors.yaml  │
│  • POST /telemetry (127.0.0.1 in Phase 1)           │
│  • python/led_gauge.py packs a 12×8 frame: live     │
│    CPU % (left bar) / memory % (right bar),         │
│    pushed to the MCU via the set_matrix RPC         │
│                                                       │
│  ── Phase 1: everything below also runs HERE ──────  │
└───────────┬─────────────────────────────────────────┘
            │ Phase 1: in-process · Phase 2: HTTP POST (SenML JSON) over Wi-Fi 5
            ▼
┌─────────────────────────────────────────────────────┐
│              Ingestion Agent                         │
│  • Schema validation (Pydantic)                     │
│  • Unit normalization                               │
│  • Outlier tagging                                  │
│  • SQLite time-series storage                       │
└───────────┬─────────────────────────────────────────┘
            │ KnowledgeChunk events
            ▼
┌─────────────────────────────────────────────────────┐
│           Knowledge Builder Agent                   │
│  • Text representation of readings                  │
│  • nomic-embed-text embeddings (768d)               │
│  • ChromaDB upsert with metadata                    │
│  • Sliding window eviction                          │
└───────────┬─────────────────────────────────────────┘
            │ store_updated events
            ▼
┌─────────────────────────────────────────────────────┐
│              Reasoner Agent                         │◄──── User queries
│  • Top-k retrieval (k=8)                            │      Agent queries
│  • Recency-weighted similarity                      │
│  • RAG-derived confidence (coverage × similarity    │
│    × recency × consistency — no LLM introspection)  │
│  • LLM generation (smollm2:135m via Ollama)         │
│  • Belief tracking (beliefs.jsonl)                  │
└───────┬───────────────────┬─────────────────────────┘
        │ low confidence    │ high confidence beliefs
        ▼                   ▼
┌───────────────┐    ┌─────────────────────┐
│ Explorer      │    │ Belief store        │
│ Agent         │    │ data/beliefs.jsonl  │
│               │    └─────────────────────┘
│ Hypothesis    │
│ generation    │
│ & scheduling  │
└───────┬───────┘
        │ labeled examples
        ▼
┌─────────────────────────────────────────────────────┐
│              Trainer Agent                          │
│  • LoRA fine-tuning (PEFT)                          │
│  • Eval & checkpoint promotion                      │
│  • Replay buffer for anti-forgetting                │
└─────────────────────────────────────────────────────┘
```

## Storage layout

```
data/
  chroma/              # ChromaDB vector store files
  timeseries.db        # SQLite time-series (upgrade to TimescaleDB)
  beliefs.jsonl        # current belief state
  hypothesis_queue.jsonl
  labeled_examples.jsonl
  training_runs.jsonl
  model_registry.json
checkpoints/
  current/             # promoted LoRA adapter
  20260605_143000/     # historical checkpoints (keep last 3)
```

## API endpoints (FastAPI)

| Method | Path | Description |
|---|---|---|
| POST | `/telemetry` | Ingest sensor reading(s) |
| GET | `/query?q=...` | RAG query to Reasoner |
| GET | `/beliefs` | Current belief state |
| GET | `/hypotheses` | Current hypothesis queue |
| POST | `/experiment/run` | Trigger next experiment |
| POST | `/train` | Kick off training run |
| GET | `/health` | System health + stats |

## Hardware (Arduino UNO Q reference build)

### Board specs

| Component | Detail |
|---|---|
| MCU | STM32U585, ARM Cortex-M33, 160 MHz, 2 MB Flash, 786 kB SRAM |
| MPU | Qualcomm Dragonwing QRB2210, quad-core Cortex-A53 @ 2.0 GHz, Adreno GPU |
| RAM (MPU) | 2 GB or 4 GB LPDDR4x |
| Storage (MPU) | 16 GB or 32 GB eMMC |
| Wi-Fi | Wi-Fi 5 (802.11ac) dual-band 2.4/5 GHz, onboard antenna (WCBN3536A module) |
| Bluetooth | Bluetooth 5.1, onboard antenna |
| USB | USB-C (power + host/device) |
| Form factor | Standard UNO shield-compatible footprint |
| MCU OS | Zephyr RTOS (Arduino Core) |
| MPU OS | Debian Linux (upstream support) |
| MCU↔MPU link | Arduino Bridge RPC over internal USB CDC |
| Onboard display | 12×8 monochrome LED matrix — MCU-owned, driven as a CPU/memory load gauge: the App's `python/led_gauge.py` packs the frame and the sketch's `set_matrix` RPC renders it (`src/ingestion/led_matrix.py` for the bench fallback; assumed parity with UNO R4 WiFi's matrix; confirm against your unit) |
| Expansion | Qwiic / Modulino connector, MIPI-CSI (2× camera), MIPI-DSI (display) |

> **WiFi transport only.** No Ethernet shield. All telemetry leaves the board over Wi-Fi from the MPU side.

```
Arduino UNO Q
├── STM32U585 MCU  (Cortex-M33, 160 MHz)  ← runs apps/iot_node/sketch/sketch.ino
│   ├── D4  ── DHT11 data pin  (10 kΩ pull-up to 3.3 V)
│   ├── A0  ── MQ-135 AOUT
│   └── D7  ── HC-SR501 OUT
└── QRB2210 MPU   (quad Cortex-A53, 2 GHz, 2–4 GB RAM)  ← Debian + apps/iot_node/python/main.py
    ├── RouterBridge RPC        ← read_* / set_matrix calls to the MCU
    ├── 12×8 LED matrix         ← led_gauge.py frame → set_matrix RPC: live CPU %/mem % gauge
    ├── WCBN3536A Wi-Fi 5       ← HTTP POST /telemetry (127.0.0.1 in Phase 1)
    └── Phase 1: also hosts the ingestion API, SQLite, ChromaDB,
        and smollm2:135m (Ollama) — the whole stack, on one board
```

| Sensor | Model | Measures | MCU Pin | Power |
|---|---|---|---|---|
| Temperature + humidity | DHT11 | °C, %RH | D4 | 3.3 V |
| Air quality (CO₂ proxy) | MQ-135 | ppm (uncalibrated) | A0 | 5 V |
| Motion | HC-SR501 PIR | bool | D7 | 5 V |

> **CO₂ accuracy note:** MQ-135 is an inexpensive metal-oxide sensor giving a relative air-quality proxy. Replace with an MH-Z19B (UART, pin D0/D1) for accurate CO₂ PPM — no changes to the bridge or pipeline are required.

## Event bus (internal)

Simple in-process event bus for the tiny phase; replace with NATS or Redis Streams for multi-process scale.

| Topic | Producer | Consumer |
|---|---|---|
| `knowledge_chunks` | Ingestion | Knowledge Builder |
| `store_updated` | Knowledge Builder | Reasoner (cache invalidation) |
| `low_confidence` | Reasoner | Explorer |
| `belief_invalidated` | Reasoner | Explorer |
| `labeled_examples` | Explorer | Trainer |
| `model_updated` | Trainer | Reasoner (model reload) |
