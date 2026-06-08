# Building a World-Aware AI: How IoT Sensors Can Teach a Language Model About Reality

**A Technical White Paper**
*IoT World-Model Project — v0.1, June 2026*

---

## Abstract

Standard AI language models are trained once on text from the internet and then frozen — they never learn anything new after training ends. This paper describes a different approach: a small AI system that continuously learns about the physical world by reading data from IoT (Internet of Things) sensors, like thermometers, motion detectors, and air quality monitors. The system builds up a living memory of its environment, forms hypotheses about how the world works, runs experiments to test those hypotheses, and gradually improves its own reasoning over time. It also includes an optional, strictly consent-based module that lets individuals choose to be recognized by their own daily routines — never by their face, voice, or any other biometric trait. We describe the architecture, each component's role, the learning methods used, the privacy choices behind the identity capability, and how the system can grow from a single laptop to a building-wide network.

---

## 1. Introduction: Why Should an AI Care About Temperature?

Imagine you walk into a room and someone asks you, "Is it stuffy in here?" You don't look up the answer in a book — you use your senses, your memory of what "stuffy" feels like, and your knowledge of things like CO2 levels and ventilation. You have a **world model**: a mental representation of how physical things relate to each other.

Today's AI language models (like the ones that power chatbots) are very good at answering questions about text but they have no awareness of the physical world. They cannot tell you what the temperature is right now, whether a room is occupied, or why the CO2 level is rising. They have no memory of yesterday's readings, and they cannot learn from today's.

This project builds a system that gives an AI exactly that kind of grounded, physical awareness — by connecting it to cheap, widely available IoT sensors and teaching it to reason about what those sensors mean.

### What are IoT sensors?

**IoT** stands for Internet of Things. It refers to small, inexpensive devices that measure something in the physical world and send that data over the internet. Examples:

- A **temperature sensor** that reports "22.4°C" every minute
- A **motion sensor** that reports "motion detected" or "no motion"
- A **CO2 sensor** that reports air quality in parts per million (ppm)
- A **humidity sensor** that reports relative humidity as a percentage

These sensors are already everywhere — in smart homes, factories, hospitals, and farms. They generate enormous amounts of data, but most of that data is just logged and forgotten. This project makes that data *meaningful* by having an AI reason about it.

---

## 2. The Core Problem: AI That Can't Learn or Update

### 2.1 How standard language models are trained

A large language model (LLM) like GPT or Claude is trained by reading billions of pages of text. During training, the model adjusts billions of internal numbers (called **weights** or **parameters**) so that it gets better and better at predicting what word comes next in a sentence. After training is complete, those weights are locked — the model is frozen.

This is a fundamental limitation:

- The model cannot learn anything that happens after training ends
- If the world changes, the model does not know about it
- The model has no concept of "right now" or "yesterday"

For a model trying to understand a physical environment, this is a dealbreaker. Sensor data is constantly changing. What matters is *what is happening now*, not what was in a training dataset from months ago.

### 2.2 Two bad solutions and why we reject them

**Bad solution 1: Retrain the model every day.**
Full retraining is extremely expensive — it takes weeks and costs millions of dollars even for moderate-sized models. You cannot do this continuously.

**Bad solution 2: Just feed all sensor data into the prompt.**
Language models can only read a limited amount of text at once (called the **context window**). If you have months of sensor data from dozens of sensors, it will not fit. You need a smarter way to select what is relevant.

### 2.3 Our solution: a two-layer memory

We use a combination of two techniques:

1. **RAG (Retrieval-Augmented Generation)** — a fast, always-current external memory that the model can search before answering a question. Think of it like a library the model can consult.

2. **LoRA fine-tuning** — a lightweight way to slowly update the model's own weights when enough new knowledge has been confirmed. Think of it like the model gradually internalizing lessons it has learned many times.

Together these give the system both fast updates (milliseconds, via RAG) and deep learning (hours, via fine-tuning).

---

## 3. System Overview

The system is divided into five specialized **agents**. Each agent has a single job, and they pass information to each other like workers on an assembly line.

```
┌──────────────────────────────────────┐
│ IoT Sensors                          │
│ DHT22 · MQ-135 · HC-SR501 PIR        │
└──────┬───────────────────────────────┘
       │  GPIO (digital / analog)
       ▼
┌──────────────────────┐
│  STM32 co-processor  │  reads sensors → packages as JSON
└──────┬───────────────┘
       │  USB serial (every 30 s + on motion change)
       ▼
┌──────────────────────┐
│  Serial Bridge       │  adds timestamps → POST /telemetry
└──────┬───────────────┘
       │  HTTP POST
       ▼
┌──────────────────┐
│ Ingestion Agent  │  "Did this reading make sense? Store it."
└──────┬───────────┘
       │  clean, normalized data
       ▼
┌──────────────────────────┐
│ Knowledge Builder Agent  │  "Turn this reading into a searchable memory."
└──────┬───────────────────┘
       │  embeddings in a vector store
       ▼
┌──────────────────┐
│  Reasoner Agent  │ ◄── User asks a question
│                  │     "What do you know about the living room?"
└──────┬─────┬─────┘
       │     │
       │     └──► high-confidence beliefs stored
       │
       │  low-confidence → needs more data
       ▼
┌──────────────────┐
│  Explorer Agent  │  "Let's design an experiment to find out."
└──────┬───────────┘
       │  confirmed experimental results (labeled examples)
       ▼
┌──────────────────┐
│  Trainer Agent   │  "We've learned enough to update the model."
└──────────────────┘
```

