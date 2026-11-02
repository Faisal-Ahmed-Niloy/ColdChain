// =====================================================================
// ColdChain Fridge Node — ESP32 firmware (one per fridge)
//
// Core 1 (Arduino loop): sensor, door, power button, compressor control,
//                         LEDs, buzzer, OLED. Never blocks on the network,
//                         so the fridge stays controlled while offline.
// Core 0 (netTask):       Wi-Fi, NTP, MQTT, signed publish/verify, outbox drain.
//
// Libraries (Arduino Library Manager):
//   PubSubClient (Nick O'Leary) 2.8+, ArduinoJson (Benoit Blanchon) 7.x,
//   Adafruit SSD1306 2.5+, Adafruit GFX, OneWire, DallasTemperature 3.9+
// Board: "ESP32 Dev Module" (esp32 by Espressif, core 2.0.x or 3.x)
// =====================================================================
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Preferences.h>
#include <time.h>

#include "config.h"
#include "secrets.h"     // copy secrets.example.h -> secrets.h
#include "control.h"
#include "outbox.h"
#include "protocol.h"
#include "display.h"

// ---------------- Globals shared between cores ----------------
static SemaphoreHandle_t gLock;         // guards gOutbox and gSendIdx
static Outbox gOutbox;
static size_t gSendIdx = 0;             // index (from oldest) of next entry to send
static uint32_t gLastDropSeen = 0;

static volatile bool gWifiOk = false;
static volatile bool gMqttOk = false;
static volatile bool gDiscardLatch = false;  // red LED latch, set/cleared by server command
static volatile uint32_t gRejectedCmds = 0;

// ---------------- Core-1 state ----------------
static OneWire oneWire(PIN_ONEWIRE);
static DallasTemperature sensors(&oneWire);
static CompressorController gComp;
static Preferences gPrefs;

static float gTemp = NAN;
static bool gSensorOk = false;
static uint32_t gLastValidMs = 0, gConvStartMs = 0;
static bool gSimActive = false;
static float gSimTemp = 5.0f;

static bool gDoorOpen = false, gDoorRaw = false, gDoorAlarm = false;
static uint32_t gDoorRawChangedMs = 0, gDoorOpenedMs = 0;

static bool gMains = true;
static uint8_t gBatt = 100;
static bool gBtnLast = HIGH;
static uint32_t gBtnChangedMs = 0, gBattTickMs = 0;

static uint32_t gSeq = 0;
static char gBootHex[9];
static uint32_t gLastPublishMs = 0, gLastDisplayMs = 0;

// ---------------- Helpers ----------------
static bool clockSynced() { return time(nullptr) > 1700000000; }
static uint32_t nowEpoch() { return (uint32_t)time(nullptr); }

static String topic(const char* leaf) {
  return String(TOPIC_PREFIX) + DEVICE_ID + "/" + leaf;
}

static void setRelay(bool on) {
  digitalWrite(PIN_RELAY, (on ^ (bool)RELAY_ACTIVE_LOW) ? HIGH : LOW);
}

// Snapshot the current state into the outbox (called on core 1 only).
static void pushReading(uint8_t reason) {
  Reading r;
  r.seq = ++gSeq;
  r.ts = clockSynced() ? nowEpoch() : 0;
  r.upMs = millis();
  r.tempC100 = gSensorOk ? (int16_t)lroundf(gTemp * 100.0f) : 0;
  r.flags = (gDoorOpen ? FL_DOOR_OPEN : 0) | (gComp.isOn() ? FL_COMP_ON : 0) |
            (gMains ? FL_MAINS : 0) | (gSensorOk ? FL_SENSOR_OK : 0) |
            (gDiscardLatch ? FL_LED_LATCH : 0);
  r.batt = gBatt;
  r.state = classifyTemp(gTemp, gSensorOk);
  r.reason = reason;
  xSemaphoreTake(gLock, portMAX_DELAY);
  gOutbox.push(r);
  xSemaphoreGive(gLock);
}

// ---------------- Sensor (non-blocking DS18B20) ----------------
static void sensorTick(uint32_t now) {
  if (gSimActive) {                       // demo hook: override the probe
    gTemp = gSimTemp; gSensorOk = true; gLastValidMs = now;
    return;
  }
  if (now - gConvStartMs >= 800) {        // 12-bit conversion needs 750 ms
    float t = sensors.getTempCByIndex(0);
    // -127 = disconnected, exactly 85.0 = power-on reset value (not a reading)
    if (t != DEVICE_DISCONNECTED_C && t > -55.0f && t < 125.0f && t != 85.0f) {
      gTemp = t; gSensorOk = true; gLastValidMs = now;
    }
    sensors.requestTemperatures();
    gConvStartMs = now;
  }
  if (now - gLastValidMs > SENSOR_FAULT_MS) gSensorOk = false;
}

