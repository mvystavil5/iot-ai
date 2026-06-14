# TODO

Last updated: 2026-06-13 — Sensor node repackaged as the `apps/iot_node/` Arduino App Lab app (RouterBridge MCU↔MPU transport, MPU loop POSTs `/telemetry`, LED gauge driven over the `set_matrix` RPC). This is now the **primary** Phase 1 sensor path; the USB-serial firmware + `serial_bridge.py` + `led_matrix.py` trio is demoted to a bench-only fallback, and the never-built standalone `wifi_bridge.py` is superseded by `apps/iot_node/python/main.py`. (Prior: 2026-06-08 — Phase 1 ingestion/knowledge/model/exploration/security/wellness stacks + API wiring implemented and tested.)

## Phase 1 — Local MVP (single machine, ~10 sensors)

### Ingestion
- [x] `src/ingestion/pipeline.py` — main ingestion entry point (validate → normalize → store → emit chunk via event bus)
- [x] `src/ingestion/normalizer.py` — unit conversion rules (canonical units + outlier flag against `expected_range`)
- [x] `src/ingestion/storage.py` — SQLite time-series writer (schema: sensor_id, timestamp, value, unit, outlier, tags; composite PK dedupes silently)
- [x] `src/ingestion/validator.py` — CLI wrapper: `python -m src.ingestion.validator --input <file> [--ingest]`
- [x] `src/ingestion/simulator.py` — one-shot + continuous random-walk simulation, `--pipeline` bypass flag
- [ ] `src/ingestion/mqtt_bridge.py` — subscribe to MQTT topic, forward to pipeline (Phase 1 uses HTTP POST only — see Hardware § Arduino App Lab app)
- [x] `tests/ingestion/test_pipeline.py` — 13 tests covering validate/normalize/store/emit + outlier/dedup paths

