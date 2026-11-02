#include "protocol.h"
#include <ArduinoJson.h>
#include "mbedtls/md.h"
#include <esp_system.h>   // esp_random() on Arduino core 2.x and 3.x

String hmacSha256Hex(const char* key, const String& msg) {
  uint8_t out[32];
  const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_hmac(info, (const unsigned char*)key, strlen(key),
                  (const unsigned char*)msg.c_str(), msg.length(), out);
  char hex[65];
  for (int i = 0; i < 32; i++) sprintf(hex + 2 * i, "%02x", out[i]);
  hex[64] = 0;
  return String(hex);
}

bool constTimeEquals(const String& a, const String& b) {
  if (a.length() != b.length()) return false;
  uint8_t diff = 0;
  for (size_t i = 0; i < a.length(); i++) diff |= (uint8_t)(a[i] ^ b[i]);
  return diff == 0;
}

String randomNonce() {
  char hex[33];
  for (int i = 0; i < 4; i++) sprintf(hex + 8 * i, "%08lx", (unsigned long)esp_random());
  hex[32] = 0;
  return String(hex);
}

String buildEnvelope(const char* dev, const char* key, uint32_t ts, const String& body) {
  String nonce = randomNonce();
  String signing = String(dev) + "|" + String(ts) + "|" + nonce + "|" + body;
  JsonDocument env;
  env["dev"] = dev;
  env["ts"] = ts;
  env["nonce"] = nonce;
  env["body"] = body;  // embedded as an escaped string: exact bytes are what was signed
  env["sig"] = hmacSha256Hex(key, signing);
  String out;
  serializeJson(env, out);
  return out;
}

// Small ring of recently seen command nonces (replay protection on the device side).
static String gSeenNonces[NONCE_CACHE_SIZE];
static size_t gNonceIdx = 0;

static bool nonceSeen(const String& n) {
  for (size_t i = 0; i < NONCE_CACHE_SIZE; i++)
    if (gSeenNonces[i] == n) return true;
  return false;
}

bool verifyEnvelope(const char* dev, const char* key, const uint8_t* payload, unsigned int len,
                    uint32_t nowEpoch, String& bodyOut, String& errOut) {
  JsonDocument env;
  if (deserializeJson(env, payload, len)) { errOut = "bad_json"; return false; }
  const char* d = env["dev"] | "";
  uint32_t ts = env["ts"] | 0;
  String nonce = env["nonce"] | "";
  String body = env["body"] | "";
  String sig = env["sig"] | "";
  if (strcmp(d, dev) != 0) { errOut = "wrong_device"; return false; }
  if (nonce.length() < 16 || sig.length() != 64) { errOut = "malformed"; return false; }
  if (nowEpoch < 1700000000UL) { errOut = "clock_not_synced"; return false; }
  long skew = (long)nowEpoch - (long)ts;
  if (skew > MAX_TS_SKEW_S || skew < -MAX_TS_SKEW_S) { errOut = "stale_timestamp"; return false; }
  String signing = String(d) + "|" + String(ts) + "|" + nonce + "|" + body;
  if (!constTimeEquals(hmacSha256Hex(key, signing), sig)) { errOut = "bad_signature"; return false; }
  if (nonceSeen(nonce)) { errOut = "replayed_nonce"; return false; }
  gSeenNonces[gNonceIdx] = nonce;
  gNonceIdx = (gNonceIdx + 1) % NONCE_CACHE_SIZE;
  bodyOut = body;
  return true;
}