// ---------------- Door switch + 60 s alarm ----------------
static void doorTick(uint32_t now) {
  bool raw = digitalRead(PIN_DOOR) == HIGH;   // HIGH = magnet away = OPEN
  if (raw != gDoorRaw) { gDoorRaw = raw; gDoorRawChangedMs = now; }
  if (now - gDoorRawChangedMs >= DOOR_DEBOUNCE_MS && raw != gDoorOpen) {
    gDoorOpen = raw;
    if (gDoorOpen) {
      gDoorOpenedMs = now;
      pushReading(RS_DOOR);                    // publish on door-state change
    } else {
      bool wasAlarm = gDoorAlarm;
      gDoorAlarm = false;
      pushReading(wasAlarm ? RS_DOOR_CLOSED_ALARM : RS_DOOR);
    }
  }
  if (gDoorOpen && !gDoorAlarm && now - gDoorOpenedMs > DOOR_ALARM_MS) {
    gDoorAlarm = true;
    pushReading(RS_DOOR_ALARM);                // server turns this into SMS/Telegram/email
  }
}

// ---------------- Power-failure simulation + battery ----------------
static void powerTick(uint32_t now) {
  bool b = digitalRead(PIN_POWER_BTN);
  if (b != gBtnLast && now - gBtnChangedMs > BTN_DEBOUNCE_MS) {
    gBtnChangedMs = now;
    gBtnLast = b;
    if (b == LOW) {                            // pressed
      gMains = !gMains;
      Serial.printf("[power] mains %s\n", gMains ? "RESTORED" : "LOST");
      pushReading(RS_POWER);
    }
  }
#if USE_BATTERY_ADC
  if (now - gBattTickMs > 2000) {
    gBattTickMs = now;
    float v = analogReadMilliVolts(PIN_BATT_ADC) / 1000.0f * BATT_DIVIDER;
    float pct = (v - BATT_V_EMPTY) / (BATT_V_FULL - BATT_V_EMPTY) * 100.0f;
    gBatt = (uint8_t)constrain((int)pct, 0, 100);
  }
#else
  // Simulated: discharge 1 %/36 s on battery (~1 h), charge 1 %/30 s on mains.
  if (!gMains && now - gBattTickMs > 36000) { gBattTickMs = now; if (gBatt > 0) gBatt--; }
  if (gMains && now - gBattTickMs > 30000)  { gBattTickMs = now; if (gBatt < 100) gBatt++; }
#endif
}

// ---------------- LEDs and buzzer ----------------
static void indicatorsTick(uint32_t now, TempState st) {
  bool blink = (now / 250) % 2;
  digitalWrite(PIN_LED_GREEN, st == ST_SAFE && !gDiscardLatch);
  digitalWrite(PIN_LED_YELLOW, st == ST_WARNING || st == ST_FAULT);
  // Red: SOLID while the discard latch is set (until admin reset), BLINK on breach.
  digitalWrite(PIN_LED_RED, gDiscardLatch ? HIGH : (st == ST_BREACH && blink));
  bool beep = false;
  if (gDoorAlarm) beep = (now / 500) % 2;                  // door alarm: 1 Hz beeping
  else if (st == ST_FAULT) beep = (now % 5000) < 120;      // sensor fault: chirp every 5 s
  digitalWrite(PIN_BUZZER, beep);
}

// ---------------- Serial demo commands ----------------
static void serialTick() {
#if DEMO_SERIAL_CMDS
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.startsWith("simtemp ")) {
    String arg = line.substring(8);
    if (arg == "off") { gSimActive = false; Serial.println("[sim] probe restored"); }
    else { gSimTemp = arg.toFloat(); gSimActive = true; Serial.printf("[sim] temp=%.2f\n", gSimTemp); }
  } else if (line == "status") {
    xSemaphoreTake(gLock, portMAX_DELAY);
    size_t q = gOutbox.size(); uint32_t d = gOutbox.dropped();
    xSemaphoreGive(gLock);
    Serial.printf("T=%.2f ok=%d door=%d comp=%d mains=%d batt=%u wifi=%d mqtt=%d queue=%u dropped=%u latch=%d\n",
                  gTemp, gSensorOk, gDoorOpen, gComp.isOn(), gMains, gBatt, gWifiOk, gMqttOk,
                  (unsigned)q, (unsigned)d, gDiscardLatch);
  }
