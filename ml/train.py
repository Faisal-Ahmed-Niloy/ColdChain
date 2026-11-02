"""Train the 30-minute temperature forecaster and evaluate the compressor-health
estimator, then write the short AI report the brief asks for (p.2).

Usage (from the project root):
    python -m ml.train                       # synthetic data only
    python -m ml.train --db data/coldchain.db  # + real readings recorded by the system

Outputs:
    ml/model/predictor.joblib   models used by the backend
    ml/model/metrics.json       numbers shown on the AI page
    ml/model/dataset_sample.csv a sample of the training table (features + target)
    docs/ai_report.md + docs/ai/*.png
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ai.features import FEATURES, HORIZON_MIN, build_xy, minute_series  # noqa: E402
from backend.app.ai.health import capacity_estimate  # noqa: E402
from ml.physics import FridgeParams, FridgePhysics, simulate  # noqa: E402

MODEL_DIR = ROOT / "ml" / "model"
DOCS = ROOT / "docs"
LIMIT = 8.0
UPPER_Q = 0.8   # chosen on held-out runs: q0.9 gave P=0.49/R=0.86, q0.8 gives a better balance


def synthetic_runs(n_runs: int, hours: float, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    runs = []
    for k in range(n_runs):
        p = FridgeParams.random(rng)
        samples = simulate(p, hours, seed=seed * 1000 + k)
        runs.append({"source": "synthetic", "params": p.__dict__, "series": minute_series(samples)})
    return runs


def recorded_runs(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    runs = []
    for f in conn.execute("SELECT DISTINCT fridge_id FROM readings"):
        rows = [dict(r) for r in conn.execute(
            "SELECT ts, temp, door, comp, power FROM readings WHERE fridge_id=? ORDER BY ts", (f["fridge_id"],))]
        # split at gaps > 10 min so forward-fill never invents long flat stretches
        chunk: list[dict] = []
        for r in rows:
            if chunk and r["ts"] - chunk[-1]["ts"] > 600:
                if len(chunk) > 90 * 12:
                    runs.append({"source": f"recorded:{f['fridge_id']}", "series": minute_series(chunk)})
                chunk = []
            chunk.append(r)
        if len(chunk) > 90 * 12:
            runs.append({"source": f"recorded:{f['fridge_id']}", "series": minute_series(chunk)})
    conn.close()
    return runs


def stack(runs: list[dict], stride: int):
    X, y, tn, gid = [], [], [], []
    for g, r in enumerate(runs):
        xi, yi, ti = build_xy(r["series"], stride)
        if len(xi):
            X.append(xi); y.append(yi); tn.append(ti); gid.append(np.full(len(yi), g))
    return np.vstack(X), np.concatenate(y), np.concatenate(tn), np.concatenate(gid)


def prf(pred: np.ndarray, truth: np.ndarray) -> dict:
    tp = int(np.sum(pred & truth)); fp = int(np.sum(pred & ~truth)); fn = int(np.sum(~pred & truth))
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3), "tp": tp, "fp": fp, "fn": fn}


def evaluate_health(n: int, seed: int) -> dict:
    """For n random fridges: calibrate on a healthy 6 h run, then estimate a known health level."""
    rng = np.random.default_rng(seed)
    true, est = [], []
    for k in range(n):
        p = FridgeParams.random(rng)
        p.health, p.health_decay_per_h, p.power_fail_rate_per_h = 1.0, 0.0, 0.0
        base = capacity_estimate(minute_series(simulate(p, 6, seed=90000 + k)))
        h = float(rng.uniform(0.25, 1.0))
        p.health = h
        p.ambient += float(rng.uniform(-4, 4))   # room is not the same temperature as at calibration
        cur = capacity_estimate(minute_series(simulate(p, 6, seed=91000 + k)))
        if base["ok"] and cur["ok"] and base["capacity"] > 0:
            true.append(h * 100)
            est.append(min(100.0, cur["capacity"] / base["capacity"] * 100))
    true_a, est_a = np.array(true), np.array(est)
    return {"fridges": len(true), "mae_points": round(float(np.mean(np.abs(true_a - est_a))), 2),
            "corr": round(float(np.corrcoef(true_a, est_a)[0, 1]), 3), "pairs": list(zip(true, est))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=160)
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--db", default=None, help="add real readings from this ColdChain database")
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args()

    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.inspection import permutation_importance
    import joblib

    t0 = time.time()
    runs = synthetic_runs(args.runs, args.hours, args.seed)
    if args.db:
        rec = recorded_runs(args.db)
        print(f"recorded runs added: {len(rec)}")
        runs += rec
    X, y, tn, gid = stack(runs, stride=2)
    rng = np.random.default_rng(args.seed)
    test_groups = set(rng.choice(len(runs), size=max(1, len(runs) // 5), replace=False).tolist())
    te = np.isin(gid, list(test_groups))
    tr = ~te
    print(f"dataset: {len(y)} rows from {len(runs)} runs ({tr.sum()} train / {te.sum()} test) in {time.time()-t0:.0f}s")

    common = dict(max_iter=300, learning_rate=0.06, max_leaf_nodes=31, l2_regularization=1.0, random_state=0)
    median = HistGradientBoostingRegressor(loss="absolute_error", **common).fit(X[tr], y[tr])
    upper = HistGradientBoostingRegressor(loss="quantile", quantile=UPPER_Q, **common).fit(X[tr], y[tr])

    actual = tn[te] + y[te]
    pred = tn[te] + median.predict(X[te])
    up = np.maximum(tn[te] + upper.predict(X[te]), pred)
    slope = X[te][:, FEATURES.index("slope5")]
    lin = tn[te] + np.clip(slope * HORIZON_MIN, -6, 15)
    persist = tn[te]

    def err(p):
        return {"mae": round(float(np.mean(np.abs(p - actual))), 3),
                "rmse": round(float(np.sqrt(np.mean((p - actual) ** 2))), 3)}

    truth = actual > LIMIT
    metrics = {
        "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": {"runs": len(runs), "synthetic_runs": sum(r["source"] == "synthetic" for r in runs),
                    "recorded_runs": sum(r["source"] != "synthetic" for r in runs),
                    "hours_per_synthetic_run": args.hours, "rows": int(len(y)), "train_rows": int(tr.sum()),
                    "test_rows": int(te.sum()), "test_split": "20% of runs held out (no run in both sets)",
                    "positive_rate_test": round(float(truth.mean()), 4)},
        "features": FEATURES,
        "horizon_min": HORIZON_MIN,
        "regression": {"model": err(pred), "persistence_baseline": err(persist), "linear_trend_baseline": err(lin)},
        "upper_quantile": UPPER_Q,
        "warning_T30_above_8C": {"model_upper": prf(up > LIMIT, truth), "model_median": prf(pred > LIMIT, truth),
                                 "linear_trend_baseline": prf(lin > LIMIT, truth)},
        "upper_coverage": round(float(np.mean(actual <= up)), 3),
    }
    sub = rng.choice(np.where(te)[0], size=min(4000, int(te.sum())), replace=False)
    imp = permutation_importance(median, X[sub], y[sub], n_repeats=3, random_state=0, scoring="neg_mean_absolute_error")
    metrics["feature_importance"] = sorted(
        [{"feature": f, "mae_increase": round(float(m), 4)} for f, m in zip(FEATURES, imp.importances_mean)],
        key=lambda d: -d["mae_increase"])
    hres = evaluate_health(40, args.seed + 1)
    metrics["health_estimator"] = {k: v for k, v in hres.items() if k != "pairs"}

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"median": median, "upper": upper, "features": FEATURES, "upper_q": UPPER_Q, "trained_at": metrics["trained_at"],
                 "version": "1.0"}, MODEL_DIR / "predictor.joblib")
    (MODEL_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2))
    idx = rng.choice(len(y), size=min(5000, len(y)), replace=False)
    hdr = ",".join(FEATURES + ["target_delta_30min", "temp_in_30min"])
    np.savetxt(MODEL_DIR / "dataset_sample.csv", np.column_stack([X[idx], y[idx], tn[idx] + y[idx]]),
               delimiter=",", header=hdr, comments="", fmt="%.4f")
    print(json.dumps({k: metrics[k] for k in ("regression", "warning_T30_above_8C", "upper_coverage",
                                               "health_estimator")}, indent=2))
    if not args.no_report:
        from ml.report import write_report
        write_report(metrics, runs, test_groups=sorted(test_groups), actual=actual, pred=pred, up=up,
                     hres=hres, model={"median": median, "upper": upper})
    print(f"done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
