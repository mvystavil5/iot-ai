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
│         STM32U585 co-processor  (Cortex-M33)         │
│  • firmware/sensors/sensors.ino (Arduino sketch)    │
│  • Reads all sensors every 30 s                     │
│  • Immediate send on PIR state change               │
│  • Sends newline-delimited JSON over USB CDC serial  │
└───────────┬─────────────────────────────────────────┘
            │ USB CDC serial  /dev/ttyACM0  @ 115200 baud
            ▼
┌─────────────────────────────────────────────────────┐
│      Serial Bridge  (src/ingestion/serial_bridge.py) │
│  • Adds UTC timestamps (MCU has no RTC)             │
│  • Validates sensor IDs against config/sensors.yaml  │
│  • Reconnects automatically on USB unplug           │
│  • POST /telemetry for each reading                 │
└───────────┬─────────────────────────────────────────┘
            │ HTTP POST / MQTT (SenML JSON)
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

```
Arduino UNO Q
├── STM32U585  (Cortex-M33, 160 MHz)  ← runs sensors.ino
│   ├── D4  ── DHT22 data pin  (10 kΩ pull-up to 3.3 V)
│   ├── A0  ── MQ-135 AOUT
│   └── D7  ── HC-SR501 OUT
└── QRB2210   (quad Cortex-A53, 2 GHz, 4 GB RAM)  ← runs Debian + Python stack
    └── /dev/ttyACM0  ← USB CDC from STM32
```

| Sensor | Model | Measures | Pin | Power |
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