Each of these agents is described in detail in the sections below.

Alongside this five-agent assembly line sits one more, optional capability: the **Identity module**. Unlike the agents above, it doesn't run automatically on every reading — it only switches on when a person explicitly chooses to take part, by *registering* their own routine. Section 11 explains how it works and, just as importantly, the careful boundaries around what it deliberately does not do.

---

## 4. The Ingestion Agent: The Doorman

### What it does

The Ingestion Agent is the entry point for all sensor data. Its job is to make sure that every piece of data that enters the system is clean, consistent, and properly stored. Think of it as a strict doorman who checks everyone's ID before letting them in.

### How sensors reach the system

On the Arduino UNO Q reference hardware, the physical sensors are not connected directly to the Linux computer — they are wired to a separate, low-power **microcontroller** (the STM32U585 co-processor built into the board). The microcontroller runs a small firmware program (`firmware/sensors/sensors.ino`) that reads the sensors and packages their values as a compact JSON message, which it sends over a USB serial connection to the Linux side.

```
DHT22 (temperature/humidity) ─── D4 ──┐
MQ-135 (air quality)         ─── A0 ──┤── STM32 MCU ──► USB serial ──► Serial Bridge ──► /telemetry
HC-SR501 (motion PIR)        ─── D7 ──┘
```

A small Python script called the **serial bridge** (`src/ingestion/serial_bridge.py`) listens on that USB connection, adds a UTC timestamp to each batch (the microcontroller has no real-time clock), and forwards the readings to the ingestion API. This separation is intentional: the microcontroller handles the real-time, electrically noisy job of reading sensors reliably; the Linux side handles the computationally intensive AI pipeline. If the USB cable is unplugged and re-plugged, the bridge reconnects automatically.

The bridge also sends motion events **immediately** when the PIR sensor changes state, rather than waiting for the next 30-second batch — so the AI side learns about occupancy changes in near real-time.

### The problem it solves

Raw sensor data is messy. Different sensors use different units (one might report temperature in Fahrenheit, another in Celsius). Sensors sometimes malfunction and report impossible values. Timestamps can be in different formats. If you let messy data into your AI system, you get messy, unreliable answers — "garbage in, garbage out."

### How it works

When a reading arrives at the API, the Ingestion Agent does three things:

**Step 1 — Validate.** Check that the reading has all required fields: sensor ID, timestamp, value, and unit. If anything is missing, reject it with an error.

**Step 2 — Normalize.** Convert everything to standard units:
- All temperatures → Celsius
- All pressures → kilopascals (kPa)
- All timestamps → UTC (Coordinated Universal Time)

This means the rest of the system never has to worry about unit conversions. It always sees Celsius, always sees UTC.

**Step 3 — Check for outliers.** Each sensor has an expected range (for example, a living room temperature sensor should never read -50°C or 200°C — those would indicate a broken sensor). Readings outside the expected range are stored but tagged as `outlier: true`, so downstream agents know to be skeptical.

### What it produces

A clean `TelemetryReading` object:
```
sensor_id:  "temp_01"
timestamp:  "2026-06-05T14:30:00Z"
value:      22.4
unit:       "C"
outlier:    false
tags:       { location: "living_room" }
```

This gets saved to a time-series database (a special kind of database optimized for time-stamped data) and passed to the next agent.

---

## 5. The Knowledge Builder Agent: The Librarian

### What it does

The Knowledge Builder Agent takes the clean readings from the Ingestion Agent and transforms them into a format that can be searched by meaning, not just by exact value. It is the librarian of the system.

### The problem it solves

Imagine you want to answer the question: "Has the living room been warm lately?" A normal database would require you to write a very specific query: "Show me all temperature readings from sensor temp_01 in the last 24 hours above 23°C." But what if you want to ask in plain English? What if different people phrase the question differently?

To answer questions in natural language, the system needs to be able to search by *meaning*, not just by exact match. This requires **embeddings**.

### What is an embedding?

An **embedding** is a list of numbers (a vector) that represents the meaning of a piece of text. The key property is: text with similar meanings has similar vectors, even if the words are completely different.

For example, the sentences:
- "The living room is warm"
- "Temperature in the main area is high"
- "It's hot in here"

...would all produce very similar vectors, even though they use different words. This allows the system to find relevant memories even when the query is phrased differently from how the data was stored.

### How text is created from sensor readings

You cannot embed a raw number like `22.4`. You first need to turn it into a sentence. The Knowledge Builder creates a human-readable description of each reading:

> "Sensor temp_01 reported 22.4°C at 2026-06-05T14:30:00Z. Location: living_room. Tags: normal."

This sentence is then converted into a 768-dimensional vector (a list of 768 numbers) using an embedding model called `nomic-embed-text`, which runs locally via a tool called Ollama.

### The vector store

All these embedding vectors are stored in a **vector store** (we use ChromaDB for the small version, Qdrant for scale). A vector store is a special database that is very fast at answering the question: "Which stored vectors are most similar to this query vector?"

