"""Heat-exposure budget per vaccine batch (brief p.1).

Rules from the brief:
  - each batch has a 60-minute budget above 8 C
  - exposure above 12 C counts double
  - remaining < 20 %  -> AT_RISK
  - remaining = 0 %   -> DISCARD (red LED latched until admin reset)

Implementation choices (documented in the project doc):
  - Exposure is integrated per fridge between consecutive readings (in time order).
  - For each interval the HIGHER of the two endpoint temperatures is used
    (conservative: never under-counts heat).
  - A batch is charged only for the part of an interval it actually spent in that
    fridge, using its move history, so moves mid-exposure are handled.
  - Readings with no valid temperature (sensor fault) carry the last known value.
"""
from __future__ import annotations

import math
import sqlite3

from .config import settings

SAFE, AT_RISK, DISCARD = "SAFE", "AT_RISK", "DISCARD"


def weight(temp: float) -> int:
    if temp > 12.0:
        return 2
    if temp > 8.0:
        return 1
    return 0


def status_for(exposure_min: float, budget_min: float | None = None, at_risk_frac: float | None = None) -> str:
    budget_min = settings.budget_min if budget_min is None else budget_min
    at_risk_frac = settings.at_risk_frac if at_risk_frac is None else at_risk_frac
    remaining = max(0.0, budget_min - exposure_min)
    if remaining <= 1e-9:
        return DISCARD
    if remaining / budget_min < at_risk_frac:
        return AT_RISK
    return SAFE


def remaining_pct(exposure_min: float, budget_min: float | None = None) -> float:
    budget_min = settings.budget_min if budget_min is None else budget_min
    return round(max(0.0, budget_min - exposure_min) / budget_min * 100.0, 1)


def location_intervals(conn: sqlite3.Connection, batch_id: int) -> list[tuple[str | None, float, float]]:
    """[(fridge, start, end)] from the batch's move history; the last interval is open (inf)."""
    moves = conn.execute(
        "SELECT to_fridge, ts FROM batch_moves WHERE batch_id=? ORDER BY ts, id", (batch_id,)
    ).fetchall()
    out = []
    for i, m in enumerate(moves):
        end = moves[i + 1]["ts"] if i + 1 < len(moves) else math.inf
        out.append((m["to_fridge"], m["ts"], end))
    return out


def overlap_seconds(intervals, fridge_id: str, a: float, b: float) -> float:
    total = 0.0
    for f, s, e in intervals:
        if f != fridge_id:
            continue
        lo, hi = max(a, s), min(b, e)
        if hi > lo:
            total += hi - lo
    return total


def _candidate_batches(conn, fridge_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM batches WHERE active=1 AND id IN
             (SELECT DISTINCT batch_id FROM batch_moves WHERE to_fridge=? OR from_fridge=?)""",
        (fridge_id, fridge_id),
    ).fetchall()


def process_reading(conn: sqlite3.Connection, fridge_id: str, ts: float, temp: float | None) -> list[dict]:
    """Advance the fridge's exposure integrator to `ts`. Returns status transitions."""
    f = conn.execute("SELECT last_proc_ts, last_proc_temp FROM fridges WHERE id=?", (fridge_id,)).fetchone()
    prev_ts, prev_temp = (f["last_proc_ts"], f["last_proc_temp"]) if f else (None, None)
    if temp is None:            # sensor fault: assume the last known temperature continues
        temp = prev_temp
    if prev_ts is None or prev_temp is None:
        conn.execute("UPDATE fridges SET last_proc_ts=?, last_proc_temp=? WHERE id=?", (ts, temp, fridge_id))
        return []
    if ts <= prev_ts:           # never integrate backwards (sync is in-order, this is a guard)
        return []
    transitions: list[dict] = []
    w = weight(max(prev_temp, temp if temp is not None else prev_temp))
    if w:
        for b in _candidate_batches(conn, fridge_id):
            secs = overlap_seconds(location_intervals(conn, b["id"]), fridge_id, prev_ts, ts)
            if secs <= 0:
                continue
            new_exp = b["exposure_min"] + w * secs / 60.0
            old_status, new_status = b["status"], status_for(new_exp)
            if old_status == DISCARD:          # discard is final
                new_status = DISCARD
            conn.execute("UPDATE batches SET exposure_min=?, status=?, discarded_at=COALESCE(discarded_at, ?) "
                         "WHERE id=?", (new_exp, new_status, ts if new_status == DISCARD else None, b["id"]))
            if new_status != old_status:
                transitions.append({"batch_id": b["id"], "vaccine": b["vaccine"], "lot_no": b["lot_no"],
                                    "fridge_id": fridge_id, "old": old_status, "new": new_status,
                                    "exposure_min": round(new_exp, 2), "ts": ts})
    conn.execute("UPDATE fridges SET last_proc_ts=?, last_proc_temp=? WHERE id=?", (ts, temp, fridge_id))
    return transitions


def recompute_exposure(conn: sqlite3.Connection, batch_id: int) -> float:
    """Independent recomputation from raw readings (used by tests and the audit view)."""
    total = 0.0
    intervals = location_intervals(conn, batch_id)
    for fridge_id in {f for f, _, _ in intervals if f}:
        prev = None
        for r in conn.execute("SELECT ts, temp FROM readings WHERE fridge_id=? ORDER BY ts, id", (fridge_id,)):
            temp = r["temp"] if r["temp"] is not None else (prev[1] if prev else None)
            if prev is not None and temp is not None and r["ts"] > prev[0]:
                w = weight(max(prev[1], temp))
                if w:
                    total += w * overlap_seconds(intervals, fridge_id, prev[0], r["ts"]) / 60.0
            if temp is not None:
                prev = (r["ts"], temp)
    return total
