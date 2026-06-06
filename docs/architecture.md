# System Architecture

## Data flow

```
┌─────────────────────────────────────────────────────┐
│                   IoT Sensors                        │
│  DHT22 (D4)    MQ-135 (A0)    HC-SR501 PIR (D7)     │
└───────────┬─────────────────────────────────────────┘
            │ GPIO — analog / digital
            ▼
┌─────────────────────────────────────────────────────┐
│   STM32U585 MCU  (Cortex-M33, 160 MHz)  on UNO Q   │
│  • firmware/arduino_uno_q/sensor_node.ino           │
│  • Reads all sensors every 30 s                     │
│  • Immediate send on PIR state change               │
│  • Sends newline-delimited SenML JSON via RPC       │
│    (Arduino Bridge library → internal USB CDC)      │
└───────────┬─────────────────────────────────────────┘
            │ Arduino Bridge RPC (internal USB CDC, on-board)
            ▼
┌─────────────────────────────────────────────────────┐
│  QRB2210 MPU  (quad Cortex-A53, 2 GHz)  on UNO Q   │
│  Running: Debian Linux + src/ingestion/wifi_bridge.py│
│  • Receives frames from MCU via Bridge library      │
│  • Adds UTC timestamps (MCU has no RTC)             │
│  • Validates sensor IDs against config/sensors.yaml  │
│  • POST /telemetry over Wi-Fi 5 (WCBN3536A module)  │
└───────────┬─────────────────────────────────────────┘
            │ HTTP POST (SenML JSON) over Wi-Fi 5 2.4/5 GHz
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
| Expansion | Qwiic / Modulino connector, MIPI-CSI (2× camera), MIPI-DSI (display) |

> **WiFi transport only.** No Ethernet shield. All telemetry leaves the board over Wi-Fi from the MPU side.

```
Arduino UNO Q
├── STM32U585 MCU  (Cortex-M33, 160 MHz)  ← runs sensor_node.ino
│   ├── D4  ── DHT22 data pin  (10 kΩ pull-up to 3.3 V)
│   ├── A0  ── MQ-135 AOUT
│   └── D7  ── HC-SR501 OUT
└── QRB2210 MPU   (quad Cortex-A53, 2 GHz, 2–4 GB RAM)  ← Debian + wifi_bridge.py
    ├── Arduino Bridge RPC  ← frames from MCU
    └── WCBN3536A Wi-Fi 5   ← HTTP POST to ingestion server
```

| Sensor | Model | Measures | MCU Pin | Power |
|---|---|---|---|---|
| Temperature + humidity | DHT22 | °C, %RH | D4 | 3.3 V |
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