When the Reasoner Agent wants to find relevant sensor data, it:
1. Converts the question into a vector
2. Asks the vector store: "Give me the 8 most similar vectors to this question"
3. Gets back the original sensor reading descriptions

This is **semantic search** — search by meaning.

### Handling high-frequency sensors

Some sensors report many times per second. Storing and embedding every single reading would be wasteful. For high-frequency sensors, the Knowledge Builder creates **aggregate chunks** — 60-second summaries:

> "Sensor temp_01 over 60 seconds ending at 14:31: min=22.1°C, max=22.7°C, mean=22.4°C, trend=stable."

One chunk per minute is much more manageable than 60 chunks per minute, and it captures the same essential information.

---

## 6. The Reasoner Agent: The Thinker

### What it does

The Reasoner Agent is the system's brain. It answers questions about the physical environment by combining two things: retrieved sensor data from the vector store, and the reasoning ability of a language model.

This combination is called **RAG — Retrieval-Augmented Generation**.

### What is RAG?

RAG works like an open-book exam. Instead of asking the AI to answer purely from memory (which may be outdated or wrong), you first find the relevant pages in the textbook, and then ask the AI to answer using those pages.

**The RAG process, step by step:**

```
User asks: "Is the living room currently occupied?"

Step 1 — Embed the question
  "Is the living room currently occupied?"
  → [0.23, -0.41, 0.87, ... 768 numbers]

Step 2 — Search the vector store
  Find the 8 most similar stored chunks.
  Results might include:
  - "Motion sensor motion_01 reported activity at 14:28"
  - "CO2 sensor co2_01 reported 890 ppm at 14:29 (rising)"
  - "Temperature rising 0.3°C over last 10 minutes"

Step 3 — Compute confidence from retrieval quality
  Before calling the LLM, score the retrieval result on four dimensions:
  • Coverage:    6 of 8 requested chunks were found         → 0.75
  • Similarity:  average cosine similarity of chunks = 0.87 → 0.87
  • Recency:     chunks average 4 minutes old               → 0.997
  • Consistency: motion and CO2 values agree on occupancy   → 0.91
  Weighted sum → confidence = 0.88

Step 4 — Build a prompt and call the language model
  "You are a physical-world reasoning system.
   Here is recent sensor data:
   [the 6 retrieved chunks]
   Question: Is the living room currently occupied?"

  The LLM reads the prompt and produces:
  "The living room is likely occupied.
   Motion was detected 2 minutes ago, and CO2 is elevated
   and rising, consistent with one or more people present."

  Note: the LLM is not asked to state a confidence number.
  Confidence comes from Step 3, not from the model's self-assessment.

Step 5 — Store the belief
  {
    claim: "living room is occupied",
    confidence: 0.88,            ← from retrieval quality, not LLM output
    evidence: [chunk_041, chunk_089, chunk_093],
    timestamp: "2026-06-05T14:30:00Z"
  }
```

### Why confidence scores matter

The system always produces a confidence score between 0.0 and 1.0. This is crucial:

- **High confidence (> 0.7)**: The retrieval was strong. Store as an active belief.
- **Medium confidence (0.4–0.7)**: The retrieval was partial. Include caveats in the answer.
- **Low confidence (< 0.4)**: The retrieval was weak or stale. Flag for the Explorer Agent to investigate.

If the same question keeps coming back with low confidence, it means there is a genuine knowledge gap — the sensors that would answer the question are either not installed, not reporting, or not being understood correctly. The Explorer Agent's job is to fix this.

### Where confidence comes from

A common misconception is that the language model produces the confidence score. It does not. LLMs cannot reliably assess their own certainty — they tend to sound equally confident whether they are right or wrong.

Instead, confidence is computed **from the retrieval result itself**, before the LLM is called. Four factors contribute:

| Factor | What a low score means |
|---|---|
| **Coverage** | Few chunks found — the vector store has little relevant data |
| **Similarity** | Chunks are not very similar to the query — retrieval is a loose match |
| **Recency** | Chunks are old — the data may no longer reflect current conditions |
| **Consistency** | Values within a sensor stream vary widely — the environment is noisy or changing fast |

This means confidence reflects how well the evidence supports an answer, not how fluently the LLM expressed it. A beautifully written answer based on two-day-old, poorly-matching chunks will still receive a low confidence score.

### Belief tracking

Every high-confidence answer is stored as a **belief** — a structured claim with evidence. When new sensor data comes in that contradicts an existing belief, the old belief is marked as invalidated and the Explorer is triggered.

For example: if the system believes the room is empty, and a motion sensor fires, the belief is invalidated. This prevents the system from holding stale knowledge.

---

## 7. The Explorer Agent: The Scientist

### What it does

The Explorer Agent is the system's curiosity engine. When the Reasoner cannot answer a question confidently, the Explorer designs and runs experiments to fill in the knowledge gap. It follows the scientific method: observe, hypothesize, test, conclude.

### Why active exploration matters

A purely passive system just waits for data to arrive. But some relationships in the physical world are not obvious from casual observation — you have to look for them deliberately.

