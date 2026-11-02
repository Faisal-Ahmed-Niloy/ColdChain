// Host-side unit tests for firmware/fridge_node/control.cpp and outbox.cpp.
// Build & run (see tests/firmware_host/run.sh)

#include <cassert>
#include <cstdio>
#include "control.h"
#include "outbox.h"

static int fails = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)

int main() {
  // ---- status bands
  CHECK(classifyTemp(5.0f, true) == ST_SAFE);
  CHECK(classifyTemp(7.0f, true) == ST_WARNING);
  CHECK(classifyTemp(8.0f, true) == ST_WARNING);
  CHECK(classifyTemp(8.1f, true) == ST_BREACH);
  CHECK(classifyTemp(2.5f, true) == ST_WARNING);
  CHECK(classifyTemp(1.9f, true) == ST_BREACH);
  CHECK(classifyTemp(5.0f, false) == ST_FAULT);

  // ---- compressor: ON at 7, OFF at 3, 60 s minimum off-time
  CompressorController c;
  uint32_t t = 0;
  CHECK(!c.update(7.5f, true, true, t += 1000));          // boot counts as OFF -> locked out
  CHECK(c.update(7.5f, true, true, t = 61000));          // after 60 s it may start
  CHECK(c.update(5.0f, true, true, t += 1000));          // stays ON in the band
  CHECK(!c.update(3.0f, true, true, t += 1000));         // OFF at 3.0
  uint32_t offAt = t;
  CHECK(!c.update(9.0f, true, true, offAt + 30000));     // hot again but only 30 s off -> wait
  CHECK(c.lockoutRemaining(offAt + 30000) == 30000);
  CHECK(!c.update(9.0f, true, true, offAt + 59999));
  CHECK(c.update(9.0f, true, true, offAt + 60000));      // exactly 60 s -> ON
  CHECK(!c.update(9.0f, true, false, offAt + 61000));    // mains lost -> forced OFF
  CHECK(!c.update(9.0f, true, true, offAt + 62000));     // mains back, but min off-time restarts
  CHECK(c.update(9.0f, true, true, offAt + 121000));
  CHECK(!c.update(9.0f, false, true, offAt + 122000));   // sensor fault -> OFF
  // millis() wrap-around safety
  CompressorController w;
  uint32_t near = 0xFFFFFFFFu - 10000u;
  w.update(7.5f, true, true, near);
  CHECK(w.update(7.5f, true, true, near + 70000u));      // wraps past 0, still counts 70 s

  // ---- outbox: order, ACK, overflow
  static Outbox ob;
  for (uint32_t s = 1; s <= 10; s++) { Reading r{}; r.seq = s; ob.push(r); }
  CHECK(ob.size() == 10 && ob.at(0)->seq == 1 && ob.at(9)->seq == 10);
  CHECK(ob.ackUpTo(4) == 4 && ob.at(0)->seq == 5);
  CHECK(ob.ackUpTo(4) == 0);                              // duplicate ACK is harmless
  for (uint32_t s = 11; s <= OUTBOX_CAPACITY + 20; s++) { Reading r{}; r.seq = s; ob.push(r); }
  CHECK(ob.size() == OUTBOX_CAPACITY);
  CHECK(ob.dropped() == 16);                              // 6 left + 730 pushed - 720 capacity
  CHECK(ob.at(OUTBOX_CAPACITY - 1)->seq == OUTBOX_CAPACITY + 20);
  CHECK(ob.at(0)->seq == 21);
  CHECK(OUTBOX_CAPACITY * PUBLISH_INTERVAL_MS / 60000 >= 30);   // >= 30 min buffering

  printf(fails ? "%d FAILED\n" : "all firmware logic checks passed\n", fails);
  return fails ? 1 : 0;
}
