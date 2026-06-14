// Arduino UNO Q — STM32U585 MCU firmware (App Lab / RouterBridge edition)
//
// Exposes DHT22 + MQ-135 + HC-SR501 readings to the MPU (Debian) over the
// internal Arduino RouterBridge RPC. The Python half (python/main.py) calls
// these provided functions, timestamps the readings, and POSTs them to the
// on-board ingestion API.
//
// This is the App Lab counterpart to firmware/sensors/sensors.ino: there is no
// Serial JSON framing — the Bridge handles MCU<->MPU transport.
//
// It also owns the onboard 12x8 LED matrix: the MPU computes a CPU/memory load
// frame (python/led_gauge.py) and pushes it here via the "set_matrix" RPC.
//
// Libraries (declared in sketch.yaml):
//   DHT sensor library (Adafruit) + Adafruit Unified Sensor
//   Arduino_RouterBridge (ships with App Lab)
//   Arduino_LED_Matrix (onboard 12x8 matrix; confirm the exact lib on UNO Q)
//
// Wiring (unchanged from the serial firmware):
//   DHT22    VCC -> 3.3 V   GND -> GND   DATA -> D4   (10 kOhm pull-up DATA->VCC)
//   MQ-135   VCC -> 5 V     GND -> GND   AOUT -> A0
//   HC-SR501 VCC -> 5 V     GND -> GND   OUT  -> D7

#include "Arduino_RouterBridge.h"
#include "Arduino_LED_Matrix.h"
#include <DHT.h>

ArduinoLEDMatrix matrix;

static constexpr uint8_t PIN_DHT   = 4;
static constexpr uint8_t PIN_MQ135 = A0;
static constexpr uint8_t PIN_PIR   = 7;

DHT dht(PIN_DHT, DHT22);

// --- RPC handlers exposed to the MPU --------------------------------------
// The MPU invokes these by name via Bridge.call("read_temp"), etc.

float read_temp() {
  return dht.readTemperature();   // returns NaN on read failure; MPU skips NaN
}

float read_humidity() {
  return dht.readHumidity();      // returns NaN on read failure; MPU skips NaN
}

int read_co2() {
  // MQ-135: map the 10-bit ADC [0, 1023] to a rough PPM proxy [300, 5000].
  // Calibrate the baseline against a reference meter in clean air (~400 ppm).
  int raw = analogRead(PIN_MQ135);
  return (int)(300.0f + (raw / 1023.0f) * 4700.0f);
}

int read_motion() {
  return digitalRead(PIN_PIR);    // 0 / 1
}

// Display a load frame pushed from the MPU. The three words are the 96-pixel
// (12x8) bitmap packed by python/led_gauge.py, MSB-first, matching the layout
// ArduinoLEDMatrix::loadFrame(const uint32_t[3]) expects.
void set_matrix(uint32_t w0, uint32_t w1, uint32_t w2) {
  uint32_t frame[3] = { w0, w1, w2 };
  matrix.loadFrame(frame);
}

void setup() {
  Bridge.begin();
  Bridge.provide("read_temp",     read_temp);
  Bridge.provide("read_humidity", read_humidity);
  Bridge.provide("read_co2",      read_co2);
  Bridge.provide("read_motion",   read_motion);
  Bridge.provide("set_matrix",    set_matrix);

  matrix.begin();
  pinMode(PIN_PIR, INPUT);
  dht.begin();
  delay(2000);   // DHT22 needs ~2 s after power-on before its first valid read
}

void loop() {
  // RouterBridge services incoming RPC calls in the background; nothing to do
  // here. Keep the loop yielding so the MCU stays responsive.
}
