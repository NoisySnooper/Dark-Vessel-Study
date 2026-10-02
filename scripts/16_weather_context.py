"""Weather at the radar time for every regional object: GFS 10 m wind and Himawari-9 cloud-top temperature.

For each processed Sentinel-1 scene, the GFS 0.25 degree 10 m wind of the nearest hour and the
Himawari-9 cloud-top temperature of the nearest 10-minute full disk (parallax corrected) are taken at
every candidate, fixed structure and clutter-flagged object (src/darkvessel/weather.py).

Two checks come out of it:
  1. The clutter-zone rule: are flagged objects under deep convection (cloud tops colder than 220 K)
     more often than kept candidates? The rule was set from the radar alone, so this is independent.
  2. Sea state for the transfer letter (docs/paper1_design.md, M6): chip background against wind,
     for 1C and 1D objects on shared sea (data/ml/shared_cells_cnn.parquet).

Outputs: data/weather_context.parquet (det_id, wind_ms, ctt_k, deep_convection), data/weather_context.json
Usage: python scripts/16_weather_context.py [--workers 4]
"""

import argparse
import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pyogrio

from darkvessel import weather
from darkvessel.aoi import aoi_gdf
from darkvessel.config import DATA_DIR, DEFAULT_AOI
from darkvessel.ml.evaluate import wilson

CACHE = DATA_DIR / "cache" / "weather"

ap = argparse.ArgumentParser()
ap.add_argument("--workers", type=int, default=4)
args = ap.parse_args()
CACHE.mkdir(parents=True, exist_ok=True)
t0 = time.time()
log = lambda m: print(f"[{time.time() - t0:6.0f}s] {m}", flush=True)  # noqa: E731

full = DATA_DIR / "detections_regional_all.gpkg"
obj = pyogrio.read_dataframe(full, layer="detections_regional_4326", read_geometry=False,
                             columns=["det_id", "scene_id", "acq_utc", "confidence", "low_reason", "lon", "lat", "mission"],
                             where="confidence IN ('high', 'medium', 'fixed') OR low_reason IN ('clutter_zone', 'near_fixed')")
obj["group"] = np.where(obj.confidence == "low", obj.low_reason, obj.confidence)
log(f"{len(obj)} objects in {obj.scene_id.nunique()} scenes")

# One Himawari window for the whole AOI, found once and cached
aoi = aoi_gdf(DEFAULT_AOI).geometry.iloc[0]
win_path = CACHE / "himawari_window.json"
first_t = pd.Timestamp(obj.acq_utc.min()).to_pydatetime()
if win_path.exists():
    win = weather.HimawariWindow.__new__(weather.HimawariWindow)
    win.__dict__.update(json.loads(win_path.read_text()))
else:
    win = weather.HimawariWindow(weather.himawari_key(first_t), aoi.bounds)
    win_path.write_text(json.dumps(win.__dict__))


def scene(item):
    sid, g = item
    t = pd.Timestamp(g.acq_utc.iloc[0]).to_pydatetime()
    out = pd.DataFrame({"det_id": g.det_id.to_numpy()})
    try:
        spd, tr = weather.gfs_wind(t, cache_dir=CACHE)
        out["wind_ms"] = np.round(weather.sample_grid(spd, tr, g.lon.to_numpy(), g.lat.to_numpy()), 2)
    except Exception as e:  # noqa: BLE001
        log(f"GFS failed for {sid[:32]}: {e!r}"[:200])
        out["wind_ms"] = np.nan
    try:
        key = weather.himawari_key(t)
        lon, lat, ctt = weather.himawari_ctt(key, win)
        out["ctt_k"] = np.round(weather.ctt_at(lon, lat, ctt, g.lon.to_numpy(), g.lat.to_numpy()), 1)
        out["himawari_start"] = key.split("_s")[1][:13] if key else None
    except Exception as e:  # noqa: BLE001
        log(f"Himawari failed for {sid[:32]}: {e!r}"[:200])
        out["ctt_k"] = np.nan
    return out


parts = []
with ThreadPoolExecutor(args.workers) as ex:
    for i, part in enumerate(ex.map(scene, obj.groupby("scene_id")), 1):
        parts.append(part)
        if i % 10 == 0:
            log(f"{i}/{obj.scene_id.nunique()} scenes")
wx = pd.concat(parts, ignore_index=True)
wx["deep_convection"] = wx.ctt_k < weather.DEEP_CONVECTION_K
wx.to_parquet(DATA_DIR / "weather_context.parquet")
m = obj.merge(wx, on="det_id")

summary = {"objects": int(len(m)), "with_wind": int(m.wind_ms.notna().sum()), "with_cloud_top": int(m.ctt_k.notna().sum()),
           "deep_convection_threshold_k": weather.DEEP_CONVECTION_K, "by_group": []}
for grp, g in m.groupby("group"):
    k, n = int(g.deep_convection.sum()), len(g)
    summary["by_group"].append({"group": grp, "n": n, "deep_convection_share": round(k / n, 3),
                                "deep_convection_ci": [round(x, 3) for x in wilson(k, n)],
                                "cloud_free_share": round(float(g.ctt_k.isna().mean()), 3),
                                "wind_median_ms": round(float(g.wind_ms.median()), 1)})

# Sea state against chip background on shared 1C/1D sea (paper 1, M6)
shared = DATA_DIR / "ml" / "shared_cells_cnn.parquet"
if shared.exists():
    sc = pd.read_parquet(shared, columns=["det_id", "mission", "confidence", "cell", "bg_vv_db", "bg_vh_db"]).merge(wx, on="det_id")
    sc = sc[sc.confidence.isin(["high", "medium"]) & sc.wind_ms.notna()]
    fits = {}
    for mis, g in sc.groupby("mission"):
        for pol in ("bg_vv_db", "bg_vh_db"):
            slope, icpt = np.polyfit(g.wind_ms, g[pol], 1)
            fits[f"{mis}_{pol}"] = {"slope_db_per_ms": round(float(slope), 3), "intercept_db": round(float(icpt), 2), "n": int(len(g))}
    # wind-matched comparison: 1D minus 1C background in 2 m/s wind bins
    sc["wind_bin"] = pd.cut(sc.wind_ms, [0, 2, 4, 6, 8, 10, 30])
    tab = sc.groupby(["wind_bin", "mission"], observed=True)[["bg_vv_db", "bg_vh_db"]].median().unstack("mission").dropna()
    matched = {str(b): {"vv_1d_minus_1c": round(float(r[("bg_vv_db", "S1D")] - r[("bg_vv_db", "S1C")]), 2),
                        "vh_1d_minus_1c": round(float(r[("bg_vh_db", "S1D")] - r[("bg_vh_db", "S1C")]), 2)} for b, r in tab.iterrows()}
    summary["shared_cells_background_vs_wind"] = {"linear_fits": fits, "wind_matched_difference": matched,
                                                  "wind_median_ms": sc.groupby("mission").wind_ms.median().round(1).to_dict()}
(DATA_DIR / "weather_context.json").write_text(json.dumps(summary, indent=1, default=str))
print(pd.DataFrame(summary["by_group"]).to_string(index=False))
print(json.dumps(summary.get("shared_cells_background_vs_wind", {}), indent=1, default=str))
log("done")
