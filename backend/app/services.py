"""Business operations behind the REST API. Framework-free: the FastAPI routers in
routers/ only parse requests, check permissions and call these functions."""
from __future__ import annotations

import datetime as dt
import json
import re
import secrets
from pathlib import Path

from . import audit, budget, db, events, hashchain
from .ai import predictor
from .config import settings
from .security import ROLES, check_password, hash_password, password_problem, throttle


class ServiceError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _today(now: float) -> dt.date:
    return dt.datetime.fromtimestamp(now, dt.timezone.utc).date()


def _batch_view(b: dict, now: float) -> dict:
    b = dict(b)
    b["remaining_pct"] = budget.remaining_pct(b["exposure_min"])
    b["remaining_min"] = round(max(0.0, settings.budget_min - b["exposure_min"]), 1)
    b["exposure_min"] = round(b["exposure_min"], 2)
    b["expired"] = dt.date.fromisoformat(b["expiry_date"]) < _today(now)
    return b


# ================================================================== users
def ensure_admin(now: float) -> None:
    with db.tx() as conn:
        if conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0:
            conn.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                         ("admin", hash_password(settings.admin_initial_password), "admin", now))
            audit.log(conn, now, "system", "user.created", {"username": "admin", "role": "admin", "initial": True})


def authenticate(username: str, password: str, now: float) -> dict:
    if throttle.blocked(username, now):
        with db.tx() as conn:
            audit.log(conn, now, f"user:{username}", "auth.locked", {})
        raise ServiceError(429, "Too many failed attempts. Try again in 5 minutes.")
    with db.read() as conn:
        u = db.one(conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)))
    if not u or not check_password(password, u["password_hash"]):
        throttle.fail(username, now)
        with db.tx() as conn:
            audit.log(conn, now, f"user:{username}", "auth.failed", {})
        raise ServiceError(401, "Wrong username or password")
    throttle.reset(username)
    with db.tx() as conn:
        audit.log(conn, now, f"user:{username}", "auth.login", {"role": u["role"]})
    return {"id": u["id"], "username": u["username"], "role": u["role"]}


def list_users() -> list[dict]:
    with db.read() as conn:
        return db.rows(conn.execute("SELECT id, username, role, active, created_at FROM users ORDER BY id"))


def create_user(username: str, password: str, role: str, actor: str, now: float) -> dict:
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise ServiceError(400, "Username: 3-32 letters, digits, _ . -")
    if role not in ROLES:
        raise ServiceError(400, f"Role must be one of {ROLES}")
    if (p := password_problem(password)):
        raise ServiceError(400, p)
    with db.tx() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ServiceError(409, "Username already exists")
        cur = conn.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                           (username, hash_password(password), role, now))
        audit.log(conn, now, actor, "user.created", {"username": username, "role": role})
    return {"id": cur.lastrowid, "username": username, "role": role, "active": 1}


def update_user(user_id: int, actor: str, now: float, role: str | None = None,
                active: bool | None = None, password: str | None = None) -> dict:
    with db.tx() as conn:
        u = db.one(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)))
        if not u:
            raise ServiceError(404, "User not found")
        changes = {}
        if role is not None:
            if role not in ROLES:
                raise ServiceError(400, "Invalid role")
            changes["role"] = role
        if active is not None:
            changes["active"] = 1 if active else 0
        if password is not None:
            if (p := password_problem(password)):
                raise ServiceError(400, p)
            changes["password_hash"] = hash_password(password)
        # never lock out the last active admin
        if u["role"] == "admin" and (changes.get("role", "admin") != "admin" or changes.get("active", 1) == 0):
            n = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND active=1").fetchone()["n"]
            if n <= 1:
                raise ServiceError(400, "Cannot remove the last active admin")
        for k, v in changes.items():
            conn.execute(f"UPDATE users SET {k}=? WHERE id=?", (v, user_id))
        audit.log(conn, now, actor, "user.updated", {"user_id": user_id,
                                                      "fields": sorted(k for k in changes if k != "password_hash")
                                                      + (["password"] if "password_hash" in changes else [])})
    return {"ok": True}


