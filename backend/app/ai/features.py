"""Feature engineering shared by training (ml/train.py) and live inference.

Raw readings (every 5 s) are resampled into 1-minute bins; features describe the
last 30 minutes. Using the SAME function in training and in production avoids
train/serve skew.
"""
from __future__ import annotations

import numpy as np

FEATURES = [
    "t_now",          # mean temperature in the latest minute
    "d5", "d15", "d30",  # change over the last 5/15/30 min
    "slope5",         # least-squares slope over the last 6 minutes (C/min)
    "t_max15", "t_min15",
    "comp_now", "comp15",   # compressor duty (fraction of time ON)
    "door_now", "door15",   # door-open fraction
    "mains_now",            # 1 = mains, 0 = battery
    "batt_minutes",         # consecutive minutes on battery (cap 120)
    "since_comp_on",        # minutes since the compressor last ran (cap 60)
]
HISTORY_MIN = 30   # minutes of history a prediction needs
HORIZON_MIN = 30   # prediction horizon (brief p.2)


def minute_series(samples: list[dict]) -> dict[str, np.ndarray]:
    """samples: dicts with t|ts (s), temp (C or None), door, comp, mains|power.
    Returns contiguous per-minute arrays; gaps are forward-filled."""
    if not samples:
        return {k: np.array([]) for k in ("minute", "temp", "comp", "door", "mains")}
    def tkey(s):
        return s["t"] if "t" in s else s["ts"]
    m0 = int(tkey(samples[0]) // 60)
    m1 = int(tkey(samples[-1]) // 60)
    n = m1 - m0 + 1
    acc = {k: np.zeros(n) for k in ("temp", "comp", "door", "mains")}
    cnt = np.zeros(n)
    tcnt = np.zeros(n)
    mains_min = np.ones(n)
    for s in samples:
        i = int(tkey(s) // 60) - m0
        cnt[i] += 1
        if s.get("temp") is not None:
            acc["temp"][i] += s["temp"]
            tcnt[i] += 1
        acc["comp"][i] += s.get("comp", 0)
        acc["door"][i] += s.get("door", 0)
        mains = s["mains"] if "mains" in s else (1 if s.get("power", "MAINS") == "MAINS" else 0)
        mains_min[i] = min(mains_min[i], mains)
    out = {"minute": np.arange(m0, m1 + 1)}
    temp = np.where(tcnt > 0, acc["temp"] / np.maximum(tcnt, 1), np.nan)
    comp = np.where(cnt > 0, acc["comp"] / np.maximum(cnt, 1), np.nan)
    door = np.where(cnt > 0, acc["door"] / np.maximum(cnt, 1), np.nan)
    mains = np.where(cnt > 0, mains_min, np.nan)
    for name, arr in (("temp", temp), ("comp", comp), ("door", door), ("mains", mains)):
        out[name] = _ffill(arr)
    return out


def _ffill(a: np.ndarray) -> np.ndarray:
    a = a.copy()
    last = np.nan
    for i in range(len(a)):
        if np.isnan(a[i]):
            a[i] = last
        else:
            last = a[i]
    # back-fill any leading NaNs
    first = next((x for x in a if not np.isnan(x)), 0.0)
    a[np.isnan(a)] = first
    return a


def features_at(s: dict[str, np.ndarray], i: int) -> np.ndarray | None:
    """Feature vector for 'now' = minute index i. Needs i >= HISTORY_MIN."""
    if i < HISTORY_MIN or i >= len(s["temp"]):
        return None
    T, C, D, M = s["temp"], s["comp"], s["door"], s["mains"]
    x = np.arange(6, dtype=float)
    slope5 = float(np.polyfit(x, T[i - 5:i + 1], 1)[0])
    batt = 0
    while batt < 120 and i - batt >= 0 and M[i - batt] < 0.5:
        batt += 1
    since = 0
    while since < 60 and i - since >= 0 and C[i - since] <= 0.0:
        since += 1
    return np.array([
        T[i], T[i] - T[i - 5], T[i] - T[i - 15], T[i] - T[i - 30], slope5,
        T[i - 14:i + 1].max(), T[i - 14:i + 1].min(),
        C[i], C[i - 14:i + 1].mean(), D[i], D[i - 14:i + 1].mean(),
        M[i], batt, since,
    ], dtype=float)


def build_xy(s: dict[str, np.ndarray], stride: int = 1) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Training rows from one series: X, y (= temp 30 min later minus temp now), t_now."""
    X, y, tn = [], [], []
    for i in range(HISTORY_MIN, len(s["temp"]) - HORIZON_MIN, stride):
        f = features_at(s, i)
        X.append(f)
        y.append(s["temp"][i + HORIZON_MIN] - s["temp"][i])
        tn.append(s["temp"][i])
    return np.array(X), np.array(y), np.array(tn)
