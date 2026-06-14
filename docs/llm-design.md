# LLM Design: IoT World-Model

## The core problem

Standard LLMs are trained offline on static corpora. IoT sensor data is continuous, high-frequency, and context-dependent. A model that truly "understands" an environment needs:

1. **Continuous memory** — yesterday's reading matters for today's anomaly detection
2. **Causal reasoning** — "CO2 rises when people are home" is more useful than raw readings
3. **Active curiosity** — the model should seek data that reduces its uncertainty, not just passively record
4. **Updatable priors** — beliefs should harden into weights over time, not just live in retrieval

This document describes how the architecture achieves each of these.

---

## Continuous learning: the RAG-first strategy

### Why RAG before fine-tuning?

| Property | RAG | Fine-tuning |
|---|---|---|
| Update latency | ~ms (write to vector store) | hours (GPU training) |
| Forgetting risk | None (old chunks remain) | Catastrophic forgetting possible |
| Inference cost | Higher (retrieval + generation) | Lower (no retrieval) |
| Knowledge capacity | Limited by vector store size | Baked into weights |
| Privacy | Data stays in store (can delete) | Data is in weights (hard to remove) |

**Strategy**: Use RAG as the primary memory. When enough labeled, high-confidence examples accumulate (confirmed hypotheses), bake them into the model via LoRA. This gives fast updates with gradually improving priors.

### RAG implementation detail

The retrieval query is enriched before lookup:

```
raw_query: "Is the living room warm?"
enriched: "living room temperature trend current status [time: 14:30] [recent_context: last 2h]"
```

Time-aware retrieval uses **recency weighting**: similarity score is multiplied by `exp(-age_hours / decay_constant)` so recent readings rank higher for current-state questions, but older readings still surface for trend questions.

### Confidence derivation (RAG-derived, no LLM introspection)

LLMs cannot reliably self-report confidence — they tend to be overconfident and their stated certainty is poorly calibrated. Confidence is therefore computed **entirely from retrieval metadata** in `src/model/rag_confidence.py`, before the LLM is called. Four components are combined as a weighted sum (weights in `config/model.yaml rag.confidence_weights`):

| Component | Formula | What it captures |
|---|---|---|
| **Coverage** | `min(n_chunks / top_k, 1.0)` | Sparse retrieval signals thin knowledge |
| **Similarity** | mean cosine sim of chunks above `min_similarity` | Low similarity = vector store is guessing |
| **Recency** | `mean(exp(-age_h / recency_decay_hours))` | Stale data matters less for current-state queries |
| **Consistency** | `mean(exp(-cv))` per sensor stream | High value spread signals noise or fast-changing state |

```python
confidence = (
    0.25 * coverage
  + 0.40 * similarity   # heaviest weight — relevance is the strongest signal
  + 0.20 * recency
  + 0.15 * consistency
)
```

The resulting score is passed to the belief tracker and to the Explorer's escalation logic. The LLM prompt does **not** ask the model to state a confidence number.

---

## The world model: from readings to causal beliefs

### Three levels of representation

```
Level 3: Causal graph     "high CO2 → people home → temperature rises"
              ↑ Explorer infers these
Level 2: Temporal pattern  "CO2 peaks at 08:00 and 18:00 on weekdays"
              ↑ Knowledge Builder aggregates these
Level 1: Raw reading       "CO2: 850 ppm at 2026-06-05T08:03:00Z"
              ↑ Ingestion stores these
```

The model operates at all three levels simultaneously. RAG retrieves from L1 and L2; fine-tuned weights encode L2 patterns and some L3 causal links.

### Belief representation

A belief is a structured claim with uncertainty:

```json
{
  "claim": "Temperature in living_room correlates with CO2 level",
  "direction": "positive",
  "strength": 0.73,
  "confidence": 0.81,          // RAG-derived: coverage × similarity × recency × consistency
  "evidence_chunks": ["chunk_0041", "chunk_0089", "chunk_0142"],
  "first_observed": "2026-06-05T10:00:00Z",
  "last_updated": "2026-06-05T14:30:00Z",
  "durable": false
}
```

When confidence crosses 0.9 and the belief has been held for > 7 days, it is promoted to `durable: true` and prioritized for the next fine-tuning run.

---

## Active exploration: curiosity-driven learning

### Information gain maximization

The Explorer ranks hypotheses by expected information gain (EIG):

```
EIG(h) = H(beliefs) - E[H(beliefs | outcome(h))]
```

