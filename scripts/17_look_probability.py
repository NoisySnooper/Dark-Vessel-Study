"""Chance that Sentinel-1 looks at each cell of the AOI within 1, 7 and 30 days (coverage term of paper 2).

For each 0.05 degree cell and each start day of the 90-day window, the cell counts as seen if any Sentinel-1
IW pass covers it within the next k days. The probability is the share of start days on which it is seen
(src/darkvessel/coverage.py, look_probability). A vessel that stays in one cell for k days is imaged with at
least this probability; a moving vessel samples several cells.

Inputs: data/s1_footprints.gpkg, data/s1_search_summary.json (scripts/02_search_scenes.py).
Outputs: data/outputs/small/s1_look_prob_{1,7,30}d_4326.tif and _utm49n.tif (COG, uint8 percent, 255 outside
the AOI), data/s1_look_probability.json, docs/figures/look_probability.png.
Usage: python scripts/17_look_probability.py
"""

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
from darkvessel.config import DATA_DIR, DEFAULT_AOI, FIG_DIR
from darkvessel.coverage import cell_area_km2, look_probability, merge_passes
from darkvessel.io import utm_suffix
from darkvessel.s1.export import write_cog
from darkvessel.viz.style import INK, INK_2, MUTED, apply_matplotlib_style

WINDOWS = (1, 7, 30)

aoi = aoi_gdf(DEFAULT_AOI).geometry.iloc[0]
fp = gpd.read_file(DATA_DIR / "s1_footprints.gpkg", layer="s1_footprints_4326")
summary = json.loads((DATA_DIR / "s1_search_summary.json").read_text())
start, end = [dt.date.fromisoformat(s.strip()) for s in summary["window"].split(" to ")]
days = (end - start).days
passes = merge_passes(fp)
prob, mask, tr = look_probability(passes, aoi, start, days, WINDOWS)
area = cell_area_km2(tr, mask.shape)
a_tot = float(area[mask].sum())

stats = {"window": summary["window"], "days": days, "passes": int(len(passes)), "res_deg": 0.05, "by_window": {}}
for k, p in prob.items():
    w = area[mask]
    v = p[mask]
    stats["by_window"][f"{k}d"] = {
        "aoi_mean": round(float((v * w).sum() / a_tot), 4),
        "share_of_aoi_p_ge_50pct": round(float(w[v >= 0.5].sum() / a_tot), 4),
        "share_of_aoi_p_eq_100pct": round(float(w[v >= 0.999].sum() / a_tot), 4),
        "share_of_aoi_p_eq_0": round(float(w[v == 0].sum() / a_tot), 4),
    }
(DATA_DIR / "s1_look_probability.json").write_text(json.dumps(stats, indent=2))
print(json.dumps(stats, indent=2))

small = DATA_DIR / "outputs" / "small"
utm = aoi_utm(DEFAULT_AOI)
for k, p in prob.items():
    out = np.where(mask, np.round(100 * p), 255).astype(np.uint8)
    tags = {"units": f"percent of start days on which a Sentinel-1 IW pass covers the cell within {k} day(s), {summary['window']}",
            "nodata": "255 = outside AOI", "source": "scripts/17_look_probability.py; footprints from the AWS Open Data mirror of Sentinel-1 GRD"}
    write_cog(out, tr, "EPSG:4326", small / f"s1_look_prob_{k}d_4326.tif", nodata=255, tags=tags)
    t2, w2, h2 = calculate_default_transform("EPSG:4326", utm, out.shape[1], out.shape[0], *aoi.bounds, resolution=5000)
    dst = np.full((h2, w2), 255, np.uint8)
    reproject(out, dst, src_transform=tr, src_crs="EPSG:4326", dst_transform=t2, dst_crs=utm,
              resampling=Resampling.nearest, src_nodata=255, dst_nodata=255)
    write_cog(dst, t2, utm, small / f"s1_look_prob_{k}d_{utm_suffix(utm)}.tif", nodata=255, tags=tags)

# Figure: three panels, one sequential hue (reference blue ramp); gray = never seen in the window
apply_matplotlib_style()
bins = [0, 1e-9, 0.1, 0.25, 0.5, 0.75, 0.999, 1.01]
labels = ["0 (never)", "under 10 %", "10-25 %", "25-50 %", "50-75 %", "75-99 %", "100 %"]
colors = ["#e4e2dc", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#0d366b"]
cmap, norm = ListedColormap(colors), BoundaryNorm(bins, len(colors))
west, south, east, north = aoi.bounds
pad = 0.4
land = natural_earth_land(bbox=(west - pad, south - pad, east + pad, north + pad))
h, w = mask.shape
extent = (tr.c, tr.c + w * tr.a, tr.f + h * tr.e, tr.f)
fig, axes = plt.subplots(1, 3, figsize=(15, 7.6))
fig.subplots_adjust(left=0.05, right=0.99, top=0.83, bottom=0.21, wspace=0.08)
for ax, (k, p) in zip(axes, prob.items()):
    ax.imshow(np.ma.masked_where(~mask, p), cmap=cmap, norm=norm, extent=extent, interpolation="nearest", zorder=1)
    land.plot(ax=ax, color="#c9c6bd", edgecolor="none", zorder=2)
    gpd.GeoSeries([aoi], crs="EPSG:4326").boundary.plot(ax=ax, color=INK, linewidth=0.5, zorder=3)
    ax.set_xlim(west - pad, east + pad)
    ax.set_ylim(south - pad, north + pad)
    ax.set_aspect(1 / np.cos(np.radians((south + north) / 2)))
    ax.tick_params(labelsize=8)
    ax.grid(True, zorder=0)
    ax.set_xlabel("Longitude (degrees E)", fontsize=8)
    ax.set_ylabel("Latitude (degrees N)" if ax is axes[0] else "", fontsize=8)
    m = stats["by_window"][f"{k}d"]["aoi_mean"]
    ax.set_title(f"Within {k} day{'s' if k > 1 else ''}: AOI mean {100 * m:.0f} %", fontsize=11, color=INK, loc="left")
handles = [Patch(facecolor=c, edgecolor="none", label=l) for c, l in zip(colors, labels)]
fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.05), ncol=7, fontsize=9, frameon=False,
           title="Chance that Sentinel-1 images the cell", title_fontsize=9)
fig.text(0.04, 0.965, "How soon does free Sentinel-1 look? South China Sea", fontsize=15, color=INK, fontweight="bold", va="top")
fig.text(0.04, 0.925, f"Share of start days in {summary['window']} on which at least one Sentinel-1C/1D IW pass covers the "
         f"0.05 degree cell within 1, 7 or 30 days ({len(passes)} passes).", fontsize=10, color=INK_2, va="top")
fig.text(0.04, 0.015, "Footprints: AWS Open Data mirror of Sentinel-1 GRD (contains modified Copernicus Sentinel data 2026). "
         "AOI and land: Natural Earth (public domain). No maritime boundaries or claims are drawn.",
         fontsize=7.5, color=MUTED, va="bottom")
FIG_DIR.mkdir(parents=True, exist_ok=True)
fig.savefig(FIG_DIR / "look_probability.png", dpi=150)
print("wrote", FIG_DIR / "look_probability.png")
