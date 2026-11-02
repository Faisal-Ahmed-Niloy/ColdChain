"""Security demo for judges: shows that forged, tampered and replayed device
messages are rejected and logged (Logs page -> Security events).

    python scripts/attack_demo.py --device FRIDGE-A

1. captures one genuine telemetry message from the broker
2. replays it unchanged                -> replayed_nonce
3. changes the temperature in it        -> bad_signature
4. signs a message with a wrong key     -> bad_signature
5. sends a message with an old timestamp-> stale_timestamp
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paho.mqtt.client as mqtt  # noqa: E402

from backend.app.config import settings  # noqa: E402
from backend.app.device_auth import build_envelope  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="FRIDGE-A")
    ap.add_argument("--host", default=settings.mqtt_host)
    ap.add_argument("--port", type=int, default=settings.mqtt_port)
    ap.add_argument("--user", default="attacker")
    ap.add_argument("--password", default="")
    args = ap.parse_args()
    topic = f"{settings.topic_prefix}{args.device}/telemetry"
    captured: list[bytes] = []
    got = threading.Event()

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="attack-demo")
    if args.user:
        c.username_pw_set(args.user, args.password or None)
    c.on_message = lambda cl, ud, msg: (captured.append(msg.payload), got.set())
    c.connect(args.host, args.port)
    c.subscribe(topic)
    c.loop_start()
    print(f"waiting for a genuine message on {topic} ...")
    if not got.wait(30):
        sys.exit("no message captured - is the node or simulator running?")
    c.unsubscribe(topic)
    genuine = captured[0]
    env = json.loads(genuine)
    body = json.loads(env["body"])

    print("1) replaying the captured message unchanged")
    c.publish(topic, genuine)
    time.sleep(1)

    print("2) tampering: changing the temperature without the key")
    env2 = dict(env)
    env2["body"] = json.dumps({**body, "temp": 4.0, "state": "SAFE", "seq": body["seq"] + 1000})
    c.publish(topic, json.dumps(env2))
    time.sleep(1)

    print("3) forging a message signed with a guessed key")
    c.publish(topic, build_envelope(args.device, "guessed-key", int(time.time()), {**body, "seq": body["seq"] + 2000}))
    time.sleep(1)

    print("4) sending a message with a 10-minute-old timestamp")
    c.publish(topic, build_envelope(args.device, "guessed-key", int(time.time()) - 600, body))
    time.sleep(1)
    c.loop_stop()
    print("done - open Logs -> Security events in the dashboard, then run Verify Integrity")


if __name__ == "__main__":
    main()
