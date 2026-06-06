// Arduino UNO Q — STM32U585 co-processor firmware
// Reads DHT22 (temp + humidity), MQ-135 (air quality proxy), HC-SR501 (motion)
// and sends newline-delimited JSON batches to the Debian Linux host over USB CDC serial.
//
// The Linux host (src/ingestion/serial_bridge.py) adds UTC timestamps and
// forwards readings to the ingestion API — the MCU does not need an RTC.
//
// Libraries required (install via Arduino Library Manager):
//   "DHT sensor library" by Adafruit  (+ Adafruit Unified Sensor)
//   "ArduinoJson"        by Benoit Blanchon  (v6)
//
// Wiring:
//   DHT22    VCC → 3.3 V    GND → GND    DATA → D4    (10 kΩ pull-up DATA→VCC)
//   MQ-135   VCC → 5 V      GND → GND    AOUT → A0
//   HC-SR501 VCC → 5 V      GND → GND    OUT  → D7

#include <DHT.h>
#include <ArduinoJson.h>

static constexpr uint8_t  PIN_DHT    = 4;
static constexpr uint8_t  PIN_MQ135  = A0;
static constexpr uint8_t  PIN_PIR    = 7;
static constexpr uint32_t INTERVAL_MS = 30000UL;  // periodic batch interval

DHT dht(PIN_DHT, DHT22);

static int lastMotion = -1;  // track PIR state for immediate-send on change

// Serialises all four sensor readings to Serial as a JSON array.
void sendBatch(float tempC, float humidPct, float aqPpm, int motion) {
  StaticJsonDocument<256> doc;
  JsonArray arr = doc.to<JsonArray>();

  if (!isnan(tempC)) {
    JsonObject o = arr.createNestedObject();
    o["sensor_id"] = "temp_01";
    o["value"]     = round(tempC * 10.0f) / 10.0f;
    o["unit"]      = "C";
  }

  if (!isnan(humidPct)) {
    JsonObject o = arr.createNestedObject();
    o["sensor_id"] = "humid_01";
    o["value"]     = round(humidPct * 10.0f) / 10.0f;
    o["unit"]      = "%RH";
  }

  {
    JsonObject o = arr.createNestedObject();
    o["sensor_id"] = "co2_01";
    o["value"]     = (int)aqPpm;
    o["unit"]      = "ppm";
  }

  {
    JsonObject o = arr.createNestedObject();
    o["sensor_id"] = "motion_01";
    o["value"]     = motion;
    o["unit"]      = "bool";
  }

  serializeJson(doc, Serial);
  Serial.println();  // newline is the packet delimiter the bridge reads
}

void setup() {
  Serial.begin(115200);
  while (!Serial);   // block until Linux opens the USB CDC port

  pinMode(PIN_PIR, INPUT);
  dht.begin();
  delay(2000);       // DHT22 requires ~2 s after power-on before first valid read
}

void loop() {
  static uint32_t lastSent = 0;
  uint32_t now = millis();

  float tempC    = dht.readTemperature();
  float humidPct = dht.readHumidity();
  int   motion   = digitalRead(PIN_PIR);

  // MQ-135: map 10-bit ADC [0, 1023] to a rough PPM proxy [300, 5000].
  // Calibrate against a reference CO2 meter in clean air (~400 ppm) by
  // adjusting the baseline offset below.
  int   rawAQ  = analogRead(PIN_MQ135);
  float aqPpm  = 300.0f + (rawAQ / 1023.0f) * 4700.0f;

  // Immediate send on PIR state change so the Linux side gets real-time events.
  if (motion != lastMotion) {
    lastMotion = motion;
    sendBatch(tempC, humidPct, aqPpm, motion);
    lastSent = now;
    return;
  }

  // Periodic batch regardless of motion state.
  if (now - lastSent >= INTERVAL_MS) {
    lastSent = now;
    sendBatch(tempC, humidPct, aqPpm, motion);
  }
}
