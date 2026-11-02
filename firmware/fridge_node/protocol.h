// Signed message envelope shared with the backend (backend/app/device_auth.py).
//
//   {"dev":"FRIDGE-A","ts":1730457600,"nonce":"9f2c...","body":"<JSON string>","sig":"<hex>"}
//   sig = HMAC-SHA256(key, dev + "|" + ts + "|" + nonce + "|" + body)
//
// `ts` is the SEND time (freshness / replay window). The measurement time of a
// buffered reading is inside `body` ("t"), so 30-min-old buffered data is still
// accepted while replayed packets are not.
#pragma once
#include <Arduino.h>
#include "config.h"

String hmacSha256Hex(const char* key, const String& msg);
bool constTimeEquals(const String& a, const String& b);
String randomNonce();  // 16 random bytes -> 32 hex chars (hardware RNG)

// Wraps a body JSON string into a signed envelope.
String buildEnvelope(const char* dev, const char* key, uint32_t ts, const String& body);

// Verifies a command envelope from the server. On success, fills bodyOut.
// Rejects: bad JSON, wrong device, stale/future timestamp, reused nonce, bad signature.
bool verifyEnvelope(const char* dev, const char* key, const uint8_t* payload, unsigned int len,
                    uint32_t nowEpoch, String& bodyOut, String& errOut);