### Knowledge
- [x] `src/knowledge/embedder.py` — call nomic-embed-text via Ollama (lazy import), `embed`/`embed_one`
- [x] `src/knowledge/store.py` — ChromaDB client wrapper (lazy import; upsert, query, evict_to_limit, stats; JSON-encodes `tags` for Chroma's scalar-only metadata)
- [x] `src/knowledge/chunker.py` — single-reading chunks + 60s aggregate chunks (min/max/mean/stddev/trend) for high-freq sensors
- [x] `src/knowledge/event_chunk.py` — detect `expected_range` threshold crossings, create event chunks tagged `weight=2.0`
- [x] `src/knowledge/builder.py` — orchestrator wiring `knowledge_chunks` events → embedder → store → `store_updated` (new; not originally listed but the natural integration point)
- [x] `python -m src.knowledge.cli stats` — inspect store health
- [x] `tests/knowledge/` — 22 tests across chunker, event_chunk, embedder, store, builder

### Model / Reasoner
- [x] `src/model/rag_confidence.py` — RAG-derived confidence scoring (coverage × similarity × recency × consistency, no LLM introspection); `tests/model/test_rag_confidence.py`
- [x] `src/model/retriever.py` — top-k retrieval with recency weighting (`score = similarity * exp(-age_h / decay_hours)`)
- [x] `src/model/llm.py` — thin wrapper: Ollama backend + Claude API backend (lazy imports, reads `config/model.yaml: llm`)
- [x] `src/model/reasoner.py` — full RAG chain: retrieve → `compute_rag_confidence` (canonical confidence source — smollm2:135m self-reports aren't trusted) → build prompt → call LLM (with context-summary fallback) → record belief → publish `low_confidence`
- [x] `src/model/beliefs.py` — read/write `data/beliefs.jsonl`; invalidation when same `query_hash` + different answer + confidence > `invalidation_threshold` → `belief_invalidated` event
- [x] `src/model/cli.py` — `python -m src.model.cli "<query>" [--show-context] [--show-beliefs]`
- [x] `tests/model/test_retriever.py`, `test_llm.py`, `test_beliefs.py`, `test_reasoner.py` — 51 tests total for the model layer (rag_confidence/adapter_sync/trainer covered separately, see above/below)

### Exploration
- [x] `src/exploration/hypothesis_generator.py` — rank candidate sensor-relationship hypotheses from low-confidence/invalidated beliefs by `(information_gain × feasibility) / cost`; queues to `data/hypothesis_queue.jsonl`
- [x] `src/exploration/scheduler.py` — `--list`, `--run-next [--verbose]` CLI; dispatches the top-ranked pending hypothesis to its experiment runner and marks it done
- [x] `src/exploration/experiments.py` — observation (trend-correlation over recent history), alert (expected_range breach check), and simulation (synthetic random-walk trend check) experiment runners — Phase 1 has no actuation hardware, so "active query" has no runner yet
- [x] `src/exploration/outcomes.py` — log results to `data/experiment_outcomes.jsonl`; forwards non-inconclusive outcomes as labeled examples to `training.labeled_examples_path` + `labeled_examples` event
- [x] `tests/exploration/` — 22 tests across hypothesis_generator, experiments, outcomes, scheduler

### Security / occupancy-baseline anomaly detection

Ambient-sensor intrusion detection, scoped deliberately to stay out of
biometric-/identification-surveillance territory: no cameras/microphones, no
faceprints or voiceprints, no per-person profiles, no inference of protected
attributes (sex/age/health). The system learns exactly *one* aggregate
**occupancy baseline** — what normal activity timing/level/session-length
looks like for the space (derived from the existing PIR/DHT11/CO2 sensors)
— and compares live activity against it with an honestly-reported similarity
score: "does this look like the usual pattern here", never "who is this
person". A reset is a hard purge of the baseline + alert history.

(Renamed and redesigned from the earlier "identity / opt-in person
registration" plan — that design matched live activity against named,
per-person profiles, which on reflection was an identification system with
extra steps. This redesign keeps the same sensor-aggregation math but drops
named profiles entirely in favor of one space-level baseline + anomaly
flagging, which is what an intrusion-detection use case actually needs.)
- [x] `src/security/signature.py` — pure aggregation: `build_signature` derives `presence_ratio`, `hourly_activity` (24-bucket histogram), `mean_session_length_min` from a chronological motion-reading window
- [x] `src/security/store.py` — JSONL I/O for `data/occupancy_baseline.jsonl` / `data/occupancy_alerts.jsonl`, mirroring `beliefs.py`'s read/append/rewrite helpers
- [x] `src/security/detector.py` — `score_similarity` (config-driven weighted similarity, mirrors `compute_rag_confidence`'s pure-scoring shape), `detect` (`expected` / `anomalous` / `no_baseline` against `security.anomaly_similarity_threshold`), `run_live_check` (I/O glue + `occupancy_checked` / `occupancy_anomaly_detected` events); CLI `--check` / `--watch [--interval N]`
- [x] `src/security/learner.py` — `learn_baseline` (build + persist a baseline over an observed calibration window, `occupancy_baseline_learned` event), `reset_baseline` (**hard delete** — purges the baseline AND every alert record, `occupancy_baseline_reset` event), `get_baseline`; CLI `--learn --duration N` / `--reset` / `--show`
- [x] Wired `POST /security/baseline/learn`, `POST /security/baseline/reset`, `GET /security/baseline`, `GET /security/check` into `src/api/main.py`
- [x] Privacy guardrails made structural, not just documented: `data/occupancy_*.jsonl` covered by `.gitignore`'s `data/*.jsonl`; `adapter_sync.py`'s push path is hardcoded to `training.labeled_examples_path` only (cannot glob occupancy data to the external training host)
- [x] `tests/security/` — tests across signature, detector, learner (incl. the hard-delete purge guarantee and event-bus publish assertions)

### Wellness / personal activity self-experiment

A different *kind* of opt-in than the security module's: this one is run by
one person, on themselves, for themselves — a personal experiment in whether
the board's existing motion sensor can say anything informative about
movement vs. stillness over time (sedentary minutes, longest still streak,
movement sessions, and how those shift week to week). It deliberately does
**not** build a profile to compare anyone *against* — there is no baseline
"of a person" here, only a private diary of one's own days that the person
who generated it owns outright, including the right to wipe it completely.
It never diagnoses, never claims medical authority, and never leaves the
board — same structural guardrails as the security module's occupancy data,
applied to data that is, if anything, more personal.

- [x] `src/wellness/metrics.py` — pure aggregation: `build_daily_summary` derives `active_minutes` / `sedentary_minutes` (always summing to the window length), `longest_sedentary_streak_min`, `activity_sessions`, `mean_session_length_min` from a chronological motion-reading window plus its [start, end) bounds
- [x] `src/wellness/store.py` — JSONL I/O for `data/wellness_daily.jsonl` / `data/wellness_trends.jsonl`, mirroring `beliefs.py`'s / `security/store.py`'s read/append/rewrite helpers
- [x] `src/wellness/tracker.py` — `record_day` (build + persist one UTC calendar day's summary, `wellness_day_recorded` event), `get_recent_days`, `reset_history` (**hard delete** — purges every recorded day AND every trend check, `wellness_history_reset` event); CLI `--record [--day YYYY-MM-DD]` / `--reset` / `--show [--days N]`
- [x] `src/wellness/trends.py` — `score_trend` (signed average-per-day deltas between a recent window and the window before it, mirrors `detector.score_similarity`'s pure-scoring shape), `detect_trend` (`stable` / `more_sedentary` / `more_active` / `insufficient_data` against `wellness.trend_alert_minutes`), `run_trend_check` (I/O glue + `wellness_trend_checked` / `wellness_risk_flagged` events); CLI `--check`
- [x] Wired `POST /wellness/day/record`, `GET /wellness/days`, `GET /wellness/trend`, `POST /wellness/reset` into `src/api/main.py`
- [x] Privacy guardrails made structural, matching the security module: `data/wellness_*.jsonl` covered by `.gitignore`'s `data/*.jsonl`; `adapter_sync.py`'s push path is hardcoded to `training.labeled_examples_path` only (cannot glob this person's activity history to the external training host)
- [x] `tests/wellness/` — tests across metrics, tracker, trends (incl. the hard-delete purge guarantee, the active+sedentary-minutes-sum-to-window-length invariant, and event-bus publish assertions)

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
- [x] Wire `POST /telemetry` → ingestion pipeline
- [x] Wire `GET /query` → reasoner
- [x] Wire `GET /beliefs` → beliefs store
- [x] Wire `GET /hypotheses` → hypothesis queue (`scheduler.list_queue`)
- [x] Wire `POST /experiment/run` → explorer scheduler (`scheduler.run_next`)
- [ ] Wire `POST /train` → trainer
- [ ] `tests/api/` — FastAPI `TestClient` coverage for the wired routes (none yet — routes verified only via direct unit tests of the agents/services they delegate to + an import-time route-listing smoke check)

### Internal event bus
- [x] `src/events.py` — `EventBus` in-process pub/sub singleton (`bus`); `subscribe`/`unsubscribe`/`publish`/`clear`, handlers run inline with exceptions logged not raised (topics in use: knowledge_chunks, store_updated, low_confidence, belief_invalidated, labeled_examples; model_updated reserved for Trainer)

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
points the App's `python/main.py` `API_BASE` at the new host.

#### Arduino App Lab app (`apps/iot_node/`) — **done** (primary Phase 1 sensor path)
Packages the sensor node as an App Lab app: App Lab builds + flashes the MCU
sketch and runs the MPU Python half together over the RouterBridge RPC. This
replaces the bench-only USB-serial trio for production and is the realization
of the MCU↔MPU bridge transport that the standalone `wifi_bridge.py` was going
to provide.
- [x] `sketch/sketch.ino` — `Bridge.provide` handlers (`read_temp`/`read_humidity`/`read_co2`/`read_motion`) + `set_matrix`; owns the LED matrix via `ArduinoLEDMatrix::loadFrame`
- [x] `python/main.py` — `Bridge.call`s the handlers every 30 s (+ immediate post on PIR state change), timestamps, `POST /telemetry` (127.0.0.1 Phase 1; edit `API_BASE` for Phase 2), pushes LED frames every 2 s
- [x] `python/led_gauge.py` — pure-python CPU/mem → 3×uint32 frame packer (unit-testable; mirrors `led_matrix.py` semantics)
- [x] `app.yaml` / `sketch.yaml` — App Lab manifest + FQBN `arduino:zephyr:unoq` + library pins
- [x] `apps/iot_node/README.md` — deploy (App Lab GUI / `arduino-app-cli`), verify, and on-hardware confirmation notes
- [ ] On real hardware: confirm RouterBridge symbol names (`Bridge.begin/provide/call`), library versions, return-value RPC support, and the LED-matrix frame bit order (see README § Notes)
- [ ] `tests/apps/` (or reuse) — unit tests for `led_gauge.py` frame packing (none yet)

#### MCU firmware (`firmware/sensors/sensors.ino`) — **done** (USB-serial bench fallback)
- [x] Read DHT11 (D4), MQ-135 (A0), HC-SR501 (D7) every 30 s + immediate send on PIR state change;
      serialize to newline-delimited JSON arrays over USB CDC serial (pin map inlined as `constexpr`s — no separate `config.h`)
- [x] Library deps documented in the file header: `DHT sensor library` + `Adafruit Unified Sensor`, `ArduinoJson` v6
- [x] Bridge-RPC transport now lives in the App Lab app above (`apps/iot_node/sketch/sketch.ino`); this serial sketch stays as the USB-tethered bench fallback

#### MPU bridges (`src/ingestion/`)
- [x] `serial_bridge.py` — **done**, tethered/bench variant: reads newline-delimited JSON from the MCU over
      USB CDC serial (`pyserial`), stamps UTC timestamps, validates sensor IDs against `config/sensors.yaml`,
      auto-reconnects on `SerialException`, posts to `POST /telemetry`. CLI:
      `python -m src.ingestion.serial_bridge [--port /dev/ttyACM0] [--baud 115200] [--api http://127.0.0.1:8000] [--debug]`
- [x] ~~`wifi_bridge.py`~~ — **superseded by the App Lab app**: the MCU-Bridge-RPC + `POST /telemetry`
      role this standalone script was going to fill is now done by `apps/iot_node/python/main.py`
      (RouterBridge `Bridge.call`, UTC timestamps, sensor-ID validation, POST to the on-board API).
      No separate `wifi_bridge.py` will be built; Phase 2's separate-server retarget is just editing `API_BASE`.
- [ ] `tests/ingestion/test_serial_bridge.py` — mock serial port + mock HTTP server; verify timestamp injection, reconnect logic, JSON schema

#### System load indicator (`src/ingestion/led_matrix.py`) — **done**
- [x] Render CPU %/memory % (via `psutil`) as bottom-up bar graphs on the onboard 12×8 LED matrix (left = CPU, right = memory)
- [x] Vendor-binding adapter with simulated (ASCII-log) fallback for dev machines without the physical matrix
- [x] CLI: `python -m src.ingestion.led_matrix [--interval 2.0] [--debug]`
- [x] `tests/ingestion/test_led_matrix.py` — frame-rendering unit tests

#### Config & tests
- [ ] `config/sensors.yaml` — entries marked `interface: wifi`; the App's `python/main.py` posts over HTTP regardless, so confirm whether the `interface`/`wifi_standard` fields are still consumed by anything (`src/config.py` / `serial_bridge.py`) or should be pruned now that `wifi_bridge.py` is gone
- [ ] `tests/apps/test_led_gauge.py` — unit-test `led_gauge.py` frame packing (render + pack + word layout); the bridge-RPC path itself needs the board to exercise

### Infra / DX
- [x] `src/ingestion/__init__.py`, `src/model/__init__.py`, `src/api/__init__.py`, `src/knowledge/__init__.py`, `src/exploration/__init__.py` — present
- [ ] `Makefile` — `make dev`, `make test`, `make simulate`, `make query Q="..."`
- [ ] `.env.example` — document all env vars (OLLAMA_HOST, LOG_LEVEL, etc.)
- [ ] `pyproject.toml` — replace `requirements.txt` with `uv`-compatible pyproject
- [ ] Set up `ollama pull nomic-embed-text && ollama pull phi3:mini` in quickstart docs
- [ ] `data/` directory — `model_registry.json` is seeded; `beliefs.jsonl`, `hypothesis_queue.jsonl`, `labeled_examples.jsonl`, `experiment_outcomes.jsonl`, `timeseries.db`, `chroma/` are all created on first write by their owning modules and excluded via `.gitignore` — still no `training_runs.jsonl` seed/`.gitkeep`
- [x] `.gitignore` — present, covers `data/chroma/`, `data/timeseries.db`, `data/*.jsonl`, `data/sync_state.json`, `checkpoints/`

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
