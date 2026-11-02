"""Software twin of the ESP32 fridge node.

Implements the SAME protocol as firmware/fridge_node (signed envelope, per-boot
sequence numbers, 60-min outbox, cumulative ACKs, resend on timeout, discard-LED
latch, door alarm, power button) on top of the physics model. Used by:
  - simulator/fridge_sim.py  (demo without hardware, over real MQTT)
  - tests/                   (end-to-end protocol tests without a broker)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections import deque
from typing import Callable

import numpy as np

from backend.app.device_auth import build_envelope
from ml.physics import FridgeParams, FridgePhysics

PUBLISH_S = 5.0
DOOR_ALARM_S = 60.0
CAPACITY = 720           # 60 min at 5 s
SEND_WINDOW = 40
ACK_TIMEOUT_S = 8.0
TS_WINDOW = 120


def classify(t: float | None) -> str:
    if t is None:
        return "FAULT"
    if t > 8.0 or t < 2.0:
        return "BREACH"
    if t >= 7.0 or t < 3.0:
        return "WARNING"
    return "SAFE"


class SimDevice:
    def __init__(self, dev_id: str, key: str, params: FridgeParams | None = None, seed: int = 0,
                 clock: Callable[[], float] = time.time, start_temp: float = 5.0,
                 random_events: bool = False, prefix: str = "coldchain/v1/") -> None:
        self.dev, self.key, self.clock, self.prefix = dev_id, key, clock, prefix
        self.phys = FridgePhysics(params or FridgeParams(), np.random.default_rng(seed), temp=start_temp)
        self.random_events = random_events
        self.boot = secrets.token_hex(4)
        self.seq = 0
        self.outbox: deque[dict] = deque()
        self.dropped = 0
        self.send_idx = 0
        self.last_ack_progress = clock()
        self.wifi = True
        self.door = False
        self.door_since: float | None = None
        self.door_alarm = False
        self.mains = True
        self.temp_override: float | None = None
        self.led_latch = False
        self.rejected_cmds = 0
        self._seen_nonces: deque[str] = deque(maxlen=32)
        self._last_pub = -1e18
        self._last_meas: float | None = None
        self.sent_count = 0
        self._push("boot")

    # ------------------------------------------------------------ controls (what a person would do)
    def set_door(self, open_: bool) -> None:
        if open_ == self.door:
            return
        self.door = open_
        if open_:
            self.door_since = self.clock()
            self._push("door")
        else:
            was = self.door_alarm
            self.door_alarm, self.door_since = False, None
            self._push("door_closed_after_alarm" if was else "door")

    def toggle_power(self) -> None:
        self.mains = not self.mains
        self._push("power")

    def set_wifi(self, up: bool) -> None:
        if up and not self.wifi:
            self.send_idx = 0                    # reconnect: resend everything not ACKed
            self.last_ack_progress = self.clock()
        self.wifi = up

    def reboot(self) -> None:
        """RAM outbox is lost on a real reboot; a new boot id starts seq at 1."""
        self.boot, self.seq = secrets.token_hex(4), 0
        self.outbox.clear(); self.send_idx = 0
        self._push("boot")

    # ------------------------------------------------------------ firmware loop
    def tick(self, dt: float = 1.0) -> None:
        self.phys.step(dt, door=self.door, mains=self.mains, random_events=False)
        meas = self.phys.measured() if self.temp_override is None else self.temp_override
        self._last_meas = meas
        now = self.clock()
        if self.door and not self.door_alarm and self.door_since is not None and now - self.door_since > DOOR_ALARM_S:
            self.door_alarm = True
            self._push("door_alarm")
        if now - self._last_pub >= PUBLISH_S:
            self._last_pub = now
            self._push("periodic")

    def _push(self, reason: str) -> None:
        self.seq += 1
        t = self._last_meas if self._last_meas is not None else self.phys.measured()
        r = {"seq": self.seq, "boot": self.boot, "t": int(self.clock()), "temp": round(float(t), 2),
             "door": int(self.door), "comp": int(self.phys.comp), "power": "MAINS" if self.mains else "BATTERY",
             "batt": 100, "state": classify(t), "reason": reason, "led": int(self.led_latch)}
        if len(self.outbox) >= CAPACITY:
            self.outbox.popleft()
            self.dropped += 1
            self.send_idx = max(0, self.send_idx - 1)
        self.outbox.append(r)

    # ------------------------------------------------------------ network side
    def outgoing(self, max_n: int = 1000) -> list[tuple[str, bytes]]:
        if not self.wifi:
            return []
        now = self.clock()
        if self.send_idx > 0 and now - self.last_ack_progress > ACK_TIMEOUT_S:
            self.send_idx = 0
            self.last_ack_progress = now
        out = []
        while self.send_idx < len(self.outbox) and self.send_idx < SEND_WINDOW and len(out) < max_n:
            if self.send_idx == 0:
                self.last_ack_progress = now
            body = dict(self.outbox[self.send_idx], backlog=len(self.outbox), drop=self.dropped,
                        rej=self.rejected_cmds, rssi=-55, fw="sim-1.0")
            out.append((f"{self.prefix}{self.dev}/telemetry",
                        build_envelope(self.dev, self.key, int(now), json.dumps(body, separators=(",", ":")))))
            self.send_idx += 1
            self.sent_count += 1
        return out

    def receive(self, payload: bytes) -> str:
        """Verify a command exactly like protocol.cpp verifyEnvelope(). Returns 'ok' or the reason."""
        try:
            env = json.loads(payload)
        except ValueError:
            self.rejected_cmds += 1
            return "bad_json"
        if env.get("dev") != self.dev:
            self.rejected_cmds += 1
            return "wrong_device"
        if abs(self.clock() - env.get("ts", 0)) > TS_WINDOW:
            self.rejected_cmds += 1
            return "stale_timestamp"
        msg = f"{env['dev']}|{env['ts']}|{env['nonce']}|{env['body']}"
        sig = hmac.new(self.key.encode(), msg.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, env.get("sig", "")):
            self.rejected_cmds += 1
            return "bad_signature"
        if env["nonce"] in self._seen_nonces:
            self.rejected_cmds += 1
            return "replayed_nonce"
        self._seen_nonces.append(env["nonce"])
        cmd = json.loads(env["body"])
        if cmd.get("cmd") == "ack" and cmd.get("boot") == self.boot:
            removed = 0
            while self.outbox and self.outbox[0]["seq"] <= cmd["seq"]:
                self.outbox.popleft()
                removed += 1
            self.send_idx = max(0, self.send_idx - removed)
            if removed:
                self.last_ack_progress = self.clock()
        elif cmd.get("cmd") == "led_latch":
            self.led_latch = bool(cmd.get("on"))
        return "ok"

    @property
    def temp(self) -> float | None:
        return self._last_meas
