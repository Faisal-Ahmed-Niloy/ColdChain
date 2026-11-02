"""30-minute-ahead temperature prediction (brief p.2).

Two gradient-boosted models trained by ml/train.py:
  - "median" predicts the most likely temperature in 30 min
  - "upper"  predicts the 80th percentile (quantile regression)
Warning rule: if the upper forecast exceeds 8 C the fridge "may go
above 8 C" -> warning. If the model file is missing or cannot be loaded (for
example a different scikit-learn version), a transparent linear-trend fallback
is used and labelled as such.
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from ..config import settings
from .features import FEATURES, HISTORY_MIN, HORIZON_MIN, features_at, minute_series

LIMIT = 8.0
_model = None
_model_err: str | None = None
_lock = threading.Lock()


def load_model(path: str | None = None):
    global _model, _model_err
    with _lock:
        p = Path(path or settings.model_path)
        try:
            import joblib
            m = joblib.load(p)
            if m.get("features") != FEATURES:
                raise ValueError("feature list mismatch - retrain with python -m ml.train")
            _model, _model_err = m, None
        except Exception as e:  # missing file, version mismatch, ...
            _model, _model_err = None, f"{type(e).__name__}: {e}"
        return _model


def model_info() -> dict:
    if _model is None and _model_err is None:
        load_model()
    if _model is None:
        return {"loaded": False, "error": _model_err, "mode": "linear-trend fallback"}
    return {"loaded": True, "mode": f"gradient boosting (median + q{_model.get('upper_q', 0.8)})",
            "trained_at": _model.get("trained_at"), "version": _model.get("version")}


def predict_from_series(s: dict) -> dict:
    n = len(s["temp"])
    if n < HISTORY_MIN + 1:
        return {"ok": False, "note": f"need {HISTORY_MIN} min of history, have {n}"}
    i = n - 1
    x = features_at(s, i)
    t_now = float(s["temp"][i])
    if _model is None and _model_err is None:
        load_model()
    if _model is not None:
        X = x.reshape(1, -1)
        pred = t_now + float(_model["median"].predict(X)[0])
        upper = t_now + float(_model["upper"].predict(X)[0])
        upper = max(upper, pred)
        mode = "model"
    else:
        slope = float(x[FEATURES.index("slope5")])
        pred = t_now + float(np.clip(slope * HORIZON_MIN, -6, 15))
        upper = pred + 1.0
        mode = "fallback"
    pred, upper = round(pred, 1), round(upper, 1)
    warn = upper > LIMIT
    if pred > LIMIT:
        note = f"Likely above 8 °C in 30 min (forecast {pred:.1f} °C)"
    elif warn:
        note = f"May exceed 8 °C in 30 min (upper bound {upper:.1f} °C)"
    else:
        note = f"Expected {pred:.1f} °C in 30 min"
    return {"ok": True, "t_now": round(t_now, 2), "pred": pred, "upper": upper,
            "warning": bool(warn), "note": note, "mode": mode}


def predict_from_readings(readings: list[dict]) -> dict:
    return predict_from_series(minute_series(readings))