In practice, estimated as:
- Belief entropy (spread of confidence scores across related beliefs)
- Minus expected posterior entropy if the experiment confirms/refutes h
- Divided by experiment cost (time, sensor API calls, compute)

### Hypothesis types (ascending complexity)

1. **Correlation**: "Does X co-vary with Y over the last 24h?"
2. **Threshold event**: "Does Y exceed Z when X > threshold?"
3. **Lag relationship**: "Does X at time T predict Y at time T+lag?"
4. **Causal intervention**: "If I change X (via actuation), does Y respond?"
5. **Multi-sensor**: "Do sensors A, B, C jointly predict outcome D?"

The system starts with type 1 and escalates as its causal graph fills in.

---

## Continuous training: LoRA on a tiny base

### Why LoRA

Full fine-tuning of even a 135M model requires significant RAM and time. LoRA freezes base weights and trains two low-rank matrices per attention layer:

```
W' = W + BA   where B ∈ R^{d×r}, A ∈ R^{r×k}, r << d
```

With rank=8, this adds ~0.1% of parameters vs. the base model. Training on 50 examples takes ~2 minutes on a laptop CPU.

### Training data format

Each fine-tuning example is an instruction tuple derived from a confirmed hypothesis:

```json
{
  "instruction": "Given the sensor readings, what is the current occupancy state of the living room?",
  "input": "CO2: 1240 ppm (rising, +80 ppm/h). Motion: active 8 min ago. Temperature: 22.8°C (stable).",
  "output": "The living room is likely occupied (confidence: 0.87). CO2 is elevated and rising, consistent with 1-2 people present. Motion detected recently confirms this.",
  "label": "occupied",
  "source_hypothesis": "hyp_0023",
  "outcome": "confirmed"
}
```

### Continual learning without catastrophic forgetting

To prevent the model from forgetting old knowledge when fine-tuning on new examples:

1. **Replay buffer**: include 20% of examples from previous training runs in each new run
2. **EWC (Elastic Weight Consolidation)**: penalize changes to weights that were important for previous tasks (implemented when the model scales beyond 1B params)
3. **LoRA checkpoint chaining**: each new LoRA adapter is initialized from the previous one, not from the base model — preserving learned priors

---

## Scaling path

### Phase 1: Local (current)
- Arduino UNO Q: STM32U585 co-processor reads sensors; QRB2210 (quad Cortex-A53, 4 GB RAM) runs the full Python stack
- `smollm2:135m` via Ollama — 135 M params, ~90 MB at 4-bit, fits comfortably alongside OS and ChromaDB
- Sensor node ships as the `apps/iot_node/` Arduino App Lab app: the MCU sketch exposes `read_*` RouterBridge RPCs and the MPU half (`python/main.py`) calls them every 30 s (plus immediately on PIR state change) and POSTs to the ingestion API. A USB-serial firmware + `src/ingestion/serial_bridge.py` remain as a bench-only fallback.
- 1–10 sensors, one user; ChromaDB in-process, SQLite time-series

### Phase 2: Multi-room / multi-building
- Qdrant replaces ChromaDB (multi-node, filtered search)
- TimescaleDB replaces SQLite (time-series queries, continuous aggregates)
- Multiple Reasoner instances per room, shared Explorer
- MQTT broker (Mosquitto) replaces HTTP polling

### Phase 3: Federated
- Each building has a local model + vector store
- A global "meta-reasoner" aggregates cross-building beliefs (with privacy: only belief summaries, not raw readings, are shared)
- Federated LoRA: local adapters are aggregated via FedAvg into a global adapter

### Phase 4: Foundation model
- A larger base model (7B–13B) fine-tuned on the aggregate IoT corpus
- The local agents use the foundation model for priors and update via in-context learning for local specifics
- Training infrastructure: DeepSpeed ZeRO-3 on a small GPU cluster

---

## Open questions / research directions

1. **Sensor modality fusion**: how to jointly embed time-series, images (camera sensors), and text (user annotations) into one retrieval space
2. **Temporal attention**: should the LLM see readings as a table, a natural-language summary, or a specialized time-series token sequence?
3. **Anomaly as exploration trigger**: can surprise (high reconstruction error on new readings) drive hypothesis generation better than confidence thresholds?
4. **World model vs. prediction head**: should the model predict future sensor values (regression head) as a training signal, separate from the generative LLM path?
5. **Privacy-preserving retrieval**: if sensor data is private, can homomorphic encryption or secure aggregation protect it during retrieval?
