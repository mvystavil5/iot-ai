# TODO

Last updated: 2026-06-06

## Phase 1 — Local MVP (single machine, ~10 sensors)

### Ingestion
- [ ] `src/ingestion/pipeline.py` — main ingestion entry point (validate → normalize → store → emit chunk)
- [ ] `src/ingestion/normalizer.py` — unit conversion rules (°F→°C, psi→kPa, etc.)
- [ ] `src/ingestion/storage.py` — SQLite time-series writer (schema: sensor_id, timestamp, value, unit, outlier, tags)
- [ ] `src/ingestion/validator.py` — CLI wrapper: `python -m src.ingestion.validator --input <file>`
- [ ] `src/ingestion/simulator.py` — generate fake sensor readings for local testing
- [ ] `src/ingestion/mqtt_bridge.py` — subscribe to MQTT topic, forward to pipeline
- [ ] `tests/ingestion/test_pipeline.py`

### Knowledge
- [ ] `src/knowledge/embedder.py` — call nomic-embed-text via Ollama, return 768d vector
- [ ] `src/knowledge/store.py` — ChromaDB client wrapper (upsert, query, evict, stats)
- [ ] `src/knowledge/chunker.py` — single-reading chunks + 60s aggregate chunks for high-freq sensors
- [ ] `src/knowledge/event_chunk.py` — detect threshold crossings, create event chunks with higher weight
- [ ] `python -m src.knowledge.cli stats` — inspect store health
- [ ] `tests/knowledge/`

### Model / Reasoner
- [ ] `src/model/retriever.py` — top-k retrieval with recency weighting (`score *= exp(-age_h / decay)`)
- [ ] `src/model/llm.py` — thin wrapper: Ollama backend + Claude API fallback (reads `config/model.yaml`)
- [ ] `src/model/reasoner.py` — full RAG chain: enrich query → retrieve → build prompt → call LLM → parse confidence
- [ ] `src/model/beliefs.py` — read/write `data/beliefs.jsonl`, invalidation logic
- [ ] `src/model/cli.py` — `python -m src.model.cli "<query>" [--show-context] [--show-beliefs]`
- [ ] `tests/model/`

### Exploration
- [ ] `src/exploration/hypothesis_generator.py` — produce ranked hypotheses from low-confidence beliefs
- [ ] `src/exploration/scheduler.py` — `--list`, `--run-next`, `--verbose` CLI
- [ ] `src/exploration/experiments.py` — observation, alert, and simulation experiment runners
- [ ] `src/exploration/outcomes.py` — log results to `data/labeled_examples.jsonl`
- [ ] `tests/exploration/`

### Training & adapter sync

LoRA fine-tuning runs off-board on a separate training host (the QRB2210 MPU
has no CUDA-class GPU and its RAM is committed to inference) — see
`docs/architecture.md` § Training & adapter sync for the push/pull data-flow
diagram and `docs/installation.md` § 4.3 for deployment instructions.

#### Adapter sync (`src/model/adapter_sync.py`) — **done** (runs on the board)
- [x] Push: batch labeled examples to `{host}/training/examples`, gated by
      `training.sync.push_batch_size`, tracked via high-water mark in `data/sync_state.json`
- [x] Pull: poll `{host}/training/registry` every `training.sync.poll_interval_s`;
      download and atomically swap a newer adapter into `checkpoints/current/`
      (old version archived to `checkpoints/.previous/`)
- [x] CLI: `python -m src.model.adapter_sync [--once] [--debug]`
- [x] `tests/model/test_adapter_sync.py` — push batching/high-water-mark, version
      comparison, atomic swap, full pull flow against a mocked training host (`httpx.MockTransport`)

