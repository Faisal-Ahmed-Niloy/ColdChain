#include "outbox.h"

const char* reasonName(uint8_t r) {
  switch (r) {
    case RS_PERIODIC:          return "periodic";
    case RS_DOOR:              return "door";
    case RS_DOOR_ALARM:        return "door_alarm";
    case RS_POWER:             return "power";
    case RS_BOOT:              return "boot";
    case RS_DOOR_CLOSED_ALARM: return "door_closed_after_alarm";
    default:                   return "unknown";
  }
}

void Outbox::push(const Reading& r) {
  if (count_ == OUTBOX_CAPACITY) {     // full: overwrite the oldest
    head_ = (head_ + 1) % OUTBOX_CAPACITY;
    count_--;
    dropped_++;
  }
  buf_[(head_ + count_) % OUTBOX_CAPACITY] = r;
  count_++;
}

const Reading* Outbox::at(size_t i) const {
  if (i >= count_) return nullptr;
  return &buf_[(head_ + i) % OUTBOX_CAPACITY];
}

size_t Outbox::ackUpTo(uint32_t ackSeq) {
  size_t removed = 0;
  while (count_ > 0 && buf_[head_].seq <= ackSeq) {
    head_ = (head_ + 1) % OUTBOX_CAPACITY;
    count_--;
    removed++;
  }
  return removed;
}
