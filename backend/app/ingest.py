"""MQTT message processing — the heart of the backend.

`Ingestor.handle(topic, payload)` is transport-independent: the MQTT bridge
feeds it real messages, the tests feed it simulator messages directly. It returns
the outgoing messages (ACKs and commands) the caller must publish.

Per telemetry message:
  1. verify HMAC + timestamp + nonce  -> reject & log on failure
  2. de-duplicate / order by (boot, seq) -> duplicates are ACKed but not stored
  3. store reading in the hash chain
  4. update live fridge state (only for the newest reading)
  5. integrate heat-exposure budget, raise alerts, latch the discard LED
  6. ACK the highest accepted seq so the node can free its outbox
"""
from __future__ import annotations

import json
import time
from typing import Callable

from . import alerts, audit, budget, db, events, hashchain
from .config import settings
from .device_auth import AuthError, build_envelope, prune_nonces, verify_envelope

Outgoing = list[tuple[str, bytes]]

VALID_POWER = {"MAINS", "BATTERY"}
VALID_STATES = {"SAFE", "WARNING", "BREACH", "FAULT"}


class BodyError(ValueError):
    pass


def _validate_body(b: dict, now: float) -> dict:
    """Strict schema check of the signed body. Raises BodyError."""
    def need(cond: bool, what: str) -> None:
        if not cond:
            raise BodyError(what)

    seq, boot, t = b.get("seq"), b.get("boot"), b.get("t")
    need(isinstance(seq, int) and seq >= 1, "seq")
    need(isinstance(boot, str) and 1 <= len(boot) <= 16, "boot")
    need(isinstance(t, (int, float)) and 1_600_000_000 < t <= now + settings.ts_window_s, "t")
    temp = b.get("temp")
    need(temp is None or (isinstance(temp, (int, float)) and -50 <= temp <= 90), "temp")
    door, comp = b.get("door"), b.get("comp")
    need(door in (0, 1) and comp in (0, 1), "door/comp")
    power = b.get("power")
    need(power in VALID_POWER, "power")
    batt = b.get("batt")
    need(batt is None or (isinstance(batt, int) and 0 <= batt <= 100), "batt")
    state = b.get("state", "SAFE")
    need(state in VALID_STATES, "state")
    return {
        "seq": seq, "boot": boot, "ts": float(t), "temp": None if temp is None else float(temp),
        "door": door, "comp": comp, "power": power, "batt": batt, "state": state,
        "reason": str(b.get("reason", "periodic"))[:32], "led": 1 if b.get("led") else 0,
        "backlog": int(b.get("backlog", 0) or 0), "drop": int(b.get("drop", 0) or 0),
        "rssi": b.get("rssi") if isinstance(b.get("rssi"), int) else None,
    }