#### Training service (`src/model/training_service.py`) — **done** (runs on the training host)
- [x] `POST /training/examples` — append a pushed batch to `data/labeled_examples.jsonl`
- [x] `GET /training/registry` — serve `data/model_registry.json`
- [x] `GET /training/adapter/{version}` — stream a checkpoint directory as a tarball
- [x] Run via `uvicorn src.model.training_service:app --host 0.0.0.0 --port 8100`

#### Trainer orchestration (`src/model/trainer.py`) — **partially done** (runs on the training host)
- [x] `--check-readiness` — example count vs. `trigger_threshold`, current registry
      version/score, last run timestamp
- [x] `_format_pairs` / `_split` — instruction-tuning pair formatting, 80/10/10
      split stratified by sensor type
- [x] `_promote` / registry bookkeeping — promote only if `eval_score` beats the
      best on record, prune to `keep_last_n`, append `data/training_runs.jsonl`
- [x] `_fine_tune` — PEFT `LoraConfig`/`get_peft_model` + HF `Trainer`, wired to
      `config/model.yaml: lora`/`training` (lazy-imported; needs a real base
      model + GPU to exercise)
- [ ] `_evaluate` — currently `NotImplementedError`; needs `src/model/reasoner.py`
      (not yet implemented) to score a fine-tuned adapter against held-out test pairs
- [x] CLI: `python -m src.model.trainer --check-readiness` / `--run --verbose`
- [x] `tests/model/test_trainer.py` — readiness, formatting/splitting, promotion/pruning, run-guard

#### Registry seed
- [x] `data/model_registry.json` — initial `{"current_version": null, "checkpoints": []}`

### API
- [ ] Wire `POST /telemetry` → ingestion pipeline
- [ ] Wire `GET /query` → reasoner
- [ ] Wire `GET /beliefs` → beliefs store
- [ ] Wire `GET /hypotheses` → hypothesis queue
- [ ] Wire `POST /experiment/run` → explorer scheduler
- [ ] Wire `POST /train` → trainer
- [ ] `tests/api/`

### Internal event bus
- [ ] `src/events.py` — simple in-process pub/sub (topics: knowledge_chunks, store_updated, low_confidence, belief_invalidated, labeled_examples, model_updated)

### Hardware — Arduino UNO Q

**Board:** Arduino UNO Q — STM32U585 MCU (Cortex-M33, 160 MHz, 2 MB Flash, 786 kB SRAM) +
Qualcomm Dragonwing QRB2210 MPU (quad Cortex-A53, 2 GHz, 2–4 GB LPDDR4x, 16–32 GB eMMC).
Wi-Fi 5 dual-band 2.4/5 GHz (WCBN3536A, onboard antenna). Onboard 12×8 LED matrix.
**No Ethernet — WiFi only.**
MCU runs Arduino sketch (Zephyr/Arduino Core); MPU runs Debian Linux.
MCU→MPU link: Arduino Bridge RPC over internal USB CDC.

**Deployment target: Phase 1 runs the whole stack on this board** (MPU hosts
ingestion API + ChromaDB + `smollm2:135m`/Ollama, sized to fit its 2–4 GB RAM —
see `config/model.yaml`). Phase 2 migrates the knowledge/reasoning stack to a
separate server (see `docs/installation.md` § Deployment for the migration
checklist); the UNO Q then keeps doing sensor I/O + LED matrix monitoring and
points `wifi_bridge.py --server` at the new host.

#### MCU firmware (`firmware/arduino_uno_q/`)
- [ ] `sensor_node.ino` — read DHT22 (D4), MQ-135 (A0), HC-SR501 (D7) every 30 s + PIR interrupt; serialize to newline-delimited SenML JSON; send to MPU via `Bridge.put()` RPC call
- [ ] `config.h` — pin map, sensor IDs, sample interval (default 30 s), Bridge key names
- [ ] Library deps to document: `ArduinoJson`, `DHT sensor library`, `Arduino Bridge`

