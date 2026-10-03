"""One night, two sensors: VIIRS lights at about 01:00 local and Sentinel-1 radar contacts at dawn, same sea.

Picks the radar scenes of one Sentinel-1 pass (product_id prefix), takes the VIIRS lights of the night
before it inside the scenes' tested footprint, and maps both side by side. The two looks are hours apart,
so contacts are not matched one to one; the figure shows where lit activity and radar contacts agree.
Counts and a 0.1 degree rank correlation go to data/viirs_radar_pair_<tag>.json.

Inputs: data/detections_regional.gpkg (radar candidates, scenes processed),
data/viirs_lights_all.gpkg (scripts/15_viirs_lights.py --merge).
Output: docs/figures/viirs_radar_<tag>.png
Usage: python scripts/18_viirs_radar_pair.py --pass S1D_IW_GRDH_1SDV_20261001T2309 --night 2026-10-01 --tag gulf_of_thailand
"""

import argparse
import json
import textwrap

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyogrio
import shapely
from matplotlib.lines import Line2D
from scipy.stats import spearmanr

from darkvessel.aoi import natural_earth_land
from darkvessel.config import DARK_CAVEAT_SHORT, DATA_DIR, FIG_DIR
from darkvessel.viz.style import INK, INK_2, MUTED, SERIES_DARK, SERIES_EXTENDED, apply_matplotlib_style

ap = argparse.ArgumentParser()
ap.add_argument("--pass", dest="pass_prefix", required=True, help="product_id prefix of the radar pass (all slices)")
ap.add_argument("--night", required=True, help="VIIRS night (local evening date, UTC+7) before the radar pass")
ap.add_argument("--tag", required=True)
ap.add_argument("--cell", type=float, default=0.1)
args = ap.parse_args()

reg = DATA_DIR / "detections_regional.gpkg"
scenes = gpd.read_file(reg, layer="scenes_processed_4326")
scenes = scenes[scenes.product_id.str.startswith(args.pass_prefix)]
if scenes.empty:
    raise SystemExit(f"no processed scene starts with {args.pass_prefix}")
foot = shapely.union_all(scenes.geometry.to_list()).buffer(0.002).buffer(-0.002)  # close seams between slices
idx = ",".join(str(int(i)) for i in scenes.scene_idx)
radar = pyogrio.read_dataframe(reg, layer="detections_regional_4326", read_geometry=False,
                               where=f"scene_idx IN ({idx}) AND confidence IN ('high', 'medium')",
                               columns=["det_id", "acq_utc", "confidence", "lat", "lon", "length_est_m"])
lights = pyogrio.read_dataframe(DATA_DIR / "viirs_lights_all.gpkg", layer="viirs_lights_4326", read_geometry=False,
                                where=f"night = '{args.night}'",
                                columns=["light_id", "satellite", "time_utc", "lat", "lon", "radiance_nw", "quality", "class"])
inside = shapely.contains_xy(foot, lights.lon.to_numpy(), lights.lat.to_numpy())
lights = lights[inside].reset_index(drop=True)
lit = lights[lights["class"] == "lit_vessel_candidate"]
rec = lights[lights["class"] == "persistent_light"]

t_radar = pd.to_datetime(radar.acq_utc, utc=True)
t_light = pd.to_datetime(lit.time_utc, utc=True)
gap_h = (t_radar.median() - t_light.median()).total_seconds() / 3600 if len(lit) else float("nan")

# Rank correlation of counts on a common grid, cells inside the footprint only
w, s, e, n = foot.bounds
nx, ny = int(np.ceil((e - w) / args.cell)), int(np.ceil((n - s) / args.cell))


def grid(df):
    c = np.clip(((df.lon.to_numpy() - w) / args.cell).astype(int), 0, nx - 1)
    r = np.clip(((df.lat.to_numpy() - s) / args.cell).astype(int), 0, ny - 1)
    g = np.zeros((ny, nx))
    np.add.at(g, (r, c), 1)
    return g


cx, cy = np.meshgrid(w + (np.arange(nx) + 0.5) * args.cell, s + (np.arange(ny) + 0.5) * args.cell)
cell_in = shapely.contains_xy(foot, cx, cy)
gr, gl = grid(radar), grid(lit)
rho = spearmanr(gr[cell_in], gl[cell_in])[0] if cell_in.sum() > 10 else float("nan")
both = (gr > 0) & (gl > 0) & cell_in
summary = {"radar_pass": args.pass_prefix, "radar_scenes": scenes.product_id.tolist(), "viirs_night": args.night,
           "radar_candidates": int(len(radar)), "radar_high": int((radar.confidence == "high").sum()),
           "lit_vessel_candidates": int(len(lit)), "lit_clear": int((lit.quality == "clear").sum()), "recurring_lights": int(len(rec)),
           "median_gap_hours": round(gap_h, 1), "cell_deg": args.cell, "cells_in_footprint": int(cell_in.sum()),
           "spearman_rho_cell_counts": round(float(rho), 3),
           "share_of_lit_cells_with_radar": round(float(both.sum() / max(1, ((gl > 0) & cell_in).sum())), 3),
           "share_of_radar_cells_with_lights": round(float(both.sum() / max(1, ((gr > 0) & cell_in).sum())), 3),
           "caveat": DARK_CAVEAT_SHORT + " Lights and radar contacts are hours apart and are not matched one to one."}
