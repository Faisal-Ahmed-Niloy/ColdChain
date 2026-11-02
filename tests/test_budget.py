"""Heat-exposure budget rules from the brief (p.1)."""
import pytest

from backend.app import budget, db, services
from simulator.device import SimDevice

from .conftest import KEY_A, run


@pytest.mark.parametrize("exposure,expected", [
    (0, "SAFE"), (47.9, "SAFE"), (48.0, "SAFE"),       # exactly 20 % left is not "below 20 %"
    (48.01, "AT_RISK"), (59.99, "AT_RISK"), (60, "DISCARD"), (75, "DISCARD"),
])
def test_status_thresholds(exposure, expected):
    assert budget.status_for(exposure, 60, 0.2) == expected


def test_weights():
    assert [budget.weight(t) for t in (5, 8.0, 8.01, 12.0, 12.01, 20)] == [0, 0, 1, 1, 2, 2]


def _batch(fridge="FRIDGE-A", now=None):
    return services.create_batch({"vaccine": "BCG", "lot_no": "L-1", "quantity": 10,
                                  "expiry_date": "2027-12-31", "fridge_id": fridge}, "user:test", now)


def _get(bid):
    with db.read() as conn:
        return dict(conn.execute("SELECT * FROM batches WHERE id=?", (bid,)).fetchone())


def test_exposure_counts_minutes_above_8(ingestor, clock):
    bid = _batch(now=clock())["id"]
    dev = SimDevice("FRIDGE-A", KEY_A, clock=clock)
    dev.temp_override = 10.0
    run(dev, ingestor, clock, 20 * 60, step=5)
    b = _get(bid)
    assert 19.5 <= b["exposure_min"] <= 20.2 and b["status"] == "SAFE"


def test_above_12_counts_double_and_discard_latches_led(ingestor, clock):
    bid = _batch(now=clock())["id"]
    dev = SimDevice("FRIDGE-A", KEY_A, clock=clock)
    dev.temp_override = 13.0
    run(dev, ingestor, clock, 25 * 60, step=5)          # 25 min x2 = 50 min -> AT_RISK
    assert _get(bid)["status"] == "AT_RISK"
    run(dev, ingestor, clock, 6 * 60, step=5)           # 31 min x2 = 62 -> DISCARD
    b = _get(bid)
    assert b["status"] == "DISCARD" and b["discarded_at"] is not None
    assert dev.led_latch, "server must latch the node's red LED"
    with db.read() as conn:
        kinds = [r["kind"] for r in conn.execute("SELECT kind FROM alerts")]
    assert "at_risk" in kinds and "discard" in kinds
    # cooling down never revives a discarded batch
    dev.temp_override = 4.0
    run(dev, ingestor, clock, 60, step=5)
    assert _get(bid)["status"] == "DISCARD" and dev.led_latch
    # admin reset clears the LED only
    res = services.reset_discard_led("FRIDGE-A", "user:admin", clock(), None)
    assert res["ok"]
    run(dev, ingestor, clock, 15, step=5)               # reconciliation sends led_latch off
    assert not dev.led_latch and _get(bid)["status"] == "DISCARD"


def test_move_mid_exposure_charges_only_time_in_hot_fridge(ingestor, clock):
    bid = _batch(now=clock())["id"]
    dev = SimDevice("FRIDGE-A", KEY_A, clock=clock)
    dev.temp_override = 10.0
    run(dev, ingestor, clock, 10 * 60, step=5)
    services.move_batch(bid, "FRIDGE-B", "user:test", clock())
    run(dev, ingestor, clock, 10 * 60, step=5)
    assert 9.5 <= _get(bid)["exposure_min"] <= 10.2


def test_incremental_matches_recomputation(ingestor, clock):
    bid = _batch(now=clock())["id"]
    dev = SimDevice("FRIDGE-A", KEY_A, clock=clock)
    for temp, mins in ((5, 3), (9, 4), (14, 3), (6, 2), (11, 5)):
        dev.temp_override = temp
        run(dev, ingestor, clock, mins * 60, step=5)
    with db.read() as conn:
        assert abs(budget.recompute_exposure(conn, bid) - _get(bid)["exposure_min"]) < 1e-6
