"""Detection metrics for CFAR-only and CFAR+CNN against AI2 point labels.

Conventions (object-detection style):
  precision  over candidates: accepted candidates matched to a label / all accepted candidates
  recall     over labels on testable sea: labels with at least one accepted candidate matched
             to them / all such labels. CFAR misses count as misses at every threshold.
Two matching rules are reported side by side:
  strict     a candidate counts as the vessel only within 50 m of the label ('vessel' class);
             'ambiguous' candidates (50-150 m, or inside 0.75 x a known length) are excluded
             from both precision counts
  loose      'vessel' and 'ambiguous' candidates both count as the vessel. Candidate-to-label
             distances grow with ship length (clicks on the bridge or bow, fragmented returns),
             so the strict rule understates recall for ships over 100 m.
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


def _matched_classes(loose: bool) -> tuple[str, ...]:
    return ("vessel", "ambiguous") if loose else ("vessel",)


def label_detected(labels: pd.DataFrame, cands: pd.DataFrame, accept: np.ndarray, loose: bool = False) -> pd.Series:
    """Per label: True if some accepted candidate of a matching class points to it."""
    acc = np.asarray(accept, bool)
    sel = acc & cands.cand_class.isin(_matched_classes(loose)).values
    hit = cands.loc[sel, "match_label_id"].unique()
    return labels.label_id.isin(hit)


def _rates(tp_c, fp_c, tp_l, n_lab) -> dict:
    prec = tp_c / (tp_c + fp_c) if tp_c + fp_c else float("nan")
    rec = tp_l / n_lab if n_lab else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if np.isfinite(prec) and np.isfinite(rec) and (prec + rec) else float("nan")
    return {"precision": prec, "precision_ci": wilson(tp_c, tp_c + fp_c), "recall": rec, "recall_ci": wilson(tp_l, n_lab), "f1": f1}


def system_metrics(labels: pd.DataFrame, cands: pd.DataFrame, accept: np.ndarray) -> dict:
    """Precision (candidates), recall (labels), F1 under the strict and the loose rule."""
    cls = cands.cand_class.values
    acc = np.asarray(accept, bool)
    tp_c = int((acc & (cls == "vessel")).sum())
    fp_c = int((acc & (cls == "clutter")).sum())
    amb_c = int((acc & (cls == "ambiguous")).sum())
    n_lab = int(len(labels))
    tp_l = int(label_detected(labels, cands, acc).sum())
    tp_l_loose = int(label_detected(labels, cands, acc, loose=True).sum())
    out = {"accepted": int(acc.sum()), "tp_candidates": tp_c, "fp_candidates": fp_c, "ambiguous_accepted": amb_c,
           "labels": n_lab, "labels_detected": tp_l, "labels_missed": n_lab - tp_l, "labels_detected_loose": tp_l_loose}
    out.update(_rates(tp_c, fp_c, tp_l, n_lab))
    out.update({k + "_loose": v for k, v in _rates(tp_c + amb_c, fp_c, tp_l_loose, n_lab).items()})
    return out


def pr_curve(labels: pd.DataFrame, cands: pd.DataFrame, scores: np.ndarray, n_points: int = 200) -> pd.DataFrame:
    """System precision/recall over score thresholds, strict and loose (CFAR misses are fixed misses)."""
    cls = cands.cand_class.values
    s = np.asarray(scores, float)
    # the lowest threshold is the minimum score, which accepts every candidate (= CFAR only)
    thresholds = np.unique(np.quantile(s, np.linspace(0, 1, n_points))) if len(s) else np.array([0.0])
    # label -> best score among its matched candidates; undetected labels get -inf
    best = {}
    for loose in (False, True):
        sel = np.isin(cls, _matched_classes(loose))
        b = pd.Series(s[sel]).groupby(cands.match_label_id.values[sel]).max()
        best[loose] = labels.label_id.map(b).fillna(-np.inf).values
    n_lab = len(labels)
    rows = []
    for t in thresholds:
        acc = s >= t
        tp_c = int((acc & (cls == "vessel")).sum())
        fp_c = int((acc & (cls == "clutter")).sum())
        amb_c = int((acc & (cls == "ambiguous")).sum())
        r = {"threshold": t, "accepted": int(acc.sum())}
        for loose, tp in ((False, tp_c), (True, tp_c + amb_c)):
            prec = tp / (tp + fp_c) if tp + fp_c else np.nan
            rec = (best[loose] >= t).sum() / n_lab if n_lab else np.nan
            suf = "_loose" if loose else ""
            r["precision" + suf], r["recall" + suf] = prec, rec
            r["f1" + suf] = 2 * prec * rec / (prec + rec) if prec + rec else np.nan
        rows.append(r)
    return pd.DataFrame(rows)


def recall_by_length(labels: pd.DataFrame, cands: pd.DataFrame, accept: np.ndarray, bins=LENGTH_BINS) -> pd.DataFrame:
    """CFAR and CFAR+CNN recall by AIS length bin, strict and loose, for labels with a length.

    `labels` must hold cfar_detected, cfar_detected_loose and length_m; rows without length are dropped.
    """
    lab = labels[labels.length_m.notna()].copy()
    # positional assignment: length_bin returns a fresh RangeIndex that must not be aligned
    lab["bin"] = length_bin(lab.length_m.values, bins).values
    lab["cnn_detected"] = label_detected(lab, cands, accept).values
    lab["cnn_detected_loose"] = label_detected(lab, cands, accept, loose=True).values
    rows = []
    groups = list(lab.groupby("bin", observed=False)) + [("all", lab)]
    for name, d in groups:
        n = len(d)
        r = {"length_bin": str(name), "n_labels": n}
        for col, pre in (("cfar_detected", "cfar"), ("cnn_detected", "cnn"),
                         ("cfar_detected_loose", "cfar_loose"), ("cnn_detected_loose", "cnn_loose")):
            k = int(d[col].sum())
            lo, hi = wilson(k, n)
            r[f"{pre}_detected"], r[f"{pre}_recall"], r[f"{pre}_ci_lo"], r[f"{pre}_ci_hi"] = k, (k / n if n else np.nan), lo, hi
        rows.append(r)
    return pd.DataFrame(rows)


def by_group(labels: pd.DataFrame, cands: pd.DataFrame, accept: np.ndarray, key: str) -> pd.DataFrame:
    """system_metrics per value of `key` (a column present in both frames, e.g. region)."""
    rows = []
    for g in sorted(set(labels[key].dropna()) | set(cands[key].dropna())):
        m = system_metrics(labels[labels[key] == g], cands[cands[key] == g], accept[(cands[key] == g).values])
        rows.append({key: g, **m})
    return pd.DataFrame(rows)
