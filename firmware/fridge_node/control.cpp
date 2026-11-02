#include "control.h"

const char* stateName(TempState s) {
  switch (s) {
    case ST_SAFE:    return "SAFE";
    case ST_WARNING: return "WARNING";
    case ST_BREACH:  return "BREACH";
    default:         return "FAULT";
  }
}

TempState classifyTemp(float t, bool sensorOk) {
  if (!sensorOk) return ST_FAULT;
  if (t > T_BREACH_HIGH || t < T_BREACH_LOW) return ST_BREACH;
  if (t >= T_WARN_HIGH || t < T_WARN_LOW) return ST_WARNING;
  return ST_SAFE;
}

void CompressorController::switchTo(bool on, uint32_t nowMs) {
  if (on == on_) return;
  on_ = on;
  everSwitched_ = true;
  if (!on) lastOffMs_ = nowMs;
}

uint32_t CompressorController::lockoutRemaining(uint32_t nowMs) const {
  if (on_) return 0;
  uint32_t offFor = nowMs - lastOffMs_;  // unsigned math is safe across millis() wrap
  return offFor >= COMP_MIN_OFF_MS ? 0 : COMP_MIN_OFF_MS - offFor;
}

bool CompressorController::update(float t, bool sensorOk, bool mainsOn, uint32_t nowMs) {
  if (!mainsOn || !sensorOk) {          // no mains or no trustworthy reading -> stop
    switchTo(false, nowMs);
    return on_;
  }
  if (on_) {
    if (t <= T_COMP_OFF) switchTo(false, nowMs);
  } else {
    if (t >= T_COMP_ON && lockoutRemaining(nowMs) == 0) switchTo(true, nowMs);
  }
  return on_;
}
