"""Optical check of the radar classes with Sentinel-2: is there a bright object at the spot in a clear view?

A fixed structure (platform, turbine, aquaculture frame) stays put, so it should show in a cloud-free
Sentinel-2 image taken days from the radar pass; a moving vessel usually should not; open sea should not.
The check runs on a fixed random sample per class (hash of the object id) and on open-sea control points.

For each point and each Sentinel-2 L2A item of its tile between --start and --end (least cloudy first, up
to --max-items), the view is clear when no cloud, cirrus, shadow or no-data pixel (scene classification)
lies within 170 m. In the first clear view, the near-infrared (B08, 10 m) contrast is the brightest pixel
within 20 m of the point minus the median of the 310 m window. The detection threshold is the 99th
percentile of the control contrasts (1 % false positives at sea), so it is set by the data, not by hand.

Inputs: data/structures_regional.gpkg, data/detections_regional.gpkg, data/cache/viirs/seagrid.npz (sea
beyond 2 km from land; scripts/15_viirs_lights.py).
Outputs: data/optical_check.gpkg (EPSG:4326 and UTM 49N, one point per sampled object),
data/optical_check.json, docs/figures/optical_check.png
Usage: python scripts/19_optical_check.py [--n-fixed 1500 --n-high 800 --n-medium 800 --n-control 1000] [--workers 8]
"""

import argparse
import datetime as dt
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import rasterio
import shapely
from scipy.spatial import cKDTree

from darkvessel import s2
from darkvessel.config import CRS_UTM_REGIONAL, DARK_CAVEAT_SHORT, DATA_DIR, FIG_DIR
from darkvessel.io import write_dual_crs
from darkvessel.ml.evaluate import wilson

ap = argparse.ArgumentParser()
ap.add_argument("--n-fixed", type=int, default=1500)
ap.add_argument("--n-high", type=int, default=800)
ap.add_argument("--n-medium", type=int, default=800)
ap.add_argument("--n-control", type=int, default=1000)
ap.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2026, 9, 15))
ap.add_argument("--end", type=dt.date.fromisoformat, default=dt.date(2026, 10, 2))
ap.add_argument("--max-items", type=int, default=4)
ap.add_argument("--workers", type=int, default=8)
args = ap.parse_args()
t0 = time.time()
log = lambda m: print(f"[{time.time() - t0:6.0f}s] {m}", flush=True)  # noqa: E731


def hkey(s: str) -> float:
    return int(hashlib.sha1(f"optical:{s}".encode()).hexdigest()[:8], 16) / 2 ** 32


def sample(df: pd.DataFrame, n: int) -> pd.DataFrame:
    k = df.det_id.map(hkey)
    return df.assign(k=k).nsmallest(n, "k").drop(columns="k")


# ---- Sample: radar objects by class, and open-sea controls inside the tested footprints
cols = ["det_id", "scene_idx", "acq_utc", "confidence", "lat", "lon", "length_est_m"]
vess = pyogrio.read_dataframe(DATA_DIR / "detections_regional.gpkg", layer="detections_regional_4326", read_geometry=False, columns=cols)
fixed = pyogrio.read_dataframe(DATA_DIR / "structures_regional.gpkg", layer="structures_regional_4326", read_geometry=False,
                               columns=[c for c in cols if c != "scene_idx"])
pts = pd.concat([sample(fixed, args.n_fixed).assign(group="fixed"),
                 sample(vess[vess.confidence == "high"], args.n_high).assign(group="high"),
                 sample(vess[vess.confidence == "medium"], args.n_medium).assign(group="medium")], ignore_index=True)

