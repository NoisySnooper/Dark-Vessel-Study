"""Score the owner's labels from the demo page against the detector classes and the CNN verifier.

Input: CSV files exported with "Copy labels as CSV" on the demo page (default: data/labels/*.csv),
columns det_id, label, ... Labels: vessel, structure, clutter, unsure. "unsure" is counted but left
out of the shares.

Only labels in the random sample enter the estimates. A contact is in the sample when
queue_key(det_id) < QUEUE_RATES[view][class] (darkvessel.viz.demo): every contact of a class had the
same inclusion probability, so the share of each label within a class estimates that class's
population share without bias. Shares get Wilson 95 % intervals. The vessel share over all
candidates (high + medium) is the class-size-weighted mean with a stratified normal interval.

CNN (Ca Mau view): every CNN-accepted contact is in the queue (a census), the rest come from the class
sample. Precision and recall of the CNN verdict against the owner's labels, vessel = positive, raw and
weighted by 1 / inclusion probability (bootstrap intervals). This is the 1D number to compare with precision 0.77 and recall
0.75 on held-out Sentinel-1A/1B scenes (docs/ml_verifier.md).

Output: data/labels/label_scores.json and printed tables.
Usage: python scripts/12_score_labels.py [files ...]
"""

import argparse
import glob
import json
import math

import numpy as np
import pandas as pd
import pyogrio

from darkvessel.config import DATA_DIR
from darkvessel.ml.evaluate import wilson
from darkvessel.viz.demo import QUEUE_RATES, queue_key

LABELS = ("vessel", "structure", "clutter")


def products() -> pd.DataFrame:
    """det_id -> view, class, mission (and CNN verdict for the Ca Mau view) from the current products."""
    reg = pd.concat([pyogrio.read_dataframe(DATA_DIR / f, layer=layer, read_geometry=False,
                                            columns=["det_id", "confidence", "mission"])
                     for f, layer in (("detections_regional.gpkg", "detections_regional_4326"),
                                      ("structures_regional.gpkg", "structures_regional_4326"))
                     if (DATA_DIR / f).exists()], ignore_index=True)
    reg["view"] = "regional"
    det = pyogrio.read_dataframe(DATA_DIR / "detections_baseline.gpkg", layer="detections_baseline_4326",
                                 read_geometry=False, columns=["det_id", "confidence"])
    det["view"], det["mission"] = "detail", "S1D"
    ml = DATA_DIR / "detections_ml.gpkg"
    if ml.exists():
        cnn = pyogrio.read_dataframe(ml, layer="detections_verified_4326", read_geometry=False,
                                     columns=["det_id", "cnn_score", "cnn_vessel"])
        det = det.merge(cnn, on="det_id", how="left")
    return pd.concat([reg, det], ignore_index=True)


def stratified_share(rows: list[dict]) -> dict | None:
    """Class-size-weighted vessel share over strata, normal 95 % interval (finite strata ignored)."""
    rows = [r for r in rows if r["n"] > 0]
    if not rows:
        return None
    N = sum(r["N"] for r in rows)
    p = sum(r["N"] * r["vessel_share"] for r in rows) / N
    var = sum((r["N"] / N) ** 2 * r["vessel_share"] * (1 - r["vessel_share"]) / r["n"] for r in rows)
    half = 1.96 * math.sqrt(var)
    return {"vessel_share": round(p, 3), "ci": [round(max(0.0, p - half), 3), round(min(1.0, p + half), 3)],
            "classes": [r["class"] for r in rows]}


