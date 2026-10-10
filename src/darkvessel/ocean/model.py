"""Expected-activity model: how many lit boats or radar vessel candidates a 0.25 degree cell should hold on a night or
radar pass, given the sea and the weather, and where the observations depart from that.

Target and exposure. Each row is a cell-night (VIIRS) or a cell-scene (Sentinel-1) with a count (clear-sky lit vessel
candidates, or radar vessel candidates after the clutter rules) and an exposure (clear searched sea km2 summed over
the night's passes, or imaged sea km2 of the scene). The model is fitted on the rate = count / exposure with
sample_weight = exposure and a Poisson loss. The weighted Poisson deviance of the rates equals the Poisson deviance of
the counts against exposure x rate, so this is the same optimisation as a Poisson count model with a log-exposure
offset (sklearn's Poisson regression example uses the same construction, frequency with exposure as the weight:
https://scikit-learn.org/stable/auto_examples/linear_model/plot_poisson_regression_non_normal_loss.html);
`test_ocean_model.py` checks the identity numerically.

Models.
  global_rate   one rate for the whole sea (the intercept-only model; the reference for D2)
  climatology   rate per cell, shrunk towards the region rate by exposure-weighted empirical Bayes (Marshall 1991,
                doi:10.2307/2347593: a gamma prior per region whose variance is the exposure-weighted variance of the
                cell rates minus the Poisson part)
  static        gradient-boosted trees on position, region and the static sea layers (depth, coast, ports, shipping
                presence share)
  full          the same trees with the daily layers (SST, fronts, chlorophyll, currents), wind, waves and moon
Trees: sklearn HistGradientBoostingRegressor(loss='poisson') (Friedman 2001, doi:10.1214/aos/1013203451), region as a
categorical feature (categories unseen in the fit, such as the held-out region, are treated as missing values).
Forbidden features (check_features): the Marine Regions EEZ attributes and any shipping magnitude; the World Bank/IMF
shipping layers enter only as presence shares (docs/ocean_context.md 3.4, board decision D4.3).

Validation. Leave-one-week-out (nights the model has not seen) and leave-one-region-out (seas it has not seen).
Scores: exposure-weighted mean Poisson deviance and D2 = 1 - deviance(model) / deviance(baseline) against each
baseline's own out-of-fold prediction (compare_oof), pooled and per fold; calibration by exposure-weighted decile of
the predicted rate; permutation importance per feature group on the held-out folds (Breiman 2001,
doi:10.1023/A:1010933404324); partial dependence.

Anomalies. Out-of-fold expected count mu, z = (obs - mu) / sqrt(mu), Poisson tail probabilities, a two-sided p (twice
the smaller tail, capped at 1) and Benjamini-Hochberg control of the false discovery rate at P_FLAG over every tested
cell-night (Benjamini and Hochberg 1995, doi:10.1111/j.2517-6161.1995.tb02031.x). gated_anomalies flags nothing
unless the model beats the climatology baseline out of sample (beats_baseline). The Poisson tail assumes the model's
only error is Poisson noise; overdispersion() measures how far that fails, and nb_two_sided gives the negative
binomial tail with a moment estimate of the dispersion (Cameron and Trivedi 1990, doi:10.1016/0304-4076(90)90014-K) as
a sensitivity check.
An anomaly is a count difference from a model, a lead for review, not vessels and not evidence: `OCEAN_CAVEAT` in
darkvessel.ocean.grid goes on every output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson

from darkvessel.ocean.grid import REPORTING_BOXES, region_of  # noqa: F401  region_of is part of this module's API

# Reporting boxes (west, south, east, north) of scripts/21_viirs_regions.py: plain boxes for reporting, not boundaries
# or claims. 'other' is sea outside every box.
REGIONS = REPORTING_BOXES
REGION_NAMES = list(REGIONS) + ["other"]
EXPOSURE_MIN_KM2 = 25.0
P_FLAG = 0.01
CALM_WIND_MS = 12.0
TIME_OF_DAY = {"morning": 0, "evening": 1}
PASS_DIR = {"ASCENDING": 0, "DESCENDING": 1}
MISSION = {"S1C": 0, "S1D": 1}

# Feature groups by column-name pattern (first match wins). The join scripts name their columns after the layer.
FEATURE_GROUP_RULES = (
    ("position", (r"^lon$", r"^lat$")),
    ("region", (r"^region$",)),
    ("radar_geometry", (r"^inc_angle", r"^time_of_day", r"^pass_dir", r"^mission$")),
    ("weather", (r"^wind", r"^wave", r"^hs(_|$)", r"^swh")),
    ("moon", (r"^moon",)),
    ("sst", (r"^sst", r"^front", r"^dist_front", r"^grad_sst")),
    ("chl", (r"^chl",)),
    ("dynamics", (r"^ssh", r"^sla", r"^current", r"^speed", r"^mld", r"^mixed", r"^sbl", r"^bl_", r"^boundary", r"^u_",
                  r"^v_", r"^eke", r"^vort")),
    ("static", (r"^depth", r"^shallow", r"^share_shallower", r"^share_shelf", r"^shelf", r"^slope", r"^dist_coast",
                r"^coast", r"^dist_port", r"^port", r"^ship_presence_share_")),
)
# Never model features: EEZ attributes (display only) and shipping magnitudes (not counts, docs/ocean_context.md 3.4).
FORBIDDEN_FEATURE_RULES = (r"marineregions", r"^eez", r"density", r"lane", r"^ship_(?!presence_share_)")
DAILY_GROUPS = ("weather", "moon", "sst", "chl", "dynamics")
HGB_PARAMS = dict(learning_rate=0.05, max_iter=300, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=1.0,
                  early_stopping=False, random_state=0)


def feature_group(name: str) -> str:
    """Feature group of a column name ('other' when no rule matches; 'forbidden' for check_features' patterns)."""
    if any(re.search(p, name) for p in FORBIDDEN_FEATURE_RULES):
        return "forbidden"
    for group, patterns in FEATURE_GROUP_RULES:
        if any(re.search(p, name) for p in patterns):
            return group
    return "other"


def check_features(features) -> list[str]:
    """The features unchanged, or ValueError if one is an EEZ attribute or a shipping magnitude."""
    bad = [f for f in features if feature_group(f) == "forbidden"]
    if bad:
        raise ValueError(f"not allowed as model features (EEZ attributes or shipping magnitudes): {bad}")
    return list(features)


def group_features(columns) -> dict[str, list[str]]:
    """{group: [columns]} for the columns that belong to a known group (forbidden and unknown columns left out)."""
    out: dict[str, list[str]] = {}
    for c in columns:
        g = feature_group(c)
        if g not in ("other", "forbidden"):
            out.setdefault(g, []).append(c)
    return out


def week_folds(nights, days: int = 7) -> np.ndarray:
    """Fold label per row: blocks of `days` consecutive dates from the first date ('w1', 'w2', ...)."""
    d = pd.to_datetime(pd.Series(np.asarray(nights)))
    k = ((d - d.min()).dt.days // days).astype(int)
    return np.array([f"w{i + 1}" for i in k])


def region_folds(region) -> np.ndarray:
    return np.asarray(region, dtype=str)


# ----------------------------------------------------------------------------------------------------------------------
# Deviance and scores


def poisson_deviance(count, expected) -> np.ndarray:
    """Unit Poisson deviance per row: 2 (c log(c / mu) - (c - mu)), with c log c = 0 at c = 0."""
    c = np.asarray(count, float)
    mu = np.maximum(np.asarray(expected, float), 1e-12)
    with np.errstate(divide="ignore", invalid="ignore"):
        term = np.where(c > 0, c * np.log(c / mu), 0.0)
    return 2.0 * (term - (c - mu))


def rate_deviance(rate, mu_rate, weight) -> np.ndarray:
    """Weighted unit deviance of rates, weight x dev(rate, mu_rate): equals poisson_deviance(count, weight x mu_rate)."""
    return np.asarray(weight, float) * poisson_deviance(rate, mu_rate)


def mean_deviance(count, expected, exposure) -> float:
    """Exposure-weighted mean Poisson deviance: total deviance per 1,000 km2 of exposure."""
    e = float(np.sum(exposure))
    return float(np.sum(poisson_deviance(count, expected)) / e * 1000.0) if e > 0 else float("nan")


def d2_score(count, expected, baseline_expected) -> float:
    """1 - deviance(model) / deviance(baseline); 0 = no better than the baseline, 1 = perfect, below 0 = worse."""
    den = float(np.sum(poisson_deviance(count, baseline_expected)))
    return float(1.0 - np.sum(poisson_deviance(count, expected)) / den) if den > 0 else float("nan")


# ----------------------------------------------------------------------------------------------------------------------
# Models: fit(df) / predict_rate(df); df carries count_col, exposure_col and the feature columns


class GlobalRate:
    """One rate for everything: total count over total exposure of the training rows."""

    def __init__(self, count_col: str = "count", exposure_col: str = "exposure_km2"):
        self.count_col, self.exposure_col, self.rate_ = count_col, exposure_col, float("nan")

    def fit(self, df: pd.DataFrame):
        self.rate_ = float(df[self.count_col].sum() / max(df[self.exposure_col].sum(), 1e-12))
        return self

    def predict_rate(self, df: pd.DataFrame) -> np.ndarray:
        return np.full(len(df), self.rate_)


def eb_shrink(count, exposure, groups) -> tuple[np.ndarray, pd.DataFrame]:
    """Empirical Bayes rate per unit, shrunk towards its group rate (Marshall 1991, exposure-weighted moments).

    Per group: m = sum c / sum E; A = max(sum E (c/E - m)^2 / sum E - m / mean(E), 0), the between-unit variance of the
    true rates; posterior mean per unit = m + A / (A + m / E) (c / E - m). A = 0 shrinks every unit to m.
    Returns (rate per unit, table per group with m, A and the mean shrinkage weight).
    """
    c, e, g = np.asarray(count, float), np.asarray(exposure, float), np.asarray(groups)
    out = np.zeros(len(c))
    rows = []
    for name in pd.unique(g):
        i = g == name
        ei, ci = e[i], c[i]
        pos = ei > 0
        if not pos.any() or ei[pos].sum() <= 0:
            out[i] = np.nan
            rows.append({"group": name, "units": int(i.sum()), "rate": np.nan, "prior_var": np.nan, "mean_weight": np.nan})
            continue
        m = ci[pos].sum() / ei[pos].sum()
        r = ci[pos] / ei[pos]
        a = max(float(np.sum(ei[pos] * (r - m) ** 2) / ei[pos].sum() - m / ei[pos].mean()), 0.0)
        w = a / (a + m / ei[pos]) if (a > 0 and m > 0) else np.zeros(pos.sum())
        est = np.full(i.sum(), m)
        est[pos] = m + w * (r - m)
        out[i] = est
        rows.append({"group": name, "units": int(i.sum()), "rate": m, "prior_var": a, "mean_weight": float(np.mean(w))})
    return out, pd.DataFrame(rows)


class Climatology:
    """Rate per cell from the training rows, shrunk to the region rate by empirical Bayes; region rate for cells
    without training exposure, the global rate for regions without any."""

    def __init__(self, count_col="count", exposure_col="exposure_km2", cell_cols=("row", "col"), region_col="region"):
        self.count_col, self.exposure_col, self.cell_cols, self.region_col = count_col, exposure_col, list(cell_cols), region_col

    def fit(self, df: pd.DataFrame):
        agg = df.groupby(self.cell_cols + [self.region_col], as_index=False)[[self.count_col, self.exposure_col]].sum()
        rate, self.prior_ = eb_shrink(agg[self.count_col], agg[self.exposure_col], agg[self.region_col])
        self.cell_rate_ = pd.Series(rate, index=pd.MultiIndex.from_frame(agg[self.cell_cols]))
        reg = df.groupby(self.region_col)[[self.count_col, self.exposure_col]].sum()
        self.region_rate_ = (reg[self.count_col] / reg[self.exposure_col].clip(lower=1e-12)).to_dict()
        self.global_rate_ = float(df[self.count_col].sum() / max(df[self.exposure_col].sum(), 1e-12))
        return self

    def predict_rate(self, df: pd.DataFrame) -> np.ndarray:
        idx = pd.MultiIndex.from_frame(df[self.cell_cols])
        r = self.cell_rate_.reindex(idx).to_numpy(dtype=float)
        reg = df[self.region_col].map(self.region_rate_).to_numpy(dtype=float)
        return np.where(np.isfinite(r), r, np.where(np.isfinite(reg), reg, self.global_rate_))


def encode_features(df: pd.DataFrame, features: list[str]) -> tuple[np.ndarray, list[bool]]:
    """Feature matrix for the trees: region -> code in REGION_NAMES order, time_of_day, pass_dir and mission -> codes,
    rest float. Returns (X, categorical mask). Unknown labels become NaN (missing)."""
    cols, cat = [], []
    codes = {"region": {n: i for i, n in enumerate(REGION_NAMES)}, "time_of_day": TIME_OF_DAY, "pass_dir": PASS_DIR,
             "mission": MISSION}
    for f in features:
        v = df[f]
        if f in codes:
            is_text = v.dtype == object or pd.api.types.is_string_dtype(v) or isinstance(v.dtype, pd.CategoricalDtype)
            cols.append(v.astype(object).map(codes[f]).to_numpy(dtype=float) if is_text else v.to_numpy(dtype=float))
            cat.append(True)
        else:
            cols.append(v.to_numpy(dtype=float))
            cat.append(False)
    X = np.column_stack(cols) if cols else np.zeros((len(df), 0))
    return X, cat


class RateTrees:
    """HistGradientBoostingRegressor(loss='poisson') on rate = count / exposure, sample_weight = exposure."""

    def __init__(self, features: list[str], count_col="count", exposure_col="exposure_km2", params: dict | None = None):
        self.features, self.count_col, self.exposure_col = check_features(features), count_col, exposure_col
        self.params = {**HGB_PARAMS, **(params or {})}

    def fit(self, df: pd.DataFrame):
        from sklearn.ensemble import HistGradientBoostingRegressor

        X, cat = encode_features(df, self.features)
        e = df[self.exposure_col].to_numpy(dtype=float)
        y = df[self.count_col].to_numpy(dtype=float) / np.maximum(e, 1e-12)
        self.model_ = HistGradientBoostingRegressor(loss="poisson", categorical_features=cat if any(cat) else None, **self.params)
        self.model_.fit(X, y, sample_weight=e)
        return self

    def predict_rate(self, df: pd.DataFrame) -> np.ndarray:
        X, _ = encode_features(df, self.features)
        return self.model_.predict(X)


def predict_count(model, df: pd.DataFrame, exposure_col: str = "exposure_km2") -> np.ndarray:
    return model.predict_rate(df) * df[exposure_col].to_numpy(dtype=float)


# ----------------------------------------------------------------------------------------------------------------------
# Cross-validation, model comparison, calibration, importance


@dataclass
class CVResult:
    oof_rate: np.ndarray
    folds: pd.DataFrame
    d2: float
    mean_deviance: float
    d2_by_region: dict = field(default_factory=dict)


def cross_validate(df: pd.DataFrame, make_model, fold_labels, count_col="count", exposure_col="exposure_km2",
                   region_col="region", min_train: int = 50) -> CVResult:
    """Out-of-fold rates for every row; per fold and pooled: exposure-weighted mean deviance and D2 against the global
    rate of the training fold. Rows in folds with too few training rows stay NaN."""
    labels = np.asarray(fold_labels)
    count = df[count_col].to_numpy(dtype=float)
    expo = df[exposure_col].to_numpy(dtype=float)
    oof = np.full(len(df), np.nan)
    base = np.full(len(df), np.nan)
    rows = []
    for k in pd.unique(labels):
        test, train = labels == k, labels != k
        if train.sum() < min_train or test.sum() == 0:
            continue
        model = make_model().fit(df[train])
        oof[test] = model.predict_rate(df[test])
        base[test] = GlobalRate(count_col, exposure_col).fit(df[train]).rate_
        mu, mu0 = oof[test] * expo[test], base[test] * expo[test]
        rows.append({"fold": str(k), "rows": int(test.sum()), "count": float(count[test].sum()),
                     "exposure_km2": float(expo[test].sum()), "mean_deviance": mean_deviance(count[test], mu, expo[test]),
                     "mean_deviance_global": mean_deviance(count[test], mu0, expo[test]), "d2": d2_score(count[test], mu, mu0)})
    ok = np.isfinite(oof)
    res = CVResult(oof, pd.DataFrame(rows), float("nan"), float("nan"))
    if ok.any():
        res.d2 = d2_score(count[ok], oof[ok] * expo[ok], base[ok] * expo[ok])
        res.mean_deviance = mean_deviance(count[ok], oof[ok] * expo[ok], expo[ok])
        if region_col in df:
            reg = df[region_col].to_numpy()
            for r in pd.unique(reg):
                i = ok & (reg == r)
                if i.sum() > 0 and count[i].sum() > 0:
                    res.d2_by_region[str(r)] = d2_score(count[i], oof[i] * expo[i], base[i] * expo[i])
    return res


def spread(values) -> dict:
    """mean, sd, min, max and n of finite values (fold spread)."""
    v = np.asarray([x for x in values if x is not None and np.isfinite(x)], float)
    if not len(v):
        return {"n": 0, "mean": None, "sd": None, "min": None, "max": None}
    return {"n": int(len(v)), "mean": float(v.mean()), "sd": float(v.std(ddof=1)) if len(v) > 1 else 0.0,
            "min": float(v.min()), "max": float(v.max())}


def compare_oof(count, exposure, oof_rates: dict[str, np.ndarray], fold_labels,
                baselines=("global_rate", "climatology")) -> tuple[pd.DataFrame, pd.DataFrame]:
    """D2 of every model against every baseline from their out-of-fold rates, on the rows all of them predict.

    Returns (pooled table: model, baseline, d2, rows, mean_deviance of the model, folds_better (folds with d2 > 0),
    folds, fold spread of d2; per-fold table: fold, model, baseline, d2, mean deviances, rows, count).
    """
    c, e = np.asarray(count, float), np.asarray(exposure, float)
    labels = np.asarray(fold_labels).astype(str)
    ok = e > 0
    for r in oof_rates.values():
        ok &= np.isfinite(r)
    per, pooled = [], []
    for name, r in oof_rates.items():
        for b in baselines:
            if b not in oof_rates or b == name:
                continue
            rb = oof_rates[b]
            fold_d2 = []
            for k in pd.unique(labels[ok]):
                i = ok & (labels == k)
                d2 = d2_score(c[i], r[i] * e[i], rb[i] * e[i])
                fold_d2.append(d2)
                per.append({"fold": str(k), "model": name, "baseline": b, "d2": d2, "rows": int(i.sum()), "count": float(c[i].sum()),
                            "mean_deviance": mean_deviance(c[i], r[i] * e[i], e[i]),
                            "mean_deviance_baseline": mean_deviance(c[i], rb[i] * e[i], e[i])})
            sp = spread(fold_d2)
            pooled.append({"model": name, "baseline": b, "d2": d2_score(c[ok], r[ok] * e[ok], rb[ok] * e[ok]), "rows": int(ok.sum()),
                           "mean_deviance": mean_deviance(c[ok], r[ok] * e[ok], e[ok]),
                           "mean_deviance_baseline": mean_deviance(c[ok], rb[ok] * e[ok], e[ok]),
                           "folds": len(fold_d2), "folds_better": int(np.sum(np.asarray(fold_d2) > 0)),
                           **{f"fold_d2_{k}": v for k, v in sp.items() if k != "n"}})
    return pd.DataFrame(pooled), pd.DataFrame(per)


def beats_baseline(d2_pooled: float, fold_d2, min_fold_share: float = 0.5) -> bool:
    """The model beats a baseline out of sample: pooled D2 against it above 0 and above 0 in more than
    `min_fold_share` of the folds."""
    f = np.asarray([x for x in fold_d2 if x is not None and np.isfinite(x)], float)
    return bool(np.isfinite(d2_pooled) and d2_pooled > 0 and len(f) and np.mean(f > 0) > min_fold_share)


def calibration_deciles(count, exposure, expected_rate, n: int = 10) -> pd.DataFrame:
    """Observed against predicted rate per exposure-weighted decile of the predicted rate (per 1,000 km2)."""
    c, e, r = (np.asarray(a, float) for a in (count, exposure, expected_rate))
    ok = np.isfinite(r) & (e > 0)
    c, e, r = c[ok], e[ok], r[ok]
    order = np.argsort(r, kind="stable")
    cum = np.cumsum(e[order]) / e.sum()
    dec = np.minimum((cum * n).astype(int), n - 1)
    rows = []
    for d in range(n):
        i = order[dec == d]
        if len(i) == 0:
            continue
        rows.append({"decile": d + 1, "rows": int(len(i)), "exposure_km2": float(e[i].sum()),
                     "predicted_per_1000km2": float(np.sum(r[i] * e[i]) / e[i].sum() * 1000),
                     "observed_per_1000km2": float(c[i].sum() / e[i].sum() * 1000), "count": float(c[i].sum())})
    return pd.DataFrame(rows)


def permutation_importance_groups(df: pd.DataFrame, make_model, fold_labels, groups: dict[str, list[str]],
                                  count_col="count", exposure_col="exposure_km2", n_repeats: int = 3, seed: int = 0,
                                  min_train: int = 50) -> pd.DataFrame:
    """Rise in the held-out mean deviance when a feature group is shuffled within the test fold (mean over folds and
    repeats, per 1,000 km2 of exposure, and as a share of the unshuffled deviance), with the fold spread."""
    rng = np.random.default_rng(seed)
    labels = np.asarray(fold_labels)
    count = df[count_col].to_numpy(dtype=float)
    expo = df[exposure_col].to_numpy(dtype=float)
    rows = []
    for k in pd.unique(labels):
        test, train = labels == k, labels != k
        if train.sum() < min_train or test.sum() == 0:
            continue
        model = make_model().fit(df[train])
        dte = df[test].reset_index(drop=True)
        base = mean_deviance(count[test], model.predict_rate(dte) * expo[test], expo[test])
        for g, cols in groups.items():
            cols = [c for c in cols if c in dte]
            if not cols:
                continue
            vals = []
            for _ in range(n_repeats):
                perm = dte.copy()
                idx = rng.permutation(len(perm))
                for c in cols:
                    perm[c] = perm[c].to_numpy()[idx]
                vals.append(mean_deviance(count[test], model.predict_rate(perm) * expo[test], expo[test]) - base)
            rows.append({"fold": str(k), "group": g, "deviance_rise": float(np.mean(vals)), "base_deviance": base})
    if not rows:
        return pd.DataFrame(columns=["group", "deviance_rise", "share_of_deviance", "folds", "fold_min", "fold_max"])
    t = pd.DataFrame(rows)
    t["share"] = t.deviance_rise / t.base_deviance
    out = t.groupby("group").agg(deviance_rise=("deviance_rise", "mean"), base=("base_deviance", "mean"), folds=("fold", "nunique"),
                                 fold_min=("share", "min"), fold_max=("share", "max")).reset_index()
    out["share_of_deviance"] = out.deviance_rise / out.base
    return out.drop(columns="base").sort_values("deviance_rise", ascending=False).reset_index(drop=True)


# ----------------------------------------------------------------------------------------------------------------------
# Anomalies


def bh_adjust(p) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (q) over the finite entries of `p`; NaN stays NaN (not in the family)."""
    p = np.asarray(p, float)
    q = np.full(p.shape, np.nan)
    ok = np.isfinite(p)
    m = int(ok.sum())
    if m == 0:
        return q
    pv = p[ok]
    order = np.argsort(pv, kind="stable")
    ranked = pv[order] * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(adj, 1.0)
    q[ok] = out
    return q


def anomaly_table(count, expected, p_flag: float = P_FLAG, eligible=None, control: str = "bh") -> pd.DataFrame:
    """z = (obs - mu) / sqrt(mu), Poisson upper tail P(X >= obs), lower tail P(X <= obs), two-sided p = min(1, 2 x the
    smaller tail), and a flag high/low/none.

    control='bh' (default): Benjamini-Hochberg q over the family of rows with a finite positive mu and `eligible`
    True (for example calm weather); flag where q < p_flag. control='none': each one-sided tail against p_flag
    without multiple-testing control (shown for comparison only). Rows outside the family get q NaN and flag none.
    """
    c = np.asarray(count, float)
    mu = np.asarray(expected, float)
    ok = np.isfinite(mu) & (mu > 0) & np.isfinite(c)
    fam = ok & (np.ones(len(c), bool) if eligible is None else np.asarray(eligible, bool))
    z = np.full(len(c), np.nan)
    p_hi = np.full(len(c), np.nan)
    p_lo = np.full(len(c), np.nan)
    z[ok] = (c[ok] - mu[ok]) / np.sqrt(mu[ok])
    p_hi[ok] = poisson.sf(c[ok] - 1, mu[ok])
    p_lo[ok] = poisson.cdf(c[ok], mu[ok])
    p2 = np.minimum(1.0, 2.0 * np.fmin(p_hi, p_lo))
    q = bh_adjust(np.where(fam, p2, np.nan))
    flag = np.full(len(c), "none", dtype=object)
    if control == "bh":
        hit = fam & (q < p_flag)
        flag[hit & (c > mu)] = "high"
        flag[hit & (c < mu)] = "low"
    elif control == "none":
        flag[fam & (p_hi < p_flag) & (c > mu)] = "high"
        flag[fam & (p_lo < p_flag) & (c < mu)] = "low"
    else:
        raise ValueError(f"control must be 'bh' or 'none', not {control!r}")
    return pd.DataFrame({"z": z, "p_high": p_hi, "p_low": p_lo, "p_two_sided": p2, "q_bh": q, "tested": fam,
                         "flag": flag.astype(str)})


def overdispersion(count, expected) -> dict:
    """Pearson dispersion sum((c - mu)^2 / mu) / n (1 for Poisson) and the NB2 moment estimate of alpha in
    Var = mu + alpha mu^2: sum((c - mu)^2 - c) / sum(mu^2) (Cameron and Trivedi 1990), floored at 0."""
    c, mu = np.asarray(count, float), np.asarray(expected, float)
    ok = np.isfinite(c) & np.isfinite(mu) & (mu > 0)
    c, mu = c[ok], mu[ok]
    if not len(c):
        return {"rows": 0, "pearson_dispersion": None, "nb_alpha": None}
    return {"rows": int(len(c)), "pearson_dispersion": float(np.sum((c - mu) ** 2 / mu) / len(c)),
            "nb_alpha": float(max(np.sum((c - mu) ** 2 - c) / np.sum(mu ** 2), 0.0))}


def nb_two_sided(count, expected, alpha: float) -> np.ndarray:
    """Two-sided tail of a negative binomial with mean mu and Var = mu + alpha mu^2 (Poisson when alpha is 0)."""
    c, mu = np.asarray(count, float), np.asarray(expected, float)
    out = np.full(len(c), np.nan)
    ok = np.isfinite(c) & np.isfinite(mu) & (mu > 0)
    if alpha <= 0:
        hi, lo = poisson.sf(c[ok] - 1, mu[ok]), poisson.cdf(c[ok], mu[ok])
    else:
        n = 1.0 / alpha
        p = n / (n + mu[ok])
        hi, lo = nbinom.sf(c[ok] - 1, n, p), nbinom.cdf(c[ok], n, p)
    out[ok] = np.minimum(1.0, 2.0 * np.minimum(hi, lo))
    return out


def gated_anomalies(count, expected, gate: bool, eligible=None, p_flag: float = P_FLAG, reason: str = "") -> tuple[pd.DataFrame, dict]:
    """anomaly_table with BH control, or no flags at all when `gate` is False (the model did not beat the climatology
    baseline out of sample). The p-values are kept either way so the table can be inspected; only the flags depend on
    the gate. Returns (table, summary with the gate, the family size and the flag counts)."""
    t = anomaly_table(count, expected, p_flag=p_flag, eligible=eligible, control="bh")
    raw = anomaly_table(count, expected, p_flag=p_flag, eligible=eligible, control="none").flag
    s = {"gate_passed": bool(gate), "reason": reason, "p_flag": p_flag, "control": "Benjamini-Hochberg FDR over the tested rows",
         "tested": int(t.tested.sum()), "would_flag_high": int((t.flag == "high").sum()), "would_flag_low": int((t.flag == "low").sum()),
         "raw_tail_high_without_control": int((raw == "high").sum()), "raw_tail_low_without_control": int((raw == "low").sum())}
    if not gate:
        t["flag"] = "none"
    s["flagged_high"] = int((t.flag == "high").sum())
    s["flagged_low"] = int((t.flag == "low").sum())
    return t, s


def partial_dependence(model, df: pd.DataFrame, feature: str, grid=None, n_grid: int = 12,
                       quantiles=(0.05, 0.95)) -> pd.DataFrame:
    """Mean predicted rate (per 1,000 km2) over the rows of `df` with `feature` set to each grid value (Friedman's
    partial dependence). The grid defaults to n_grid points between the feature's quantiles."""
    v = df[feature].to_numpy(dtype=float)
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return pd.DataFrame(columns=["value", "rate_per_1000km2"])
    if grid is None:
        lo, hi = np.quantile(v, quantiles)
        grid = np.linspace(lo, hi, n_grid) if hi > lo else np.array([lo])
    rows = []
    work = df.copy()
    for g in grid:
        work[feature] = g
        rows.append({"value": float(g), "rate_per_1000km2": float(np.mean(model.predict_rate(work)) * 1000)})
    return pd.DataFrame(rows)


def simulate_cells(n_cells: int = 300, n_nights: int = 20, seed: int = 0, noise_features: int = 2) -> pd.DataFrame:
    """Synthetic cell-night table with a known rate: log rate = -6 + 0.8 depth_std - 0.25 wind + 0.004 moon + region
    effect, exposures from 5 to 600 km2 (used by the tests, kept here so the script can self-check too)."""
    rng = np.random.default_rng(seed)
    lon = rng.uniform(100, 120, n_cells)
    lat = rng.uniform(0, 23, n_cells)
    depth = rng.uniform(-3000, -10, n_cells)
    region = region_of(lon, lat)
    reg_eff = {n: v for n, v in zip(REGION_NAMES, [0.9, 0.3, 0.6, 0.5, -0.4, -0.3, 0.0])}
    rows = []
    for k in range(n_nights):
        wind = rng.gamma(3.0, 1.5, n_cells)
        moon = rng.uniform(0, 100)
        expo = rng.uniform(5, 600, n_cells) * (rng.uniform(0, 1, n_cells) > 0.15)
        d = pd.DataFrame({"night": pd.Timestamp("2026-09-05") + pd.Timedelta(days=k), "row": np.arange(n_cells),
                          "col": np.zeros(n_cells, int), "lon": lon, "lat": lat, "region": region,
                          "depth_mean_m": depth, "wind_ms": wind, "moon_illum_pct": moon, "exposure_km2": expo})
        for j in range(noise_features):
            d[f"noise_{j}"] = rng.normal(size=n_cells)
        log_rate = (-6.0 + 0.8 * (depth + 1500) / 1000 - 0.25 * wind + 0.004 * moon
                    + np.array([reg_eff[r] for r in region]))
        d["true_rate"] = np.exp(log_rate)
        d["count"] = rng.poisson(d.true_rate * expo)
        rows.append(d)
    return pd.concat(rows, ignore_index=True)