scenes = gpd.read_file(DATA_DIR / "detections_regional.gpkg", layer="scenes_processed_4326")
foot = shapely.union_all(scenes.geometry.to_list())
z = np.load(DATA_DIR / "cache" / "viirs" / "seagrid.npz")
sea, res, west, north = z["mask"], float(z["res"]), float(z["west"]), float(z["north"])
rng = np.random.default_rng(20261003)
r, c = np.nonzero(sea)
pick = rng.choice(len(r), size=min(len(r), args.n_control * 6), replace=False)
clon = west + (c[pick] + rng.random(len(pick))) * res
clat = north - (r[pick] + rng.random(len(pick))) * res
keep = shapely.contains_xy(foot, clon, clat)
clon, clat = clon[keep], clat[keep]
lat0 = np.radians(15.0)
xy = lambda lo, la: np.c_[np.radians(lo) * 6371008.8 * np.cos(lat0), np.radians(la) * 6371008.8]  # noqa: E731
objs = pd.concat([vess[["lon", "lat"]], fixed[["lon", "lat"]]])
far = cKDTree(xy(objs.lon.to_numpy(), objs.lat.to_numpy())).query(xy(clon, clat))[0] > 1000
clon, clat = clon[far][: args.n_control], clat[far][: args.n_control]
ctrl = pd.DataFrame({"det_id": [f"control_{i:05d}" for i in range(len(clon))], "lat": clat, "lon": clon, "group": "control"})
pts = pd.concat([pts, ctrl], ignore_index=True)
pts["tile"] = [s2.tile_of(la, lo) for la, lo in zip(pts.lat, pts.lon)]
log(f"{len(pts)} points in {pts.tile.nunique()} Sentinel-2 tiles: " + ", ".join(f"{g} {n}" for g, n in pts.group.value_counts().items()))


# ---- Per tile: list items once, open each band once, read small windows per point
def tile_job(item):
    tile, g = item
    out = []
    try:
        items = [i for i in s2.list_items(tile, args.start, args.end) if i["cloud"] < 95][: args.max_items]
    except Exception as e:  # noqa: BLE001
        return [dict(det_id=d, view="no_listing", error=repr(e)[:120]) for d in g.det_id]
    opened = {}
    with rasterio.Env(**s2.GDAL_ENV):
        try:
            for p in g.itertuples():
                rec = {"det_id": p.det_id, "view": "no_clear_view", "items_tried": 0}
                for it in items:
                    rec["items_tried"] += 1
                    try:
                        if it["item"] not in opened:
                            opened[it["item"]] = (s2.open_band(it, "SCL"), s2.open_band(it, "B08"))
                        scl_ds, nir_ds = opened[it["item"]]
                        scl = s2.read_around(scl_ds, p.lon, p.lat, 8)
                        if scl is None or np.isin(scl, s2.SCL_BAD).any():
                            continue
                        nir = s2.read_around(nir_ds, p.lon, p.lat, 15)
                        if nir is None:
                            continue
                    except Exception as e:  # noqa: BLE001 (one bad read should not stop the tile)
                        rec["error"] = repr(e)[:120]
                        continue
                    peak, med, con = s2.nir_contrast(nir)
                    rec.update(view="clear", s2_item=it["item"], s2_datetime=it["datetime"], s2_tile_cloud=round(it["cloud"], 1),
                               scl_center=int(scl[8, 8]), nir_peak_dn=peak, nir_median_dn=med, nir_contrast_dn=con)
                    break
                out.append(rec)
        finally:
            for a, b in opened.values():
                a.close()
                b.close()
    return out


rows = []
groups = list(pts.groupby("tile"))
with ThreadPoolExecutor(args.workers) as ex:
    for i, res_ in enumerate(ex.map(tile_job, groups), 1):
        rows += res_
        if i % 20 == 0:
            log(f"{i}/{len(groups)} tiles")
res = pts.merge(pd.DataFrame(rows), on="det_id", how="left")

# ---- Threshold from the controls, shares per class
clear = res.view == "clear"
ctrl_c = res[clear & (res.group == "control")].nir_contrast_dn
thr = float(np.percentile(ctrl_c, 99)) if len(ctrl_c) >= 50 else float("nan")
res["optical_object"] = np.where(clear, res.nir_contrast_dn >= thr, np.nan)
summary = {"window": f"{args.start} to {args.end}", "points": int(len(res)), "tiles": int(pts.tile.nunique()),
           "threshold_nir_contrast_dn": round(thr, 1), "threshold_rule": "99th percentile of control contrasts (clear views)",
           "by_group": []}
for grp in ("fixed", "high", "medium", "control"):
    g = res[res.group == grp]
    gc = g[g.view == "clear"]
    k, n = int(gc.optical_object.sum()), int(len(gc))
    summary["by_group"].append({"group": grp, "sampled": int(len(g)), "clear_view": n,
                                "clear_share": round(n / max(1, len(g)), 3),
                                "optical_object_share": round(k / n, 3) if n else None,
                                "optical_object_ci": [round(x, 3) for x in wilson(k, n)] if n else None,
                                "contrast_median_dn": round(float(gc.nir_contrast_dn.median()), 1) if n else None})
