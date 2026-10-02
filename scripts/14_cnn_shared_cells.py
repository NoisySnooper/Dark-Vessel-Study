"""CNN verifier on Sentinel-1C and 1D objects in sea that both satellites imaged (transfer check, no truth).

For every 0.25 degree cell in which each satellite observed at least 30 % of the cell's sea during
the regional run, take the high, medium and fixed objects of both satellites, cut 64 x 64 px VV/VH
chips exactly as in training (sigma0 in dB, thermal noise not removed), score them with
data/models/verifier_v0.pt and compare by satellite and class:
  - CNN acceptance share (Wilson 95 % interval) and score quartiles
  - chip background: median dB of the chip outside its central 16 x 16 px
Same sea, same weeks, different satellite: a difference points at the sensor or the pass time;
equal results rule out a gross 1C/1D difference but do not prove transfer (there is no truth).
Outputs: data/ml/shared_cells_cnn.parquet (one row per object), data/ml/shared_cells_cnn.json
Usage (conda env with torch): python scripts/14_cnn_shared_cells.py [--min-share 0.3]
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
from rasterio import features
from rasterio.windows import Window

from darkvessel.aoi import aoi_gdf, natural_earth_land
from darkvessel.config import DATA_DIR, DEFAULT_AOI
from darkvessel.coverage import cell_area_km2, grid_for
from darkvessel.ml.chips import CHIP_HALF, chips_db
from darkvessel.ml.evaluate import wilson
from darkvessel.ml.model import load_model, predict_proba
from darkvessel.s1.grd import GRDScene
from darkvessel.viz.demo import _scene_path

ap = argparse.ArgumentParser()
ap.add_argument("--min-share", type=float, default=0.3)
ap.add_argument("--res", type=float, default=0.25)
ap.add_argument("--block", type=int, default=1024)
ap.add_argument("--max-scenes", type=int, default=None, help="test on the first N scenes only")
ap.add_argument("--out-suffix", default="")
args = ap.parse_args()
t0 = time.time()
log = lambda m: print(f"[{time.time() - t0:6.0f}s] {m}", flush=True)  # noqa: E731

# 1. Shared cells: observed sea per satellite on a fine grid, summed to res-degree cells
aoi = aoi_gdf(DEFAULT_AOI).geometry.iloc[0]
k = int(round(args.res / 0.01))
tr_c, sc = grid_for(aoi.bounds, args.res)
sf = (sc[0] * k, sc[1] * k)
tr_f = tr_c * tr_c.scale(1 / k)
w, s, e, n = aoi.bounds
land = natural_earth_land(bbox=(w - 1, s - 1, e + 1, n + 1))
sea = (features.rasterize([(aoi, 1)], out_shape=sf, transform=tr_f, dtype="uint8").astype(bool)
       & ~features.rasterize([(g, 1) for g in land.geometry], out_shape=sf, transform=tr_f, dtype="uint8").astype(bool))
af = cell_area_km2(tr_f, sf)
proc = gpd.read_file(DATA_DIR / "detections_regional_all.gpkg", layer="scenes_processed_4326")
block_sum = lambda a: a.reshape(sc[0], k, sc[1], k).sum(axis=(1, 3))  # noqa: E731
cell = cell_area_km2(tr_c, sc)
share = {}
for m in ("S1C", "S1D"):
    hit = features.rasterize([(g, 1) for g in proc[proc.mission == m].geometry], out_shape=sf, transform=tr_f,
                             dtype="uint8").astype(bool)
    share[m] = block_sum(np.where(sea & hit, af, 0.0)) / cell
shared = (share["S1C"] >= args.min_share) & (share["S1D"] >= args.min_share)
log(f"shared cells: {int(shared.sum())}")

# 2. Objects of both satellites in shared cells
det = pyogrio.read_dataframe(DATA_DIR / "detections_regional_all.gpkg", layer="detections_regional_4326", read_geometry=False,
                             columns=["det_id", "scene_id", "mission", "confidence", "row", "col", "lon", "lat",
                                      "length_est_m", "inc_angle_deg", "pass_dir"],
                             where="confidence IN ('high', 'medium', 'fixed')")
ci = np.floor((det.lat.values - tr_c.f) / tr_c.e).astype(int)
cj = np.floor((det.lon.values - tr_c.c) / tr_c.a).astype(int)
ok = (ci >= 0) & (ci < sc[0]) & (cj >= 0) & (cj < sc[1])
ok[ok] = shared[ci[ok], cj[ok]]
det = det[ok].assign(cell=(ci[ok] * sc[1] + cj[ok])).reset_index(drop=True)
if args.max_scenes:
    det = det[det.scene_id.isin(det.scene_id.drop_duplicates().head(args.max_scenes))].reset_index(drop=True)
log(f"objects in shared cells: {len(det)} {det.groupby(['mission', 'confidence']).size().to_dict()}")


# 3. Chips per scene, read in blocks around clustered objects
def scene_chips(sid: str, g: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    scene = GRDScene(_scene_path(sid))
    H, W = scene.shape
    out = np.full((len(g), 2, 2 * CHIP_HALF, 2 * CHIP_HALF), np.nan, np.float16)
    rows, cols = g.row.to_numpy(float), g.col.to_numpy(float)
    key = (rows // args.block).astype(int) * 100000 + (cols // args.block).astype(int)
    for kk in np.unique(key):
        sel = np.nonzero(key == kk)[0]
        r0 = max(0, int(rows[sel].min()) - CHIP_HALF - 1)
        r1 = min(H, int(rows[sel].max()) + CHIP_HALF + 2)
        c0 = max(0, int(cols[sel].min()) - CHIP_HALF - 1)
        c1 = min(W, int(cols[sel].max()) + CHIP_HALF + 2)
        win = Window(c0, r0, c1 - c0, r1 - r0)
        for attempt in range(3):
            try:
                sigma = {p: scene.read_sigma0(p, win, denoise=False) for p in ("VV", "VH")}
                break
            except Exception:  # noqa: BLE001 (transient network reads)
                if attempt == 2:
                    raise
                time.sleep(3 * (attempt + 1))
        out[sel] = chips_db(sigma, rows[sel], cols[sel], r0, c0)
    return g.index.to_numpy(), out


chips = np.full((len(det), 2, 2 * CHIP_HALF, 2 * CHIP_HALF), np.nan, np.float16)
with ThreadPoolExecutor(3) as ex:
    futs = [ex.submit(scene_chips, sid, g) for sid, g in det.groupby("scene_id")]
    for i, f in enumerate(futs, 1):
        idx, c = f.result()
        chips[idx] = c
        if i % 5 == 0:
            log(f"chips: {i}/{len(futs)} scenes")

# 4. Score and background
model, meta = load_model(str(DATA_DIR / "models" / "verifier_v0.pt"))
thr = float(meta["threshold"])
det["cnn_score"] = predict_proba(model, chips, meta, tta=True).astype(float)
det["cnn_vessel"] = det.cnn_score >= thr
ring = np.ones((2 * CHIP_HALF, 2 * CHIP_HALF), bool)
ring[CHIP_HALF - 8:CHIP_HALF + 8, CHIP_HALF - 8:CHIP_HALF + 8] = False
c32 = chips.astype(np.float32)
det["bg_vv_db"] = np.nanmedian(c32[:, 0][:, ring], axis=1)
det["bg_vh_db"] = np.nanmedian(c32[:, 1][:, ring], axis=1)
det.to_parquet(DATA_DIR / "ml" / f"shared_cells_cnn{args.out_suffix}.parquet")

summ = {"threshold": thr, "shared_cells": int(shared.sum()), "min_share": args.min_share, "objects": int(len(det)), "by": []}
for (m, cls), g in det.groupby(["mission", "confidence"]):
    kk, nn = int(g.cnn_vessel.sum()), len(g)
    summ["by"].append({"mission": m, "class": cls, "n": nn, "accepted": kk, "accept_share": round(kk / nn, 3),
                       "accept_ci": [round(x, 3) for x in wilson(kk, nn)],
                       "score_q25_q50_q75": [round(float(x), 3) for x in g.cnn_score.quantile([0.25, 0.5, 0.75])],
                       "bg_vv_db_median": round(float(g.bg_vv_db.median()), 2), "bg_vh_db_median": round(float(g.bg_vh_db.median()), 2),
                       "length_median_m": round(float(g.length_est_m.median()), 1)})
# Paired by cell: median background per cell and satellite, then the 1D minus 1C difference
pc = det.groupby(["cell", "mission"])[["bg_vv_db", "bg_vh_db"]].median().unstack("mission").dropna()
both = all((c, m) in pc.columns for c in ("bg_vv_db", "bg_vh_db") for m in ("S1C", "S1D"))
summ["paired_cells_bg"] = {
    "cells": int(len(pc)) if both else 0,
    "vv_1d_minus_1c_median": round(float((pc[("bg_vv_db", "S1D")] - pc[("bg_vv_db", "S1C")]).median()), 2) if both else None,
    "vh_1d_minus_1c_median": round(float((pc[("bg_vh_db", "S1D")] - pc[("bg_vh_db", "S1C")]).median()), 2) if both else None}
(DATA_DIR / "ml" / f"shared_cells_cnn{args.out_suffix}.json").write_text(json.dumps(summ, indent=1))
print(pd.DataFrame(summ["by"]).to_string(index=False))
print(json.dumps(summ["paired_cells_bg"]))
log("done")
