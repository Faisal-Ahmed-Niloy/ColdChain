"""Compressor health score 0-100 (brief p.2), physics-informed.

During steady operation (door closed, temperature inside the 3-7 C band):
    warming rate when OFF  r_off = leak
    cooling rate when ON   r_on  = leak - capacity
so the compressor's cooling capacity can be estimated WITHOUT an ambient sensor:
    capacity = r_off - r_on          (deg C per minute)
Health = 100 * capacity / baseline_capacity, where the baseline is learned the
first time enough clean data exists (or re-calibrated by an admin after service).
A weakening compressor shows up as a falling capacity long before the fridge
actually breaches.
"""
from __future__ import annotations

import numpy as np

MIN_PAIRS = 8


def capacity_estimate(s: dict[str, np.ndarray]) -> dict:
    T, C, D, M = s["temp"], s["comp"], s["door"], s["mains"]
    on, off = [], []
    for j in range(len(T) - 1):
        if D[j] > 0 or D[j + 1] > 0:
            continue
        if not (2.0 <= T[j] <= 8.5):
            continue
        dT = T[j + 1] - T[j]
        if C[j] >= 0.99 and C[j + 1] >= 0.99 and M[j] >= 1 and M[j + 1] >= 1:
            on.append(dT)
        elif C[j] <= 0.01 and C[j + 1] <= 0.01:
            off.append(dT)
    if len(on) < MIN_PAIRS or len(off) < MIN_PAIRS:
        return {"ok": False, "on_pairs": len(on), "off_pairs": len(off),
                "note": f"need {MIN_PAIRS} clean ON and OFF minutes, have {len(on)}/{len(off)}"}
    r_on, r_off = float(np.median(on)), float(np.median(off))
    return {"ok": True, "capacity": max(0.0, r_off - r_on), "r_on": r_on, "r_off": r_off,
            "on_pairs": len(on), "off_pairs": len(off)}


def health_score(capacity: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0
    return round(float(np.clip(capacity / baseline, 0.0, 1.0)) * 100.0, 1)


def describe(score: float) -> str:
    if score >= 80:
        return "Healthy"
    if score >= 60:
        return "Slight wear - schedule a check"
    if score >= 40:
        return "Weakening - service soon"
    return "Failing - relocate stock and service now"
