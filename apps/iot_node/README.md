# IoT World-Model Sensor Node — Arduino App Lab app

App Lab packaging of the UNO Q sensor node. Bundles the MCU sketch (reads
DHT22 / MQ-135 / HC-SR501) and the MPU Python loop (forwards readings to the
ingestion API) into one deployable App, using the **RouterBridge** RPC for
MCU↔MPU transport instead of USB-serial JSON.

```
iot_node/
├── app.yaml              # App Lab manifest (name, version, bricks, ports)
├── python/
│   ├── main.py           # runs on the MPU — pulls readings, POSTs /telemetry, drives gauge
│   ├── led_gauge.py      # pure-python CPU/mem -> 12x8 frame packer (unit-testable)
│   └── requirements.txt  # MPU Python deps (requests, psutil)
└── sketch/
    ├── sketch.ino        # runs on the MCU — read_* RPC handlers + set_matrix
    └── sketch.yaml       # FQBN arduino:zephyr:unoq + library pins
```

## How it relates to the rest of the repo

| Replaces | With |
|---|---|
| `firmware/sensors/sensors.ino` (serial JSON @115200) | `sketch/sketch.ino` (RouterBridge `Bridge.provide`) |
| `src/ingestion/serial_bridge.py` (parses serial frames) | `python/main.py` (`Bridge.call` + POST `/telemetry`) |
| `src/ingestion/led_matrix.py` (vendor MPU matrix binding) | `python/led_gauge.py` + the sketch's `set_matrix` RPC |

### LED-matrix load gauge

There is **no standalone `arduino:led_matrix` Brick** — on the UNO Q the 12x8
matrix is MCU-owned (R4-style), so App Lab apps drive it from the sketch over
the Bridge (the same approach as Arduino's `led-matrix-painter` example, which
uses `arduino:web_ui` + storage Bricks for the UI, not the matrix). Here the
MPU samples CPU/memory via `psutil`, `led_gauge.py` packs an 8x12 frame into
3x uint32, and `main.py` pushes it every 2 s via `Bridge.call("set_matrix", …)`;
the sketch's `set_matrix` loads it with `ArduinoLEDMatrix::loadFrame`. Left half
= CPU %, right half = memory %, filling bottom-up — identical semantics to the
standalone `src/ingestion/led_matrix.py`.

The heavy stack — FastAPI ingestion API, SQLite, ChromaDB, Ollama — is **not**
part of this App. Keep running it as supervised processes per
[`docs/installation.md` §4.1](../../docs/installation.md). This App is only the
sensor producer feeding `http://127.0.0.1:8000/telemetry`; edit `API_BASE` in
`python/main.py` if the API lives elsewhere (Phase 2 separate-server topology).

## Deploy

### Option A — App Lab desktop GUI
1. Install Arduino App Lab (https://docs.arduino.cc/software/app-lab/).
2. Connect the UNO Q (USB-C or network); let App Lab update the board.
3. Open this folder as an App and press **Run** — it builds + flashes the
   sketch, installs `requirements.txt` on the MPU, and starts both halves.
   Watch output in Run & Monitor.

### Option B — `arduino-app-cli` on the board (headless)
From a dev machine on the same network (SSH + WiFi enabled during board setup):

```bash
ssh arduino@<UNO_Q_IP> 'mkdir -p ~/ArduinoApps/iot_node'
scp -r ./* arduino@<UNO_Q_IP>:~/ArduinoApps/iot_node
ssh arduino@<UNO_Q_IP>
arduino-app-cli app start ~/ArduinoApps/iot_node
arduino-app-cli app logs  ~/ArduinoApps/iot_node
arduino-app-cli app stop  ~/ArduinoApps/iot_node
```

## Verify
With the ingestion API running on the board, `GET /beliefs` (or the API logs)
should show `temp_01 / humid_01 / co2_01 / motion_01` readings tagged
`{"source":"stm32","interface":"bridge"}` within ~30 s.

## Notes / things to confirm on real hardware
- **RouterBridge API names.** `Bridge.begin/provide/call` and the
  `Arduino_RouterBridge.h` header follow the App Lab examples; confirm the
  exact symbols against the version installed on your board and adjust if the
  bridge library renamed anything.
- **Library versions** in `sketch.yaml` are best-effort pins — reconcile with
  `arduino-cli lib search` on your install.
- **Return-value RPC.** `main.py` assumes `Bridge.call("read_temp")` returns
  the MCU function's value. If your RouterBridge build only supports one-way
  calls, switch to having the MCU push readings (an MCU→MPU `Bridge.call`) and
  have `main.py` register handlers instead.
- **LED matrix library + frame format.** The sketch uses `Arduino_LED_Matrix`
  / `ArduinoLEDMatrix::loadFrame(const uint32_t[3])` (UNO R4 parity, per
  `CLAUDE.md`). Confirm the package name and the bit order on your UNO Q; if the
  matrix expects a different layout, adjust `pack_frame()` in `led_gauge.py` —
  it's pure-python and unit-testable without the board.
