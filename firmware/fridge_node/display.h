// 0.96" SSD1306 128x64 I2C OLED status screen
#pragma once
#include <Arduino.h>

struct DisplayModel {
  const char* deviceId;
  float temp;
  bool sensorOk;
  const char* state;      // SAFE / WARNING / BREACH / FAULT
  bool doorOpen;
  bool compOn;
  uint32_t compLockoutS;  // seconds of min off-time remaining
  bool mains;
  uint8_t batt;
  bool wifiOk;
  bool mqttOk;
  size_t backlog;         // readings waiting in the outbox
  bool discardLatch;
  bool doorAlarm;
};

bool displayBegin();
void displayRender(const DisplayModel& m);