#endif
}

// =====================================================================
// Network task (core 0)
// =====================================================================
static WiFiClient gNet;
static PubSubClient gMqtt(gNet);
static uint32_t gLastSendMs = 0, gLastAckProgressMs = 0;

// If the outbox dropped old entries (overflow), indices shifted: fix gSendIdx.
static void adjustForDrops() {
  uint32_t d = gOutbox.dropped();
  if (d != gLastDropSeen) {
    uint32_t delta = d - gLastDropSeen;
    gSendIdx = gSendIdx > delta ? gSendIdx - delta : 0;
    gLastDropSeen = d;
  }
}

static String buildBody(const Reading& r, size_t backlog, uint32_t dropped) {
  // Back-fill capture time for readings taken before NTP sync
  uint32_t ts = r.ts ? r.ts : nowEpoch() - (millis() - r.upMs) / 1000;
  JsonDocument b;
  b["seq"] = r.seq;
  b["boot"] = gBootHex;
  b["t"] = ts;
  if (r.flags & FL_SENSOR_OK) b["temp"] = serialized(String(r.tempC100 / 100.0, 2));
  else b["temp"] = nullptr;
  b["door"] = (r.flags & FL_DOOR_OPEN) ? 1 : 0;
  b["comp"] = (r.flags & FL_COMP_ON) ? 1 : 0;
  b["power"] = (r.flags & FL_MAINS) ? "MAINS" : "BATTERY";
  b["batt"] = r.batt;
  b["state"] = stateName((TempState)r.state);
  b["reason"] = reasonName(r.reason);
  b["led"] = (r.flags & FL_LED_LATCH) ? 1 : 0;
  b["backlog"] = (uint32_t)backlog;
  b["drop"] = dropped;
  b["rej"] = (uint32_t)gRejectedCmds;
  b["rssi"] = WiFi.RSSI();
  b["fw"] = FW_VERSION;
  String s;
  serializeJson(b, s);
  return s;
}

static void onCommand(char* t, byte* payload, unsigned int len) {
  String body, err;
  if (!verifyEnvelope(DEVICE_ID, DEVICE_HMAC_KEY, payload, len, nowEpoch(), body, err)) {
    gRejectedCmds++;
    Serial.printf("[sec] rejected command: %s\n", err.c_str());
    return;
  }
  JsonDocument cmd;
  if (deserializeJson(cmd, body)) return;
  const char* c = cmd["cmd"] | "";
  if (strcmp(c, "ack") == 0) {
    if (strcmp(cmd["boot"] | "", gBootHex) != 0) return;   // ACK for a previous boot
    uint32_t seq = cmd["seq"] | 0;
    xSemaphoreTake(gLock, portMAX_DELAY);
    adjustForDrops();
    size_t removed = gOutbox.ackUpTo(seq);
    gSendIdx = gSendIdx > removed ? gSendIdx - removed : 0;
    if (removed) gLastAckProgressMs = millis();
    xSemaphoreGive(gLock);
  } else if (strcmp(c, "led_latch") == 0) {
    bool on = cmd["on"] | false;
    gDiscardLatch = on;
    gPrefs.putBool("latch", on);            // survives reboot until admin reset
    Serial.printf("[cmd] discard LED latch %s\n", on ? "SET" : "CLEARED (admin reset)");
  }
}

static void mqttConnect() {
  String status = topic("status");
  bool ok = gMqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASS, status.c_str(), 1, true, "offline");
  if (!ok) { Serial.printf("[mqtt] connect failed rc=%d\n", gMqtt.state()); return; }
  gMqtt.publish(status.c_str(), "online", true);
  gMqtt.subscribe(topic("cmd").c_str(), 1);
  xSemaphoreTake(gLock, portMAX_DELAY);
  gSendIdx = 0;                              // resend everything not yet ACKed
  xSemaphoreGive(gLock);
  gLastAckProgressMs = millis();
  Serial.println("[mqtt] connected");
}

