#!/usr/bin/env sh
# Compiles the ESP32 firmware's pure-logic files for the host and runs their tests.
set -e
cd "$(dirname "$0")"
g++ -std=c++17 -Wall -Wextra -I. -I../../firmware/fridge_node test_firmware_logic.cpp \
    ../../firmware/fridge_node/control.cpp ../../firmware/fridge_node/outbox.cpp -o "${TMPDIR:-/tmp}/fw_test"
"${TMPDIR:-/tmp}/fw_test"
