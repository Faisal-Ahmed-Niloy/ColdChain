"""Periodic AI cycle: forecast, compressor health, relocation suggestions.
Runs every CC_AI_PERIOD_S seconds on a background thread (see main.py)."""
from __future__ import annotations

import json

from . import alerts, audit, db, events
from .ai import health as health_mod
from .ai.features import minute_series
from .ai.predictor import predict_from_series
from .ai.relocation import build_plan, risk_reasons

HISTORY_S = 6 * 3600     # health uses up to 6 h; the forecast uses the last 30 min of it
STALE_S = 120            # no forecast if the newest reading is older than this
EXPIRE_MIN_AGE_S = 120   # a suggestion must live 2 min before it can auto-expire


def run_ai_cycle(now: float) -> dict:
    results: dict[str, dict] = {}
    with db.read() as conn:
        fridges = [dict(f) for f in conn.execute("SELECT * FROM fridges").fetchall()]
        data = {f["id"]: [dict(r) for r in conn.execute(
            "SELECT ts, temp, door, comp, power FROM readings WHERE fridge_id=? AND ts>=? ORDER BY ts, id",
            (f["id"], now - HISTORY_S)).fetchall()] for f in fridges}

    for f in fridges:
        rows = data[f["id"]]
        res: dict = {"fridge_id": f["id"]}
        if rows and now - rows[-1]["ts"] <= STALE_S:
            s = minute_series(rows)
            res["pred"] = predict_from_series(s)
            cap = health_mod.capacity_estimate(s)
            res["capacity"] = cap
        else:
            res["pred"] = {"ok": False, "note": "no recent data"}
            res["capacity"] = {"ok": False, "note": "no recent data"}
        results[f["id"]] = res

    changed = []
    with db.tx() as conn:
        for f in fridges:
            fid, res = f["id"], results[f["id"]]
            p, cap = res["pred"], res["capacity"]
            # ---- forecast ----
            if p.get("ok"):
                conn.execute("UPDATE fridges SET pred_temp_30=?, pred_upper_30=?, pred_warning=?, pred_note=?, "
                             "pred_ts=? WHERE id=?", (p["pred"], p["upper"], int(p["warning"]),
                                                      p["note"] + (" [fallback]" if p["mode"] == "fallback" else ""),
                                                      now, fid))
                if p["warning"] and not f["pred_warning"]:
                    alerts.raise_alert(conn, now, fid, "predicted_breach", "warning", f"{fid}: {p['note']}", cooldown_s=900)
                    audit.log(conn, now, "ai", "ai.predicted_breach", {"fridge": fid, **p})
            else:
                conn.execute("UPDATE fridges SET pred_warning=0, pred_note=?, pred_ts=? WHERE id=?",
                             (p.get("note"), now, fid))
            # ---- compressor health ----
            score = f["health_score"]
            if cap.get("ok"):
                baseline = f["health_baseline"]
                if baseline is None and cap["capacity"] > 0:
                    baseline = cap["capacity"]
                    audit.log(conn, now, "ai", "ai.health_calibrated", {"fridge": fid, "baseline": baseline})
                score = health_mod.health_score(cap["capacity"], baseline)
                conn.execute("UPDATE fridges SET health_score=?, health_baseline=?, health_note=?, health_ts=? "
                             "WHERE id=?", (score, baseline, health_mod.describe(score), now, fid))
                if f["health_score"] is not None and f["health_score"] >= 50 > score:
                    alerts.raise_alert(conn, now, fid, "compressor_weak", "warning",
                                       f"{fid} compressor health dropped to {score:.0f}/100", cooldown_s=3600)
                    audit.log(conn, now, "ai", "ai.compressor_weak", {"fridge": fid, "score": score})
            elif f["health_score"] is None:
                conn.execute("UPDATE fridges SET health_note=? WHERE id=?", (cap.get("note"), fid))
            conn.execute("INSERT INTO predictions (fridge_id, ts, temp_now, pred_temp, pred_upper, health) "
                         "VALUES (?,?,?,?,?,?)", (fid, now, p.get("t_now"), p.get("pred"), p.get("upper"), score))
            changed.append(fid)

        # ---- relocation suggestions (uses the values just written) ----
        for f in conn.execute("SELECT * FROM fridges").fetchall():
            f = dict(f)
            pending = conn.execute("SELECT * FROM relocations WHERE source_fridge=? AND status='PENDING'",
                                   (f["id"],)).fetchone()
            plan = build_plan(conn, f["id"]) if risk_reasons(f) else None
            if plan:
                if pending:
                    conn.execute("UPDATE relocations SET plan=?, reason=?, updated_ts=? WHERE id=?",
                                 (json.dumps(plan), "; ".join(plan["reasons"]), now, pending["id"]))
                else:
                    cur = conn.execute("INSERT INTO relocations (created_ts, updated_ts, source_fridge, reason, plan, "
                                       "status) VALUES (?,?,?,?,?, 'PENDING')",
                                       (now, now, f["id"], "; ".join(plan["reasons"]), json.dumps(plan)))
                    audit.log(conn, now, "ai", "relocation.suggested", {"id": cur.lastrowid, "plan": plan})
                    n = len(plan["moves"])
                    alerts.raise_alert(conn, now, f["id"], "relocation_suggested", "warning",
                                       f"{f['id']} may become unsafe ({plan['reasons'][0]}). "
                                       f"Suggested: move {n} batch(es). Pharmacist confirmation needed.")
                    events.publish({"type": "relocation", "id": cur.lastrowid})
            elif pending and now - pending["created_ts"] >= EXPIRE_MIN_AGE_S:
                conn.execute("UPDATE relocations SET status='EXPIRED', updated_ts=? WHERE id=?", (now, pending["id"]))
                audit.log(conn, now, "ai", "relocation.expired", {"id": pending["id"], "why": "risk cleared"})
                events.publish({"type": "relocation", "id": pending["id"]})
    for fid in changed:
        events.publish({"type": "ai", "fridge_id": fid})
    return results