class Ingestor:
    def __init__(self, clock: Callable[[], float] = time.time,
                 publisher: Callable[[str, bytes], None] | None = None) -> None:
        self.clock = clock
        self.publisher = publisher
        self._last_led_cmd: dict[str, float] = {}
        self._last_prune = 0.0

    # ------------------------------------------------------------ helpers
    def _topic(self, dev: str, leaf: str) -> str:
        return f"{settings.topic_prefix}{dev}/{leaf}"

    def command(self, conn, dev: str, body: dict, now: float) -> tuple[str, bytes]:
        key = conn.execute("SELECT hmac_key FROM devices WHERE device_id=?", (dev,)).fetchone()["hmac_key"]
        return self._topic(dev, "cmd"), build_envelope(dev, key, int(now), body)

    def send_command(self, dev: str, body: dict) -> bool:
        """Used outside of ingest (admin reset, etc.). Returns False if MQTT is down;
        LED reconciliation re-sends on the next telemetry anyway."""
        now = self.clock()
        with db.read() as conn:
            topic, payload = self.command(conn, dev, body, now)
        if self.publisher is None:
            return False
        try:
            self.publisher(topic, payload)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------ entry point
    def handle(self, topic: str, payload: bytes) -> Outgoing:
        parts = topic.split("/")
        if len(parts) != 4 or f"{parts[0]}/{parts[1]}/" != settings.topic_prefix:
            return []
        dev, leaf = parts[2], parts[3]
        now = self.clock()
        if leaf == "status":
            return self._handle_status(dev, payload, now)
        if leaf == "telemetry":
            return self._handle_telemetry(dev, payload, now, topic)
        return []

    def _handle_status(self, dev: str, payload: bytes, now: float) -> Outgoing:
        online = 1 if payload.strip() == b"online" else 0
        with db.tx() as conn:
            f = conn.execute("SELECT id, online FROM fridges WHERE device_id=?", (dev,)).fetchone()
            if not f or f["online"] == online:
                return []
            conn.execute("UPDATE fridges SET online=? WHERE id=?", (online, f["id"]))
            audit.log(conn, now, f"device:{dev}", "device.online" if online else "device.offline", {})
            if not online:
                alerts.raise_alert(conn, now, f["id"], "device_offline", "warning",
                                   f"{f['id']} node went offline (buffering locally up to 60 min)")
        events.publish({"type": "fridge", "fridge_id": f["id"]})
        return []

    def _handle_telemetry(self, dev: str, payload: bytes, now: float, topic: str) -> Outgoing:
        out: Outgoing = []
        evts: list[dict] = []
        with db.tx() as conn:
            # 1. authenticate
            try:
                _, body = verify_envelope(conn, payload, dev, now, settings.ts_window_s)
                r = _validate_body(body, now)
            except (AuthError, BodyError) as e:
                kind = e.reason if isinstance(e, AuthError) else "bad_body"
                detail = getattr(e, "detail", "") or str(e)
                audit.security_event(conn, now, dev, kind, detail, topic)
                evts.append({"type": "security", "device_id": dev, "kind": kind})
                self._emit(evts)
                return []

            d = conn.execute("SELECT * FROM devices WHERE device_id=?", (dev,)).fetchone()
            fridge_id = d["fridge_id"]

            # 2. order + de-duplicate within a boot
            if d["boot"] != r["boot"]:
                if d["boot"] is not None:
                    audit.log(conn, now, f"device:{dev}", "device.reboot",
                              {"old_boot": d["boot"], "new_boot": r["boot"], "first_seq": r["seq"]})
                conn.execute("UPDATE devices SET boot=?, last_seq=0 WHERE device_id=?", (r["boot"], dev))
                last_seq = 0
            else:
                last_seq = d["last_seq"]
            if r["seq"] <= last_seq:
                conn.execute("UPDATE devices SET duplicates=duplicates+1 WHERE device_id=?", (dev,))
                out.append(self.command(conn, dev, {"cmd": "ack", "boot": r["boot"], "seq": last_seq}, now))
                return out
            if r["seq"] > last_seq + 1 and last_seq > 0:
                conn.execute("UPDATE devices SET gaps=gaps+1 WHERE device_id=?", (dev,))
                audit.log(conn, now, f"device:{dev}", "device.sequence_gap",
                          {"expected": last_seq + 1, "got": r["seq"], "node_dropped_total": r["drop"]})

            # 3. store in the hash chain
            buffered = 1 if now - r["ts"] > 15 else 0
            hashchain.append(conn, "readings", {
                "device_id": dev, "fridge_id": fridge_id, "boot": r["boot"], "seq": r["seq"], "ts": r["ts"],
                "temp": r["temp"], "door": r["door"], "comp": r["comp"], "power": r["power"],
                "batt": r["batt"], "state": r["state"], "reason": r["reason"], "led": r["led"],
                "buffered": buffered, "received_at": now,
            }, settings.log_seal_key)
            conn.execute("UPDATE devices SET last_seq=?, accepted=accepted+1 WHERE device_id=?", (r["seq"], dev))

            # 4. live state
            f = conn.execute("SELECT * FROM fridges WHERE id=?", (fridge_id,)).fetchone()
            is_newest = f["state_ts"] is None or r["ts"] >= f["state_ts"]
            if is_newest:
                door_since = None
                if r["door"] == 1:
                    door_since = f["door_open_since"] if f["door"] == 1 and f["door_open_since"] else r["ts"]
                breach_since = None
                if r["state"] == "BREACH":
                    breach_since = f["breach_since"] if f["state"] == "BREACH" and f["breach_since"] else r["ts"]
                conn.execute(
                    """UPDATE fridges SET online=1, last_seen=?, state_ts=?, temp=?, state=?, breach_since=?, door=?,
                       door_open_since=?, comp=?, power=?, batt=?, led_reported=?, backlog=?, rssi=?
                       WHERE id=?""",
                    (now, r["ts"], r["temp"], r["state"], breach_since, r["door"], door_since, r["comp"],
                     r["power"], r["batt"], r["led"], r["backlog"], r["rssi"], fridge_id))
            else:
                conn.execute("UPDATE fridges SET online=1, last_seen=? WHERE id=?", (now, fridge_id))
            if not f["online"]:
                audit.log(conn, now, f"device:{dev}", "device.online", {})

            # 5a. temperature / power / door alerts (only on state changes)
            delayed = " (reported late from offline buffer)" if buffered else ""
            if is_newest:
                if r["state"] == "BREACH" and f["state"] != "BREACH":
                    t = "unknown" if r["temp"] is None else f"{r['temp']:.1f} °C"
                    alerts.raise_alert(conn, now, fridge_id, "breach", "critical",
                                       f"{fridge_id} temperature {t} is outside 2–8 °C{delayed}", cooldown_s=600)
                if r["power"] == "BATTERY" and f["power"] != "BATTERY":
                    alerts.raise_alert(conn, now, fridge_id, "power_lost", "critical",
                                       f"{fridge_id} lost mains power, running on battery ({r['batt']}%){delayed}", cooldown_s=60)
                    audit.log(conn, now, f"device:{dev}", "power.lost", {"batt": r["batt"], "ts": r["ts"]})
                elif r["power"] == "MAINS" and f["power"] == "BATTERY":
                    alerts.raise_alert(conn, now, fridge_id, "power_restored", "info",
                                       f"{fridge_id} mains power restored{delayed}")
                    audit.log(conn, now, f"device:{dev}", "power.restored", {"ts": r["ts"]})
            if r["reason"] == "door_alarm":
                f2 = conn.execute("SELECT door_open_since, last_door_alert_for FROM fridges WHERE id=?",
                                  (fridge_id,)).fetchone()
                episode = f2["door_open_since"] or r["ts"]
                if f2["last_door_alert_for"] != episode:
                    conn.execute("UPDATE fridges SET last_door_alert_for=? WHERE id=?", (episode, fridge_id))
                    alerts.raise_alert(conn, now, fridge_id, "door_alarm", "critical",
                                       f"{fridge_id} door open for more than 60 s{delayed}")
                    audit.log(conn, now, f"device:{dev}", "door.alarm", {"since": episode})

            # 5b. heat-exposure budget
            for tr in budget.process_reading(conn, fridge_id, r["ts"], r["temp"]):
                audit.log(conn, now, "system", f"batch.status.{tr['new'].lower()}", tr)
                evts.append({"type": "batch", **tr})
                if tr["new"] == budget.AT_RISK:
                    alerts.raise_alert(conn, now, fridge_id, "at_risk", "warning",
                                       f"Batch {tr['vaccine']} lot {tr['lot_no']} is AT RISK "
                                       f"({budget.remaining_pct(tr['exposure_min'])}% heat budget left)")
                elif tr["new"] == budget.DISCARD:
                    alerts.raise_alert(conn, now, fridge_id, "discard", "critical",
                                       f"Batch {tr['vaccine']} lot {tr['lot_no']} must be DISCARDED "
                                       f"(heat budget used up) - red LED latched on {fridge_id}")
                    conn.execute("UPDATE fridges SET led_latch=1 WHERE id=?", (fridge_id,))

            # 5c. keep the node's red LED in sync with the desired latch (self-healing)
            want = conn.execute("SELECT led_latch FROM fridges WHERE id=?", (fridge_id,)).fetchone()["led_latch"]
            if not buffered and r["led"] != want and now - self._last_led_cmd.get(dev, 0) > 10:
                self._last_led_cmd[dev] = now
                out.append(self.command(conn, dev, {"cmd": "led_latch", "on": bool(want)}, now))

            # 6. cumulative ACK
            out.append(self.command(conn, dev, {"cmd": "ack", "boot": r["boot"], "seq": r["seq"]}, now))
            if now - self._last_prune > 60:
                prune_nonces(conn, now, settings.ts_window_s)
                self._last_prune = now
            evts.append({"type": "fridge", "fridge_id": fridge_id, "buffered": buffered})
        self._emit(evts)
        return out

    @staticmethod
    def _emit(evts: list[dict]) -> None:
        for e in evts:
            events.publish(e)

    # ------------------------------------------------------------ periodic checks
    def watchdog(self) -> None:
        """Server-side safety net: door alarm if the node's own alarm message was lost,
        and offline detection if the broker's last-will never arrived."""
        now = self.clock()
        changed = []
        with db.tx() as conn:
            for f in conn.execute("SELECT * FROM fridges").fetchall():
                if f["online"] and f["last_seen"] and now - f["last_seen"] > settings.offline_after_s:
                    conn.execute("UPDATE fridges SET online=0 WHERE id=?", (f["id"],))
                    audit.log(conn, now, "system", "device.offline", {"fridge": f["id"], "detected_by": "watchdog"})
                    alerts.raise_alert(conn, now, f["id"], "device_offline", "warning",
                                       f"{f['id']} has not reported for {int(now - f['last_seen'])} s")
                    changed.append(f["id"])
                since = f["door_open_since"]
                if (f["online"] and f["door"] == 1 and since and now - since > settings.door_alarm_s
                        and f["last_door_alert_for"] != since):
                    conn.execute("UPDATE fridges SET last_door_alert_for=? WHERE id=?", (since, f["id"]))
                    alerts.raise_alert(conn, now, f["id"], "door_alarm", "critical",
                                       f"{f['id']} door open for more than 60 s (server watchdog)")
                    audit.log(conn, now, "system", "door.alarm", {"since": since, "detected_by": "watchdog"})
                    changed.append(f["id"])
        for fid in changed:
            events.publish({"type": "fridge", "fridge_id": fid})


def seed_devices(conn, now: float) -> None:
    """Create the two fridges/devices from CC_DEVICE_KEYS if they don't exist yet."""
    names = {"FRIDGE-A": "Fridge A", "FRIDGE-B": "Fridge B"}
    for dev, key in settings.device_keys.items():
        fid = dev
        if not conn.execute("SELECT 1 FROM fridges WHERE id=?", (fid,)).fetchone():
            conn.execute("INSERT INTO fridges (id, name, location, capacity_units, device_id) VALUES (?,?,?,?,?)",
                         (fid, names.get(fid, fid), "Pharmacy store room", 500, dev))
        row = conn.execute("SELECT hmac_key FROM devices WHERE device_id=?", (dev,)).fetchone()
        if not row:
            conn.execute("INSERT INTO devices (device_id, fridge_id, hmac_key) VALUES (?,?,?)", (dev, fid, key))
            audit.log(conn, now, "system", "device.registered", {"device": dev, "fridge": fid})
        elif row["hmac_key"] != key:
            conn.execute("UPDATE devices SET hmac_key=? WHERE device_id=?", (key, dev))
            audit.log(conn, now, "system", "device.key_rotated", {"device": dev})
