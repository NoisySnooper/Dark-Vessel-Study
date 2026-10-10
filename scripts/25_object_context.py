"""Ocean context at every object: radar candidates and clutter classes, the Ca Mau detail detections and VIIRS lights.

For each object the sea at its position and time (src/darkvessel/ocean/context.py):
  keys     cell_id (r<row>c<col> on the 0.25 degree model grid; joins the Cell record) and region (reporting box)
  static   depth_m, dist_coast_km, dist_port_km (containing 0.01 degree cell of the COGs of scripts/22_static_layers.py)
           and ship_presence_<type> for the six World Bank/IMF vessel types: True when the published value of the
           object's 0.01 degree cell is above 0 (any AIS record of that type, 2015 to 2021), False at 0. Presence only:
           many published values cannot be counts, so no magnitude or threshold is used (rule text in the JSON)
  daily    sst_c and sst_grad on the object's UTC date, dist_front_km to the nearest front pixel of that day, chl_log10,
           current_speed_ms and mld_m (RTOFS hourly nowcast within 2 h, nearest RTOFS sea cell within 10 km) and
           wave_hs_m (GFS-Wave within 3 h, bilinear), each with the valid time of the source it came from
           (scripts/23_daily_ocean.py caches, darkvessel.ocean.daily, fronts)
The summary compares the classes: lit vessel candidates against recurring lights against radar candidates against the
radar clutter classes (fixed, clutter zone, near fixed; low classes of the detail scene), with denominators, and gives
the fill rate of every field. EEZ attributes are not object context (the product reads them from the Cell record).

Method. Objects are read with their own ids (det_id, light_id). Rasters are sampled at the containing cell; daily
layers by UTC date; RTOFS and waves by the object's whole hour. With --fetch the RTOFS hours and GFS-Wave files that
are missing from the caches (the radar pass hours) are read from the anonymous NOAA buckets and cached.

Research only, on request (Global Fishing Watch, CC BY-NC 4.0): GFW's Sentinel-1 vessel detections, which carry a
neural-network fishing score (Likely fishing, Likely non fishing, Unknown) and an AIS match flag, against our regional
radar classes, per object. --gfw auto reads the 4Wings report cells pulled by scripts/27_gfw_pull.py
(data/research/gfw_sar_detections.parquet, hourly cells with the matched flag; gfw_sar_detections_by_neural_type.parquet)
and asks, for every one of our radar objects, whether GFW detected something in the same 0.01 degree cell at the same
hour and what its model called it. --gfw FILE takes a Data Download Portal export (one row per detection with
fishing_score, matched, length_m) and matches detections within --radius-m on the same pass. Written under
data/research/ only, kept out of the product files. The GFW API needs a token; none is read here.

Inputs: data/detections_regional_all.gpkg (else data/detections_regional.gpkg), data/detections_baseline.gpkg,
        data/viirs_lights_all.gpkg (else data/viirs_lights.gpkg), data/outputs/small/{depth_m,dist_coast_km,dist_port_km,
        ship_density_<type>}_4326.tif, data/cache/ocean/{mur,fronts,chl,rtofs,gfswave}/
Output: docs/figures/ocean_context_classes.png, data/ocean_context_objects.parquet (object_type radar|radar_detail|viirs, object_id, group, keys, the fields,
        source times, caveat on every row), data/ocean_context_objects.json (fill rates, medians and shares by class
        with denominators, rules, sources, caveat); with --gfw: data/research/gfw_sar_objects.parquet and
        gfw_sar_object_comparison.json
Usage: python scripts/25_object_context.py --fetch [--static-only] [--figure-only] [--gfw auto|FILE] [--radius-m 500]
       Without --fetch the cached hours are used and the radar pass hours stay without currents (the daily build
       caches RTOFS at 18 UTC only).
"""

import argparse
import json
import time
from pathlib import Path

import darkvessel  # noqa: F401  sets PROJ_DATA before rasterio is imported
import pandas as pd

