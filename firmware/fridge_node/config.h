// =====================================================================
// ColdChain Fridge Node — hardware pins, thresholds and protocol limits
// Target: ESP32 DevKit V1 (ESP32-WROOM-32), Arduino core 2.x or 3.x
// Per-fridge identity, Wi-Fi and keys live in secrets.h (not in git).
// =====================================================================
#pragma once

#define FW_VERSION "1.0.0"

// ---------- Pins ----------
#define PIN_ONEWIRE      4    // DS18B20 data (4.7 kOhm pull-up to 3V3)
#define PIN_DOOR         27   // Reed switch to GND, INPUT_PULLUP. LOW = magnet near = door CLOSED
#define PIN_RELAY        26   // Compressor relay IN
#define RELAY_ACTIVE_LOW 1    // Most blue 1-channel relay modules switch ON when IN is LOW
#define PIN_POWER_BTN    25   // Push button to GND: each press toggles simulated mains power
#define PIN_BUZZER       33   // Active buzzer (+) via NPN transistor, or direct for a small 3 V buzzer
#define PIN_LED_GREEN    18
#define PIN_LED_YELLOW   19
#define PIN_LED_RED      23
#define PIN_BATT_ADC     34   // Optional battery voltage divider (input-only pin, ADC1)
#define USE_BATTERY_ADC  0    // 1 = read real battery via divider, 0 = simulate battery %
#define BATT_DIVIDER     2.0f // Vbatt = Vadc * BATT_DIVIDER (100k/100k divider)
#define BATT_V_EMPTY     3.30f
#define BATT_V_FULL      4.15f
#define OLED_SDA         21
#define OLED_SCL         22
#define OLED_ADDR        0x3C

// ---------- Temperature thresholds (deg C) ----------
// Compressor rule from the brief (p.1): ON at 7, OFF at 3, 60 s minimum off-time.
#define T_COMP_ON        7.0f
#define T_COMP_OFF       3.0f
#define COMP_MIN_OFF_MS  60000UL

// Display status bands (the brief gives only 2-8 C; Warning band is our assumption):
//   BREACH  : T > 8.0 or T < 2.0
//   WARNING : 7.0 <= T <= 8.0 or 2.0 <= T < 3.0
//   SAFE    : otherwise
#define T_BREACH_HIGH    8.0f
#define T_BREACH_LOW     2.0f
#define T_WARN_HIGH      7.0f
#define T_WARN_LOW       3.0f

// ---------- Timing ----------
#define SENSOR_PERIOD_MS     1000UL   // DS18B20 non-blocking conversion cycle
#define PUBLISH_INTERVAL_MS  5000UL   // telemetry every 5 s (brief p.1)
#define DOOR_DEBOUNCE_MS     60UL
#define DOOR_ALARM_MS        60000UL  // door open > 60 s -> buzzer + alert (brief p.1)
#define BTN_DEBOUNCE_MS      250UL
#define DISPLAY_PERIOD_MS    500UL
#define SENSOR_FAULT_MS      10000UL  // no valid reading for this long -> FAULT state

// ---------- Offline outbox ----------
// 720 x 5 s = 60 min of readings (brief requires >= 30 min). ~20 bytes each = ~14 KB RAM.
#define OUTBOX_CAPACITY      720
#define SEND_SPACING_MS      40UL     // max ~25 msgs/s while draining a backlog
#define SEND_WINDOW          40       // max messages in flight before an ACK
#define ACK_TIMEOUT_MS       8000UL   // no ACK progress -> resend from oldest unacked

// ---------- Security ----------
#define MAX_TS_SKEW_S        120      // accepted |now - ts| for commands from the server
#define NONCE_CACHE_SIZE     32

// ---------- Network ----------
#define NTP_SERVER_1     "pool.ntp.org"
#define NTP_SERVER_2     "time.google.com"
#define TOPIC_PREFIX     "coldchain/v1/"
#define MQTT_BUFFER_SIZE 1024
#define MQTT_RETRY_MS    5000UL

// ---------- Demo / test hooks ----------
// Serial commands: "simtemp 9.5", "simtemp off", "status". Set to 0 for production.
#define DEMO_SERIAL_CMDS 1
