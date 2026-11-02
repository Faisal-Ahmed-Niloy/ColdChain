"""Relocation advice (brief p.2): when a fridge may become unsafe, suggest which
batches to move and where.

Source fridge is "at risk" if ANY of:
  - it has been in BREACH for 5 minutes or more (a door opening alone is not a reason)
  - the 30-min forecast says it may exceed 8 C
  - it lost mains power
  - its compressor health is below 40
A target fridge must be online, on mains, not at risk, forecast <= 7.5 C,
compressor health >= 50 (or not yet known) and have free capacity.

Batches are moved in order of urgency: least heat budget left first, then the
earliest expiry. Discarded batches are never moved (they must be destroyed).
"""
from __future__ import annotations

import sqlite3

from .. import budget

HEALTH_SOURCE_MIN = 40
HEALTH_TARGET_MIN = 50
TARGET_MAX_FORECAST = 7.5


BREACH_MIN_S = 300


def risk_reasons(f: dict) -> list[str]:
    reasons = []
    if (f.get("state") == "BREACH" and f.get("breach_since") and f.get("state_ts")
            and f["state_ts"] - f["breach_since"] >= BREACH_MIN_S):
        mins = int((f["state_ts"] - f["breach_since"]) // 60)
        reasons.append(f"out of range for {mins} min (now {f.get('temp'):.1f} °C)")
    if f.get("pred_warning"):
        reasons.append(f"forecast may exceed 8 °C within 30 min (upper bound {f.get('pred_upper_30'):.1f} °C)")
    if f.get("power") == "BATTERY":
        reasons.append("running on battery, so the compressor cannot run")
    if f.get("health_score") is not None and f["health_score"] < HEALTH_SOURCE_MIN:
        reasons.append(f"compressor health {f['health_score']:.0f}/100")
    return reasons


def used_units(conn: sqlite3.Connection, fridge_id: str) -> int:
    r = conn.execute("SELECT COALESCE(SUM(quantity),0) AS u FROM batches WHERE fridge_id=? AND active=1 "
                     "AND status!='DISCARD'", (fridge_id,)).fetchone()
    return int(r["u"])


def target_ok(f: dict) -> bool:
    forecast = f.get("pred_upper_30") if f.get("pred_upper_30") is not None else f.get("temp")
    return (bool(f.get("online")) and f.get("power") == "MAINS" and not risk_reasons(f)
            and (forecast is None or forecast <= TARGET_MAX_FORECAST)
            and (f.get("health_score") is None or f["health_score"] >= HEALTH_TARGET_MIN))


def build_plan(conn: sqlite3.Connection, source_id: str) -> dict | None:
    fridges = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM fridges").fetchall()}
    src = fridges[source_id]
    reasons = risk_reasons(src)
    if not reasons:
        return None
    batches = [dict(b) for b in conn.execute(
        "SELECT * FROM batches WHERE fridge_id=? AND active=1 AND status!='DISCARD'", (source_id,)).fetchall()]
    if not batches:
        return None
    batches.sort(key=lambda b: (budget.remaining_pct(b["exposure_min"]), b["expiry_date"]))
    targets = [f for fid, f in fridges.items() if fid != source_id and target_ok(f)]
    targets.sort(key=lambda f: (-(f["health_score"] if f["health_score"] is not None else 75),
                                f["pred_upper_30"] if f["pred_upper_30"] is not None else (f["temp"] or 5)))
    free = {f["id"]: f["capacity_units"] - used_units(conn, f["id"]) for f in targets}
    moves, unplaced = [], []
    for b in batches:
        item = {"batch_id": b["id"], "vaccine": b["vaccine"], "lot_no": b["lot_no"], "quantity": b["quantity"],
                "remaining_pct": budget.remaining_pct(b["exposure_min"]), "status": b["status"]}
        dest = next((t for t in targets if free[t["id"]] >= b["quantity"]), None)
        if dest:
            free[dest["id"]] -= b["quantity"]
            moves.append({**item, "to_fridge": dest["id"], "to_name": dest["name"]})
        else:
            unplaced.append(item)
    advice = ("Move the listed batches now, most urgent first." if moves else
              "No safe fridge has room: use a validated cold box with ice packs and call the supervisor.")
    if moves and unplaced:
        advice += " Remaining batches need a cold box."
    return {"source": source_id, "source_name": src["name"], "reasons": reasons,
            "moves": moves, "unplaced": unplaced, "advice": advice}
