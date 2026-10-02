"""Detail demo: one Sentinel-1D scene over the Ca Mau sub-area -> CA-CFAR detections, maps, COGs.

Needs data/s1_scenes_ca_mau.csv from: python scripts/02_search_scenes.py --aoi ca_mau

Windowed reads only (no full-scene download). Two earlier dates on the same relative
orbit are processed for the persistence test that separates fixed structures from vessels.

Usage: python scripts/03_run_baseline.py [--scene GRD/...] [--bbox W S E N] [--persist 2]
Outputs:
  data/detections_baseline.gpkg   detections_baseline_*, detections_vv_*, detections_vh_*, processing_window_*
  data/outputs/sigma0_vv_db_utm48n_20m.tif          (COG, float32 dB; gitignored, regenerate)
  data/outputs/small/sigma0_vv_db_utm48n_40m_u8.tif (COG, uint8 = round((dB + 35) * 255 / 35))
  docs/figures/baseline_map.png, docs/figures/baseline_chips.png
  data/baseline_run_summary.json
"""

import argparse
import json
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from rasterio.warp import Resampling

from darkvessel.aoi import aoi_geometry
from darkvessel.config import CRS_UTM, DARK_CAVEAT_SHORT, DATA_DIR, FIG_DIR
from darkvessel.detect.postprocess import apply_persistence, assign_confidence, persistence
from darkvessel.pipeline import ICT, run_baseline
from darkvessel.products import write_detection_products
from darkvessel.s1.export import scale_gcps, warp_to_map, window_gcps, write_cog
from darkvessel.viz.maps import chip_gallery, detection_map

DEFAULT_SCENE = "GRD/2026/9/29/IW/DV/S1D_IW_GRDH_1SDV_20260929T111023_20260929T111053_004792_008FC8_A5CA"
DEFAULT_BBOX = (104.7, 8.0, 105.9, 9.2)

ap = argparse.ArgumentParser()
ap.add_argument("--scene", default=DEFAULT_SCENE)
ap.add_argument("--bbox", nargs=4, type=float, default=DEFAULT_BBOX)
ap.add_argument("--persist", type=int, default=2, help="earlier same-orbit dates to check")
ap.add_argument("--pfa", type=float, default=1e-6)
args = ap.parse_args()
t0 = time.time()
bbox = tuple(args.bbox)

# Persistence first (same relative orbit and pass, earlier dates, same box and settings), so the
# main scene's large arrays never sit in memory alongside another scene's.
from darkvessel.s1.grd import GRDScene

ref = GRDScene(args.scene)
scenes = pd.read_csv(DATA_DIR / "s1_scenes_ca_mau.csv", parse_dates=["start_utc"])
same = scenes[(scenes.orbit_rel == ref.manifest["orbit_rel"]) & (scenes.pass_dir == ref.manifest["pass_dir"])
              & (scenes.start_utc < pd.Timestamp(ref.meta["start"])) & (scenes.aoi_coverage > 0.2)]
others_paths = same.sort_values("start_utc", ascending=False)["path"].head(args.persist).tolist()
others = []
cache_dir = DATA_DIR / "cache" / "persistence"
cache_dir.mkdir(parents=True, exist_ok=True)
for p in others_paths:
    key = cache_dir / f"{p.rsplit('/', 1)[-1]}_{'_'.join(f'{v:g}' for v in bbox)}_{args.pfa:g}.parquet"
    if key.exists():
        others.append(gpd.read_parquet(key))
        continue
    o = run_baseline(p, bbox, pfa=args.pfa, keep_sigma=())
    keep = assign_confidence(o["detections"])
    keep = keep[keep.confidence != "low"]
    keep.to_parquet(key)
    others.append(keep)
    del o

main = run_baseline(args.scene, bbox, pfa=args.pfa, keep_sigma=("VV",))
scene, window, meta = main["scene"], main["window"], main["meta"]
dets = assign_confidence(main["detections"])
dets = apply_persistence(dets, persistence(dets, others), len(others))
dets["persist_scenes"] = ";".join(p.rsplit("/", 1)[-1] for p in others_paths)
print("classes:", dets.confidence.value_counts().to_dict())

# Vector products (lean committed file + gitignored per-polarisation file)
write_detection_products(dets, main["per_pol"], main["window_gdf"], meta, DATA_DIR / "detections_baseline.gpkg",
                         DATA_DIR / "detections_baseline_perpol.gpkg", geocoder=scene.geocoder)

