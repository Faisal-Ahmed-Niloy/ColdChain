// Compressor hysteresis controller and temperature status classification.
// Pure logic (no hardware calls) so it is easy to reason about and test.
#pragma once
#include <Arduino.h>
#include "config.h"

enum TempState : uint8_t { ST_SAFE = 0, ST_WARNING = 1, ST_BREACH = 2, ST_FAULT = 3 };

const char* stateName(TempState s);
TempState classifyTemp(float t, bool sensorOk);

class CompressorController {
 public:
  // Returns the desired relay state for this tick.
  //  - ON  when T >= 7.0 and the compressor has been OFF for >= 60 s
  //  - OFF when T <= 3.0
  //  - forced OFF when mains power is lost (compressor runs on mains only)
  //  - forced OFF when the sensor has faulted (alarm raised elsewhere)
  bool update(float t, bool sensorOk, bool mainsOn, uint32_t nowMs);
  bool isOn() const { return on_; }
  // ms the compressor still has to wait before it may start (0 = may start)
  uint32_t lockoutRemaining(uint32_t nowMs) const;

 private:
  void switchTo(bool on, uint32_t nowMs);
  bool on_ = false;
  // Treat boot as an OFF transition so a power-cycled compressor also respects
  // the 60 s minimum off-time (protects the compressor on restart).
  uint32_t lastOffMs_ = 0;
  bool everSwitched_ = false;
};