(DATA_DIR / f"viirs_radar_pair_{args.tag}.json").write_text(json.dumps(summary, indent=1))
print(json.dumps(summary, indent=1))

# Figure: same extent, radar left, lights right
apply_matplotlib_style()
pad = 0.15
land = natural_earth_land(bbox=(w - 1, s - 1, e + 1, n + 1))
ratio = (e - w + 2 * pad) * np.cos(np.radians((s + n) / 2)) / (n - s + 2 * pad)
fig, axes = plt.subplots(1, 2, figsize=(float(np.clip(2 * 5.6 * ratio + 2.2, 9.5, 16)), 7.4))
fig.subplots_adjust(left=0.07, right=0.99, top=0.78, bottom=0.12, wspace=0.08)
loc = lambda t: (t + pd.Timedelta(hours=7)).strftime("%d %b %H:%M")  # noqa: E731
for ax in axes:
    land.plot(ax=ax, color="#c9c6bd", edgecolor="none", zorder=1)
    gpd.GeoSeries([foot], crs="EPSG:4326").boundary.plot(ax=ax, color=MUTED, linewidth=0.8, linestyle="--", zorder=2)
    ax.set_xlim(w - pad, e + pad)
    ax.set_ylim(s - pad, n + pad)
    ax.set_aspect(1 / np.cos(np.radians((s + n) / 2)))
    ax.grid(True, zorder=0)
    ax.tick_params(labelsize=8)
    ax.set_xlabel("Longitude (degrees E)", fontsize=8)
axes[0].set_ylabel("Latitude (degrees N)", fontsize=8)
axes[1].set_ylabel("")
hi, me = radar[radar.confidence == "high"], radar[radar.confidence == "medium"]
axes[0].scatter(me.lon, me.lat, s=4, color=SERIES_DARK[1], linewidths=0, zorder=3)
axes[0].scatter(hi.lon, hi.lat, s=4, color=SERIES_DARK[0], linewidths=0, zorder=4)
axes[0].set_title(f"Radar: {scenes.mission.iloc[0]} vessel candidates, {loc(t_radar.median())} UTC+7\n"
                  f"{len(radar):,} candidates ({len(hi):,} in both channels)", fontsize=10.5, color=INK, loc="left")
axes[0].legend(handles=[Line2D([], [], linestyle="none", marker="o", markersize=4, color=SERIES_DARK[0], label="both channels"),
                        Line2D([], [], linestyle="none", marker="o", markersize=4, color=SERIES_DARK[1], label="one channel")],
               loc="lower left", fontsize=8.5, frameon=True, facecolor="#fcfcfb", edgecolor="#e1e0d9")
axes[1].scatter(lit.lon, lit.lat, s=9, facecolor=SERIES_EXTENDED[3], edgecolor=INK, linewidth=0.3, zorder=3)
axes[1].scatter(rec.lon, rec.lat, s=26, facecolor="none", edgecolor=INK, linewidth=0.7, zorder=4)
tl = f"{loc(t_light.min())} to {loc(t_light.max())[-5:]}" if len(lit) else "no lights"
axes[1].set_title(f"Night lights: VIIRS, {tl} UTC+7\n{len(lit):,} lights that do not recur, {len(rec):,} recurring",
                  fontsize=10.5, color=INK, loc="left")
axes[1].legend(handles=[Line2D([], [], linestyle="none", marker="o", markersize=5, markerfacecolor=SERIES_EXTENDED[3],
                               markeredgecolor=INK, markeredgewidth=0.4, label="light at sea"),
                        Line2D([], [], linestyle="none", marker="o", markersize=6, markerfacecolor="none", markeredgecolor=INK,
                               label="recurring light (structure)")],
               loc="lower left", fontsize=8.5, frameon=True, facecolor="#fcfcfb", edgecolor="#e1e0d9")
fig.text(0.07, 0.965, "One night, two sensors", fontsize=15, color=INK, fontweight="bold", va="top")
fig_w = fig.get_size_inches()[0]
sub_txt = (f"Same sea (dashed: radar footprint). The looks are {gap_h:.1f} hours apart, so contacts are not matched one to one. "
           f"Counts per {args.cell:g} degree cell: Spearman rho {rho:.2f}. Radar sees hulls, lit or not; VIIRS sees lights, "
           f"boat or not. {DARK_CAVEAT_SHORT}")
fig.text(0.07, 0.928, textwrap.fill(sub_txt, int(fig_w * 12.5)), fontsize=9.5, color=INK_2, va="top")
fig.text(0.07, 0.012, textwrap.fill("Contains modified Copernicus Sentinel data 2026 (AWS Open Data mirror). VIIRS DNB: NOAA JPSS on the "
                                     "AWS Open Data Registry. Land: Natural Earth (public domain). No maritime boundaries or claims are drawn.",
                                     int(fig_w * 16)), fontsize=7.5, color=MUTED, va="bottom")
FIG_DIR.mkdir(parents=True, exist_ok=True)
fig.savefig(FIG_DIR / f"viirs_radar_{args.tag}.png", dpi=150)
print("wrote", FIG_DIR / f"viirs_radar_{args.tag}.png")
