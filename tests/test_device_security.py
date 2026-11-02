"""HMAC-SHA256 + timestamp + nonce: invalid and replayed messages are rejected AND logged."""
import json

from backend.app import db
from backend.app.device_auth import build_envelope

from .conftest import KEY_A, KEY_B

TOPIC = "coldchain/v1/FRIDGE-A/telemetry"


def body(seq=1, t=None, temp=5.0, boot="b001"):
    return {"seq": seq, "boot": boot, "t": int(t), "temp": temp, "door": 0, "comp": 0, "power": "MAINS",
            "batt": 100, "state": "SAFE", "reason": "periodic", "led": 0}


def count(table, where=""):
    with db.read() as conn:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table} {where}").fetchone()["n"]


def kinds():
    with db.read() as conn:
        return [r["kind"] for r in conn.execute("SELECT kind FROM security_events ORDER BY id")]


def test_valid_message_is_accepted_and_acked(ingestor, clock):
    out = ingestor.handle(TOPIC, build_envelope("FRIDGE-A", KEY_A, int(clock()), body(t=clock())))
    assert count("readings") == 1
    ack = json.loads(json.loads(out[-1][1])["body"])
    assert ack == {"cmd": "ack", "boot": "b001", "seq": 1}


def test_replayed_message_is_rejected_and_logged(ingestor, clock):
    msg = build_envelope("FRIDGE-A", KEY_A, int(clock()), body(t=clock()))
    ingestor.handle(TOPIC, msg)
    clock.advance(3)
    assert ingestor.handle(TOPIC, msg) == []
    assert count("readings") == 1
    assert kinds() == ["replayed_nonce"]
    assert count("audit_log", "WHERE action='security.reject.replayed_nonce'") == 1


def test_old_capture_replayed_later_is_rejected(ingestor, clock):
    msg = build_envelope("FRIDGE-A", KEY_A, int(clock()), body(t=clock()))
    clock.advance(600)
    ingestor.handle(TOPIC, msg)
    assert kinds() == ["stale_timestamp"] and count("readings") == 0


def test_tampered_body_fails_signature(ingestor, clock):
    env = json.loads(build_envelope("FRIDGE-A", KEY_A, int(clock()), body(t=clock(), temp=9.5)))
    env["body"] = env["body"].replace("9.5", "4.5")       # attacker hides a breach
    ingestor.handle(TOPIC, json.dumps(env).encode())
    assert kinds() == ["bad_signature"] and count("readings") == 0


def test_wrong_key_unknown_device_topic_mismatch_future(ingestor, clock):
    ingestor.handle(TOPIC, build_envelope("FRIDGE-A", KEY_B, int(clock()), body(t=clock())))
    ingestor.handle("coldchain/v1/FRIDGE-X/telemetry", build_envelope("FRIDGE-X", KEY_A, int(clock()), body(t=clock())))
    ingestor.handle(TOPIC, build_envelope("FRIDGE-B", KEY_B, int(clock()), body(t=clock())))
    ingestor.handle(TOPIC, build_envelope("FRIDGE-A", KEY_A, int(clock()) + 900, body(t=clock())))
    ingestor.handle(TOPIC, b"not json at all")
    assert kinds() == ["bad_signature", "unknown_device", "topic_mismatch", "future_timestamp", "bad_json"]
    assert count("readings") == 0


def test_invalid_body_schema_is_rejected(ingestor, clock):
    bad = body(t=clock())
    bad["temp"] = "hot"
    ingestor.handle(TOPIC, build_envelope("FRIDGE-A", KEY_A, int(clock()), bad))
    assert kinds() == ["bad_body"]


def test_server_commands_verify_on_device_and_replay_fails(ingestor, clock):
    from simulator.device import SimDevice
    dev = SimDevice("FRIDGE-A", KEY_A, clock=clock)
    with db.read() as conn:
        _, cmd = ingestor.command(conn, "FRIDGE-A", {"cmd": "led_latch", "on": True}, clock())
    assert dev.receive(cmd) == "ok" and dev.led_latch
    assert dev.receive(cmd) == "replayed_nonce"
    forged = json.loads(cmd); forged["body"] = forged["body"].replace("true", "false")
    assert dev.receive(json.dumps(forged).encode()) == "bad_signature"
