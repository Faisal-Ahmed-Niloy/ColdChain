# ColdChain — Vaccine & Medicine Storage Monitor (V1 for hackathon)

IoT cold-chain system for two vaccine fridges:

- **ESP32 nodes** keep each fridge at 3–7 °C on their own.
- **Signed MQTT** carries the data and survives Wi-Fi loss.
- **FastAPI web app** tracks each batch's 60-minute heat budget, enforces roles and keeps tamper-evident records.
- **AI layer** forecasts breaches 30 minutes ahead, scores compressor health and suggests where to move stock.

```
ESP32 (Fridge A) ─┐   signed MQTT    ┌─ Mosquitto ─┐      ┌─ FastAPI ── SQLite (hash-chained)
ESP32 (Fridge B) ─┘  (HMAC+ts+nonce) └─────────────┘ ───► ├─ AI engine (forecast, health, relocation)
                                                          └─ Web app (dashboard, inventory, logs, QR check)
```

## What's in the box

| Folder | What it is |
| --- | --- |
| `firmware/fridge_node/` | ESP32 Arduino sketch: sensor, door, compressor control, OLED, LEDs, buzzer, power button, 60-min outbox, HMAC |
| `backend/app/` | FastAPI server: MQTT ingest, security, heat budget, alerts, REST API, WebSocket, AI engine |
| `frontend/` | Web app (no build step): dashboard, inventory, logs and integrity, AI, admin, public QR page |
| `ml/` | Physics fridge model, training script, AI report generator |
| `simulator/` | Software twin of the node (same protocol) for demos without hardware |
| `scripts/` | Demo data, security attack demo, tamper demo, MQTT user setup |
| `tests/` | 36 automated tests (Python + host-compiled firmware logic) |
| `docs/ai_report.md` | The short AI report the brief asks for (dataset, features, results) |

## Quick start (software only, about 10 minutes)

Needs Python 3.10+ and Mosquitto 2.x ([mosquitto.org/download](https://mosquitto.org/download/)), or Docker.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                  # then edit the secrets (Windows: copy .env.example .env)

sh scripts/setup_mosquitto_users.sh   # creates config/passwd (MQTT users)
mosquitto -c config/mosquitto.conf -v # terminal 1

python -m ml.train                    # trains the forecaster for YOUR scikit-learn version (~1 min)
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000   # terminal 2
python scripts/seed_demo.py           # demo users + 6 vaccine batches
python -m simulator.fridge_sim --password <simulator MQTT password>   # terminal 3
```

Open http://localhost:8000 and log in as `admin` (password from `CC_ADMIN_PASSWORD`), `pharmacist / Pharma@123` or `inspector / Inspect@123`.

In the simulator terminal, type `power A`, `door B open`, `wifi A off`, `temp A 13`, `degrade A 0.4`, `status`.

## With the real ESP32 nodes

1. **Arduino IDE 2.x**: install the board package *esp32 by Espressif*. Then install these libraries from the Library Manager: **PubSubClient**, **ArduinoJson 7**, **Adafruit SSD1306**, **Adafruit GFX**, **OneWire**, **DallasTemperature**.
2. Copy `firmware/fridge_node/secrets.example.h` to `secrets.h`. Set Wi-Fi, the laptop's IP, the MQTT user/password, `DEVICE_ID` and `DEVICE_HMAC_KEY`. The key must equal that fridge's key in `.env` → `CC_DEVICE_KEYS`.
3. Board: *ESP32 Dev Module*. Upload. Serial Monitor at 115200 shows the boot id. Type `status` for live values and `simtemp 9.5` to force a temperature during tests.
4. Repeat for Fridge B with `DEVICE_ID "FRIDGE-B"`. To run one real node plus one simulated fridge: `python -m simulator.fridge_sim --devices FRIDGE-B`.

### Wiring (each node)

| Part | ESP32 pin | Notes |
| --- | --- | --- |
| DS18B20 data | GPIO 4 | 4.7 kΩ pull-up to 3V3; VCC 3V3, GND |
| Door reed switch | GPIO 27 ↔ GND | Internal pull-up. Magnet near = door closed |
| Compressor relay IN | GPIO 26 | Relay VCC 5V (VIN). Active-low modules: `RELAY_ACTIVE_LOW 1` |
| Power-fail button | GPIO 25 ↔ GND | Each press toggles simulated mains |
| Buzzer (+) | GPIO 33 | Active buzzer; NPN driver for 5 V buzzers |
| LEDs green / yellow / red | GPIO 18 / 19 / 23 | Each through a 220 Ω resistor to GND |
| OLED SDA / SCL | GPIO 21 / 22 | SSD1306 128×64 I²C at 0x3C, VCC 3V3 |
| Battery sense (optional) | GPIO 34 | 100k/100k divider from the battery +; set `USE_BATTERY_ADC 1` |

## Demo helpers

| Command | Shows |
| --- | --- |
| `python scripts/attack_demo.py --password <attacker pw>` | Replayed, tampered, forged and stale messages are rejected. They appear under Logs → Security events |
| `python scripts/tamper_demo.py` then **Verify Integrity** | An edited database row is caught. Undo with `--restore` |
| Admin → **Send test alert** | Telegram / e-mail delivery works |

## Tests

```bash
python -m pytest            # 36 tests, about 15 s, no broker or hardware needed
sh tests/firmware_host/run.sh   # firmware control + outbox logic compiled with g++
```

## Configuration

All settings are environment variables (see `.env.example`). Thresholds from the brief are in:

- `firmware/fridge_node/config.h`: 7 / 3 °C, 60 s off-time, 5 s publish, 60 s door alarm, 60-min outbox
- `backend/app/budget.py`: 60-minute budget, ×2 above 12 °C, At Risk below 20 %

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Node OLED shows `WIFI`, not `ONLINE` | Broker not reachable: check `MQTT_HOST`, laptop firewall port 1883, MQTT user/password |
| Every node message is rejected with `bad_signature` | `DEVICE_HMAC_KEY` in `secrets.h` differs from `CC_DEVICE_KEYS` |
| `stale_timestamp` rejections | Node has no NTP time. It needs internet once after boot, or a local NTP server |
| AI page says "Fallback active" | Run `python -m ml.train` (the model must be trained with your scikit-learn version) |
| QR link doesn't open on a phone | Set `CC_PUBLIC_BASE_URL` to the laptop's LAN IP, not localhost |