For example: does CO2 level in a room predict whether people will arrive home soon? Is there a lag between a door being opened and the temperature changing? These patterns exist but they require targeted observation to discover.

The Explorer finds these patterns by generating **hypotheses** and testing them.

### How hypothesis generation works

The Explorer looks at the current belief state and asks: "What do I know least about? What, if I understood it, would most improve my overall picture of this environment?"

It generates hypotheses in a structured format:

```
Given that: CO2 in the living room rises sharply between 17:00 and 19:00
I hypothesize that: this pattern indicates people arriving home from work
This would be falsified if: CO2 rises even when motion sensors show no activity
To test this I need: motion sensor data correlated with CO2 readings over 7 days
```

### Ranking hypotheses by information gain

Not all hypotheses are equally valuable. The Explorer ranks them using a concept called **expected information gain (EIG)**: how much would my understanding improve if this hypothesis turned out to be true?

Think of it like this: if you are trying to solve a puzzle and you can ask one question, you should ask the question whose answer will eliminate the most possibilities, not the one whose answer you already mostly know.

The Explorer also considers **cost**: some experiments are free (just look at existing data), while others are expensive (querying a remote sensor API, or sending a command to a smart device).

The ranking formula is simple:
```
Score = information_gain / experiment_cost
```

Highest score goes first.

### Types of experiments

The Explorer can run four types of experiments, in ascending order of complexity:

| Type | Description | Example |
|---|---|---|
| Observation | Analyze existing data at higher resolution | Look at CO2 per-minute instead of per-hour |
| Alert | Set a threshold; wait for it to fire and record the context | Alert when CO2 > 1000 ppm; record who was home |
| Simulation | Run a software simulation with perturbed parameters | Simulate what temperature would be without heating |
| Intervention | Send a command to an actuator; observe the response | Turn thermostat up 1°C; measure temperature lag |

The system starts with observation experiments (no risk, no cost) and only escalates to interventions when the system has enough confidence to justify it.

### Labeled examples: the reward

When an experiment concludes with a clear outcome (confirmed or refuted), it produces a **labeled example** — a training record that says "given this sensor context, the correct answer is this." These are gold: they become the training data for the Trainer Agent.

---

## 8. The Trainer Agent: The Teacher

### What it does

The Trainer Agent periodically takes the labeled examples produced by the Explorer and uses them to make the underlying language model smarter. Instead of the model always needing to look things up (RAG), it starts to *know* certain patterns from experience — baked into its own weights.

### The problem with retraining

As mentioned earlier, fully retraining a language model is prohibitively expensive. A model with 7 billion parameters (weights) can take weeks and thousands of dollars to train from scratch.

But there is a much cheaper alternative: **LoRA**.

### What is LoRA?

**LoRA** stands for Low-Rank Adaptation. It is a technique for fine-tuning (updating) a large model by only changing a tiny fraction of its parameters.

Here is the intuition. A language model's "intelligence" is stored in large matrices of numbers (the weight matrices). LoRA says: instead of updating the whole matrix, let's represent the *change* we want to make as two much smaller matrices.

```
Original weight matrix W  (1000 × 1000 = 1,000,000 numbers)

LoRA represents the update as:
  B × A  where B is 1000×8 and A is 8×1000
  = 1000×8 + 8×1000 = 16,000 numbers

So instead of updating 1,000,000 numbers,
we only train 16,000 numbers — 62× fewer.
```

The number 8 here is called the **rank**. It is a hyperparameter: lower rank means fewer parameters to train (faster, cheaper) but less expressive updates. Higher rank means more expressive but more expensive. For our small IoT use case, rank 8 is a good starting point.

The frozen base model's knowledge is fully preserved. LoRA only adds a small correction on top.

### When does training trigger?

The Trainer does not run continuously — that would be wasteful. It triggers when:

1. At least 50 new labeled examples have accumulated since the last training run, **or**
2. The Knowledge Builder detects that the model's existing beliefs are being frequently contradicted (called **concept drift** — the world has changed), **or**
3. The user manually requests it

### The training process

```
Step 1: Load labeled examples (instruction-answer pairs)
  Input: "CO2: 890 ppm (rising). Motion: active 2 min ago."
  Output: "Living room is occupied (confidence: 0.83)"

Step 2: Split into train / validation / test sets (80/10/10)

Step 3: Run LoRA fine-tuning for ~200 steps
  (takes ~2 minutes on a laptop CPU for 50 examples)

Step 4: Evaluate on the held-out test set
  Did accuracy go up? Is the model better calibrated?

Step 5: If the new checkpoint beats the current one → promote it
         If not → keep the current one, log the failure
```

### Preventing forgetting

A well-known problem in machine learning is **catastrophic forgetting**: when you train a model on new data, it can forget things it knew before. This is especially dangerous here — we do not want the model to forget what a CO2 sensor is just because we trained it on a batch of temperature examples.

We prevent this with two techniques:

1. **Replay buffer**: every new training run includes 20% of examples from *previous* training runs. This forces the model to keep practicing old knowledge.

2. **LoRA checkpoint chaining**: each new LoRA adapter is initialized from the *previous adapter*, not from the base model. This means we build on what we learned before rather than starting fresh each time.

---

## 9. The Learning Cycle: How It All Fits Together

