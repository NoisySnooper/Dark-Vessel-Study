"""Sentinel-1 coverage of the AOI: passes per 0.05 degree cell over the search window.

Inputs: data/s1_footprints<sfx>.gpkg from 02_search_scenes.py.
Outputs: data/outputs/small/s1_passes<sfx>_4326.tif and _utm49n.tif (COG, uint16),
data/s1_coverage<sfx>.json, docs/figures/coverage<sfx>.png.
Usage: python scripts/08_coverage.py [--aoi south_china_sea] [--res 0.05]
"""

import argparse
import datetime as dt
import json

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch
from rasterio.warp import Resampling, calculate_default_transform, reproject

from darkvessel.aoi import aoi_gdf, aoi_utm, natural_earth_land
from darkvessel.config import AOIS, DATA_DIR, DEFAULT_AOI, FIG_DIR, aoi_suffix
from darkvessel.coverage import coverage_stats, merge_passes, pass_counts
from darkvessel.io import utm_suffix
from darkvessel.s1.export import write_cog
from darkvessel.viz.style import INK, INK_2, MUTED, apply_matplotlib_style

ap = argparse.ArgumentParser()
ap.add_argument("--aoi", default=DEFAULT_AOI, choices=sorted(AOIS))
ap.add_argument("--res", type=float, default=0.05)
args = ap.parse_args()
sfx = aoi_suffix(args.aoi)

a = aoi_gdf(args.aoi)
aoi = a.geometry.iloc[0]
fp = gpd.read_file(DATA_DIR / f"s1_footprints{sfx}.gpkg", layer="s1_footprints_4326")
summary = json.loads((DATA_DIR / f"s1_search_summary{sfx}.json").read_text())
start, end = [s.strip() for s in summary["window"].split(" to ")]
days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days

passes = merge_passes(fp)
counts, mask, tr = pass_counts(passes, aoi, args.res)
stats = coverage_stats(counts, mask, tr, days)
stats.update({"aoi": args.aoi, "window": summary["window"], "res_deg": args.res, "n_products": int(len(fp)),
              "n_passes": int(len(passes)), "passes_by_mission": passes["mission"].value_counts().to_dict(),
              "passes_by_direction": passes["pass_dir"].value_counts().to_dict()})
(DATA_DIR / f"s1_coverage{sfx}.json").write_text(json.dumps(stats, indent=2))
print(json.dumps(stats, indent=2))

# Rasters: counts inside the AOI, 65535 outside
out = np.where(mask, counts, 65535).astype(np.uint16)
tags = {"units": f"Sentinel-1 IW passes between {start} and {end}", "nodata": "65535 = outside AOI",
        "source": "Footprints from the AWS Open Data mirror of Sentinel-1 GRD"}
small = DATA_DIR / "outputs" / "small"
write_cog(out, tr, "EPSG:4326", small / f"s1_passes{sfx}_4326.tif", nodata=65535, tags=tags)
utm = aoi_utm(args.aoi)
t2, w2, h2 = calculate_default_transform("EPSG:4326", utm, out.shape[1], out.shape[0], *aoi.bounds, resolution=5000)
dst = np.full((h2, w2), 65535, np.uint16)
reproject(out, dst, src_transform=tr, src_crs="EPSG:4326", dst_transform=t2, dst_crs=utm,
          resampling=Resampling.nearest, src_nodata=65535, dst_nodata=65535)
write_cog(dst, t2, utm, small / f"s1_passes{sfx}_{utm_suffix(utm)}.tif", nodata=65535, tags=tags)

# Map
apply_matplotlib_style()
bins = [0, 1, 3, 6, 11, 21, 10_000]
labels = ["not imaged", "1-2", "3-5", "6-10", "11-20", "21+"]
colors = ["#e4e2dc", "#b7d3f6", "#86b6ef", "#5598e7", "#256abf", "#104281"]  # gray + sequential blue steps
cmap, norm = ListedColormap(colors), BoundaryNorm(bins, len(colors))
fig = plt.figure(figsize=(10, 11.2))
ax = fig.add_axes([0.07, 0.1, 0.9, 0.78])
west, south, east, north = aoi.bounds
pad = 0.6
land = natural_earth_land(bbox=(west - pad, south - pad, east + pad, north + pad))
h, w = counts.shape
extent = (tr.c, tr.c + w * tr.a, tr.f + h * tr.e, tr.f)
ax.imshow(np.ma.masked_where(~mask, counts), cmap=cmap, norm=norm, extent=extent, interpolation="nearest", zorder=1)
land.plot(ax=ax, color="#c9c6bd", edgecolor="none", zorder=2)
gpd.GeoSeries([aoi], crs="EPSG:4326").boundary.plot(ax=ax, color=INK, linewidth=0.6, zorder=3)
ax.set_xlim(west - pad, east + pad)
ax.set_ylim(south - pad, north + pad)
ax.set_aspect(1 / np.cos(np.radians((south + north) / 2)))
ax.set_xlabel("Longitude (degrees E)", fontsize=9)
ax.set_ylabel("Latitude (degrees N)", fontsize=9)
ax.grid(True, zorder=0)
handles = [Patch(facecolor=c, edgecolor="none", label=l) for c, l in zip(colors, labels)]
ax.legend(handles=handles, title=f"Passes in {days} days", loc="lower right", fontsize=9, title_fontsize=9,
          frameon=True, facecolor="#fcfcfb", edgecolor="#e1e0d9")
pct = lambda v: f"{100 * v:.0f}%"  # noqa: E731
fig.text(0.07, 0.965, "Where Sentinel-1 looked: South China Sea" if args.aoi == "south_china_sea" else f"Sentinel-1 coverage: {AOIS[args.aoi]['label']}",
         fontsize=15, color=INK, fontweight="bold", va="top")
fig.text(0.07, 0.93, f"Sentinel-1C/1D IW passes per {args.res:g} degree cell, {start} to {end}. "
         f"{pct(stats['share_imaged_ge_1'])} of the AOI imaged at least once; "
         f"{stats['area_never_imaged_km2'] / 1e6:.2f} million km2 never imaged.", fontsize=10, color=INK_2, va="top")
fig.text(0.07, 0.015, "Footprints: AWS Open Data mirror of Sentinel-1 GRD (contains modified Copernicus Sentinel data 2026). "
         "AOI and land: Natural Earth (public domain).", fontsize=7.5, color=MUTED, va="bottom")
FIG_DIR.mkdir(parents=True, exist_ok=True)
fig.savefig(FIG_DIR / f"coverage{sfx}.png", dpi=150)
print("wrote", FIG_DIR / f"coverage{sfx}.png")
