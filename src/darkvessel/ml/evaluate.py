"""Detection metrics for CFAR-only and CFAR+CNN against AI2 point labels.

Conventions (object-detection style):
  precision  over candidates: accepted candidates within 50 m of a label / all accepted
             candidates, with 'ambiguous' candidates (50-150 m or inside 0.75 x a known
             length) excluded from both numerator and denominator and reported separately
  recall     over labels on testable sea: labels with at least one accepted candidate within
             50 m / all such labels. CFAR misses (no candidate within 50 m) count as misses
             of the whole system at every threshold.
Wilson 95 % intervals for every rate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from darkvessel.ml.labels import length_bin

LENGTH_BINS = (0, 15, 25, 50, 100, np.inf)


def wilson(k, n, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials."""
    k, n = float(k), float(n)
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def label_detected(labels: pd.DataFrame, cands: pd.DataFrame, accept: np.ndarray) -> pd.Series:
    """Per label: True if some accepted candidate has it as its matched label within 50 m."""
    hit = cands.loc[accept & (cands.cand_class == "vessel"), "match_label_id"].unique()
    return labels.label_id.isin(hit)


def system_metrics(labels: pd.DataFrame, cands: pd.DataFrame, accept: np.ndarray) -> dict:
    """Precision (candidates), recall (labels), F1 for one acceptance mask over `cands`."""
    cls = cands.cand_class.values
    acc = np.asarray(accept, bool)
    tp_c = int((acc & (cls == "vessel")).sum())
    fp_c = int((acc & (cls == "clutter")).sum())
    amb_c = int((acc & (cls == "ambiguous")).sum())
    det = label_detected(labels, cands, acc)
    n_lab = int(len(labels))
    tp_l = int(det.sum())
    prec = tp_c / (tp_c + fp_c) if tp_c + fp_c else float("nan")
    rec = tp_l / n_lab if n_lab else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) and np.isfinite(prec + rec) else float("nan")
    return {"accepted": int(acc.sum()), "tp_candidates": tp_c, "fp_candidates": fp_c, "ambiguous_accepted": amb_c,
            "labels": n_lab, "labels_detected": tp_l, "labels_missed": n_lab - tp_l,
            "precision": prec, "precision_ci": wilson(tp_c, tp_c + fp_c), "recall": rec, "recall_ci": wilson(tp_l, n_lab),
            "f1": f1}


def pr_curve(labels: pd.DataFrame, cands: pd.DataFrame, scores: np.ndarray, n_points: int = 200) -> pd.DataFrame:
    """System precision/recall over score thresholds (CFAR misses are fixed misses)."""
    cls = cands.cand_class.values
    s = np.asarray(scores, float)
    qs = np.unique(np.quantile(s, np.linspace(0, 1, n_points)))
    thresholds = np.concatenate([[-np.inf], qs])
    # label -> best score among its vessel-class candidates
    v = cands[cls == "vessel"]
    best = pd.Series(s[cls == "vessel"]).groupby(v.match_label_id.values).max()
    lab_best = labels.label_id.map(best).fillna(-np.inf).values
    rows = []
    for t in thresholds:
        acc = s >= t
        tp_c = int((acc & (cls == "vessel")).sum())
        fp_c = int((acc & (cls == "clutter")).sum())
        tp_l = int((lab_best >= t).sum())
        prec = tp_c / (tp_c + fp_c) if tp_c + fp_c else np.nan
        rec = tp_l / len(labels) if len(labels) else np.nan
        rows.append({"threshold": t, "precision": prec, "recall": rec,
                     "f1": 2 * prec * rec / (prec + rec) if prec + rec else np.nan, "accepted": int(acc.sum())})
    return pd.DataFrame(rows)


def recall_by_length(labels: pd.DataFrame, cands: pd.DataFrame, accept: np.ndarray, bins=LENGTH_BINS) -> pd.DataFrame:
    """CFAR and CFAR+CNN recall by AIS length bin for labels with a consistent attribute length.

    `labels` must hold cfar_detected (50 m rule) and length_m; rows without length are dropped.
    """
    lab = labels[labels.length_m.notna()].copy()
    lab["bin"] = length_bin(lab.length_m.values, bins)
    lab["cnn_detected"] = label_detected(lab, cands, accept).values
    rows = []
    groups = list(lab.groupby("bin", observed=False)) + [("all", lab)]
    for name, d in groups:
        n = len(d)
        k_cfar, k_cnn = int(d.cfar_detected.sum()), int(d.cnn_detected.sum())
        k_loose = int(d.cfar_detected_loose.sum()) if "cfar_detected_loose" in d else np.nan
        lo1, hi1 = wilson(k_cfar, n)
        lo2, hi2 = wilson(k_cnn, n)
        rows.append({"length_bin": str(name), "n_labels": n, "cfar_detected": k_cfar,
                     "cfar_recall": k_cfar / n if n else np.nan, "cfar_ci_lo": lo1, "cfar_ci_hi": hi1,
                     "cnn_detected": k_cnn, "cnn_recall": k_cnn / n if n else np.nan, "cnn_ci_lo": lo2, "cnn_ci_hi": hi2,
                     "cfar_detected_loose": k_loose, "cfar_recall_loose": k_loose / n if n else np.nan})
    return pd.DataFrame(rows)


def by_group(labels: pd.DataFrame, cands: pd.DataFrame, accept: np.ndarray, key: str) -> pd.DataFrame:
    """system_metrics per value of `key` (a column present in both frames, e.g. region)."""
    rows = []
    for g in sorted(set(labels[key].dropna()) | set(cands[key].dropna())):
        m = system_metrics(labels[labels[key] == g], cands[cands[key] == g], accept[(cands[key] == g).values])
        rows.append({key: g, **m})
    return pd.DataFrame(rows)
