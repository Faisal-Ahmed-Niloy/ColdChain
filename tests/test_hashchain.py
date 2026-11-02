from backend.app import audit, db, hashchain
from backend.app.config import settings

from .conftest import T0


def _fill(n=10):
    with db.tx() as conn:
        for i in range(n):
            audit.log(conn, T0 + i, "user:test", "test.event", {"i": i})


def _verify():
    with db.read() as conn:
        return hashchain.verify(conn, "audit_log", settings.log_seal_key)


def test_clean_chain_verifies(fresh_db):
    _fill()
    r = _verify()
    assert r["ok"] and r["records_checked"] >= 10


def test_edited_record_is_detected(fresh_db):
    _fill()
    with db.tx() as conn:
        conn.execute("UPDATE audit_log SET details='{\"i\":999}' WHERE action='test.event' AND id=5")
    r = _verify()
    assert not r["ok"]
    assert r["problems"][0]["id"] == 5 and r["problems"][0]["problem"] == "content_mismatch"


def test_deleted_record_is_detected(fresh_db):
    _fill()
    with db.tx() as conn:
        conn.execute("DELETE FROM audit_log WHERE id=6")
    r = _verify()
    assert not r["ok"] and r["problems"][0] == {"id": 7, "problem": "broken_link",
                                                "meaning": "a record before this one was deleted, inserted or reordered"}


def test_chain_rebuilt_without_key_is_detected(fresh_db):
    """An attacker who edits a row AND recomputes every hash still fails the HMAC seal."""
    _fill()
    with db.tx() as conn:
        conn.execute("UPDATE audit_log SET details='{\"i\":999}' WHERE id=5")
        prev = hashchain.GENESIS
        for row in conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall():
            rec = {f: row[f] for f in hashchain.CHAINS["audit_log"]}
            h, seal = hashchain.compute(prev, rec, "attacker-guess")
            conn.execute("UPDATE audit_log SET prev_hash=?, hash=?, seal=? WHERE id=?", (prev, h, seal, row["id"]))
            prev = h
    r = _verify()
    assert not r["ok"] and r["problems"][0]["problem"] == "bad_seal"
