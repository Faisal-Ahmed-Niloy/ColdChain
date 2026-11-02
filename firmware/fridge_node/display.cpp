#include "display.h"
#include "config.h"
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

static Adafruit_SSD1306 oled(128, 64, &Wire, -1);
static bool gOk = false;

bool displayBegin() {
  Wire.begin(OLED_SDA, OLED_SCL);
  gOk = oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
  if (gOk) {
    oled.clearDisplay();
    oled.setTextColor(SSD1306_WHITE);
    oled.setTextSize(1);
    oled.setCursor(0, 0);
    oled.println("ColdChain node");
    oled.println("booting...");
    oled.display();
  }
  return gOk;
}

void displayRender(const DisplayModel& m) {
  if (!gOk) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);

  // Row 1: device id + link status
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.print(m.deviceId);
  oled.setCursor(74, 0);
  if (m.backlog > 1) {          // buffering: show how many readings wait to sync
    oled.print(m.mqttOk ? "SYNC " : "BUF ");
    oled.print((unsigned)m.backlog);
  } else {
    oled.print(m.wifiOk ? (m.mqttOk ? "ONLINE" : "WIFI") : "OFFLINE");
  }

  // Row 2: big temperature
  oled.setTextSize(3);
  oled.setCursor(0, 11);
  if (m.sensorOk) {
    oled.print(m.temp, 1);
    oled.setTextSize(1);
    oled.print(" C");
  } else {
    oled.print("--.-");
  }

  // Row 3: status word; inverted when not SAFE so it stands out
  oled.setTextSize(2);
  bool alert = strcmp(m.state, "SAFE") != 0;
  if (alert) {
    oled.fillRect(0, 35, 128, 16, SSD1306_WHITE);
    oled.setTextColor(SSD1306_BLACK);
  }
  oled.setCursor(2, 36);
  oled.print(m.discardLatch ? "DISCARD" : m.state);
  oled.setTextColor(SSD1306_WHITE);

  // Row 4: door / compressor / power / backlog
  oled.setTextSize(1);
  oled.setCursor(0, 54);
  oled.print(m.doorOpen ? (m.doorAlarm ? "DOOR!" : "Open ") : "Shut ");
  oled.print(m.compOn ? "C:ON " : (m.compLockoutS ? "C:wt " : "C:off"));
  oled.print(m.mains ? " AC" : " BAT");
  oled.print(m.batt);
  oled.print("%");
  oled.display();
}
