import numpy as np

from backend.app import db, services
from backend.app.ai import health, predictor
from backend.app.ai.features import FEATURES, features_at, minute_series
from backend.app.engine import run_ai_cycle
from backend.app.security import can, permissions_for
from ml.physics import FridgeParams, simulate
from simulator.device import SimDevice

from .conftest import KEY_A, KEY_B, run


def _alerts(kind):
    with db.read() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM alerts WHERE kind=?", (kind,)).fetchone()["n"]


def test_door_open_over_60s_alerts_once(ingestor, clock):
    dev = SimDevice("FRIDGE-A", KEY_A, clock=clock)
    run(dev, ingestor, clock, 10)
    dev.set_door(True)
    run(dev, ingestor, clock, 59)
    assert _alerts("door_alarm") == 0
    run(dev, ingestor, clock, 30)
    ingestor.watchdog()                                   # server-side check must not double-alert
    assert dev.door_alarm and _alerts("door_alarm") == 1


def test_power_failure_is_reported(ingestor, clock):
    dev = SimDevice("FRIDGE-A", KEY_A, clock=clock)
    run(dev, ingestor, clock, 10)
    dev.toggle_power()
    run(dev, ingestor, clock, 10)
    with db.read() as conn:
        f = conn.execute("SELECT power FROM fridges WHERE id='FRIDGE-A'").fetchone()
    assert f["power"] == "BATTERY" and _alerts("power_lost") == 1


def test_permission_matrix():
    assert can("pharmacist", "relocation.confirm") and can("admin", "device.reset_led")
    assert not can("inspector", "inventory.edit") and not can("inspector", "relocation.confirm")
    assert not can("pharmacist", "device.reset_led") and not can("pharmacist", "users.manage")
    assert can("inspector", "logs.verify") and "logs.view" in permissions_for("inspector")


def test_features_are_computable():
    s = minute_series(simulate(FridgeParams(), 2, seed=1))
    x = features_at(s, len(s["temp"]) - 1)
    assert x.shape == (len(FEATURES),) and np.all(np.isfinite(x))


def test_health_estimator_tracks_wear():
    p = FridgeParams(power_fail_rate_per_h=0)
    base = health.capacity_estimate(minute_series(simulate(p, 6, seed=3)))
    p.health = 0.5
    worn = health.capacity_estimate(minute_series(simulate(p, 6, seed=4)))
    score = health.health_score(worn["capacity"], base["capacity"])
    assert 35 <= score <= 65


def test_forecast_warns_during_power_failure_and_relocation_flow(ingestor, clock):
    predictor.load_model()        # model if trained, otherwise the labelled fallback
    a = SimDevice("FRIDGE-A", KEY_A, clock=clock, seed=1)
    b = SimDevice("FRIDGE-B", KEY_B, clock=clock, seed=2)
    bid = services.create_batch({"vaccine": "Insulin", "lot_no": "INS-7", "quantity": 20,
                                 "expiry_date": "2027-06-30", "fridge_id": "FRIDGE-A"}, "user:test", clock())["id"]
    for _ in range(40 * 12):                              # 40 min normal operation, both fridges
        clock.advance(5); a.tick(5); b.tick(5)
        from .conftest import pump
        pump(a, ingestor); pump(b, ingestor)
    a.toggle_power()                                      # fridge A loses mains
    for _ in range(8 * 12):
        clock.advance(5); a.tick(5); b.tick(5)
        from .conftest import pump
        pump(a, ingestor); pump(b, ingestor)
    run_ai_cycle(clock())
    with db.read() as conn:
        fa = dict(conn.execute("SELECT * FROM fridges WHERE id='FRIDGE-A'").fetchone())
        rel = conn.execute("SELECT * FROM relocations WHERE status='PENDING'").fetchall()
    assert fa["pred_temp_30"] is not None
    assert len(rel) == 1 and rel[0]["source_fridge"] == "FRIDGE-A"
    res = services.confirm_relocation(rel[0]["id"], "user:pharma", clock())
    assert [m["batch_id"] for m in res["applied"]] == [bid]
    with db.read() as conn:
        assert conn.execute("SELECT fridge_id FROM batches WHERE id=?", (bid,)).fetchone()["fridge_id"] == "FRIDGE-B"


def test_public_check_hides_internal_fields(ingestor, clock):
    pub = services.create_batch({"vaccine": "MR", "lot_no": "MR-2", "quantity": 5, "expiry_date": "2020-01-01",
                                 "fridge_id": "FRIDGE-B"}, "user:test", clock())["public_id"]
    r = services.public_batch(pub, clock())
    assert set(r) == {"vaccine", "lot_no", "expiry_date", "expired", "status", "status_label", "usable", "checked_at"}
    assert r["expired"] and not r["usable"]
