"""Run software fridge nodes over real MQTT (demo and testing without hardware).

    python -m simulator.fridge_sim                    # FRIDGE-A and FRIDGE-B
    python -m simulator.fridge_sim --devices FRIDGE-B # only B (A is the real ESP32)

Type commands while it runs (fridge letter A/B):
    door A open | door A close     wifi A off | wifi A on      power A   (toggle mains)
    temp A 9.5  | temp A off       degrade A 0.4 (compressor health 0-1)
    reboot A    status             quit
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paho.mqtt.client as mqtt  # noqa: E402

from backend.app.config import settings  # noqa: E402
from ml.physics import FridgeParams  # noqa: E402
from simulator.device import SimDevice  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", default=",".join(settings.device_keys))
    ap.add_argument("--host", default=settings.mqtt_host)
    ap.add_argument("--port", type=int, default=settings.mqtt_port)
    ap.add_argument("--user", default="simulator")
    ap.add_argument("--password", default="")
    ap.add_argument("--ambient", type=float, default=30.0)
    args = ap.parse_args()

    devs: dict[str, SimDevice] = {}
    clients: dict[str, mqtt.Client] = {}
    lock = threading.Lock()
    for i, dev_id in enumerate(args.devices.split(",")):
        dev_id = dev_id.strip()
        p = FridgeParams(ambient=args.ambient, k_leak=0.008, cool_rate=0.8)
        devs[dev_id] = SimDevice(dev_id, settings.device_keys[dev_id], params=p, seed=i + 1,
                                 start_temp=4.5 + i)
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{dev_id}")
        if args.user:
            c.username_pw_set(args.user, args.password or None)
        c.will_set(f"{settings.topic_prefix}{dev_id}/status", b"offline", qos=1, retain=True)

        def on_connect(cl, ud, flags, rc, props, dev_id=dev_id):
            cl.subscribe(f"{settings.topic_prefix}{dev_id}/cmd", qos=1)
            cl.publish(f"{settings.topic_prefix}{dev_id}/status", b"online", qos=1, retain=True)
            with lock:
                devs[dev_id].set_wifi(True)

        def on_message(cl, ud, msg, dev_id=dev_id):
            with lock:
                res = devs[dev_id].receive(msg.payload)
            if res != "ok":
                print(f"[{dev_id}] rejected command: {res}")

        c.on_connect, c.on_message = on_connect, on_message
        c.connect(args.host, args.port, keepalive=15)
        c.loop_start()
        clients[dev_id] = c

    def find(letter: str) -> str | None:
        return next((d for d in devs if d.endswith(letter.upper())), None)

    def console() -> None:
        for line in sys.stdin:
            parts = line.split()
            if not parts:
                continue
            cmd, d = parts[0].lower(), find(parts[1]) if len(parts) > 1 else None
            with lock:
                dev = devs.get(d) if d else None
                if cmd == "quit":
                    import os
                    os._exit(0)
                elif cmd == "status":
                    for k, v in devs.items():
                        print(f"{k}: T={v.temp:.2f} door={v.door} comp={v.phys.comp} mains={v.mains} "
                              f"wifi={v.wifi} queue={len(v.outbox)} led={v.led_latch} health={v.phys.health_now:.2f}")
                elif dev is None:
                    print("unknown fridge; use A or B")
                elif cmd == "door":
                    dev.set_door(parts[2] == "open")
                elif cmd == "wifi":
                    dev.set_wifi(parts[2] == "on")
                elif cmd == "power":
                    dev.toggle_power()
                elif cmd == "temp":
                    dev.temp_override = None if parts[2] == "off" else float(parts[2])
                elif cmd == "degrade":
                    dev.phys.health_now = float(parts[2])
                elif cmd == "reboot":
                    dev.reboot()
                else:
                    print(__doc__)
                    continue
                print("ok")

    threading.Thread(target=console, daemon=True).start()
    print(f"simulating {', '.join(devs)} -> {args.host}:{args.port}. Type 'status' or 'quit'.")
    last_print = 0.0
    while True:
        t0 = time.time()
        with lock:
            for dev_id, dev in devs.items():
                dev.tick(1.0)
                connected = clients[dev_id].is_connected()
                if not connected and dev.wifi:
                    dev.set_wifi(False)
                for topic, payload in dev.outgoing(max_n=25):
                    clients[dev_id].publish(topic, payload, qos=0)
        if time.time() - last_print > 30:
            last_print = time.time()
            with lock:
                print("  ".join(f"{k} {v.temp:.1f}C {'ON' if v.phys.comp else 'off'} q={len(v.outbox)}"
                                for k, v in devs.items()))
        time.sleep(max(0.0, 1.0 - (time.time() - t0)))


if __name__ == "__main__":
    main()