from darkvessel.config import DATA_DIR, DEFAULT_AOI
from darkvessel.ocean import context as cx
from darkvessel.ocean.grid import SMALL_DIR

ap = argparse.ArgumentParser()
ap.add_argument("--static-only", action="store_true", help="skip the daily layers")
ap.add_argument("--fetch", action="store_true", help="fetch missing RTOFS hours and GFS-Wave files (anonymous NOAA buckets)")
ap.add_argument("--gfw", default=None, help="'auto' (cells pulled by scripts/27_gfw_pull.py) or a GFW detections export file; research only")
ap.add_argument("--radius-m", type=float, default=500.0, help="GFW point match radius for a per-detection export")
ap.add_argument("--figure-only", action="store_true", help="redraw docs/figures/ocean_context_classes.png from the JSON")
args = ap.parse_args()
t0 = time.time()
log = lambda m: print(f"[{time.time() - t0:6.0f}s] {m}", flush=True)  # noqa: E731
RESEARCH = DATA_DIR / "research"
FIG = Path(__file__).resolve().parents[1] / "docs" / "figures" / "ocean_context_classes.png"
OUT_JSON = DATA_DIR / "ocean_context_objects.json"


def figure(by_class: list[dict], path: Path = FIG) -> None:
    """Four panels, one row per class: median depth, median distance to coast, share in water with any AIS record
    (World Bank/IMF presence, 2015 to 2021) and share within 10 km of a front, each over the rows with a value."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    colours = {"radar": "#2a78d6", "radar_detail": "#eb6834", "viirs": "#1baf7a"}
    names = {"radar": "regional radar", "radar_detail": "Ca Mau radar", "viirs": "VIIRS light"}
    rows = sorted(by_class, key=lambda r: (["viirs", "radar", "radar_detail"].index(r["object_type"]), -r["n"]))
    labels = [f"{names[r['object_type']]}: {r['group'].replace('_', ' ')} (n {r['n']:,})" for r in rows]
    panels = [("depth_m_median", "depth_m_n", "Median depth (m)", 1.0),
              ("dist_coast_km_median", "dist_coast_km_n", "Median distance to coast (km)", 1.0),
              ("ship_presence_all_share", "ship_presence_all_n", "Share in water with any AIS record,\n2015 to 2021 (%)", 100.0),
              (f"within_{int(cx.FRONT_NEAR_KM)}km_of_front_share", "dist_front_km_n", "Share within 10 km of an SST front\non the day (%)", 100.0)]
    ink, muted, grid = "#0b0b0b", "#52514e", "#e4e3df"
    fig, axes = plt.subplots(1, 4, figsize=(15, 0.42 * len(rows) + 1.9), sharey=True)
    y = np.arange(len(rows))[::-1]
    for ax, (key, nkey, title, scale) in zip(axes, panels):
        vals = np.array([np.nan if r.get(key) is None else r[key] * scale for r in rows], float)
        ax.barh(y, np.nan_to_num(vals), height=0.62, color=[colours[r["object_type"]] for r in rows], edgecolor="white", linewidth=2)
        for yi, v in zip(y, vals):
            ax.text((0 if np.isnan(v) else v) + np.nanmax(vals) * 0.02, yi, "no value" if np.isnan(v) else f"{v:,.0f}" if v >= 10 else f"{v:.1f}",
                    va="center", fontsize=8, color=muted)
        ax.set_title(title, fontsize=10, color=ink, loc="left")
        ax.set_xlim(0, np.nanmax(vals) * 1.18)
        ax.grid(axis="x", color=grid, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color(grid)
        ax.spines["bottom"].set_color(grid)
        ax.tick_params(colors=muted, labelsize=8)
    axes[0].set_yticks(y)
    axes[0].set_yticklabels(labels, fontsize=8.5, color=ink)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in colours.values()]
    fig.legend(handles, [names[k] for k in colours], loc="lower center", ncol=3, frameon=False, fontsize=9)
    fig.suptitle("Ocean context by object class, September 2026 (scripts/25_object_context.py)", x=0.01, ha="left", fontsize=11, color=ink)
    fig.text(0.01, 0.005, "Context describes the sea at the object, not what the object is or does. Dark = no AIS match, not evidence "
             "of illegal activity. AIS record = World Bank/IMF shipping value above 0 (presence only).", fontsize=7.5, color=muted)
    fig.tight_layout(rect=(0, 0.05, 1, 0.95))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


if args.figure_only:
    figure(json.loads(OUT_JSON.read_text())["by_class"])
    log(f"wrote {FIG}")
    raise SystemExit(0)

obj = cx.load_objects(DATA_DIR)
log(f"{len(obj)} objects: " + ", ".join(f"{k} {v}" for k, v in obj.object_type.value_counts().items()) + f"; sources {obj.attrs['sources']}")

keys = cx.cell_keys(obj.lon.to_numpy(float), obj.lat.to_numpy(float)).set_index(obj.index)
static, smeta = cx.static_context(obj, SMALL_DIR)
log(f"static: layers present {[k for k, v in smeta['layers'].items() if v.get('present')]}; outside grid "
    f"{int((~static.in_aoi_grid).sum())}; outside the model grid {int(keys.cell_id.isna().sum())}")

if args.static_only:
    daily, dmeta = pd.DataFrame(index=obj.index), {"skipped": True}
else:
    daily, dmeta = cx.daily_context(obj, fetch=args.fetch, log=log)
    for f in cx.DAILY_FIELDS:
        log(f"daily {f}: {int(daily[f].notna().sum())} of {len(daily)} filled")
    if dmeta["missing"]:
        log(f"daily: {len(dmeta['missing'])} missing items, first: {dmeta['missing'][:5]}")

ctx = pd.concat([obj, keys, static, daily], axis=1)
ctx["caveat"] = cx.CONTEXT_CAVEAT
ctx = cx.shrink(ctx)
ctx["time_utc"] = ctx.time_utc.dt.tz_convert("UTC")
out_parquet = DATA_DIR / "ocean_context_objects.parquet"
ctx.to_parquet(out_parquet, index=False, compression="zstd")
log(f"wrote {out_parquet.name}: {len(ctx)} rows, {out_parquet.stat().st_size / 1e6:.1f} MB")

by_class = cx.summarise(ctx)


def _sources():
    """Source records (URL, version, licence, resolved status) of the layers read here, from the producers' summaries.
    The Marine Regions EEZ record is left out: EEZ attributes are not object context."""
    out = []
    for name in ("ocean_static_summary.json", "ocean_daily_summary.json"):
        path = DATA_DIR / name
        if not path.exists():
            out.append({"name": name, "missing": True})
            continue
        for rec in json.loads(path.read_text()).get("sources", []):
            label = (str(rec.get("key", "")) + " " + str(rec.get("name", ""))).lower()
            if "marine regions" in label or "marineregions" in label or "eez" in label:
                continue
            out.append({**rec, "from": f"data/{name}"})
    return out


fill_fields = (["cell_id"] + list(cx.STATIC_LAYERS) + list(cx.PRESENCE_FIELDS) + list(cx.DAILY_FIELDS)
               + ["sst_time", "chl_time", "current_time", "wave_time"])
fill = cx.fill_rates(ctx, fill_fields)
for f, r in fill.items():
    log(f"fill {f}: {r['all']['filled']} of {r['all']['n']} ({r['all']['share']:.1%})")
summary = {
    "generated_utc": pd.Timestamp.now("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"), "script": "scripts/25_object_context.py", "aoi": DEFAULT_AOI,
    "objects": {str(k): int(v) for k, v in ctx.object_type.value_counts().items()}, "object_sources": obj.attrs["sources"],
    "groups": {str(k): int(v) for k, v in ctx.groupby(["object_type", "group"], observed=True).size().items()},
    "object_date": "the UTC date of the observation (for VIIRS this equals the local night date used in viirs_lights)",
    "static": smeta,
    "daily": {k: v for k, v in dmeta.items() if k != "days"}, "daily_by_day": dmeta.get("days", {}),
    "fill_rates": fill,
    "time_matching": {"sst_c, sst_grad, dist_front_km, chl_log10": "the daily layer of the object's UTC date (MUR SST valid 09:00 UTC; "
                                                                    "OISST fallback valid 12:00 UTC; chlorophyll daily composite)",
                      "current_speed_ms, mld_m": f"RTOFS hourly nowcast nearest to the object's whole hour within {cx.MAX_HOURS_RTOFS:g} h "
                                                  f"(current_time), value of the nearest RTOFS sea cell within {cx.RTOFS_MAX_KM:g} km",
                      "wave_hs_m": f"GFS-Wave analysis or short forecast nearest to the object's whole hour within {cx.MAX_HOURS_WAVE:g} h "
                                   "(wave_time), bilinear with NaN (land) neighbours dropped"},
    "rules": {"ship_presence": cx.PRESENCE_RULE, "within_10km_of_front": f"dist_front_km <= {cx.FRONT_NEAR_KM:g}",
              "shallower_than_50m, shallower_than_200m": "depth_m < 50, depth_m < 200", "within_20km_of_coast": "dist_coast_km <= 20",
              "shares": "every share is over the rows of the class with a value; its denominator is the matching <field>_n"},
    "columns": {"object_type": "radar (regional Sentinel-1C/1D objects), radar_detail (Ca Mau scene), viirs (lights at sea)",
                "object_id": "det_id (radar) or light_id (viirs); joins data/weather_context.parquet and the GeoPackages",
                "group": "radar: high, medium (vessel candidates), fixed, clutter_zone, near_fixed, weak_vv_only, oversized; "
                         "viirs: lit_vessel_candidate_clear, lit_vessel_candidate_under_cloud, persistent_light",
                "depth_m": "GEBCO_2026 mean depth of the 0.01 degree cell, m positive down",
                "dist_coast_km, dist_port_km": "great-circle distance to the Natural Earth coastline and to the nearest major port",
                "cell_id": "r<row>c<col> on the 0.25 degree model grid (darkvessel.ocean.grid.model_grid); joins the Cell record",
                "region": "reporting box of the object (darkvessel.ocean.grid.REPORTING_BOXES), 'other' outside every box; not a boundary",
                "ship_presence_all, _commercial, _fishing, _oilgas, _passenger, _leisure": cx.PRESENCE_RULE,
                "in_aoi_grid": "False when the object lies outside the static rasters",
                "sst_c, sst_time, sst_source": "SST of the object's UTC date and the valid time and source (mur or oisst) of that field",
                "sst_grad": "SST gradient magnitude, degrees C per km, same day",
                "dist_front_km": "distance to the centre of the nearest front pixel of that day (fronts: hysteresis mask of scripts/23_daily_ocean.py)",
                "chl_log10, chl_time, chl_dataset": "log10 chlorophyll-a (mg m-3) of the day and the ERDDAP dataset it came from",
                "current_speed_ms, mld_m, current_time": "RTOFS depth-averaged current speed and mixed-layer thickness, valid time",
                "wave_hs_m, wave_time": "GFS-Wave significant wave height and its valid time",
                "length_est_m": "radar pixel-extent length (NaN for lights)"},
    "by_class": by_class,
    "sources": _sources(),
    "caveat": cx.CONTEXT_CAVEAT,
}

# ---------------------------------------------------------------- Global Fishing Watch (research only)
gfw_summary = None
if args.gfw is not None:
    RESEARCH.mkdir(parents=True, exist_ok=True)
    radar = ctx[ctx.object_type == "radar"].copy()
    gfw_summary = {"licence": cx.GFW_LICENCE_NOTE, "fishing_rule": cx.GFW_FISHING_RULE, "sources": cx.GFW_SOURCES,
                   "ours": {"objects": int(len(radar)), "groups": {str(k): int(v) for k, v in radar.group.value_counts().items()},
                            "window_utc": [radar.time_utc.min().isoformat(), radar.time_utc.max().isoformat()]}}
    per = None
    if args.gfw == "auto":
        hourly_p, neural_p = RESEARCH / "gfw_sar_detections.parquet", RESEARCH / "gfw_sar_detections_by_neural_type.parquet"
        if not hourly_p.exists():
            log(f"GFW comparison skipped: {hourly_p} not found (run scripts/27_gfw_pull.py --steps sar,outputs first)")
            gfw_summary = None
        else:
            hourly = cx.normalise_gfw_table(pd.read_parquet(hourly_p))
            neural = cx.normalise_gfw_table(pd.read_parquet(neural_p)) if neural_p.exists() else None
            log(f"GFW cells: {len(hourly)} hourly cell rows, {0 if neural is None else len(neural)} daily rows by neural type")
            per, s = cx.gfw_cells_at_objects(radar, hourly, neural, res_deg=0.01, max_hours=1.0)
            gfw_summary["input"] = {"hourly_cells": hourly_p.name, "neural_type_cells": neural_p.name if neural_p.exists() else None,
                                    "gfw_window_utc": [hourly.time_utc.min().isoformat(), hourly.time_utc.max().isoformat()]}
            gfw_summary["same_cell_hour"] = s
            gfw_summary["cells_0p1deg_day"] = cx.gfw_compare_cells(radar, hourly, res_deg=0.1, by_day=True)
            gfw_summary["cells_0p25deg_all_days"] = cx.gfw_compare_cells(radar, hourly, res_deg=0.25, by_day=False)
    else:
        gfw = cx.gfw_load_table(Path(args.gfw))
        log(f"GFW table {args.gfw}: {len(gfw)} rows, columns {list(gfw.columns)}")
        gfw_summary["input"] = {"file": str(args.gfw), "rows": int(len(gfw)), "columns": list(gfw.columns)}
        if "fishing_score" in gfw or (gfw.detections == 1).all():
            per, s = cx.gfw_compare_points(radar, gfw, radius_m=args.radius_m)
            gfw_summary["points"] = s
        gfw_summary["cells_0p1deg_day"] = cx.gfw_compare_cells(radar, gfw, res_deg=0.1, by_day=True)
        gfw_summary["cells_0p25deg_all_days"] = cx.gfw_compare_cells(radar, gfw, res_deg=0.25, by_day=False)
    if gfw_summary is not None:
        if per is not None:
            per["licence"] = "GFW CC BY-NC 4.0, research only"
            cx.shrink(per).to_parquet(RESEARCH / "gfw_sar_objects.parquet", index=False)
        gfw_summary["caveat"] = (cx.CONTEXT_CAVEAT + " GFW detections are model outputs on the same open imagery, not truth; an "
                                 "agreement rate is not a precision. GFW cells are 0.01 degree report cells, read as centres "
                                 "with up to half a cell of positional ambiguity (scripts/27_gfw_pull.py).")
        (RESEARCH / "gfw_sar_object_comparison.json").write_text(json.dumps(gfw_summary, indent=1, default=str))
        log("wrote data/research/gfw_sar_object_comparison.json")
summary["gfw_comparison"] = ("see data/research/gfw_sar_object_comparison.json (research only, CC BY-NC 4.0)" if gfw_summary
                             else "not run: pass --gfw auto after scripts/27_gfw_pull.py, or --gfw FILE; research only, CC BY-NC 4.0")

OUT_JSON.write_text(json.dumps(summary, indent=1, default=str))
figure(by_class)
log(f"wrote {OUT_JSON.name} and {FIG.name}")
cols = ["object_type", "group", "n", "depth_m_median", "dist_coast_km_median", "dist_port_km_median", "ship_presence_all_share",
        "dist_front_km_median", "sst_c_median", "chl_log10_median", "current_speed_ms_median", "wave_hs_m_median"]
tab = pd.DataFrame(by_class)
print(tab[[c for c in cols if c in tab.columns]].to_string(index=False))
log("done")