static void drainOutbox() {
  if (!clockSynced()) return;                // server needs a real timestamp to verify
  uint32_t now = millis();
  if (now - gLastSendMs < SEND_SPACING_MS) return;

  xSemaphoreTake(gLock, portMAX_DELAY);
  adjustForDrops();
  size_t n = gOutbox.size();
  if (gSendIdx > n) gSendIdx = n;
  if (gSendIdx > 0 && now - gLastAckProgressMs > ACK_TIMEOUT_MS) {
    gSendIdx = 0;                            // no ACK progress: go back to oldest unacked
    gLastAckProgressMs = now;
  }
  if (gSendIdx >= n || gSendIdx >= SEND_WINDOW) { xSemaphoreGive(gLock); return; }
  Reading r = *gOutbox.at(gSendIdx);
  uint32_t dropped = gOutbox.dropped();
  xSemaphoreGive(gLock);

  String env = buildEnvelope(DEVICE_ID, DEVICE_HMAC_KEY, nowEpoch(), buildBody(r, n, dropped));
  if (gMqtt.publish(topic("telemetry").c_str(), env.c_str())) {
    xSemaphoreTake(gLock, portMAX_DELAY);
    adjustForDrops();
    if (gSendIdx == 0) gLastAckProgressMs = now;   // start the ACK timer
    // Only advance if the entry we sent is still at this index
    const Reading* cur = gOutbox.at(gSendIdx);
    if (cur && cur->seq == r.seq) gSendIdx++;
    xSemaphoreGive(gLock);
  }
  gLastSendMs = now;
}

static void netTask(void*) {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2);   // UTC epoch; display layer adds no TZ
  gMqtt.setServer(MQTT_HOST, MQTT_PORT);
  gMqtt.setBufferSize(MQTT_BUFFER_SIZE);
  gMqtt.setKeepAlive(15);
  gMqtt.setSocketTimeout(3);
  gMqtt.setCallback(onCommand);
  uint32_t lastWifiKick = 0, lastMqttTry = 0;

  for (;;) {
    uint32_t now = millis();
    gWifiOk = WiFi.status() == WL_CONNECTED;
    if (!gWifiOk) {
      gMqttOk = false;
      if (now - lastWifiKick > 15000) { WiFi.reconnect(); lastWifiKick = now; }
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }
    if (!gMqtt.connected()) {
      gMqttOk = false;
      if (now - lastMqttTry > MQTT_RETRY_MS) { lastMqttTry = now; mqttConnect(); }
    } else {
      gMqttOk = true;
      gMqtt.loop();
      drainOutbox();
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

// =====================================================================
void setup() {
  Serial.begin(115200);
  delay(200);
  pinMode(PIN_DOOR, INPUT_PULLUP);
  pinMode(PIN_POWER_BTN, INPUT_PULLUP);
  pinMode(PIN_RELAY, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_YELLOW, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  setRelay(false);

  gLock = xSemaphoreCreateMutex();
  gPrefs.begin("coldchain", false);
  gDiscardLatch = gPrefs.getBool("latch", false);   // restore latch across reboots
  snprintf(gBootHex, sizeof(gBootHex), "%08lx", (unsigned long)esp_random());

  displayBegin();
  sensors.begin();
  sensors.setResolution(12);
  sensors.setWaitForConversion(false);
  sensors.requestTemperatures();
  gConvStartMs = millis();
  gLastValidMs = millis();
  gDoorRaw = gDoorOpen = digitalRead(PIN_DOOR) == HIGH;

  Serial.printf("ColdChain %s fw %s boot %s\n", DEVICE_ID, FW_VERSION, gBootHex);
  xTaskCreatePinnedToCore(netTask, "net", 8192, nullptr, 1, nullptr, 0);
  delay(900);                       // first DS18B20 conversion
  sensorTick(millis());
  pushReading(RS_BOOT);
  gLastPublishMs = millis();
}

void loop() {
  uint32_t now = millis();
  serialTick();
  sensorTick(now);
  doorTick(now);
  powerTick(now);

  setRelay(gComp.update(gTemp, gSensorOk, gMains, now));
  TempState st = classifyTemp(gTemp, gSensorOk);

  if (now - gLastPublishMs >= PUBLISH_INTERVAL_MS) {   // every 5 s
    gLastPublishMs = now;
    pushReading(RS_PERIODIC);
  }
  indicatorsTick(now, st);

  if (now - gLastDisplayMs >= DISPLAY_PERIOD_MS) {
    gLastDisplayMs = now;
    xSemaphoreTake(gLock, portMAX_DELAY);
    size_t backlog = gOutbox.size();
    xSemaphoreGive(gLock);
    DisplayModel m{DEVICE_ID, gTemp, gSensorOk, stateName(st), gDoorOpen, gComp.isOn(),
                   gComp.lockoutRemaining(now) / 1000, gMains, gBatt, gWifiOk, gMqttOk,
                   backlog, gDiscardLatch, gDoorAlarm};
    displayRender(m);
  }
  delay(10);
}