Here is the complete learning loop, from raw sensor reading to improved model:

```
Day 1:
  temp_01 reports 22.4°C → Ingestion → Knowledge Builder → vector store

  User asks: "Is the living room warm?"
  Reasoner retrieves → answers "Yes, 22.4°C (confidence: 0.75)"
  Belief stored.

  User asks: "Why is CO2 rising?"
  Reasoner: "Insufficient data (confidence: 0.31)"
  → Explorer triggered

Day 2:
  Explorer hypothesis: "CO2 rises when people are home"
  Explorer runs observation experiment on 24h of data
  Outcome: confirmed (CO2 > 800 ppm always coincides with motion)
  Labeled example created and saved.

Day 7:
  Explorer has confirmed 12 hypotheses.
  Labeled examples: 52 total.
  Trainer triggers. LoRA fine-tune runs.
  New checkpoint: 8% better accuracy on held-out set. Promoted.

Day 14:
  Reasoner now answers occupancy questions with confidence > 0.8
  without needing to retrieve as many chunks.
  The model has internalized the CO2-occupancy relationship.
  RAG is still used for current values; weights encode the pattern.
```

This is **continual learning**: the model never stops improving as long as sensors keep reporting and the Explorer keeps running experiments.

---

## 10. Knowledge Representation: Three Levels

The system represents knowledge at three levels of abstraction, each more powerful than the last.

### Level 1: Raw readings (what happened)

The raw time-stamped values stored in the time-series database.

> "temp_01: 22.4°C at 14:30:00"

This is factual but not useful on its own. There are millions of these.

### Level 2: Temporal patterns (what usually happens)

Aggregated regularities detected by the Knowledge Builder and confirmed by the Explorer.

> "Living room CO2 peaks between 17:00–19:00 on weekdays"

This requires aggregation and pattern detection across many Level 1 readings.

### Level 3: Causal relationships (why things happen)

Causal links inferred by the Reasoner and confirmed by interventional experiments.

> "High CO2 in the living room is caused by people being home, not by time of day per se"

This is the most valuable level — it lets the model make predictions and understand the *mechanism* behind patterns, not just correlate them.

The RAG system retrieves from Level 1 and Level 2. Fine-tuning encodes Level 2 and eventually Level 3 into the model's weights.

---

## 11. The Identity Module: Whose Routine Is This — Not "Who Are You"

### A natural question, and why we slowed down before answering it

Once a system can tell *whether* a room is occupied, the next question people usually ask is: can it tell *who* is in the room?

The obvious technical answer is "add a camera and run face recognition" — or a microphone and run voice recognition. We deliberately did not do that, and it's worth explaining why, because the reasoning shapes everything else in this part of the system.