summary["caveat"] = (DARK_CAVEAT_SHORT + " Sentinel-2 sees objects of roughly 10 m and larger in daylight; small stake nets and "
                     "low rafts can be missed, so the fixed-class share is a lower bound on its precision. A vessel candidate "
                     "with an optical object may be anchored or may be a structure in the wrong class.")
(DATA_DIR / "optical_check.json").write_text(json.dumps(summary, indent=1))
print(pd.DataFrame(summary["by_group"]).to_string(index=False))

res["caveat"] = DARK_CAVEAT_SHORT + " Optical check only: an object in a daytime image days from the radar pass."
keep_cols = ["det_id", "group", "confidence", "acq_utc", "lat", "lon", "length_est_m", "tile", "view", "items_tried", "s2_item",
             "s2_datetime", "s2_tile_cloud", "scl_center", "nir_peak_dn", "nir_median_dn", "nir_contrast_dn", "optical_object", "caveat"]
res = res[[c for c in keep_cols if c in res]]
out = DATA_DIR / "optical_check.gpkg"
if out.exists():
    out.unlink()
write_dual_crs(gpd.GeoDataFrame(res, geometry=gpd.points_from_xy(res.lon, res.lat), crs="EPSG:4326"), out, "optical_check",
               utm_crs=CRS_UTM_REGIONAL)
pyogrio.write_dataframe(pd.DataFrame([{"about": "Sentinel-2 L2A optical check of sampled radar objects (scripts/19_optical_check.py)",
                                       "threshold": summary["threshold_rule"], "caveat_full": summary["caveat"],
                                       "data_credit": "Sentinel-2 L2A COGs from the AWS Open Data mirror (sentinel-cogs); "
                                                      "contains modified Copernicus Sentinel data 2026"}]), out, layer="about", driver="GPKG")
log(f"wrote {out}")

# ---- Figure: contrast distributions per class, threshold line
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from darkvessel.viz.style import INK, INK_2, MUTED, SERIES_LIGHT, apply_matplotlib_style  # noqa: E402

apply_matplotlib_style()
fig, ax = plt.subplots(figsize=(9, 5.4))
fig.subplots_adjust(left=0.09, right=0.97, top=0.8, bottom=0.14)
bins = np.r_[-200, np.arange(-100, 2001, 50), 1e6]
labels = {"control": ("Open sea (control)", "#898781"), "high": ("Both-channel candidates", SERIES_LIGHT[0]),
          "medium": ("One-channel candidates", SERIES_LIGHT[1]), "fixed": ("Fixed structures", SERIES_LIGHT[2])}
for grp in ("control", "medium", "high", "fixed"):
    v = res[(res.group == grp) & (res.view == "clear")].nir_contrast_dn.clip(-150, 2050)
    if len(v):
        h, e = np.histogram(v, bins=bins)
        ax.step(np.clip(e[:-1], -150, 2050), np.cumsum(h[::-1])[::-1] / len(v), where="post", color=labels[grp][1], linewidth=1.8,
                label=f"{labels[grp][0]} (n = {len(v):,})")
ax.axvline(thr, color=INK, linewidth=0.8, linestyle="--")
ax.text(thr + 20, 0.95, f"threshold {thr:.0f} DN\n(1 % of open sea above)", fontsize=8.5, color=INK_2, va="top")
ax.set_xlim(-150, 2050)
ax.set_ylim(0, 1.02)
ax.set_xlabel("Near-infrared contrast at the point, Sentinel-2 B08 (DN; 10,000 DN = reflectance 1)", fontsize=9)
ax.set_ylabel("Share of points with at least this contrast", fontsize=9)
ax.grid(True, zorder=0)
ax.legend(loc="upper right", fontsize=8.5)
fig.text(0.09, 0.96, "Optical check of the radar classes with Sentinel-2", fontsize=14, color=INK, fontweight="bold", va="top")
fig.text(0.09, 0.915, f"First cloud-free Sentinel-2 view between {args.start:%d %b} and {args.end:%d %b %Y}, days from the radar pass. "
         "Fixed objects should stay bright;\nmoving vessels and open sea should not. " + DARK_CAVEAT_SHORT, fontsize=9, color=INK_2, va="top")
fig.text(0.09, 0.012, "Sentinel-2 L2A from the AWS Open Data mirror (contains modified Copernicus Sentinel data 2026).",
         fontsize=7.5, color=MUTED, va="bottom")
FIG_DIR.mkdir(parents=True, exist_ok=True)
fig.savefig(FIG_DIR / "optical_check.png", dpi=150)
log(f"wrote {FIG_DIR / 'optical_check.png'}")