# ================================================================== dashboard
def dashboard(now: float) -> dict:
    with db.read() as conn:
        fridges = db.rows(conn.execute("SELECT * FROM fridges ORDER BY id"))
        batches = [_batch_view(b, now) for b in db.rows(conn.execute(
            "SELECT * FROM batches WHERE active=1 ORDER BY fridge_id, exposure_min DESC"))]
        alerts_ = db.rows(conn.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 25"))
        relocs = db.rows(conn.execute("SELECT * FROM relocations WHERE status='PENDING' ORDER BY id DESC"))
        devices = {d["device_id"]: d for d in db.rows(conn.execute(
            "SELECT device_id, accepted, duplicates, gaps, rejected, boot, last_seq FROM devices"))}
    for f in fridges:
        f["door_open_s"] = int(now - f["door_open_since"]) if f["door"] == 1 and f["door_open_since"] else 0
        f["seen_ago_s"] = int(now - f["last_seen"]) if f["last_seen"] else None
        f["batches"] = [b for b in batches if b["fridge_id"] == f["id"]]
        f["units_used"] = sum(b["quantity"] for b in f["batches"] if b["status"] != "DISCARD")
        f["device"] = devices.get(f["device_id"])
        for k in ("last_proc_ts", "last_proc_temp", "health_baseline"):
            f.pop(k, None)
    for r in relocs:
        r["plan"] = json.loads(r["plan"])
    for a in alerts_:
        a["delivery"] = json.loads(a["delivery"]) if a["delivery"] else None
    return {"now": now, "fridges": fridges, "alerts": alerts_, "relocations": relocs,
            "summary": {s: sum(1 for b in batches if b["status"] == s) for s in ("SAFE", "AT_RISK", "DISCARD")}}


def fridge_history(fridge_id: str, minutes: int, now: float) -> dict:
    minutes = max(5, min(minutes, 24 * 60))
    with db.read() as conn:
        if not conn.execute("SELECT 1 FROM fridges WHERE id=?", (fridge_id,)).fetchone():
            raise ServiceError(404, "Unknown fridge")
        rows = db.rows(conn.execute(
            "SELECT ts, temp, door, comp, power FROM readings WHERE fridge_id=? AND ts>=? ORDER BY ts, id",
            (fridge_id, now - minutes * 60)))
        preds = db.rows(conn.execute(
            "SELECT ts, pred_temp, pred_upper, health FROM predictions WHERE fridge_id=? AND ts>=? ORDER BY ts",
            (fridge_id, now - minutes * 60)))
    step = max(1, len(rows) // 400)            # keep the payload small for phones
    return {"fridge_id": fridge_id, "readings": rows[::step], "predictions": preds[::max(1, len(preds) // 200)]}


# ================================================================== inventory
def list_batches(now: float, include_inactive: bool = False) -> list[dict]:
    q = "SELECT * FROM batches" + ("" if include_inactive else " WHERE active=1") + " ORDER BY id DESC"
    with db.read() as conn:
        return [_batch_view(b, now) for b in db.rows(conn.execute(q))]


def _check_fridge(conn, fridge_id: str) -> dict:
    f = db.one(conn.execute("SELECT * FROM fridges WHERE id=?", (fridge_id,)))
    if not f:
        raise ServiceError(400, "Unknown fridge")
    return f


def _free_units(conn, fridge_id: str, capacity: int) -> int:
    used = conn.execute("SELECT COALESCE(SUM(quantity),0) AS u FROM batches WHERE fridge_id=? AND active=1 "
                        "AND status!='DISCARD'", (fridge_id,)).fetchone()["u"]
    return capacity - used


def create_batch(data: dict, actor: str, now: float) -> dict:
    vaccine = str(data.get("vaccine", "")).strip()
    lot = str(data.get("lot_no", "")).strip()
    exp = str(data.get("expiry_date", "")).strip()
    try:
        qty = int(data.get("quantity", 0))
    except (TypeError, ValueError):
        raise ServiceError(400, "Quantity must be a whole number")
    if not (1 <= len(vaccine) <= 80) or not (1 <= len(lot) <= 40):
        raise ServiceError(400, "Vaccine name and lot number are required")
    if not 1 <= qty <= 100000:
        raise ServiceError(400, "Quantity must be 1-100000")
    if not DATE_RE.match(exp):
        raise ServiceError(400, "Expiry must be YYYY-MM-DD")
    try:
        dt.date.fromisoformat(exp)
    except ValueError:
        raise ServiceError(400, "Expiry is not a real date")
    with db.tx() as conn:
        f = _check_fridge(conn, str(data.get("fridge_id", "")))
        if _free_units(conn, f["id"], f["capacity_units"]) < qty:
            raise ServiceError(409, f"{f['id']} does not have room for {qty} units")
        pub = secrets.token_urlsafe(9)
        cur = conn.execute(
            "INSERT INTO batches (public_id, vaccine, lot_no, quantity, expiry_date, fridge_id, created_at, created_by, "
            "notes) VALUES (?,?,?,?,?,?,?,?,?)",
            (pub, vaccine, lot, qty, exp, f["id"], now, actor, str(data.get("notes", ""))[:300]))
        bid = cur.lastrowid
        conn.execute("INSERT INTO batch_moves (batch_id, from_fridge, to_fridge, ts, by_user, reason) "
                     "VALUES (?,?,?,?,?,?)", (bid, None, f["id"], now, actor, "received"))
        audit.log(conn, now, actor, "batch.created", {"batch_id": bid, "vaccine": vaccine, "lot_no": lot,
                                                       "quantity": qty, "expiry": exp, "fridge": f["id"]})
    events.publish({"type": "inventory"})
    return {"id": bid, "public_id": pub}


def update_batch(batch_id: int, data: dict, actor: str, now: float) -> dict:
    allowed = {"vaccine", "lot_no", "quantity", "expiry_date", "notes"}
    changes = {k: v for k, v in data.items() if k in allowed and v is not None}
    if "expiry_date" in changes:
        try:
            dt.date.fromisoformat(str(changes["expiry_date"]))
        except ValueError:
            raise ServiceError(400, "Expiry must be YYYY-MM-DD")
    if "quantity" in changes:
        try:
            changes["quantity"] = int(changes["quantity"])
        except (TypeError, ValueError):
            raise ServiceError(400, "Quantity must be a whole number")
        if not 1 <= changes["quantity"] <= 100000:
            raise ServiceError(400, "Quantity must be 1-100000")
    with db.tx() as conn:
        b = db.one(conn.execute("SELECT * FROM batches WHERE id=? AND active=1", (batch_id,)))
        if not b:
            raise ServiceError(404, "Batch not found")
        for k, v in changes.items():
            conn.execute(f"UPDATE batches SET {k}=? WHERE id=?", (v, batch_id))
        audit.log(conn, now, actor, "batch.updated", {"batch_id": batch_id,
                                                       "changes": {k: [b[k], v] for k, v in changes.items()}})
    events.publish({"type": "inventory"})
    return {"ok": True}


def move_batch(batch_id: int, to_fridge: str, actor: str, now: float, reason: str = "manual move") -> dict:
    with db.tx() as conn:
        result = _move(conn, batch_id, to_fridge, actor, now, reason)
    events.publish({"type": "inventory"})
    return result


def _move(conn, batch_id: int, to_fridge: str, actor: str, now: float, reason: str) -> dict:
    b = db.one(conn.execute("SELECT * FROM batches WHERE id=? AND active=1", (batch_id,)))
    if not b:
        raise ServiceError(404, "Batch not found")
    if b["status"] == "DISCARD":
        raise ServiceError(409, "Discarded batches cannot be moved; they must be destroyed")
    if b["fridge_id"] == to_fridge:
        raise ServiceError(400, "Batch is already in that fridge")
    f = _check_fridge(conn, to_fridge)
    if _free_units(conn, f["id"], f["capacity_units"]) < b["quantity"]:
        raise ServiceError(409, f"{to_fridge} does not have room for {b['quantity']} units")
    conn.execute("UPDATE batches SET fridge_id=? WHERE id=?", (to_fridge, batch_id))
    conn.execute("INSERT INTO batch_moves (batch_id, from_fridge, to_fridge, ts, by_user, reason) VALUES (?,?,?,?,?,?)",
                 (batch_id, b["fridge_id"], to_fridge, now, actor, reason))
    audit.log(conn, now, actor, "batch.moved", {"batch_id": batch_id, "from": b["fridge_id"], "to": to_fridge,
                                                 "reason": reason})
    warning = None
    if f["state"] == "BREACH" or f["pred_warning"] or f["power"] == "BATTERY":
        warning = f"Note: {to_fridge} is currently at risk"
    return {"ok": True, "warning": warning}


def deactivate_batch(batch_id: int, actor: str, now: float, reason: str) -> dict:
    with db.tx() as conn:
        b = db.one(conn.execute("SELECT * FROM batches WHERE id=? AND active=1", (batch_id,)))
        if not b:
            raise ServiceError(404, "Batch not found")
        conn.execute("UPDATE batches SET active=0 WHERE id=?", (batch_id,))
        conn.execute("INSERT INTO batch_moves (batch_id, from_fridge, to_fridge, ts, by_user, reason) "
                     "VALUES (?,?,?,?,?,?)", (batch_id, b["fridge_id"], None, now, actor, reason or "removed"))
        audit.log(conn, now, actor, "batch.removed", {"batch_id": batch_id, "reason": reason,
                                                       "status": b["status"]})
    events.publish({"type": "inventory"})
    return {"ok": True}


# ================================================================== public QR check
STATUS_LABEL = {"SAFE": "Safe to use", "AT_RISK": "At risk - check with pharmacist",
                "DISCARD": "Discard - do not use"}


def public_batch(public_id: str, now: float) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", public_id or ""):
        raise ServiceError(404, "Not found")
    with db.read() as conn:
        b = db.one(conn.execute("SELECT vaccine, lot_no, expiry_date, status, active FROM batches WHERE public_id=?",
                                (public_id,)))
    if not b:
        raise ServiceError(404, "Not found")
    expired = dt.date.fromisoformat(b["expiry_date"]) < _today(now)
    status = b["status"]
    return {"vaccine": b["vaccine"], "lot_no": b["lot_no"], "expiry_date": b["expiry_date"], "expired": expired,
            "status": status, "status_label": STATUS_LABEL.get(status, status),
            "usable": status != "DISCARD" and not expired and bool(b["active"]),
            "checked_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="seconds")}


def qr_svg(public_id: str) -> str:
    import segno  # pure-Python QR library
    url = f"{settings.public_base_url.rstrip('/')}/check/{public_id}"
    return segno.make(url, error="m").svg_inline(scale=6, border=2)


# ================================================================== logs
def audit_list(limit: int = 200, action_prefix: str = "") -> list[dict]:
    limit = max(1, min(limit, 1000))
    with db.read() as conn:
        rows = db.rows(conn.execute("SELECT * FROM audit_log WHERE action LIKE ? ORDER BY id DESC LIMIT ?",
                                    (action_prefix + "%", limit)))
    for r in rows:
        r["details"] = json.loads(r["details"])
    return rows


def security_events(limit: int = 200) -> list[dict]:
    with db.read() as conn:
        return db.rows(conn.execute("SELECT * FROM security_events ORDER BY id DESC LIMIT ?", (min(limit, 1000),)))


def verify_integrity(actor: str, now: float) -> dict:
    with db.read() as conn:
        results = [hashchain.verify(conn, t, settings.log_seal_key) for t in ("readings", "audit_log")]
    ok = all(r["ok"] for r in results)
    with db.tx() as conn:
        audit.log(conn, now, actor, "integrity.verified", {
            "ok": ok, **{r["table"]: {"checked": r["records_checked"], "first_problem_id": r["first_problem_id"]}
                         for r in results}})
    return {"ok": ok, "checked_at": now, "chains": results}


# ================================================================== alerts
def list_alerts(limit: int = 100) -> list[dict]:
    with db.read() as conn:
        rows = db.rows(conn.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (min(limit, 500),)))
    for a in rows:
        a["delivery"] = json.loads(a["delivery"]) if a["delivery"] else None
    return rows


def ack_alert(alert_id: int, actor: str, now: float) -> dict:
    with db.tx() as conn:
        a = db.one(conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)))
        if not a:
            raise ServiceError(404, "Alert not found")
        if a["ack_by"]:
            return {"ok": True, "already": True}
        conn.execute("UPDATE alerts SET ack_by=?, ack_ts=? WHERE id=?", (actor, now, alert_id))
        audit.log(conn, now, actor, "alert.acknowledged", {"alert_id": alert_id, "kind": a["kind"]})
    events.publish({"type": "alert_ack", "alert_id": alert_id})
    return {"ok": True}


# ================================================================== relocations
def list_relocations(limit: int = 50) -> list[dict]:
    with db.read() as conn:
        rows = db.rows(conn.execute("SELECT * FROM relocations ORDER BY id DESC LIMIT ?", (limit,)))
    for r in rows:
        r["plan"] = json.loads(r["plan"])
    return rows


def confirm_relocation(reloc_id: int, actor: str, now: float) -> dict:
    """One-click confirm (brief p.2): applies every still-valid move atomically."""
    applied, skipped = [], []
    with db.tx() as conn:
        r = db.one(conn.execute("SELECT * FROM relocations WHERE id=?", (reloc_id,)))
        if not r:
            raise ServiceError(404, "Suggestion not found")
        if r["status"] != "PENDING":
            raise ServiceError(409, f"Suggestion is already {r['status'].lower()}")
        plan = json.loads(r["plan"])
        for m in plan["moves"]:
            try:
                b = db.one(conn.execute("SELECT fridge_id FROM batches WHERE id=?", (m["batch_id"],)))
                if not b or b["fridge_id"] != plan["source"]:
                    raise ServiceError(409, "batch is no longer in the source fridge")
                _move(conn, m["batch_id"], m["to_fridge"], actor, now, f"AI relocation #{reloc_id}")
                applied.append(m)
            except ServiceError as e:
                skipped.append({**m, "why": e.message})
        conn.execute("UPDATE relocations SET status='CONFIRMED', decided_by=?, decided_ts=?, updated_ts=? WHERE id=?",
                     (actor, now, now, reloc_id))
        audit.log(conn, now, actor, "relocation.confirmed", {"id": reloc_id, "applied": len(applied),
                                                              "skipped": skipped})
    events.publish({"type": "relocation", "id": reloc_id})
    events.publish({"type": "inventory"})
    return {"ok": True, "applied": applied, "skipped": skipped}


def dismiss_relocation(reloc_id: int, actor: str, now: float, reason: str) -> dict:
    with db.tx() as conn:
        r = db.one(conn.execute("SELECT status FROM relocations WHERE id=?", (reloc_id,)))
        if not r:
            raise ServiceError(404, "Suggestion not found")
        if r["status"] != "PENDING":
            raise ServiceError(409, f"Suggestion is already {r['status'].lower()}")
        conn.execute("UPDATE relocations SET status='DISMISSED', decided_by=?, decided_ts=?, updated_ts=? WHERE id=?",
                     (actor, now, now, reloc_id))
        audit.log(conn, now, actor, "relocation.dismissed", {"id": reloc_id, "reason": reason})
    events.publish({"type": "relocation", "id": reloc_id})
    return {"ok": True}


# ================================================================== admin / devices
def reset_discard_led(fridge_id: str, actor: str, now: float, ingestor) -> dict:
    with db.tx() as conn:
        f = _check_fridge(conn, fridge_id)
        conn.execute("UPDATE fridges SET led_latch=0 WHERE id=?", (fridge_id,))
        audit.log(conn, now, actor, "device.discard_led_reset", {"fridge": fridge_id,
                                                                  "was_latched": f["led_latch"]})
    sent = ingestor.send_command(f["device_id"], {"cmd": "led_latch", "on": False}) if ingestor else False
    events.publish({"type": "fridge", "fridge_id": fridge_id})
    return {"ok": True, "command_sent": sent,
            "note": None if sent else "Node offline: LED will clear on its next report"}


def recalibrate_health(fridge_id: str, actor: str, now: float) -> dict:
    with db.tx() as conn:
        _check_fridge(conn, fridge_id)
        conn.execute("UPDATE fridges SET health_baseline=NULL, health_score=NULL, health_note=? WHERE id=?",
                     ("Recalibrating from the next clean cycles", fridge_id))
        audit.log(conn, now, actor, "ai.health_recalibrate", {"fridge": fridge_id})
    return {"ok": True}


def ai_overview() -> dict:
    metrics = None
    p = Path(settings.metrics_path)
    if p.exists():
        metrics = json.loads(p.read_text())
    with db.read() as conn:
        fridges = db.rows(conn.execute(
            "SELECT id, name, temp, pred_temp_30, pred_upper_30, pred_warning, pred_note, pred_ts, health_score, "
            "health_note, health_ts FROM fridges ORDER BY id"))
    return {"model": predictor.model_info(), "metrics": metrics, "fridges": fridges}
