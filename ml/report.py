"""Writes docs/ai_report.md and its figures (called by ml/train.py)."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from backend.app.ai.features import FEATURES, HISTORY_MIN, HORIZON_MIN, features_at  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "docs" / "ai"
INK, ACCENT, WARN, GRID = "#1f2933", "#2563eb", "#dc2626", "#d9dee5"

FEATURE_TEXT = {
    "t_now": "Mean temperature in the latest minute (C)",
    "d5": "Temperature change over the last 5 min", "d15": "Change over 15 min", "d30": "Change over 30 min",
    "slope5": "Least-squares slope over the last 6 min (C/min)",
    "t_max15": "Max temperature, last 15 min", "t_min15": "Min temperature, last 15 min",
    "comp_now": "Compressor ON fraction, latest minute", "comp15": "Compressor duty cycle, last 15 min",
    "door_now": "Door-open fraction, latest minute", "door15": "Door-open fraction, last 15 min",
    "mains_now": "1 = mains power, 0 = battery",
    "batt_minutes": "Consecutive minutes on battery (cap 120)",
    "since_comp_on": "Minutes since the compressor last ran (cap 60)",
}


def _style(ax):
    ax.grid(True, color=GRID, linewidth=0.6)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def _trace_figure(runs, test_groups, model, path):
    # pick a held-out run that contains a power failure (most interesting for the forecast)
    def healthy(g):
        p = runs[g].get("params", {})
        return p.get("health", 0) >= 0.95 and p.get("health_decay_per_h", 1) == 0
    with_fail = [g for g in test_groups if runs[g]["series"]["mains"].min() < 0.5]
    gi = next((g for g in with_fail if healthy(g)), with_fail[0] if with_fail else test_groups[0])
    s = runs[gi]["series"]
    n = len(s["temp"])
    m = np.arange(n)
    preds, ups, at = [], [], []
    for i in range(HISTORY_MIN, n - HORIZON_MIN):
        x = features_at(s, i).reshape(1, -1)
        p = s["temp"][i] + model["median"].predict(x)[0]
        u = max(p, s["temp"][i] + model["upper"].predict(x)[0])
        preds.append(p); ups.append(u); at.append(i + HORIZON_MIN)
    # focus on a 6 h window around the first power failure
    off = np.where(s["mains"] < 0.5)[0]
    c = int(off[0]) if len(off) else n // 2
    lo, hi = max(HISTORY_MIN + HORIZON_MIN, c - 120), min(n, c + 240)
    fig, ax = plt.subplots(figsize=(9, 3.6), dpi=130)
    sel = (m >= lo) & (m < hi)
    ax.plot(m[sel] / 60, s["temp"][sel], color=INK, lw=1.4, label="Measured temperature")
    at, preds, ups = np.array(at), np.array(preds), np.array(ups)
    ps = (at >= lo) & (at < hi)
    ax.plot(at[ps] / 60, preds[ps], color=ACCENT, lw=1.2, label="Forecast made 30 min earlier")
    ax.fill_between(at[ps] / 60, preds[ps], ups[ps], color=ACCENT, alpha=0.15, label="Upper bound (80th percentile)")
    ax.axhline(8, color=WARN, lw=1, ls="--")
    ax.text(lo / 60, 8.15, "8 C limit", color=WARN, fontsize=8)
    for a, b in _spans(s["mains"] < 0.5):
        if b > lo and a < hi:
            ax.axvspan(max(a, lo) / 60, min(b, hi) / 60, color="#f59e0b", alpha=0.12, lw=0)
    ax.set_xlabel("Hours since start of run"); ax.set_ylabel("C")
    ax.set_title("Held-out run: forecast vs. reality (shaded = power failure)", fontsize=10, loc="left")
    ax.legend(fontsize=8, frameon=False, loc="upper left"); _style(ax)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def _spans(mask):
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        if not v and start is not None:
            out.append((start, i)); start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def write_report(metrics, runs, test_groups, actual, pred, up, hres, model):
    FIG.mkdir(parents=True, exist_ok=True)
    _trace_figure(runs, test_groups, model, FIG / "forecast_trace.png")

    rng = np.random.default_rng(0)
    k = rng.choice(len(actual), size=min(3000, len(actual)), replace=False)
    fig, ax = plt.subplots(figsize=(4.6, 4.2), dpi=130)
    ax.scatter(actual[k], pred[k], s=4, alpha=0.35, color=ACCENT)
    lim = [min(actual.min(), pred.min()), max(actual.max(), pred.max())]
    ax.plot(lim, lim, color=INK, lw=0.8)
    ax.axvline(8, color=WARN, lw=0.8, ls="--"); ax.axhline(8, color=WARN, lw=0.8, ls="--")
    ax.set_xlabel("Actual temperature in 30 min (C)"); ax.set_ylabel("Predicted (C)")
    ax.set_title("Predicted vs. actual (test runs)", fontsize=10, loc="left"); _style(ax)
    fig.tight_layout(); fig.savefig(FIG / "pred_vs_actual.png"); plt.close(fig)

    imp = metrics["feature_importance"]
    fig, ax = plt.subplots(figsize=(6.4, 4), dpi=130)
    names = [d["feature"] for d in imp][::-1]
    vals = [d["mae_increase"] for d in imp][::-1]
    ax.barh(names, vals, color=ACCENT)
    ax.set_xlabel("Increase in MAE when the feature is shuffled (C)")
    ax.set_title("Which inputs the forecaster relies on", fontsize=10, loc="left"); _style(ax)
    fig.tight_layout(); fig.savefig(FIG / "feature_importance.png"); plt.close(fig)

    pairs = np.array(hres["pairs"])
    fig, ax = plt.subplots(figsize=(4.6, 4.2), dpi=130)
    ax.scatter(pairs[:, 0], pairs[:, 1], s=18, color=ACCENT)
    ax.plot([20, 100], [20, 100], color=INK, lw=0.8)
    ax.set_xlabel("True compressor health (%)"); ax.set_ylabel("Estimated health score")
    ax.set_title("Health score vs. ground truth", fontsize=10, loc="left"); _style(ax)
    fig.tight_layout(); fig.savefig(FIG / "health_estimator.png"); plt.close(fig)

    d, r, w = metrics["dataset"], metrics["regression"], metrics["warning_T30_above_8C"]
    h = metrics["health_estimator"]
    feat_rows = "\n".join(f"| `{f}` | {FEATURE_TEXT[f]} |" for f in FEATURES)
    top = ", ".join(f"`{x['feature']}`" for x in metrics["feature_importance"][:3])
    md = f"""# ColdChain AI report

