"""Brief p.1: buffer >= 30 min during Wi-Fi loss, sync in order without duplicates."""
from backend.app import db
from simulator.device import CAPACITY, SimDevice

from .conftest import KEY_A, pump, run


def _readings():
    with db.read() as conn:
        return [dict(r) for r in conn.execute("SELECT seq, ts, buffered FROM readings ORDER BY id")]


def test_35_minute_outage_syncs_in_order_without_duplicates(ingestor, clock):
    dev = SimDevice("FRIDGE-A", KEY_A, clock=clock)
    run(dev, ingestor, clock, 60)
    dev.set_wifi(False)
    run(dev, ingestor, clock, 35 * 60)                 # 35 min offline
    assert len(dev.outbox) >= 35 * 60 / 5              # all readings held on the node
    dev.set_wifi(True)
    run(dev, ingestor, clock, 60)
    rows = _readings()
    seqs = [r["seq"] for r in rows]
    assert seqs == list(range(1, len(seqs) + 1)), "every reading, in order, once"
    assert all(a["ts"] <= b["ts"] for a, b in zip(rows, rows[1:]))
    assert sum(r["buffered"] for r in rows) >= 400
    assert len(dev.outbox) <= 1                        # node freed its buffer after ACKs


def test_lost_acks_cause_resends_but_no_duplicate_rows(ingestor, clock):
    dev = SimDevice("FRIDGE-A", KEY_A, clock=clock)
    run(dev, ingestor, clock, 120, drop_acks=True)     # server receives, node never hears ACKs
    run(dev, ingestor, clock, 60)
    rows = _readings()
    with db.read() as conn:
        d = conn.execute("SELECT duplicates FROM devices WHERE device_id='FRIDGE-A'").fetchone()
    assert d["duplicates"] > 0, "node really did resend"
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))


def test_outage_longer_than_buffer_drops_oldest_and_logs_gap(ingestor, clock):
    dev = SimDevice("FRIDGE-A", KEY_A, clock=clock)
    run(dev, ingestor, clock, 30)
    dev.set_wifi(False)
    run(dev, ingestor, clock, 70 * 60, step=5)         # 70 min > 60 min capacity
    assert len(dev.outbox) == CAPACITY and dev.dropped > 0
    dev.set_wifi(True)
    run(dev, ingestor, clock, 30)
    with db.read() as conn:
        gaps = conn.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action='device.sequence_gap'").fetchone()["n"]
    assert gaps == 1
    seqs = [r["seq"] for r in _readings()]
    assert seqs == sorted(set(seqs))


def test_reboot_starts_new_sequence(ingestor, clock):
    dev = SimDevice("FRIDGE-A", KEY_A, clock=clock)
    run(dev, ingestor, clock, 30)
    n1 = len(_readings())
    dev.reboot()
    run(dev, ingestor, clock, 30)
    assert len(_readings()) > n1
    with db.read() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action='device.reboot'").fetchone()["n"] == 1


def test_publishes_every_5s_and_on_door_change(ingestor, clock):
    dev = SimDevice("FRIDGE-A", KEY_A, clock=clock)
    run(dev, ingestor, clock, 60)
    dev.set_door(True); pump(dev, ingestor)
    clock.advance(2); dev.tick(2); dev.set_door(False); pump(dev, ingestor)
    with db.read() as conn:
        reasons = [r["reason"] for r in conn.execute("SELECT reason FROM readings ORDER BY id")]
    assert reasons.count("periodic") in (12, 13)       # 60 s / 5 s
    assert reasons.count("door") == 2
