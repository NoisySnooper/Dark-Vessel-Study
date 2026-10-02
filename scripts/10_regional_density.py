"""Vessel-candidate density over the South China Sea from the regional run.

Density = vessel candidates (high + medium; fixed structures and low-confidence clutter excluded)
divided by the sea area observed, summed over scenes, per 0.25 degree cell, x 1,000. A cell seen by
3 scenes with 6 candidates in total has 6 / (3 x its sea area) x 1,000. It is a mean snapshot
density (candidates per 1,000 km2 per look), not a count of distinct vessels: the same boat seen
on 3 passes counts 3 times, against 3 looks of area.

Observed sea = scene footprint inside the AOI, minus Natural Earth land, on a 0.01 degree grid.
The detector also skips a 1 km shore buffer, so coastal cells are slightly under-normalised.

Inputs: data/detections_regional.gpkg (scripts/09_run_regional.py --merge).
Outputs: data/outputs/small/vessel_density_regional_4326.tif and _utm49n.tif
         (COG float32; -1 = not observed or less than 10 % of the cell observed),
         data/regional_density.json, docs/figures/regional_detections.png
Usage: python scripts/10_regional_density.py [--res 0.25]
"""

import argparse
import json

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch
from rasterio import features
from rasterio.warp import Resampling, calculate_default_transform, reproject

from darkvessel.aoi import aoi_gdf, aoi_utm, natural_earth_land
from darkvessel.config import DARK_CAVEAT_SHORT, DATA_DIR, DEFAULT_AOI, FIG_DIR
from darkvessel.coverage import cell_area_km2, grid_for
from darkvessel.io import utm_suffix
from darkvessel.s1.export import write_cog
from darkvessel.viz.style import INK, INK_2, MUTED, apply_matplotlib_style

ap = argparse.ArgumentParser()
ap.add_argument("--res", type=float, default=0.25)
ap.add_argument("--fine", type=float, default=0.01)
args = ap.parse_args()
k = int(round(args.res / args.fine))

aoi = aoi_gdf(DEFAULT_AOI).geometry.iloc[0]
src = DATA_DIR / "detections_regional.gpkg"
det = gpd.read_file(src, layer="detections_regional_4326")
proc = gpd.read_file(src, layer="scenes_processed_4326")
vessels = det[det.confidence.isin(["high", "medium"])]

# Fine grid aligned to the coarse grid: observed sea area per scene look
tr_c, shape_c = grid_for(aoi.bounds, args.res)
shape_f = (shape_c[0] * k, shape_c[1] * k)
tr_f = tr_c * tr_c.scale(1 / k)
west, south, east, north = aoi.bounds
land = natural_earth_land(bbox=(west - 1, south - 1, east + 1, north + 1))
aoi_f = features.rasterize([(aoi, 1)], out_shape=shape_f, transform=tr_f, fill=0, dtype="uint8").astype(bool)
land_f = features.rasterize([(g, 1) for g in land.geometry], out_shape=shape_f, transform=tr_f, fill=0, dtype="uint8").astype(bool)
sea_f = aoi_f & ~land_f
looks_f = np.zeros(shape_f, np.uint16)
for g in proc.geometry:
    looks_f += features.rasterize([(g, 1)], out_shape=shape_f, transform=tr_f, fill=0, dtype="uint16")
area_f = cell_area_km2(tr_f, shape_f)
obs_f = np.where(sea_f, looks_f * area_f, 0.0)


def block_sum(a):
    return a.reshape(shape_c[0], k, shape_c[1], k).sum(axis=(1, 3))


obs_km2 = block_sum(obs_f)  # sea km2 x looks per coarse cell
sea_km2 = block_sum(np.where(sea_f, area_f, 0.0))
looks = np.zeros(shape_c, np.float64)
np.divide(obs_km2, sea_km2, out=looks, where=sea_km2 > 0)  # mean looks over the cell's sea

# Candidate counts per coarse cell
col = np.floor((vessels.lon.values - tr_c.c) / tr_c.a).astype(int)
row = np.floor((vessels.lat.values - tr_c.f) / tr_c.e).astype(int)
ok = (row >= 0) & (row < shape_c[0]) & (col >= 0) & (col < shape_c[1])
counts = np.zeros(shape_c, np.int64)
np.add.at(counts, (row[ok], col[ok]), 1)

cell_km2 = cell_area_km2(tr_c, shape_c)
valid = (obs_km2 > 0) & (sea_km2 >= 0.1 * cell_km2) & (looks >= 0.5)
dens = np.full(shape_c, -1.0, np.float32)
dens[valid] = 1000 * counts[valid] / obs_km2[valid]

lost = int(counts[~valid].sum())
stats = {
    "res_deg": args.res, "fine_res_deg": args.fine, "vessel_candidates": int(len(vessels)),
    "candidates_outside_valid_cells": lost, "cells_valid": int(valid.sum()),
    "observed_sea_km2_looks": round(float(obs_km2.sum())),
    "tested_km2_from_detector": round(float(proc.tested_km2.sum())),
    "density_per_1000km2_overall": round(1000 * float(counts[valid].sum()) / float(obs_km2[valid].sum()), 2),
    "density_percentiles_valid_cells": {f"p{p}": round(float(np.percentile(dens[valid], p)), 2) for p in (10, 25, 50, 75, 90, 99)},
    "share_valid_cells_zero": round(float((counts[valid] == 0).mean()), 3),
}
(DATA_DIR / "regional_density.json").write_text(json.dumps(stats, indent=2))
print(json.dumps(stats, indent=2))

