"""Fixed structures against Satlas offshore infrastructure (AI2 predictions of platforms and wind turbines, ODC-BY).

Two directions, inside the sea the regional run actually tested (scene footprints of 20 September to 1 October):
  found:   share of Satlas points with a radar object within 250 m and 500 m, by Satlas category, split into
           "fixed structure" and "any radar object" (candidates of any class kept in the lean products)
  near:    share of fixed structures within 500 m of a Satlas point
Satlas points are model predictions from Sentinel-2 with a score, not ground truth, and only cover platforms and
turbines; aquaculture, stake nets, islets and anchorages are outside their scope, so "near" is not a precision.

Inputs: data/structures_regional.gpkg, data/detections_regional.gpkg, Satlas latest.geojson (cached by
src/darkvessel/satlas.py). Output: data/satlas_check.json
Usage: python scripts/20_satlas_check.py
"""

import json

import geopandas as gpd
import numpy as np
import pyogrio
import shapely
from scipy.spatial import cKDTree

from darkvessel import satlas
from darkvessel.config import DATA_DIR
from darkvessel.ml.evaluate import wilson

R = 6371008.8
scenes = gpd.read_file(DATA_DIR / "detections_regional.gpkg", layer="scenes_processed_4326")
foot = shapely.union_all(scenes.geometry.to_list())
fixed = pyogrio.read_dataframe(DATA_DIR / "structures_regional.gpkg", layer="structures_regional_4326", read_geometry=False,
                               columns=["det_id", "lat", "lon"])
cand = pyogrio.read_dataframe(DATA_DIR / "detections_regional.gpkg", layer="detections_regional_4326", read_geometry=False,
                              columns=["det_id", "lat", "lon", "confidence"])
sat = satlas.points()
sat = sat[shapely.contains_xy(foot, sat.geometry.x.to_numpy(), sat.geometry.y.to_numpy())].reset_index(drop=True)
lat0 = np.radians(float(sat.geometry.y.median()))
xy = lambda lo, la: np.c_[np.radians(np.asarray(lo)) * R * np.cos(lat0), np.radians(np.asarray(la)) * R]  # noqa: E731
d_fixed = cKDTree(xy(fixed.lon, fixed.lat)).query(xy(sat.geometry.x, sat.geometry.y))[0]
d_any = cKDTree(xy(np.r_[fixed.lon, cand.lon], np.r_[fixed.lat, cand.lat])).query(xy(sat.geometry.x, sat.geometry.y))[0]

out = {"satlas_points_in_tested_sea": int(len(sat)), "by_category": []}
for cat in ["all", *sorted(sat.category.unique())]:
    m = np.ones(len(sat), bool) if cat == "all" else (sat.category == cat).to_numpy()
    n = int(m.sum())
    row = {"category": cat, "points": n}
    for r_m in (250, 500):
        for name, d in (("fixed", d_fixed), ("any_object", d_any)):
            k = int((d[m] <= r_m).sum())
            row[f"{name}_within_{r_m}m"] = round(k / n, 3) if n else None
            row[f"{name}_within_{r_m}m_ci"] = [round(x, 3) for x in wilson(k, n)] if n else None
    out["by_category"].append(row)
d_near = satlas.distance_m(fixed.lon, fixed.lat, satlas.points())
k = int((d_near <= 500).sum())
out["fixed_within_500m_of_satlas"] = {"share": round(k / len(fixed), 3), "n": int(len(fixed)), "count": k}
out["note"] = ("Satlas points are predictions with scores, not truth; only platforms and turbines. Fixed structures far from "
               "Satlas points include aquaculture, stake nets, islets and anchorages, outside Satlas' scope.")
(DATA_DIR / "satlas_check.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
