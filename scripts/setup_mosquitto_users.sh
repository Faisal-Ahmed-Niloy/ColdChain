#!/usr/bin/env sh
# Creates config/passwd with one MQTT account per role (prompts for each password).
# Windows: run the same mosquitto_passwd commands in "C:\Program Files\mosquitto".
set -e
cd "$(dirname "$0")/.."
rm -f config/passwd
for u in backend fridge_a fridge_b simulator attacker; do
  echo "Password for MQTT user '$u':"
  if [ -f config/passwd ]; then mosquitto_passwd config/passwd "$u"; else mosquitto_passwd -c config/passwd "$u"; fi
done
echo "Done. Start the broker with: mosquitto -c config/mosquitto.conf -v"
