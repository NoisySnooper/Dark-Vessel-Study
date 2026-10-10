"""Calibrated radar vessel length: AIS hull length predicted from the raw radar extent (length_est_m), with an interval.

`length_est_m` (detect.cfar.extract_detections) is the extent of a contact's detected pixels along their principal axis
times the 10 m pixel spacing, and the longer of the VV and VH extents when both channels detect it
(pipeline.fuse_polarisations). It runs long: the Sentinel-1 IW GRD resolution cell (about 20 x 22 m) spans two 10 m
pixels, bright hulls spread over neighbouring pixels, and pixels within one pixel of each other join one object. This
module fits and applies a correction measured on matched pairs (radar contact, AIS or registry hull length); see
docs/length_calibration.md for the data, the cross-validation and the limits.

Models (all in log space, x = length_est_m, y = hull length):
  ratio      log y = c + log x                                   (one constant factor)
  loglinear  log y = a + b log x + sum_k g_k (z_k - centre_k)    (median / least absolute deviation fit)
  isotonic   log y = f(log x), f non-decreasing (pool adjacent violators), linear between knots
Interval: [m exp(q_lo), m exp(q_hi)] around the median prediction m, q_lo and q_hi the finite-sample (split conformal)
quantiles of held-out log residuals at level LEVEL (80 %).

Calibration json (SCHEMA): build, calibration_id, target, model, fallback (length only; used when a covariate of the
model is missing or outside its fitted range), min_claim_m, reasons, provenance and caveat fields. Apply with
length_cal_m() for one contact or apply_frame() for a table. Null results carry a reason code (REASONS); data carry
codes, display text lives here.

The calibration describes typical pairs, which are mostly steel ships of 25 m and longer that carry AIS. It makes no
claim below MIN_CLAIM_M, at the 2-pixel floor (PIXEL_FLOOR_M) or where it would make the hull longer than the radar
return (short_return: a model fitted on AIS ships carries their size into weak returns), and none for a return that is not
a single vessel (two hulls side by side, a hull and its
wake): such returns are part of the residual spread, not of the median.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA = "darkvessel.length_calibration/1"
LEVEL = 0.8
MIN_CLAIM_M = 25.0
PIXEL_M = 10.0
# a 2-pixel object reads 20 m along an image axis and (sqrt(2) + 1) x 10 = 24.1 m on the diagonal (cfar._principal_extent)
PIXEL_FLOOR_M = round((math.sqrt(2) + 1) * PIXEL_M, 1)
OUT_COLUMNS = ["length_cal_m", "length_cal_lo_m", "length_cal_hi_m", "length_cal_reason", "length_cal_id"]

REASONS = {
    "missing_length": "no radar length estimate",
    "pixel_floor": (f"radar length at the 2-pixel floor ({PIXEL_FLOOR_M:g} m or less): the extent cannot tell a 5 m boat "
                    "from a 30 m one"),
    "below_range": "radar length below the shortest radar length of the calibration pairs",
    "above_range": "radar length above the longest radar length of the calibration pairs",
    "short_return": ("the calibrated hull would be longer than the radar return itself: a weak or partial return, where the "
                     "hull depends on which vessels carry such returns (mostly large AIS ships in the pairs, not small boats)"),
    "below_min_claim": f"calibrated length under {MIN_CLAIM_M:.0f} m: the pairs hold almost no vessels this small, so no claim",
    "no_calibration": "no calibration loaded",
}

# Covariates a model may use, all available in the live and regional producers or derivable from their columns.
COVARIATES = {
    "scr_max_db": "peak-to-background ratio of the brighter channel (dB): max(scr_vv_db, scr_vh_db)",
    "fill_width_m": "detected area over radar length (m): n_pixels x 100 / length_est_m, the blob's mean width; low for "
                    "thin streaks (wakes, smeared or fragmented returns)",
    "inc_angle_deg": "incidence angle at the contact (degrees)",
    "cnn_score": "CNN verifier score (verifier_v0)",
    "dual_pol": "1 when detected in both VV and VH, else 0",
    "mission_s1d": "1 for Sentinel-1D, 0 otherwise",
}


# ----------------------------------------------------------------------------------------------- covariates
def derive_covariates(df: pd.DataFrame) -> pd.DataFrame:
    """COVARIATES from producer columns (scr_vv_db, scr_vh_db, n_pixels, length_est_m, inc_angle_deg, cnn_score,
    pol_class or the scr columns, mission). A covariate whose inputs are absent is NaN."""
    n = len(df)
    col = lambda c: pd.to_numeric(df[c], errors="coerce").to_numpy(float) if c in df else np.full(n, np.nan)  # noqa: E731
    vv, vh = col("scr_vv_db"), col("scr_vh_db")
    with np.errstate(invalid="ignore", divide="ignore"):
        scr = np.where(np.isnan(vv), vh, np.where(np.isnan(vh), vv, np.maximum(vv, vh)))
        L = col("length_est_m")
        fill = np.where(L > 0, col("n_pixels") * PIXEL_M * PIXEL_M / L, np.nan)
    if "pol_class" in df:
        pc = df["pol_class"].astype(object)
        dual = np.where(pc.isna(), np.nan, (pc == "VV+VH").astype(float))
    elif "scr_vv_db" in df and "scr_vh_db" in df:
        dual = np.where(np.isnan(vv) & np.isnan(vh), np.nan, (~np.isnan(vv) & ~np.isnan(vh)).astype(float))
    else:
        dual = np.full(n, np.nan)
    mis = df["mission"].astype(object) if "mission" in df else pd.Series([None] * n)
    s1d = np.where(pd.isna(mis), np.nan, (mis.astype(str) == "S1D").astype(float))
    return pd.DataFrame({"scr_max_db": scr, "fill_width_m": fill, "inc_angle_deg": col("inc_angle_deg"),
                         "cnn_score": col("cnn_score"), "dual_pol": dual, "mission_s1d": s1d}, index=df.index)


# ----------------------------------------------------------------------------------------------- fitting
def _lad(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Least absolute deviation coefficients (first column of X is the intercept) by linear programming."""
    from scipy import sparse
    from scipy.optimize import linprog

    n, p = X.shape
    # min sum(u + v) s.t. X beta + u - v = y, u, v >= 0, beta free
    c = np.concatenate([np.zeros(p), np.ones(2 * n)])
    eye = sparse.identity(n, format="csr")
    A = sparse.hstack([sparse.csr_matrix(X), eye, -eye], format="csr")
    bounds = [(None, None)] * p + [(0, None)] * (2 * n)
    res = linprog(c, A_eq=A, b_eq=y, bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(f"LAD fit failed: {res.message}")
    return res.x[:p]


def _z(model: dict, Z: pd.DataFrame | None, n: int) -> np.ndarray:
    covs = model.get("covariates") or []
    if not covs:
        return np.zeros((n, 0))
    if Z is None:
        return np.full((n, len(covs)), np.nan)
    return np.column_stack([(pd.to_numeric(Z[c["name"]], errors="coerce").to_numpy(float) if c["name"] in Z
                             else np.full(n, np.nan)) - c["centre"] for c in covs])


def fit_ratio(x, y) -> dict:
    lx, ly = np.log(np.asarray(x, float)), np.log(np.asarray(y, float))
    return {"kind": "ratio", "intercept": float(np.median(ly - lx)), "slope": 1.0, "covariates": []}


def fit_loglinear(x, y, Z: pd.DataFrame | None = None, covariates: list[str] | tuple = ()) -> dict:
    lx, ly = np.log(np.asarray(x, float)), np.log(np.asarray(y, float))
    covs, cols = [], [np.ones_like(lx), lx]
    for name in covariates:
        z = pd.to_numeric(Z[name], errors="coerce").to_numpy(float)
        centre = float(np.median(z))
        covs.append({"name": name, "centre": centre, "range": [float(np.min(z)), float(np.max(z))]})
        cols.append(z - centre)
    beta = _lad(np.column_stack(cols), ly)
    for c, g in zip(covs, beta[2:]):
        c["coef"] = float(g)
    return {"kind": "loglinear", "intercept": float(beta[0]), "slope": float(beta[1]), "covariates": covs}


def fit_isotonic(x, y) -> dict:
    from sklearn.isotonic import IsotonicRegression

    lx, ly = np.log(np.asarray(x, float)), np.log(np.asarray(y, float))
    iso = IsotonicRegression(increasing=True, out_of_bounds="clip").fit(lx, ly)
    return {"kind": "isotonic", "knots_log_x": [float(v) for v in iso.X_thresholds_],
            "knots_log_y": [float(v) for v in iso.y_thresholds_], "covariates": []}


def fit_model(kind: str, x, y, Z: pd.DataFrame | None = None, covariates=()) -> dict:
    if kind == "ratio":
        return fit_ratio(x, y)
    if kind == "loglinear":
        return fit_loglinear(x, y, Z, covariates)
    if kind == "isotonic":
        return fit_isotonic(x, y)
    raise ValueError(f"unknown model kind {kind!r}")


def predict_log(model: dict, x, Z: pd.DataFrame | None = None) -> np.ndarray:
    """Median log hull length. NaN where x is missing or a covariate is missing."""
    lx = np.log(np.asarray(x, float))
    if model["kind"] == "isotonic":
        out = np.interp(lx, model["knots_log_x"], model["knots_log_y"])
        return np.where(np.isfinite(lx), out, np.nan)
    out = model["intercept"] + model["slope"] * lx
    covs = model.get("covariates") or []
    if covs:
        z = _z(model, Z, len(lx))
        out = out + z @ np.array([c["coef"] for c in covs])
    return out


# ----------------------------------------------------------------------------------------------- intervals and CV
def conformal_quantiles(resid, level: float = LEVEL) -> tuple[float, float]:
    """Lower and upper quantiles of held-out log residuals with the finite-sample (split conformal) correction:
    ranks floor((n + 1) a) and ceil((n + 1)(1 - a)), a = (1 - level) / 2. NaN when there are too few residuals."""
    r = np.sort(np.asarray(resid, float)[np.isfinite(resid)])
    n = len(r)
    a = (1.0 - level) / 2.0
    lo_k, hi_k = math.floor((n + 1) * a + 1e-9), math.ceil((n + 1) * (1 - a) - 1e-9)
    if n == 0 or lo_k < 1 or hi_k > n:
        return float("nan"), float("nan")
    return float(r[lo_k - 1]), float(r[hi_k - 1])


def group_folds(groups, max_folds: int = 10) -> np.ndarray:
    """Fold index per row: one fold per group when there are at most max_folds groups, else groups dealt to
    max_folds folds largest first (deterministic, balanced by rows). Never splits a group."""
    g = pd.Series(np.asarray(groups, dtype=object)).astype(str)
    sizes = g.value_counts()
    sizes = sizes.sort_index().sort_values(ascending=False, kind="stable")
    if len(sizes) <= max_folds:
        fold_of = {k: i for i, k in enumerate(sorted(sizes.index))}
    else:
        load = np.zeros(max_folds)
        fold_of = {}
        for k, s in sizes.items():
            f = int(np.argmin(load))
            fold_of[k] = f
            load[f] += s
    return g.map(fold_of).to_numpy(int)


def _oof(kind, x, y, Z, covariates, folds) -> np.ndarray:
    pred = np.full(len(x), np.nan)
    for f in np.unique(folds):
        te = folds == f
        tr = ~te
        if tr.sum() < 3:
            continue
        m = fit_model(kind, x[tr], y[tr], None if Z is None else Z[tr], covariates)
        pred[te] = predict_log(m, x[te], None if Z is None else Z[te])
    return pred


def grouped_cv(kind: str, x, y, groups, Z: pd.DataFrame | None = None, covariates=(), level: float = LEVEL,
               max_folds: int = 10, inner_folds: int = 5, intervals: bool = True) -> dict:
    """Out-of-fold median predictions (log) by grouped folds, and nested intervals: in each outer fold the interval
    quantiles come from out-of-fold residuals of an inner grouped CV on the training groups only, so the held-out
    group never shapes its own interval. Returns pred_log, lo_log, hi_log, folds, n_folds."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    groups = np.asarray(groups, dtype=object)
    Z = None if Z is None else Z.reset_index(drop=True)
    folds = group_folds(groups, max_folds)
    pred = np.full(len(x), np.nan)
    lo, hi = np.full(len(x), np.nan), np.full(len(x), np.nan)
    for f in np.unique(folds):
        te, tr = folds == f, folds != f
        if tr.sum() < 3:
            continue
        Ztr = None if Z is None else Z[tr].reset_index(drop=True)
        m = fit_model(kind, x[tr], y[tr], Ztr, covariates)
        pred[te] = predict_log(m, x[te], None if Z is None else Z[te].reset_index(drop=True))
        inner = group_folds(groups[tr], inner_folds)
        if intervals and len(np.unique(inner)) >= 2:
            ip = _oof(kind, x[tr], y[tr], Ztr, covariates, inner)
            q_lo, q_hi = conformal_quantiles(np.log(y[tr]) - ip, level)
            lo[te], hi[te] = pred[te] + q_lo, pred[te] + q_hi
    return {"pred_log": pred, "lo_log": lo, "hi_log": hi, "folds": folds, "n_folds": int(len(np.unique(folds)))}


def metrics(y, pred, lo=None, hi=None) -> dict:
    """Agreement of predicted against true hull length (metres): MAE, median absolute error, median ratio
    pred / true, shares within a factor 1.5 and 2, mean absolute log error, interval coverage and median width."""
    y, p = np.asarray(y, float), np.asarray(pred, float)
    ok = np.isfinite(y) & np.isfinite(p) & (y > 0) & (p > 0)
    y, p = y[ok], p[ok]
    out = {"n": int(ok.sum())}
    if not len(y):
        return out
    r = p / y
    f = np.maximum(r, 1 / r)
    out.update({"mae_m": round(float(np.mean(np.abs(p - y))), 1), "median_ae_m": round(float(np.median(np.abs(p - y))), 1),
                "median_ratio": round(float(np.median(r)), 3), "within_1_5": round(float(np.mean(f <= 1.5)), 3),
                "within_2": round(float(np.mean(f <= 2.0)), 3), "mean_abs_log_err": round(float(np.mean(np.log(f))), 4)})
    if lo is not None and hi is not None:
        lo_, hi_ = np.asarray(lo, float)[ok], np.asarray(hi, float)[ok]
        have = np.isfinite(lo_) & np.isfinite(hi_)
        if have.any():
            out["coverage"] = round(float(np.mean((y[have] >= lo_[have]) & (y[have] <= hi_[have]))), 3)
            out["n_with_interval"] = int(have.sum())
            with np.errstate(divide="ignore"):
                out["median_interval_factor"] = round(float(np.median(hi_[have] / lo_[have])), 2)
    return out


def cv_report(kind, x, y, groups, Z=None, covariates=(), level=LEVEL, max_folds=10, inner_folds=5) -> tuple[dict, dict]:
    """(metrics before, metrics after) under grouped CV, plus the CV arrays in the second dict under 'cv'."""
    cv = grouped_cv(kind, x, y, groups, Z, covariates, level, max_folds, inner_folds)
    before = metrics(y, x)
    after = metrics(y, np.exp(cv["pred_log"]), np.exp(cv["lo_log"]), np.exp(cv["hi_log"]))
    after["n_folds"] = cv["n_folds"]
    return before, {**after, "cv": cv}


def select_model(x, y, groups, Z: pd.DataFrame | None = None, candidates=(), level: float = LEVEL,
                 max_folds: int = 10, min_gain: float = 0.05, iso_gain: float = 0.10, max_covariates: int = 2) -> dict:
    """Choose the model structure by grouped CV on the mean absolute log error (MALE).

    1. ratio against loglinear: loglinear only if its MALE is at least `min_gain` (relative) lower.
    2. covariates, forward: add the candidate (available on every row) that lowers MALE most, if by at least
       `min_gain`; at most `max_covariates`. Covariates force the loglinear form.
    3. isotonic replaces the chosen model only if its MALE is at least `iso_gain` lower and its MAE is not higher
       (the simpler model is kept unless the other is clearly better).
    Returns kind, covariates and the table of every comparison."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    Z = None if Z is None else Z.reset_index(drop=True)
    table = []

    def score(kind, covs):
        cv = grouped_cv(kind, x, y, groups, Z, covs, level, max_folds, intervals=False)
        m = metrics(y, np.exp(cv["pred_log"]))
        row = {"kind": kind, "covariates": list(covs), "male": m.get("mean_abs_log_err", np.inf), "mae_m": m.get("mae_m", np.inf),
               "n_folds": cv["n_folds"]}
        table.append(row)
        return row

    best = score("ratio", [])
    ll = score("loglinear", [])
    if ll["male"] <= best["male"] * (1 - min_gain):
        best = ll
    usable = [c for c in candidates if Z is not None and c in Z and np.isfinite(pd.to_numeric(Z[c], errors="coerce")).all()
              and pd.to_numeric(Z[c], errors="coerce").nunique() > 1]
    chosen: list[str] = []
    base = best if best["kind"] == "loglinear" else ll
    while len(chosen) < max_covariates:
        trials = [score("loglinear", chosen + [c]) for c in usable if c not in chosen]
        if not trials:
            break
        t = min(trials, key=lambda r: r["male"])
        if t["male"] <= min(best["male"], base["male"]) * (1 - min_gain):
            chosen = t["covariates"]
            best = base = t
        else:
            break
    iso = score("isotonic", [])
    if iso["male"] <= best["male"] * (1 - iso_gain) and iso["mae_m"] <= best["mae_m"]:
        best = iso
    return {"kind": best["kind"], "covariates": list(best["covariates"]), "table": table,
            "rule": (f"grouped CV, mean absolute log error: loglinear over ratio and each covariate only for a gain of at least "
                     f"{min_gain:.0%}, at most {max_covariates} covariates (available on every pair); isotonic only for a gain "
                     f"of at least {iso_gain:.0%} with MAE not higher")}


# ----------------------------------------------------------------------------------------------- calibration json
def build_model(kind: str, x, y, groups=None, Z: pd.DataFrame | None = None, covariates=(), level: float = LEVEL,
                max_folds: int = 10) -> dict:
    """Fit on all pairs; interval quantiles from grouped out-of-fold residuals when there are at least two groups,
    else from leave-one-pair-out residuals (one scene: an interval, not a validation, and likely too narrow)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    Z = None if Z is None else Z.reset_index(drop=True)
    m = fit_model(kind, x, y, Z, covariates)
    ng = 0 if groups is None else len(pd.unique(np.asarray(groups, dtype=object)))
    if ng >= 2:
        folds = group_folds(groups, max_folds)
        src = f"held-out log residuals of grouped cross-validation ({len(np.unique(folds))} folds)"
    else:
        folds = np.arange(len(x))
        src = "leave-one-pair-out log residuals (all pairs from one scene: not a grouped validation)"
    oof = _oof(kind, x, y, Z, covariates, folds)
    q_lo, q_hi = conformal_quantiles(np.log(y) - oof, level)
    m["interval"] = {"level": level, "q_lo": q_lo, "q_hi": q_hi, "factor_lo": math.exp(q_lo) if np.isfinite(q_lo) else None,
                     "factor_hi": math.exp(q_hi) if np.isfinite(q_hi) else None, "source": src}
    m["valid_length_est_m"] = [float(np.min(x)), float(np.max(x))]
    m["n_pairs"] = int(len(x))
    return m


def calibration_id(build: str, model: dict) -> str:
    h = hashlib.sha1(json.dumps(model, sort_keys=True, default=float).encode()).hexdigest()[:8]
    return f"length_cal_{build}_{h}"


def make_calibration(build: str, model: dict, fallback: dict | None = None, **fields) -> dict:
    cal = {"schema": SCHEMA, "build": build, "calibration_id": calibration_id(build, {"m": model, "f": fallback}),
           "min_claim_m": MIN_CLAIM_M, "level": model.get("interval", {}).get("level", LEVEL), "model": model,
           "fallback": fallback, "reasons": REASONS, "covariate_definitions": {c["name"]: COVARIATES.get(c["name"], "")
                                                                               for c in model.get("covariates") or []}}
    cal.update(fields)
    return cal


def load_calibration(path) -> dict:
    cal = json.loads(Path(path).read_text())
    if cal.get("schema") != SCHEMA:
        raise ValueError(f"{path}: schema {cal.get('schema')!r}, expected {SCHEMA!r}")
    return cal


# ----------------------------------------------------------------------------------------------- apply
def _covariates_ok(model: dict, Z: pd.DataFrame | None, n: int) -> np.ndarray:
    covs = model.get("covariates") or []
    if not covs:
        return np.ones(n, bool)
    if Z is None:
        return np.zeros(n, bool)
    ok = np.ones(n, bool)
    for c in covs:
        z = pd.to_numeric(Z[c["name"]], errors="coerce").to_numpy(float) if c["name"] in Z else np.full(n, np.nan)
        lo, hi = c.get("range", [-np.inf, np.inf])
        ok &= np.isfinite(z) & (z >= lo) & (z <= hi)
    return ok


def apply_frame(df: pd.DataFrame, cal: dict | None) -> pd.DataFrame:
    """OUT_COLUMNS for every row of `df` (needs length_est_m; covariate inputs as in derive_covariates). The model is
    used where its covariates are present and inside their fitted range, the length-only fallback elsewhere. Null
    length with a reason code (REASONS) outside the valid radar-length range or below MIN_CLAIM_M."""
    n = len(df)
    out = pd.DataFrame({"length_cal_m": np.full(n, np.nan), "length_cal_lo_m": np.full(n, np.nan),
                        "length_cal_hi_m": np.full(n, np.nan), "length_cal_reason": pd.Series([None] * n, dtype=object).values,
                        "length_cal_id": pd.Series([None] * n, dtype=object).values}, index=df.index)
    if cal is None:
        out["length_cal_reason"] = pd.Series(["no_calibration"] * n, index=out.index, dtype=object)
        return out
    out["length_cal_id"] = pd.Series([cal["calibration_id"]] * n, index=out.index, dtype=object)
    x = pd.to_numeric(df["length_est_m"], errors="coerce").to_numpy(float) if "length_est_m" in df else np.full(n, np.nan)
    Z = derive_covariates(df).reset_index(drop=True)
    model, fb = cal["model"], cal.get("fallback")
    use_m = _covariates_ok(model, Z, n)
    if fb is None:
        use_m[:] = True
    med, lo, hi = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    vlo, vhi = np.full(n, np.nan), np.full(n, np.nan)
    for sel, m in ((use_m, model), (~use_m, fb)):
        if m is None or not sel.any():
            continue
        p = predict_log(m, x[sel], Z[sel].reset_index(drop=True))
        med[sel] = np.exp(p)
        lo[sel] = np.exp(p + m["interval"]["q_lo"])
        hi[sel] = np.exp(p + m["interval"]["q_hi"])
        vlo[sel], vhi[sel] = m["valid_length_est_m"]
    reason = np.full(n, None, dtype=object)
    with np.errstate(invalid="ignore"):
        reason[~np.isfinite(x) | (x <= 0)] = "missing_length"
        reason[(reason == None) & (x <= PIXEL_FLOOR_M + 0.05)] = "pixel_floor"  # noqa: E711
        reason[(reason == None) & (x < vlo)] = "below_range"  # noqa: E711
        reason[(reason == None) & (x > vhi)] = "above_range"  # noqa: E711
        reason[(reason == None) & ~np.isfinite(med)] = "missing_length"  # noqa: E711
        reason[(reason == None) & (med > x)] = "short_return"  # noqa: E711
        reason[(reason == None) & (med < cal.get("min_claim_m", MIN_CLAIM_M))] = "below_min_claim"  # noqa: E711
    good = reason == None  # noqa: E711
    out["length_cal_m"] = np.where(good, np.round(med, 1), np.nan)
    out["length_cal_lo_m"] = np.where(good, np.round(lo, 1), np.nan)
    out["length_cal_hi_m"] = np.where(good, np.round(hi, 1), np.nan)
    out["length_cal_reason"] = pd.Series(reason, index=out.index, dtype=object)
    return out


def length_cal_m(length_est_m, covariates: dict | None = None, cal: dict | None = None) -> dict:
    """Calibrated length of one contact: {length_cal_m, lo_m, hi_m, level, model ('model' or 'fallback'), reason,
    reason_text, valid_range_m, calibration_id}. `covariates` holds producer columns (scr_vv_db, scr_vh_db,
    n_pixels, inc_angle_deg, cnn_score, pol_class, mission). Null length, lo and hi with a reason outside the range."""
    row = dict(covariates or {})
    row["length_est_m"] = length_est_m
    df = pd.DataFrame([row])
    o = apply_frame(df, cal).iloc[0]
    reason = o.length_cal_reason if isinstance(o.length_cal_reason, str) else None
    out = {"length_cal_m": None, "lo_m": None, "hi_m": None, "level": None, "model": None, "reason": reason,
           "reason_text": REASONS.get(reason) if reason else None, "valid_range_m": None,
           "calibration_id": o.length_cal_id if isinstance(o.length_cal_id, str) else None}
    if cal is None:
        return out
    used = "model" if (cal.get("fallback") is None or _covariates_ok(cal["model"], derive_covariates(df), 1)[0]) else "fallback"
    m = cal[used]
    out.update({"level": m["interval"]["level"], "model": used, "valid_range_m": list(m["valid_length_est_m"])})
    if reason is None:
        out.update({"length_cal_m": float(o.length_cal_m), "lo_m": float(o.length_cal_lo_m), "hi_m": float(o.length_cal_hi_m)})
    return out