#### MPU bridge (`src/ingestion/wifi_bridge.py`)
- [ ] Read sensor frames from MCU via `Bridge.get()` (Arduino Bridge Python client on Debian)
- [ ] Stamp UTC timestamps (MCU has no RTC)
- [ ] Validate sensor IDs against `config/sensors.yaml`
- [ ] HTTP POST SenML JSON to `POST /telemetry` over Wi-Fi 5 (Phase 1: `127.0.0.1`, same board; Phase 2: separate server host)
- [ ] Auto-retry with exponential backoff on network failure
- [ ] CLI: `python wifi_bridge.py --server http://<host>:8000 [--dry-run] [--verbose]`

#### System load indicator (`src/ingestion/led_matrix.py`) — **done**
- [x] Render CPU %/memory % (via `psutil`) as bottom-up bar graphs on the onboard 12×8 LED matrix (left = CPU, right = memory)
- [x] Vendor-binding adapter with simulated (ASCII-log) fallback for dev machines without the physical matrix
- [x] CLI: `python -m src.ingestion.led_matrix [--interval 2.0] [--debug]`
- [x] `tests/ingestion/test_led_matrix.py` — frame-rendering unit tests

#### Config & tests
- [ ] `config/sensors.yaml` — entries already updated to `interface: wifi`; verify `wifi_standard: 802.11ac` field is consumed by `src/config.py`
- [ ] `tests/ingestion/test_wifi_bridge.py` — mock Bridge client + mock HTTP server; verify timestamp injection, retry logic, SenML schema

### Infra / DX
- [ ] `src/ingestion/__init__.py`, `src/knowledge/__init__.py`, etc. — package init files
- [ ] `Makefile` — `make dev`, `make test`, `make simulate`, `make query Q="..."`
- [ ] `.env.example` — document all env vars (OLLAMA_HOST, LOG_LEVEL, etc.)
- [ ] `pyproject.toml` — replace `requirements.txt` with `uv`-compatible pyproject
- [ ] Set up `ollama pull nomic-embed-text && ollama pull phi3:mini` in quickstart docs
- [ ] `data/` directory with `.gitkeep` files (exclude `data/*.db`, `data/chroma/` in `.gitignore`)
- [ ] `.gitignore`

---

## Phase 2 — Multi-room / multi-building

- [ ] Swap SQLite → TimescaleDB (update `src/ingestion/storage.py`, add continuous aggregates)
- [ ] Swap ChromaDB → Qdrant (update `config/model.yaml`, `src/knowledge/store.py`)
- [ ] MQTT broker setup (Mosquitto config + `src/ingestion/mqtt_bridge.py`)
- [ ] Multiple Reasoner instances (one per room), shared Explorer
- [ ] Cross-sensor causal graph (L3 belief layer in `src/model/beliefs.py`)
- [ ] Dashboard UI (simple HTML + `/health`, `/beliefs` polling)

---

## Phase 3 — Federated

- [ ] Per-building local model + vector store
- [ ] Meta-reasoner: aggregates cross-building belief summaries (no raw readings shared)
- [ ] FedAvg LoRA adapter aggregation
- [ ] Privacy: belief summaries only, no raw sensor values cross-boundary

---

## Phase 4 — Foundation model

- [ ] Fine-tune a 7B–13B base on aggregate IoT corpus
- [ ] DeepSpeed ZeRO-3 training config
- [ ] Local agents use foundation model as prior, update via in-context learning
- [ ] Evaluate: Modal / RunPod for auto-scaling GPU training

---

## Open research questions (no owner yet)

- [ ] Multi-modal embedding: time-series + camera + text annotations in one retrieval space
- [ ] Temporal token representation: table vs. NL summary vs. dedicated time-series tokens fed to LLM
- [ ] Anomaly-as-curiosity: use reconstruction error to trigger Explorer instead of confidence thresholds
- [ ] Separate regression head for future sensor value prediction as a training signal
- [ ] Privacy-preserving retrieval: homomorphic encryption or secure aggregation for federated case