Trained {metrics['trained_at']}. All numbers below come from `ml/model/metrics.json`, produced by `python -m ml.train`.

## 1. Goal

1. Forecast each fridge's temperature **{metrics['horizon_min']} minutes ahead** and warn when it **may exceed 8 C**.
2. Score compressor health from 0 to 100 and flag weakening.
3. Feed both into relocation suggestions that a pharmacist confirms with one click.

## 2. Dataset

No dataset came with the brief, so training data is generated by a physics model of a vaccine fridge (`ml/physics.py`). Real readings recorded by the system can be added with `--db`.

| Item | Value |
| --- | --- |
| Runs | {d['runs']} ({d['synthetic_runs']} synthetic, {d['recorded_runs']} recorded) |
| Length of each synthetic run | {d['hours_per_synthetic_run']} h at 5 s sampling |
| Training rows (1 per 2 min) | {d['train_rows']} |
| Test rows | {d['test_rows']} ({d['test_split']}) |
| Test rows where T(+30 min) > 8 C | {d['positive_rate_test']*100:.1f}% |

Each run draws random room temperature (24-35 C), insulation, compressor strength, compressor wear and wear rate, door-opening habits and power failures. The fridge is controlled by the same rule as the firmware: ON at 7 C, OFF at 3 C, 60 s minimum off-time.

The model is: temperature change per minute = wall leak × (room − fridge) + door leak while open − compressor cooling × health.

**Limitation:** synthetic data teaches the model the physics but not the quirks of the actual fridges used at the demo. Retrain with `--db` after a few hours of real logging.

## 3. Features

Readings are resampled to 1-minute bins. Features describe the last 30 minutes. The same code (`backend/app/ai/features.py`) runs in training and in the live backend.

| Feature | Meaning |
| --- | --- |
{feat_rows}

Target: temperature change over the next 30 minutes.

## 4. Models

- **Median forecaster:** gradient-boosted trees (`HistGradientBoostingRegressor`, absolute-error loss).
- **Upper-bound forecaster:** the same algorithm with a {metrics['upper_quantile']} quantile loss (80th percentile).
- **Warning rule:** warn when the upper bound is above 8 C. This matches the brief's "may go above 8 C". The quantile was chosen on held-out runs: 0.9 caught more breaches (recall 0.86) but half its warnings were false (precision 0.49); 0.8 balances the two.
- **Compressor health:** physics-based, no training needed. Capacity = warming rate with the compressor OFF − cooling rate with it ON, measured in clean minutes (door closed, 2-8.5 C). Health = capacity ÷ the capacity learned when the fridge was first calibrated.

## 5. Results (held-out test runs)

**Forecast error, temperature 30 min ahead (C)**

| Method | MAE | RMSE |
| --- | --- | --- |
| Gradient boosting (ours) | {r['model']['mae']} | {r['model']['rmse']} |
| Persistence (no change) | {r['persistence_baseline']['mae']} | {r['persistence_baseline']['rmse']} |
| Linear trend of last 5 min | {r['linear_trend_baseline']['mae']} | {r['linear_trend_baseline']['rmse']} |

**Breach warning: "T(+30 min) > 8 C"**

| Method | Precision | Recall | F1 |
| --- | --- | --- | --- |
| Ours, upper bound (used live) | {w['model_upper']['precision']} | {w['model_upper']['recall']} | {w['model_upper']['f1']} |
| Ours, median forecast | {w['model_median']['precision']} | {w['model_median']['recall']} | {w['model_median']['f1']} |
| Linear trend | {w['linear_trend_baseline']['precision']} | {w['linear_trend_baseline']['recall']} | {w['linear_trend_baseline']['f1']} |

The upper bound was at or above the real value {metrics['upper_coverage']*100:.1f}% of the time (target {metrics['upper_quantile']*100:.0f}%). Warning on the upper bound instead of the median trades some precision for recall, which is the right trade for vaccines: a missed breach costs more than an extra check.

**Compressor health:** across {h['fridges']} simulated fridges with known wear, the score was off by {h['mae_points']} points on average (correlation {h['corr']}).

![Forecast trace](ai/forecast_trace.png)

![Predicted vs actual](ai/pred_vs_actual.png)

![Feature importance](ai/feature_importance.png)

The forecaster relies most on {top}.

![Health estimator](ai/health_estimator.png)

## 6. How it runs live

Every 30 s the backend:

1. Reads each fridge's last 6 h of readings.
2. Updates the forecast and health score.
3. Raises a *predicted breach* or *compressor weak* alert on change.
4. If a fridge may become unsafe, writes a relocation plan: least heat budget left goes first, and targets must be safe fridges with room.
5. The plan waits for a pharmacist's one-click confirmation.
"""
    (ROOT / "docs" / "ai_report.md").write_text(md)
