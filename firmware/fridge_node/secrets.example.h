// Copy this file to secrets.h and fill in the values for ONE fridge.
// Flash fridge A with DEVICE_ID "FRIDGE-A" and its key, fridge B with "FRIDGE-B".
// The key must match the device's key in the backend (.env DEVICE_KEYS).
#pragma once

#define WIFI_SSID      "YourWiFi"
#define WIFI_PASSWORD  "YourWiFiPassword"

#define MQTT_HOST      "192.168.1.50"   // laptop running Mosquitto
#define MQTT_PORT      1883
#define MQTT_USER      "fridge_a"       // Mosquitto user for this node
#define MQTT_PASS      "change-me"

#define DEVICE_ID      "FRIDGE-A"
// Shared HMAC-SHA256 secret (32+ random characters). Generate with:
//   python -c "import secrets; print(secrets.token_hex(24))"
#define DEVICE_HMAC_KEY "replace-with-48-hex-chars-from-token_hex-24"
