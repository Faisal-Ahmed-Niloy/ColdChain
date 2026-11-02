"""Shared test fixtures. Tests run fully offline: no MQTT broker, no network,
alerts are recorded but not sent. Time is a virtual clock so 40-minute outages
take milliseconds."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CC_DEVICE_KEYS", "FRIDGE-A:test-key-a-0123456789abcdef,FRIDGE-B:test-key-b-0123456789abcdef")
os.environ.setdefault("CC_MQTT_ENABLED", "0")

from backend.app import alerts, db  # noqa: E402
from backend.app.config import settings  # noqa: E402
from backend.app.ingest import Ingestor, seed_devices  # noqa: E402

alerts.SEND_ENABLED = False
T0 = 1_780_000_000.0   # a fixed "now" in 2026


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture()
def fresh_db(tmp_path):
    db.set_db_path(str(tmp_path / "test.db"))
    db.init_schema()
    with db.tx() as conn:
        seed_devices(conn, T0)
    yield


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def ingestor(fresh_db, clock):
    return Ingestor(clock=clock)


KEY_A = settings.device_keys["FRIDGE-A"]
KEY_B = settings.device_keys["FRIDGE-B"]


def pump(dev, ingestor, max_rounds: int = 200, drop_acks: bool = False) -> int:
    """Deliver device -> server -> device until the device has nothing left to send."""
    delivered = 0
    for _ in range(max_rounds):
        msgs = dev.outgoing()
        if not msgs:
            break
        for topic, payload in msgs:
            delivered += 1
            for _t, reply in ingestor.handle(topic, payload):
                if not drop_acks:
                    dev.receive(reply)
    return delivered


def run(dev, ingestor, clock, seconds: float, step: float = 1.0, drop_acks: bool = False) -> None:
    """Advance virtual time, ticking the device and pumping the network each step."""
    for _ in range(int(seconds / step)):
        clock.advance(step)
        dev.tick(step)
        pump(dev, ingestor, drop_acks=drop_acks)