# Raster products: VV sigma0 warped to UTM 48N
gcps = window_gcps(scene.geocoder, window)
vv_utm, tr20 = warp_to_map(main["sigma0"].pop("VV"), gcps, res_m=20.0, resampling=Resampling.average)
f = main["mask_factor"]
sea_utm, _ = warp_to_map(main["sea_ok_d"].astype(np.float32), scale_gcps(gcps, f), resampling=Resampling.nearest,
                         dst_transform=tr20, dst_shape=vv_utm.shape)
sea_utm = np.nan_to_num(sea_utm)
db20 = (10 * np.log10(vv_utm)).astype(np.float32)
tags = {"scene_id": meta["scene_id"], "acq_utc": meta["acq_utc"], "units": "sigma0 VV dB, thermal noise not removed",
        "source": "Contains modified Copernicus Sentinel data 2026"}
write_cog(db20, tr20, CRS_UTM, DATA_DIR / "outputs" / "sigma0_vv_db_utm48n_20m.tif", nodata=np.nan, tags=tags)
H2, W2 = (db20.shape[0] // 2) * 2, (db20.shape[1] // 2) * 2
lin40 = np.nanmean(vv_utm[:H2, :W2].reshape(H2 // 2, 2, W2 // 2, 2), axis=(1, 3))
db40 = 10 * np.log10(lin40)
sea40 = sea_utm[:H2:2, :W2:2] > 0.5
u8 = np.where(np.isfinite(db40), np.clip(np.round((db40 + 35) * 255 / 35), 1, 255), 0).astype(np.uint8)
tr40 = tr20 * tr20.scale(2, 2)
write_cog(u8, tr40, CRS_UTM, DATA_DIR / "outputs" / "small" / "sigma0_vv_db_utm48n_40m_u8.tif", nodata=0,
          tags={**tags, "scaling": "dB = value * 35 / 255 - 35; 0 = no data"})

# Figures
FIG_DIR.mkdir(parents=True, exist_ok=True)
acq_utc = pd.Timestamp(meta["acq_utc"])
acq_ict = acq_utc.tz_convert(ICT)
dets_utm = dets.to_crs(CRS_UTM)
aoi_utm = gpd.GeoSeries([aoi_geometry("ca_mau")], crs="EPSG:4326").to_crs(CRS_UTM).iloc[0]
area_km2 = float(main["sea_ok_d"].sum() * (10 * main["mask_factor"]) ** 2 / 1e6)
n_vessel = int(dets.confidence.isin(["high", "medium"]).sum())
title = f"{n_vessel} vessel candidates off Ca Mau, {acq_ict:%d %b %Y %H:%M} local time"
subtitle = (f"Sentinel-1D IW GRD, {meta['pass_dir'].lower()} pass, relative orbit {meta['orbit_rel']}, "
            f"VV backdrop. CA-CFAR baseline over {area_km2:,.0f} km2 of open sea.")
footer = (f"Contains modified Copernicus Sentinel data 2026. Land: ESA WorldCover 2021 v200 (CC BY 4.0). "
          f"Scene {meta['scene_id']}. CA-CFAR PFA {args.pfa:g}, guard 81 px, background 161 px, 1 km shore buffer. "
          f"Fixed = recurs on {len(others)} earlier same-orbit dates.\nAIS not checked. {DARK_CAVEAT_SHORT}")
detection_map(db40, tr40, sea40, dets_utm, title, subtitle, footer, FIG_DIR / "baseline_map.png",
              aoi_utm=aoi_utm)
chip_gallery(scene, dets, ["high", "medium", "fixed", "low"], FIG_DIR / "baseline_chips.png",
             title="What the detector found: 800 m x 800 m chips, VV (left) and VH (right)",
             footer=f"Contains modified Copernicus Sentinel data 2026. {meta['scene_id']}. Length = crude major-axis "
                    f"estimate. {DARK_CAVEAT_SHORT}")

summary = {
    **{k: v for k, v in meta.items() if k != "caveat"},
    "bbox": bbox, "window_px": [int(window.height), int(window.width)], "sea_area_km2": round(area_km2),
    "classes": dets.confidence.value_counts().to_dict(), "n_vessel_candidates": n_vessel,
    "cfar": main["cfar_meta"], "persistence_scenes": others_paths,
    "length_est_m_vessels": dets[dets.confidence.isin(["high", "medium"])].length_est_m.describe().round(1).to_dict(),
    "runtime_main_s": round(main["runtime_s"], 1), "runtime_total_s": round(time.time() - t0, 1),
}
(DATA_DIR / "baseline_run_summary.json").write_text(json.dumps(summary, indent=2, default=str))
print(json.dumps(summary, indent=2, default=str))