A face is not just a way to unlock your phone. It is a unique, lifelong identifier — a "faceprint" — and the moment a system can compute one, it has created exactly the kind of data that privacy laws around the world (GDPR in Europe, Illinois's BIPA, the EU AI Act, and others) single out for the strictest protection: *biometric identifiers*. The same is true of a voiceprint. Even labeling the output "Person A" instead of a real name changes nothing about what was captured — the underlying fingerprint can still re-identify a real human being, no matter what name is printed next to it. Building that kind of system, even casually, even for people who live in the house and would consent, crosses from "smart home automation" into "biometric surveillance" — a line worth respecting on purpose, not blurring by accident.

So we asked a different question instead: *is there a way to explore "who's probably here" that stays entirely within the kind of data the board already collects — motion, temperature, humidity, CO2 — and that only ever applies to people who choose, knowingly, to take part?*

That's what the Identity module is.

### The idea: recognize a routine, not a person

Think about how you can often tell who just came in the front door without even looking up — a familiar rhythm of footsteps, the time of day they usually arrive, how long they typically linger before heading to the kitchen. You're not running facial recognition in your head. You're recognizing a *pattern of behavior* you've come to associate with that person — and you hold the guess loosely, ready to be corrected.

That's the entire idea behind this module, made explicit and measurable. For each person who chooses to take part, the system learns three simple things from the existing motion sensor:

- **What time of day are they usually active?** — a 24-hour activity profile ("mostly mornings and evenings on weekdays")
- **How much of the time they're around are they actually moving?** — an overall activity level
- **How long do their typical active stretches last?** — a few minutes of passing through, versus hours of working from home

None of these require a camera. None of them are biometric. None of them can identify someone outside this one system, on this one device, to anyone who hasn't agreed to take part. We call this small bundle of statistics a **routine signature**, and it is the *only* thing this module ever stores about a person.

### Step 1 — Registration: nothing happens without an explicit "yes"

The system never builds a routine signature for someone in the background. Building one requires a person to actively start a **registration window** — in effect, saying "starting now, learn my pattern for the next hour." During that window, the system watches the motion sensor it already has, and when the window closes, it distills everything it observed into a routine signature, stored locally and tagged with the name the person chose for themselves and the moment they consented.

```
Person says: "Register me as 'Person A' for the next hour."

The system observes (using a sensor that was already running):
  09:02  motion detected
  09:07  motion detected
  09:08  motion detected
  ...
  10:00  window closes

It distills this into a routine signature:
  presence_ratio:           0.62   (active about 62% of the window)
  hourly_activity:          [a 24-number histogram — peaks near 9 AM,
                             quiet by 10 PM, ...]
  mean_session_length_min:  8.5    (active stretches run about 8 minutes)

Stored as:
  { profile_id: "person_a_470f29", display_name: "Person A",
    consent_at: "2026-06-01T09:00:00Z", signature: {...},
    revoked_at: null }
```

That's the entire record: no image, no recording, no audio — three numbers and a histogram describing a pattern of comings and goings, attached to a name the person picked for their own profile.

### Step 2 — Matching: an honest, confidence-scored guess

From time to time, the system looks at roughly the last half hour of motion activity, builds the *same kind* of routine signature for that short recent window, and compares it against every registered signature using simple statistical similarity — not a language model, and not an AI "judgment call."

This is a deliberate choice worth pausing on. Comparing two short numerical patterns for similarity is a job for ordinary arithmetic, not for a large language model — the same call we made for the confidence scores in Section 6, where a calculator beats a chatbot at "how similar are these two numbers": faster, cheaper, and easier to explain.

The comparison produces a confidence score between 0 and 1, and the system reports it honestly either way:

```
Best match: "Person A" — confidence 0.92  →  "This looks like Person A's routine"
Best match: "Person A" — confidence 0.38  →  "unknown occupant" (too uncertain to report)
```

If the live pattern doesn't resemble *any* registered routine closely enough, the system says so plainly — **"unknown occupant"** — rather than forcing a guess. That same honest "I don't recognize this pattern" is, not coincidentally, also exactly the signal that something — or someone — new has shown up.

### Step 3 — Revocation: leaving is as complete as joining

Anyone who registered can ask to be forgotten at any time, for any reason, no questions asked. And "forgotten" means exactly that: the system doesn't simply mark the profile inactive while quietly keeping the data around. It performs a **hard delete** — it removes the routine signature itself, *and* it walks back through the entire history of match results and removes every single record that ever referenced that person, so that no trace of them remains anywhere on the device.

```
Before revoking "Person A":
  identity_profiles.jsonl:  [ Person A's signature ]
  identity_matches.jsonl:   [ "Person A" @ 09:15, "Person A" @ 14:02,
                              "unknown occupant" @ 18:40 ]

After revoking "Person A":
  identity_profiles.jsonl:  [ ]
  identity_matches.jsonl:   [ "unknown occupant" @ 18:40 ]
```

We think this distinction — a real purge versus a flag that quietly leaves the underlying data intact — is the difference between a *consent* system and surveillance with extra steps. So it's the first thing revocation does, not an afterthought bolted on later.

### Step 4 — It never leaves the device, and that's enforced, not just promised

Section 8 described how the system periodically sends *labeled examples* — confirmed lessons about sensor patterns — to a separate machine for deeper training. It would be easy to assume identity data might get swept up in that process by accident. It can't: that export step reads from exactly one configured file path (the labeled-examples log) and nothing else. There is no step anywhere that scans "everything in the data folder" and ships it out, so routine signatures and match histories simply aren't reachable from that path — structurally, not by policy. The same files are also excluded from the project's version-control history, so they can never end up copied into a code repository either.

### What this module deliberately does *not* do

To be precise about the boundary we drew:

- It does **not** use a camera, a microphone, or any biometric sensor of any kind.
- It does **not** attempt to determine — and could not determine, even if asked — anything about a person's sex, age, health, or any other personal characteristic.
- It does **not** produce anything that could identify a person to anyone outside this one system; "Person A" is a name someone chose for their own profile on their own device, not a durable identifier that follows them anywhere else.
- It does **not** run, store, or learn anything about anyone who hasn't explicitly opted in. There is no passive "background profiling" mode — registration is the only door in, and it only opens from the inside.

In short: the question this module answers is *"whose routine does this look like"* — a soft, probabilistic, revocable guess about a pattern someone chose to share — never *"who is this person."* That distinction is the whole point of the design.

---

## 12. Scaling: From a Laptop to a Building

The system is designed to grow without requiring a rewrite. Every major component can be swapped for a larger equivalent by changing a single configuration file (`config/model.yaml`).

### Phase 1: Arduino UNO Q (current)

| Component | Technology | Notes |
|---|---|---|
| Hardware | Arduino UNO Q | Quad Cortex-A53 @ 2 GHz, 4 GB RAM, Debian Linux |
| Sensor acquisition | STM32U585 co-processor + serial bridge | Reads DHT22, MQ-135, HC-SR501 via GPIO; sends JSON over USB serial |
| LLM | Ollama + smollm2:135m | ~90 MB at 4-bit; fits in 4 GB alongside OS and pipeline |
| Embeddings | nomic-embed-text via Ollama | 768-dimensional, runs locally |
| Vector store | ChromaDB (in-process, file-backed) | ~500 K chunks |
| Time-series DB | SQLite | millions of rows |
| Event bus | In-process Python pub/sub | single process |

### Phase 2: Multi-room / multi-building

| Component | Upgrade | Why |
|---|---|---|
| Vector store | Qdrant (multi-node, filtered search) | Filter by room, building |
| Time-series DB | TimescaleDB | Continuous aggregates, time-series SQL |
| Message broker | MQTT (Mosquitto) | Hundreds of sensors, reliable delivery |
| LLM | Mistral 7B or Claude Haiku | Higher reasoning quality |

### Phase 3: Federated (many buildings, privacy-preserving)

In a federated setup, each building runs its own local model and vector store. Raw sensor readings never leave the building. Only **belief summaries** — high-level, anonymized conclusions — are shared with a central meta-reasoner.

```
Building A  →  local beliefs  →┐
Building B  →  local beliefs  →├→ Meta-Reasoner  → "What patterns
Building C  →  local beliefs  →┘                    are universal?"
```

Fine-tuning is also federated: each building trains its own LoRA adapter on local data, and the adapters are averaged together (a technique called **FedAvg**) to produce a global adapter — without any raw data leaving any building.

### Phase 4: Foundation model

Once enough data and labeled examples have accumulated across many buildings, they can be used to train a dedicated **foundation model** — a large language model (7B–13B parameters) specifically trained on IoT telemetry data. This model would have strong physical-world priors out of the box and could be fine-tuned quickly to any new environment.

---

## 13. Design Choices and Trade-offs

| Decision | What we chose | What we gave up |
|---|---|---|
| RAG vs. full retraining | RAG (immediate updates) | Inference speed (must retrieve each time) |
| LoRA vs. full fine-tuning | LoRA (cheap, fast) | Maximum expressiveness of weight updates |
| Small base model (135M) | Fast, runs on CPU | Weaker reasoning than a 7B model |
| Local-first (Ollama) | Privacy, no API costs | Slower, less capable than cloud LLMs |
| ChromaDB → Qdrant upgrade path | Zero-config start | Migration effort when scaling up |
| Sensor acquisition | STM32 co-processor + serial bridge | Adds MCU firmware complexity; gains hard real-time sensor reads and clean separation of concerns |
| Confidence source | RAG-derived (retrieval quality) | Cannot detect LLM reasoning errors — only evidence gaps |
| Confidence-based exploration | Simple, interpretable | Not as theoretically optimal as full Bayesian active learning |
| SQLite → TimescaleDB upgrade path | Simple start | No time-series SQL until you upgrade |

Every "what we gave up" column is recoverable — the system is designed so that you can swap in a more powerful component at each stage without changing the logic of the agents above it.

---

## 14. Open Research Questions

This project sits at the intersection of several active research areas. Here are the most interesting unsolved problems:

**1. How should time-series data be fed to a language model?**
Text describes events, not continuous signals. Should we feed sensor data as a table of numbers? As a natural-language summary ("temperature rose 2°C over 10 minutes")? Or as a special sequence of time-series tokens that the model is trained to understand directly? Each approach has different trade-offs for accuracy, token efficiency, and generalization.

**2. Can surprise replace confidence as the exploration trigger?**
Currently, the Explorer is triggered by low confidence scores. An alternative: trigger exploration whenever the incoming data is very different from what the model predicted (high **reconstruction error** or **surprise**). This is closer to how biological curiosity works, and it might find interesting patterns that low confidence would miss.

**3. Multi-modal sensors.**
Camera sensors produce images; microphones produce audio. How do you search across images, audio, and text readings in the same vector store? This requires multi-modal embedding models (which exist) and careful design of the retrieval pipeline.

**4. When is a causal claim safe to act on?**
The Explorer can confirm that CO2 and occupancy are correlated, but correlation is not causation. Before the system sends commands to actuators (like turning on ventilation), it needs to be very confident about causal direction. Designing safe intervention policies is an open problem.

**5. Privacy in federated systems.**
Even belief summaries can leak private information (if a building's belief is "occupied every Tuesday night 8–11pm", that reveals behavioral patterns). Techniques like **differential privacy** (adding calibrated noise to shared beliefs) can help, but there is a fundamental tension between privacy and accuracy.

---

## 15. Conclusion

We have described a system that turns a stream of raw sensor readings into a continuously-improving understanding of a physical environment. The key ideas are:

1. **Clean data first** — the Ingestion Agent ensures everything downstream operates on reliable, normalized information.

2. **Semantic memory via RAG** — sensor readings are embedded and stored so the model can search by meaning, not just by value. This gives the model an always-current, always-searchable memory without any retraining.

3. **Structured beliefs with uncertainty** — the Reasoner does not just answer questions; it tracks what it knows, how confident it is, and when that confidence should be revised.

4. **Active curiosity** — the Explorer does not wait for knowledge to arrive. It generates hypotheses, designs experiments, and seeks out the data that would most improve the model's understanding.

5. **Gradual internalization via LoRA** — lessons confirmed by experimentation are periodically baked into the model's weights, improving baseline performance over time without catastrophic forgetting.

6. **Designed to scale** — every component has a clear upgrade path, from a single laptop to a federated network of buildings.

7. **Privacy by design, not by policy** — where the system ventures into anything related to the people who share a space with it, it deliberately limits itself to patterns its existing sensors already see, requires explicit opt-in before learning anything about a specific person, and makes "forgetting" mean a real, total purge — not a quiet flag.

The result is a system that starts tiny, learns continuously, and gradually develops a genuine understanding of the physical world it inhabits — one sensor reading at a time, and never at the expense of the people living in that world.

---

## Appendix A: Glossary

| Term | Definition |
|---|---|
| Agent | An autonomous software component with a specific role, capable of using tools and making decisions |
| Arduino UNO Q | A hybrid single-board computer combining a real-time STM32 microcontroller and a quad-core ARM Linux processor on one board |
| Behavioral routine signature | A small bundle of statistics (how often someone is active, what times of day, how long their active stretches last) describing a person's pattern of presence — derived only from existing ambient sensors, never from cameras, microphones, or any biometric data |
| Belief | A structured claim the model holds about the world, with an associated confidence score |
| ChromaDB | An open-source vector database that runs in-process (no server needed) |
| Concept drift | When the statistical properties of incoming data change over time, making old beliefs invalid |
| Continual learning | The ability of a model to keep learning from new data without forgetting what it already knows |
| Embedding | A fixed-length vector of numbers that represents the meaning of a piece of text |
| EIG | Expected information gain — how much a hypothesis, if tested, would reduce overall uncertainty |
| FedAvg | Federated Averaging — a technique for combining model updates from multiple devices without sharing raw data |
| Fine-tuning | Updating a pre-trained model's weights on a new, smaller dataset |
| Foundation model | A large model trained on broad data that serves as a starting point for specialized applications |
| Hard delete (purge) | Removing a record completely — and every trace of it elsewhere — rather than just flagging it as inactive while quietly keeping the underlying data; the standard this project holds identity revocation to |
| IoT | Internet of Things — physical devices that collect and transmit data over the internet |
| Labeled example | A training record: an input paired with the correct output |
| LoRA | Low-Rank Adaptation — a parameter-efficient fine-tuning method that only trains a small number of additional weights |
| MQTT | Message Queuing Telemetry Transport — a lightweight protocol for sensor-to-server communication |
| Ollama | A tool for running open-source language models locally on your own machine |
| Opt-in registration | The consent mechanism by which a person knowingly and explicitly chooses to have the system learn their own behavioral routine signature — nothing is learned about anyone who hasn't taken this step |
| Outlier | A sensor reading that falls outside the sensor's expected range, possibly indicating a fault |
| Pydantic | A Python library for defining and validating data schemas |
| RAG | Retrieval-Augmented Generation — answering questions by first retrieving relevant context, then generating a response |
| RAG-derived confidence | A confidence score computed from retrieval quality (coverage, similarity, recency, consistency) rather than from the LLM's self-reported certainty |
| Replay buffer | A technique for preventing forgetting by including past training examples in each new training run |
| SenML | Sensor Markup Language — a standard JSON format for IoT sensor data |
| Serial bridge | A Python process that reads JSON sensor batches from the STM32 over USB serial, adds timestamps, and forwards them to the ingestion API |
| STM32 | A family of microcontrollers from STMicroelectronics; the UNO Q uses the STM32U585 (Cortex-M33) for real-time sensor reading |
| USB CDC | USB Communications Device Class — makes a microcontroller appear as a virtual serial port to the host operating system |
| Telemetry | Data automatically collected and transmitted from remote sensors |
| Time-series database | A database optimized for storing and querying data points indexed by time |
| Vector store | A database optimized for storing and searching embedding vectors |
| Weight | A numerical parameter inside a neural network that is adjusted during training |

## Appendix B: Project File Map

```
ai-setup/
├── CLAUDE.md               Project guide for Claude Code
├── TODO.md                 Living task list
├── requirements.txt        Python dependencies
├── firmware/
│   └── sensors/
│       └── sensors.ino     STM32U585 Arduino sketch (sensor reading firmware)
├── docs/
│   ├── whitepaper.md       This document
│   ├── architecture.md     Data flow and API reference
│   └── llm-design.md       Detailed model design (for engineers)
├── config/
│   ├── sensors.yaml        Sensor registry
│   ├── model.yaml          LLM + vector store configuration
│   └── agents.yaml         Agent routing and thresholds
├── .claude/
│   ├── settings.json       Claude Code permissions
│   ├── agents/             Agent prompt definitions
│   │   ├── ingestion.md
│   │   ├── knowledge-builder.md
│   │   ├── reasoner.md
│   │   ├── explorer.md
│   │   └── trainer.md
│   └── skills/             User-invocable commands
│       ├── ingest-telemetry.md
│       ├── query-knowledge.md
│       ├── run-experiment.md
│       └── train-checkpoint.md
└── src/
    ├── config.py           Config loader
    ├── ingestion/
    │   ├── serial_bridge.py  STM32 → ingestion API bridge
    │   └── ...             Validation, normalization, storage
    ├── knowledge/          Embedding and vector store code
    ├── model/
    │   ├── rag_confidence.py  RAG-derived confidence scoring
    │   └── ...             RAG chain, reasoner, belief tracker
    ├── exploration/        Hypothesis generation and experiments
    ├── identity/           Opt-in routine registration & matching (Section 11)
    │   ├── signature.py      Builds behavioral routine signatures from motion history
    │   ├── matcher.py        Confidence-scored matching against registered routines
    │   ├── registration.py   Register / revoke (hard delete) / list profiles
    │   └── store.py          Local-only JSONL storage for profiles & match history
    └── api/                FastAPI HTTP layer
```
