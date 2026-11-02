// Ring-buffer outbox. EVERY reading (live or not) goes in here and stays until
// the server acknowledges its sequence number. This gives:
//   - >= 30 min buffering during Wi-Fi loss (capacity 720 x 5 s = 60 min)
//   - in-order sync (always sent oldest-first over one TCP connection)
//   - no duplicates (server accepts only seq > last seen for this boot, and ACKs)
#pragma once
#include <Arduino.h>
#include "config.h"

// Reason codes carried with every reading
enum Reason : uint8_t {
  RS_PERIODIC = 0, RS_DOOR = 1, RS_DOOR_ALARM = 2, RS_POWER = 3, RS_BOOT = 4, RS_DOOR_CLOSED_ALARM = 5
};
const char* reasonName(uint8_t r);

// flags bits
#define FL_DOOR_OPEN  0x01
#define FL_COMP_ON    0x02
#define FL_MAINS      0x04
#define FL_SENSOR_OK  0x08
#define FL_LED_LATCH  0x10

struct Reading {
  uint32_t seq;        // per-boot sequence number, starts at 1
  uint32_t ts;         // UTC epoch seconds at capture (0 = clock not yet synced)
  uint32_t upMs;       // millis() at capture, used to back-fill ts once NTP syncs
  int16_t  tempC100;   // temperature x 100 (e.g. 523 = 5.23 C)
  uint8_t  flags;
  uint8_t  batt;       // battery %
  uint8_t  state;      // TempState
  uint8_t  reason;     // Reason
};

class Outbox {
 public:
  // Adds a reading. When full, the OLDEST entry is dropped (counted in dropped()).
  void push(const Reading& r);
  size_t size() const { return count_; }
  // i = 0 is the oldest unacknowledged entry
  const Reading* at(size_t i) const;
  // Removes every entry with seq <= ackSeq from the front; returns how many were removed.
  size_t ackUpTo(uint32_t ackSeq);
  uint32_t dropped() const { return dropped_; }

 private:
  Reading buf_[OUTBOX_CAPACITY];
  size_t head_ = 0;
  size_t count_ = 0;
  uint32_t dropped_ = 0;
};
