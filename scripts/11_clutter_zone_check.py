"""What the clutter-zone rule costs and gains, measured on AI2-labelled Sentinel-1A/1B candidates.

Input: data/ml/candidates.parquet (scripts/04_build_training_set.py): every CFAR object in the
selected AI2 windows with its heuristic class and its match to the expert labels (vessel within
50 m, ambiguous 50 to 150 m, clutter otherwise). Labels miss some real ships, so "clutter" is an
upper bound on true clutter.

For each radius and threshold: share of high/medium candidates flagged, share of labelled-vessel
candidates lost, share of clutter candidates removed, and precision of what is kept.
Output: data/clutter_zone_check.json
Usage: python scripts/11_clutter_zone_check.py
"""

import json

import pandas as pd

from darkvessel.config import DATA_DIR
from darkvessel.detect.postprocess import clutter_zone

c = pd.read_parquet(DATA_DIR / "ml" / "candidates.parquet",
                    columns=["window_id", "lon", "lat", "confidence", "cand_class", "region"])
rows = []
for radius in (500.0, 1000.0, 2000.0):
    _, n_low = clutter_zone(c, radius_m=radius, min_low=1, group="window_id")
    c[f"n_low_{int(radius)}"] = n_low
for region, sub in (("all", c), ("sea_asia", c[c.region == "sea_asia"])):
    v = sub[sub.confidence.isin(["high", "medium"])]
    vessel, clutter = v.cand_class == "vessel", v.cand_class == "clutter"
    for radius in (500, 1000, 2000):
        for k in (3, 5, 8, 12):
            flag = v[f"n_low_{radius}"] >= k
            kept = v[~flag]
            rows.append({
                "region": region, "radius_m": radius, "min_low": k, "candidates": len(v),
                "flagged_share": round(float(flag.mean()), 3),
                "vessel_lost_share": round(float((flag & vessel).sum() / vessel.sum()), 3),
                "clutter_removed_share": round(float((flag & clutter).sum() / clutter.sum()), 3),
                "precision_high_kept": round(float((kept[kept.confidence == "high"].cand_class == "vessel").mean()), 3),
                "precision_medium_kept": round(float((kept[kept.confidence == "medium"].cand_class == "vessel").mean()), 3),
                "precision_high_before": round(float((v[v.confidence == "high"].cand_class == "vessel").mean()), 3),
                "precision_medium_before": round(float((v[v.confidence == "medium"].cand_class == "vessel").mean()), 3),
            })
out = pd.DataFrame(rows)
(DATA_DIR / "clutter_zone_check.json").write_text(json.dumps(rows, indent=1))
pd.set_option("display.width", 220)
print(out[(out.radius_m == 1000)].to_string(index=False))