# Rasters
tags = {"units": "vessel candidates (high + medium) per 1,000 km2 of sea per look",
        "nodata": "-1 = not observed in the regional run, or less than 10 % of the cell is observed sea",
        "source": "scripts/10_regional_density.py; detections from scripts/09_run_regional.py",
        "caveat": DARK_CAVEAT_SHORT}
small = DATA_DIR / "outputs" / "small"
write_cog(dens, tr_c, "EPSG:4326", small / "vessel_density_regional_4326.tif", nodata=-1, tags=tags)
utm = aoi_utm(DEFAULT_AOI)
t2, w2, h2 = calculate_default_transform("EPSG:4326", utm, shape_c[1], shape_c[0], *aoi.bounds, resolution=25000)
dst = np.full((h2, w2), -1, np.float32)
reproject(dens, dst, src_transform=tr_c, src_crs="EPSG:4326", dst_transform=t2, dst_crs=utm,
          resampling=Resampling.nearest, src_nodata=-1, dst_nodata=-1)
write_cog(dst, t2, utm, small / f"vessel_density_regional_{utm_suffix(utm)}.tif", nodata=-1, tags=tags)

# Map: one sequential hue (reference blue ramp, steps 100 to 700) for magnitude; gray = not imaged
apply_matplotlib_style()
bins = [0, 1e-9, 10, 25, 50, 100, 200, 1e9]
labels = ["0", "under 10", "10-25", "25-50", "50-100", "100-200", "200+"]
colors = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
NOT_IMAGED = "#e4e2dc"
cmap, norm = ListedColormap(colors), BoundaryNorm(bins, len(colors))
fig = plt.figure(figsize=(10, 11.2))
ax = fig.add_axes([0.07, 0.1, 0.9, 0.78])
pad = 0.6
h, w = shape_c
extent = (tr_c.c, tr_c.c + w * tr_c.a, tr_c.f + h * tr_c.e, tr_c.f)
gpd.GeoSeries([aoi], crs="EPSG:4326").plot(ax=ax, color=NOT_IMAGED, edgecolor="none", zorder=0.5)
ax.imshow(np.ma.masked_where(~valid, dens), cmap=cmap, norm=norm, extent=extent, interpolation="nearest", zorder=1)
land.plot(ax=ax, color="#c9c6bd", edgecolor="none", zorder=2)
gpd.GeoSeries([aoi], crs="EPSG:4326").boundary.plot(ax=ax, color=INK, linewidth=0.6, zorder=3)
proc.boundary.plot(ax=ax, color=MUTED, linewidth=0.3, zorder=3)
ax.set_xlim(west - pad, east + pad)
ax.set_ylim(south - pad, north + pad)
ax.set_aspect(1 / np.cos(np.radians((south + north) / 2)))
ax.set_xlabel("Longitude (degrees E)", fontsize=9)
ax.set_ylabel("Latitude (degrees N)", fontsize=9)
ax.grid(True, zorder=0)
handles = [Patch(facecolor=c, edgecolor="none", label=l) for c, l in zip(colors, labels)]
handles.append(Patch(facecolor=NOT_IMAGED, edgecolor="none", label="not imaged in this window"))
handles.append(Patch(facecolor="none", edgecolor=MUTED, linewidth=0.6, label="scene outline"))
ax.legend(handles=handles, title="Candidates per 1,000 km2 per look", loc="lower right", fontsize=9,
          title_fontsize=9, frameon=True, facecolor="#fcfcfb", edgecolor="#e1e0d9")
d0, d1 = (str(x)[:10] for x in (det.acq_utc.min(), det.acq_utc.max()))
fig.text(0.07, 0.965, "Radar vessel candidates: South China Sea", fontsize=15, color=INK, fontweight="bold", va="top")
fig.text(0.07, 0.93, f"{len(vessels):,} candidates (both channels or one strong channel) in {len(proc)} Sentinel-1C/1D "
         f"scenes, {d0} to {d1}, {args.res:g} degree cells.\n"
         f"Fixed structures and weak single-channel returns removed. {DARK_CAVEAT_SHORT}",
         fontsize=10, color=INK_2, va="top")
fig.text(0.07, 0.015, "Contains modified Copernicus Sentinel data 2026 (AWS Open Data mirror). Land mask for detection: "
         "ESA WorldCover 2021 v200 (CC BY 4.0). AOI and land: Natural Earth (public domain).",
         fontsize=7.5, color=MUTED, va="bottom")
FIG_DIR.mkdir(parents=True, exist_ok=True)
fig.savefig(FIG_DIR / "regional_detections.png", dpi=150)
print("wrote", FIG_DIR / "regional_detections.png")