def score(labels: pd.DataFrame, prod: pd.DataFrame) -> dict:
    lab = labels.drop_duplicates("det_id", keep="last").merge(prod, on="det_id", how="left")
    unknown = int(lab.view.isna().sum())
    lab = lab.dropna(subset=["view"])
    lab["in_sample"] = [queue_key(d) < QUEUE_RATES[v].get(c, 0.0) for d, v, c in zip(lab.det_id, lab.view, lab.confidence)]
    # Ca Mau view: every CNN-accepted contact is also in the design, with probability 1 (census)
    accepted = lab.get("cnn_vessel", pd.Series(False, index=lab.index)).fillna(False).astype(bool) & (lab.view == "detail")
    lab["in_design"] = lab.in_sample | accepted
    out = {"labels_read": int(len(labels)), "labels_unknown_det_id": unknown,
           "labels_in_sample": int(lab.in_sample.sum()), "labels_outside_sample": int((~lab.in_design).sum()),
           "labels_cnn_census": int((lab.in_design & ~lab.in_sample).sum()),
           "by_view_class": [], "candidates": {}, "cnn_detail": None, "rates": QUEUE_RATES}
    totals = prod.groupby(["view", "confidence"]).size()
    s = lab[lab.in_sample]
    for (view, cls), g in s.groupby(["view", "confidence"]):
        sure = g[g.label.isin(LABELS)]
        n = len(sure)
        row = {"view": view, "class": cls, "N": int(totals.get((view, cls), 0)), "labelled": int(len(g)),
               "unsure": int((g.label == "unsure").sum()), "n": n}
        for lb in LABELS:
            k = int((sure.label == lb).sum())
            row[f"{lb}_share"] = round(k / n, 3) if n else None
            row[f"{lb}_ci"] = [round(x, 3) for x in wilson(k, n)] if n else None
        row["by_mission"] = {m: {"n": int(len(gm)), "vessel_share": round(float((gm.label == "vessel").mean()), 3)}
                             for m, gm in sure.groupby("mission")}
        out["by_view_class"].append(row)
    for view in ("regional", "detail"):
        rows = [r for r in out["by_view_class"] if r["view"] == view and r["class"] in ("high", "medium")]
        out["candidates"][view] = stratified_share(rows)
    s_design = lab[lab.in_design]
    d = (s_design[(s_design.view == "detail") & s_design.label.isin(LABELS) & s_design.cnn_vessel.notna()]
         if "cnn_vessel" in s_design else s_design.iloc[:0])
    if len(d):
        truth, pred = d.label == "vessel", d.cnn_vessel.astype(bool)
        tp, fp, fn = int((truth & pred).sum()), int((~truth & pred).sum()), int((truth & ~pred).sum())
        out["cnn_detail"] = {
            "n": int(len(d)), "tp": tp, "fp": fp, "fn": fn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None, "precision_ci": [round(x, 3) for x in wilson(tp, tp + fp)] if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None, "recall_ci": [round(x, 3) for x in wilson(tp, tp + fn)] if tp + fn else None,
            "note": "CNN-accepted contacts are a census; others come from the class sample. Unweighted here; "
                    "see 'weighted' for population estimates. Compare with held-out 1A/1B precision 0.77, recall 0.75.",
        }
        # Population estimate: weight = 1 / inclusion probability (1 for CNN-accepted, class rate otherwise)
        rate = d.confidence.map(QUEUE_RATES["detail"]).fillna(1.0).to_numpy()
        w = np.where(d.cnn_vessel.astype(bool).to_numpy(), 1.0, 1.0 / rate)
        t, pr = truth.to_numpy(), pred.to_numpy()
        out["cnn_detail"]["weighted"] = weighted_pr(t, pr, w)
    return out


def weighted_pr(truth, pred, w, n_boot: int = 2000, seed: int = 20261002) -> dict:
    """Horvitz-Thompson precision and recall with percentile bootstrap 95 % intervals."""
    rng = np.random.default_rng(seed)

    def pr(idx):
        tp = (w[idx] * (truth[idx] & pred[idx])).sum()
        acc, pos = (w[idx] * pred[idx]).sum(), (w[idx] * truth[idx]).sum()
        return (tp / acc if acc else np.nan), (tp / pos if pos else np.nan)

    p0, r0 = pr(np.arange(len(w)))
    boots = np.array([pr(rng.integers(0, len(w), len(w))) for _ in range(n_boot)])
    def ci(col):
        v = boots[:, col]
        v = v[np.isfinite(v)]
        return [round(float(x), 3) for x in np.percentile(v, [2.5, 97.5])] if len(v) else None

    return {"precision": None if np.isnan(p0) else round(float(p0), 3), "precision_ci": ci(0),
            "recall": None if np.isnan(r0) else round(float(r0), 3), "recall_ci": ci(1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    files = a.files or sorted(glob.glob(str(DATA_DIR / "labels" / "*.csv")))
    if not files:
        raise SystemExit("no label files: export from the demo page into data/labels/<name>.csv")
    labels = pd.concat([pd.read_csv(f, usecols=["det_id", "label"]) for f in files], ignore_index=True)
    res = score(labels, products())
    res["files"] = files
    out = DATA_DIR / "labels" / "label_scores.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1))
    t = pd.DataFrame(res["by_view_class"])
    if len(t):
        print(t[["view", "class", "N", "labelled", "unsure", "n", "vessel_share", "vessel_ci", "structure_share", "clutter_share"]].to_string(index=False))
    print(json.dumps({k: res[k] for k in ("labels_read", "labels_in_sample", "labels_outside_sample", "candidates", "cnn_detail")}, indent=1))
    print("wrote", out)


if __name__ == "__main__":
    main()
