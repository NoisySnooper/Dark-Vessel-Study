"""Live AIS reach, latest vessels and tracks, and the Sentinel-1 pass windows over the South China Sea AOI.

Purpose: say where the live AIS feed (aisstream.io, recorded by scripts/26_ais_record.py) actually hears vessels,
what it heard, and when Sentinel-1 passes let live AIS be matched to radar contacts. AIS is the truth that defines
"dark" (a radar contact with no AIS match); where AIS is never heard, "dark" cannot be judged.

Method:
  (a) Reach map on the 0.25 degree model grid (darkvessel.ocean.grid.model_grid): share of recorded UTC hours with at
      least one position in the cell, and distinct MMSI per cell; cells outside the AOI polygon are nodata. Coverage
      is broken down by Natural Earth AOI part, by reporting box (REPORT_BOXES: lon/lat boxes for reporting only, not
      boundaries) and by distance to the coast (data/outputs/small/dist_coast_km_4326.tif when present). Recording gaps
      longer than 10 min are listed.
  (b) vessels_latest: last position per MMSI joined to the latest static data (name, type, size, destination);
      tracks: one simplified line per MMSI over the recording period (positions implying over 80 kn are dropped).
  (c) Sentinel-1 passes from `--lookback-hours` ago to `--horizon-hours` ahead (default 72 h back, 288 h ahead, so
      past passes stay listed for the pipelines that name runs after them): from ESA's published acquisition plan
      KMLs in data/cache/s1_plan/ (newest file wins inside its window; `--fetch-plan` downloads the files the window
      needs from https://sentinels.copernicus.eu/web/sentinel/copernicus/sentinel-1/acquisition-plans), and from the
      12-day repeat cycle applied to data/s1_scenes.csv, with a repeat check on the archive itself. Each pass gets a
      pass_group (same physical pass across sources), the AOI parts it covers, the bbox of its AOI overlap, and the
      share of AOI cells under it where the live feed heard any AIS. The JSON is written atomically (temp file and
      rename) with NaN as null.

Inputs:  data/cache/ais/aisstream/positions/*/??.parquet, data/cache/ais/aisstream/static/*.parquet,
         data/s1_scenes.csv, data/s1_footprints.gpkg, data/cache/s1_plan/*.kml, data/aoi (Natural Earth, cached)
Output:  data/outputs/small/ais_reach_share_{4326,utm49n}.tif, ais_reach_mmsi_{4326,utm49n}.tif
         data/ais_live.gpkg: vessels_latest_*, tracks_*, s1_next_passes_* (EPSG:4326 and UTM 49N), about
         data/ais_live_summary.json, data/s1_next_passes.json
Usage:   python scripts/28_ais_reach.py [--horizon-hours 288] [--lookback-hours 72] [--hours-back N] [--no-plan]
         python scripts/28_ais_reach.py --passes-only [--fetch-plan]   # pass plan only (seconds; the watchdog runs it)

"Dark" never means illegal (darkvessel.config.DARK_CAVEAT). No AIS heard in a cell does not mean no vessel: shore
receivers reach a few tens of kilometres offshore and many boats carry no AIS. An AIS gap is not proof of intent.
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

import darkvessel  # noqa: F401  (sets PROJ_DATA before rasterio/pyogrio)
import geopandas as gpd
import pyogrio
import rasterio
from rasterio import features
from rasterio.warp import Resampling
from shapely.geometry import LineString, MultiPolygon, Polygon, box

from darkvessel.ais import aisstream as ais
from darkvessel.ais import s1_passes
from darkvessel.aoi import aoi_gdf, natural_earth_marine
from darkvessel.config import (AOIS, CRS_EQUAL_AREA, CRS_GEO, CRS_UTM_REGIONAL, DARK_CAVEAT, DARK_CAVEAT_SHORT, DATA_DIR,
                               DEFAULT_AOI)
from darkvessel.io import write_dual_crs
from darkvessel.ocean.grid import SMALL_DIR, aoi_mask, model_grid, sample, write_dual_cog

PLAN_DIR = DATA_DIR / "cache" / "s1_plan"
GPKG = DATA_DIR / "ais_live.gpkg"
SUMMARY = DATA_DIR / "ais_live_summary.json"
PASSES_JSON = DATA_DIR / "s1_next_passes.json"
REACH_TIF = SMALL_DIR / "ais_reach_share_4326.tif"
DIST_COAST_TIF = SMALL_DIR / "dist_coast_km_4326.tif"

# What the aisstream.io pages said when read on 2026-10-08 (https://aisstream.io/, https://aisstream.io/documentation,
# https://aisstream.io/privacypolicy; all three resolved with HTTP 200 on 2026-10-08 23:00 UTC). Quotes are verbatim.
# There is no terms-of-service or data-licence page (aisstream.io/terms, /termsofservice, /terms-of-service, /tos and
# /legal returned HTTP 404 on 2026-10-08).
AISSTREAM_TERMS = {
    "source": "aisstream.io websocket feed, wss://stream.aisstream.io/v0/stream",
    "docs_url": ais.AISSTREAM_DOCS,
    "read_utc": "2026-10-08",
    "provenance_quote": "Our global network of Automatic Identification System (AIS) stations, which power aisstream.io's "
                        "tracking capabilities, allows us to stream much more than just ship positions. (https://aisstream.io/)",
    "cost_quote": "Track ship movements ... from aisstream.io's websocket api in real-time and for free. (https://aisstream.io/)",
    "limits_quote": "Connections per account: 3 subscribed connections. Connections per originating IP: 3 open connections. "
                    "Initial subscription: within 3 seconds. (https://aisstream.io/documentation#limits)",
    "browser_quote": "Direct browser connections are not permitted; proxy only the information your clients need from your "
                     "own server. (https://aisstream.io/documentation)",
    "sla_quote": "The service currently provides no SLA or uptime guarantee, and events are not durably replayed. "
                 "(https://aisstream.io/documentation)",
    "compression_quote": "Beginning in September 2026, uncompressed connections will be subject to per-user bandwidth "
                         "limits, and messages exceeding those limits will be dropped. (https://aisstream.io/documentation)",
    "licence": "UNVERIFIED: no terms-of-service or data-licence page exists on aisstream.io (only a privacy policy about "
               "personal data, https://aisstream.io/privacypolicy). Redistribution and commercial use of the relayed AIS "
               "data are not addressed anywhere on the site. Treat the recording as research input until the owner asks "
               "the operator (Support link on the site) in writing.",
}

# Navigational status codes, ITU-R M.1371 as reproduced by the US Coast Guard Navigation Center
# (https://www.navcen.uscg.gov/ais-class-a-reports).
NAV_STATUS = {0: "under way using engine", 1: "at anchor", 2: "not under command", 3: "restricted manoeuvrability",
              4: "constrained by her draught", 5: "moored", 6: "aground", 7: "engaged in fishing", 8: "under way sailing",
              9: "reserved (HSC)", 10: "reserved (WIG)", 11: "power-driven vessel towing astern", 12: "power-driven vessel pushing ahead",
              13: "reserved", 14: "AIS-SART, MOB or EPIRB active", 15: "undefined"}

# Reporting boxes (west, south, east, north) for coverage statistics only. They are not boundaries and take no position
# on any maritime claim. Ca Mau and the Gulf of Tonkin boxes are the project AOIs in darkvessel.config.AOIS.
REPORT_BOXES = {
    "Pearl River mouth and Hong Kong": (113.0, 21.8, 114.6, 22.9),
    "Luzon west coast and Manila Bay": (119.5, 13.5, 121.0, 16.5),
    "Singapore Strait": (103.4, 1.0, 104.6, 1.5),
    "Bangka Strait": (105.0, -3.3, 106.5, -1.5),
    "Gulf of Thailand, Thai coast": (99.5, 11.5, 102.5, 13.8),
    "Ca Mau detail area": tuple(AOIS["ca_mau"]["bbox"]),
    "Gulf of Tonkin, Vietnamese side": tuple(AOIS["gulf_of_tonkin"]["bbox"]),
    "Central Vietnam coast": (107.5, 11.0, 110.0, 16.5),
    "Paracel Islands area": (110.5, 15.5, 113.0, 17.5),
    "Spratly Islands area": (111.0, 7.0, 117.0, 12.0),
}
DIST_BANDS_KM = [0, 20, 50, 100, 200, 10_000]

ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
ap.add_argument("--horizon-hours", type=float, default=288.0, help="pass plan this far ahead (default 12 days)")
ap.add_argument("--lookback-hours", type=float, default=72.0, help="keep passes that started this long ago")
ap.add_argument("--hours-back", type=float, default=0, help="use only the last N hours of recording (0 = all)")
ap.add_argument("--no-plan", action="store_true", help="ignore the ESA acquisition plan KMLs")
ap.add_argument("--fetch-plan", action="store_true", help="download the plan KMLs the window needs (ESA page)")
ap.add_argument("--passes-only", action="store_true", help="refresh the pass plan only (JSON and s1_next_passes layers)")
ap.add_argument("--simplify-deg", type=float, default=0.0005, help="track simplification tolerance (degrees, about 50 m)")
ap.add_argument("--max-speed-kn", type=float, default=80.0, help="drop track points that imply a faster jump")
ap.add_argument("--out-dir", default=None, help="write every output here instead of data/ (trial runs)")
args = ap.parse_args()
OUT_SMALL = SMALL_DIR
if args.out_dir:
    from pathlib import Path

    _out = Path(args.out_dir).resolve()
    _out.mkdir(parents=True, exist_ok=True)
    GPKG, SUMMARY, PASSES_JSON, OUT_SMALL = _out / GPKG.name, _out / SUMMARY.name, _out / PASSES_JSON.name, _out
t_start = time.time()


def _rel(path) -> str:
    try:
        return str(path.relative_to(DATA_DIR.parent))
    except ValueError:
        return str(path)


log = lambda m: print(f"[{time.time() - t_start:6.0f}s] {m}", flush=True)  # noqa: E731
now = pd.Timestamp.now(tz="UTC").floor("s")
aoi = aoi_gdf(DEFAULT_AOI).geometry.iloc[0]
marine = natural_earth_marine()
aoi_parts = marine[marine["name"].isin(AOIS[DEFAULT_AOI]["natural_earth"])][["name", "geometry"]].reset_index(drop=True)
transform, shape = model_grid()
mask = aoi_mask(transform, shape)
aoi_cells = int(mask.sum())

# ---------------------------------------------------------------------------------------------------------------
# (a) Reach map and coverage breakdown
# ---------------------------------------------------------------------------------------------------------------
if not args.passes_only:
    start = now - pd.Timedelta(hours=args.hours_back) if args.hours_back else None
    positions = ais.load_positions(ais.AIS_CACHE, start=start)
    static = ais.load_static(ais.AIS_CACHE)
    if positions.empty:
        raise SystemExit("no recorded positions under data/cache/ais/aisstream/positions; run scripts/26_ais_record.py first")
    period = (positions.timestamp.min(), positions.timestamp.max())
    hours = ais.recorded_hours(positions)
    log(f"{len(positions)} positions, {positions.mmsi.nunique()} MMSI, {len(static)} static rows, "
        f"{period[0]:%Y-%m-%d %H:%M} to {period[1]:%Y-%m-%d %H:%M} UTC, {len(hours)} hours with data")
    period_text = f"{period[0]:%Y-%m-%d %H:%M} to {period[1]:%Y-%m-%d %H:%M} UTC ({len(hours)} UTC hour(s) with data)"

    share, mmsi_count, _ = ais.reach_grids(positions, transform, shape, hours)
    in_aoi = sample(mask.astype(np.float32), transform, positions.lon.values, positions.lat.values, fill=0) > 0
    reached = int(((share > 0) & mask).sum())
    steady = int(((share >= 0.5) & mask).sum())
    share_f = np.where(mask, share, np.nan)
    mmsi_f = np.where(mask, mmsi_count, np.nan)
    reach_tags = {
        "period": period_text,
        "hours_recorded": str(len(hours)),
        "positions_in_aoi": str(int(in_aoi.sum())),
        "mmsi_in_aoi": str(int(positions.mmsi[in_aoi].nunique())),
        "aoi_cells_reached": f"{reached} of {aoi_cells} ({100 * reached / aoi_cells:.1f} %)",
        "note": "No AIS heard in a cell does not mean no vessel. Shore receivers reach a few tens of kilometres offshore; the "
                "open sea is silent whether or not vessels are there. Many boats carry no AIS.",
        "terms": AISSTREAM_TERMS["licence"],
        "caveat": ais.AIS_REACH_CAVEAT,
        "script": "scripts/28_ais_reach.py",
    }
    src = f"aisstream.io live AIS recorded by scripts/26_ais_record.py, {period_text}"
    cogs = write_dual_cog(share_f, transform, "ais_reach_share", "share of recorded hours with at least one AIS position", src,
                          resampling=Resampling.nearest, tags=dict(reach_tags, layer="share of recorded UTC hours with >= 1 position"),
                          out_dir=OUT_SMALL)
    cogs += write_dual_cog(mmsi_f, transform, "ais_reach_mmsi", "distinct MMSI heard", src, resampling=Resampling.nearest,
                           tags=dict(reach_tags, layer="distinct MMSI heard in the cell over the period"), out_dir=OUT_SMALL)
    log(f"reach: {reached}/{aoi_cells} AOI cells heard at least once, {steady} in half the hours or more; wrote {len(cogs)} COGs")

    # coverage by AOI part, reporting box and distance to the coast
    pos_hour = positions.timestamp.dt.floor("h")

    def _coverage(sel_pos: np.ndarray, cell_mask: np.ndarray) -> dict:
        p = positions[sel_pos]
        cells = int(cell_mask.sum())
        heard = int(((share > 0) & cell_mask).sum())
        return {"positions": int(len(p)), "mmsi": int(p.mmsi.nunique()), "aoi_cells": cells, "cells_heard": heard,
                "share_cells_heard": round(heard / cells, 4) if cells else None,
                "share_hours_with_any_position": round(float(pos_hour[sel_pos].nunique()) / max(len(hours), 1), 4),
                "positions_per_hour": round(len(p) / max(len(hours), 1), 1)}

    pts = gpd.GeoSeries(gpd.points_from_xy(positions.lon, positions.lat), crs=CRS_GEO)
    by_part = {}
    for nm, geom in zip(aoi_parts["name"], aoi_parts.geometry):
        cm = features.rasterize([(geom, 1)], out_shape=shape, transform=transform, dtype="uint8").astype(bool) & mask
        by_part[nm] = _coverage(pts.within(geom).values, cm)
    by_box = {}
    for nm, (w, s_, e, n_) in REPORT_BOXES.items():
        sel = ((positions.lon >= w) & (positions.lon <= e) & (positions.lat >= s_) & (positions.lat <= n_)).values
        cm = features.rasterize([(box(w, s_, e, n_), 1)], out_shape=shape, transform=transform, dtype="uint8").astype(bool) & mask
        by_box[nm] = dict(_coverage(sel, cm), bbox=[w, s_, e, n_])
    by_dist = None
    if DIST_COAST_TIF.exists():
        with rasterio.open(DIST_COAST_TIF) as r:
            dist = r.read(1).astype(np.float32)
            nod = r.nodata
            dist[dist == nod] = np.nan
            d_pos = sample(dist, r.transform, positions.lon.values, positions.lat.values)
            # distance of each model-grid cell centre
            rows, cols = np.indices(shape)
            cx, cy = transform * (cols + 0.5, rows + 0.5)
            d_cell = sample(dist, r.transform, cx.ravel(), cy.ravel()).reshape(shape)
        by_dist = {}
        for lo, hi in zip(DIST_BANDS_KM[:-1], DIST_BANDS_KM[1:]):
            label = f"{lo} to {hi} km" if hi < 10_000 else f"over {lo} km"
            sel = (d_pos >= lo) & (d_pos < hi)
            cm = mask & (d_cell >= lo) & (d_cell < hi)
            by_dist[label] = _coverage(sel, cm)
        by_dist["source"] = (f"{DIST_COAST_TIF.relative_to(DATA_DIR.parent)} (Natural Earth 10 m coastline, distance at the "
                             "position and at each 0.25 degree cell centre)")
    # recording gaps
    t = np.sort(positions.timestamp.values)
    dt_s = np.diff(t) / np.timedelta64(1, "s")
    gaps = [{"from_utc": pd.Timestamp(t[i], tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
             "to_utc": pd.Timestamp(t[i + 1], tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
             "minutes": round(float(dt_s[i]) / 60, 1)} for i in np.where(dt_s > 600)[0]]
    log(f"coverage: {len(by_part)} AOI parts, {len(by_box)} reporting boxes, distance bands {'yes' if by_dist else 'no'}; "
        f"{len(gaps)} recording gap(s) over 10 min")
else:
    positions = None
    share = None
    if REACH_TIF.exists():
        with rasterio.open(REACH_TIF) as r:
            arr = r.read(1)
            share = np.where(arr == r.nodata, 0, arr).astype(np.float32) if arr.shape == shape else None

# ---------------------------------------------------------------------------------------------------------------
# (b) vessels_latest and tracks
# ---------------------------------------------------------------------------------------------------------------
layers = []
if not args.passes_only:
    pos = positions.sort_values(["mmsi", "timestamp"], kind="stable").reset_index(drop=True)
    pos["in_aoi"] = sample(mask.astype(np.float32), transform, pos.lon.values, pos.lat.values, fill=0) > 0
    g = pos.groupby("mmsi", sort=False)
    latest = g.last()[["timestamp", "lon", "lat", "sog_kn", "cog_deg", "heading", "nav_status", "ais_class", "msg_type", "in_aoi"]]
    latest["ship_name"] = g.ship_name.agg(lambda s: s.dropna().iloc[-1] if s.notna().any() else None)
    latest["n_positions"] = g.size()
    latest["first_seen_utc"] = g.timestamp.min()
    latest["ever_in_aoi"] = g.in_aoi.any()
    latest = latest.rename(columns={"timestamp": "last_seen_utc"}).reset_index()
    ls = ais.latest_static(static)
    latest = latest.merge(ls, on="mmsi", how="left")
    latest["name"] = ais.as_text(latest["name"].where(latest["name"].notna(), latest.ship_name))
    latest["nav_status_label"] = latest.nav_status.map(NAV_STATUS)
    latest["mid"] = (latest.mmsi // 1_000_000).astype("int64")  # maritime identification digits (flag), ITU
    latest["gear_beacon_like"] = [ais.gear_beacon_like(n, m) for n, m in zip(latest["name"], latest.mmsi)]
    latest["ais_note"] = "heard by shore receivers; " + DARK_CAVEAT_SHORT
    cols = ["mmsi", "mid", "name", "callsign", "imo", "ais_class", "ship_type", "ship_type_label", "length_m", "width_m", "destination",
            "eta", "last_seen_utc", "first_seen_utc", "n_positions", "sog_kn", "cog_deg", "heading", "nav_status", "nav_status_label",
            "msg_type", "in_aoi", "ever_in_aoi", "static_seen_utc", "gear_beacon_like", "lon", "lat", "ais_note"]
    for c in cols:
        if c not in latest:
            latest[c] = None
    latest = latest[cols]
    for c in ("last_seen_utc", "first_seen_utc", "static_seen_utc"):
        latest[c] = pd.to_datetime(latest[c], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    for c in ("imo", "ship_type", "nav_status"):
        latest[c] = latest[c].astype("float").astype(object).where(latest[c].notna(), None)
    vessels = gpd.GeoDataFrame(latest, geometry=gpd.points_from_xy(latest.lon, latest.lat), crs=CRS_GEO)
    layers += write_dual_crs(vessels, GPKG, "vessels_latest", utm_crs=CRS_UTM_REGIONAL)
    log(f"vessels_latest: {len(vessels)} MMSI ({int(vessels.in_aoi.sum())} last heard inside the AOI); {layers}")

    # tracks: drop points that imply an impossible jump, then one line per MMSI
    lat1, lon1 = np.radians(pos.lat.values), np.radians(pos.lon.values)
    same = pos.mmsi.values[1:] == pos.mmsi.values[:-1]
    dlat, dlon = lat1[1:] - lat1[:-1], lon1[1:] - lon1[:-1]
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1[:-1]) * np.cos(lat1[1:]) * np.sin(dlon / 2) ** 2
    dist_nm = 2 * 6371000 * np.arcsin(np.sqrt(np.clip(a, 0, 1))) / 1852
    dt_h = (pos.timestamp.values[1:] - pos.timestamp.values[:-1]) / np.timedelta64(1, "h")
    with np.errstate(divide="ignore", invalid="ignore"):
        speed = np.where(dt_h > 0, dist_nm / dt_h, np.where(dist_nm > 0.5, np.inf, 0))
    jump = np.concatenate([[False], same & (speed > args.max_speed_kn)])
    pos_clean = pos[~jump]
    log(f"tracks: dropped {int(jump.sum())} points implying more than {args.max_speed_kn:.0f} kn")
    rows = []
    for mmsi, grp in pos_clean.groupby("mmsi", sort=False):
        pts_ = grp[["lon", "lat"]].drop_duplicates()
        if len(pts_) < 2:
            continue
        line = LineString(grp[["lon", "lat"]].values)
        if args.simplify_deg > 0:
            line = line.simplify(args.simplify_deg, preserve_topology=False)
        rows.append({"mmsi": int(mmsi), "ais_class": grp.ais_class.iloc[-1], "n_positions": int(len(grp)),
                     "start_utc": grp.timestamp.iloc[0].strftime("%Y-%m-%dT%H:%M:%SZ"), "end_utc": grp.timestamp.iloc[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "duration_min": round(float((grp.timestamp.iloc[-1] - grp.timestamp.iloc[0]).total_seconds() / 60), 1),
                     "mean_sog_kn": round(float(grp.sog_kn.mean()), 1) if grp.sog_kn.notna().any() else None,
                     "ever_in_aoi": bool(grp.in_aoi.any()), "geometry": line})
    if rows:
        tracks = gpd.GeoDataFrame(rows, geometry="geometry", crs=CRS_GEO)
        tracks = tracks.merge(latest[["mmsi", "name", "ship_type_label", "length_m"]], on="mmsi", how="left")
        tracks["length_km"] = (tracks.to_crs(CRS_UTM_REGIONAL).length / 1000).round(2)
        tracks["ais_note"] = DARK_CAVEAT_SHORT
        tracks = tracks[["mmsi", "name", "ais_class", "ship_type_label", "length_m", "n_positions", "start_utc", "end_utc", "duration_min",
                         "length_km", "mean_sog_kn", "ever_in_aoi", "ais_note", "geometry"]]
        layers += write_dual_crs(tracks, GPKG, "tracks", utm_crs=CRS_UTM_REGIONAL)
    else:
        tracks = gpd.GeoDataFrame(columns=["mmsi", "geometry"], geometry="geometry", crs=CRS_GEO)
    log(f"tracks: {len(tracks)} lines, {tracks.length_km.sum() if len(tracks) else 0:.0f} km in total")

# ---------------------------------------------------------------------------------------------------------------
# (c) Sentinel-1 passes
# ---------------------------------------------------------------------------------------------------------------
win_start = now - pd.Timedelta(hours=args.lookback_hours)
win_end = now + pd.Timedelta(hours=args.horizon_hours)
scenes = pd.read_csv(DATA_DIR / "s1_scenes.csv")
passes = s1_passes.passes_from_scenes(scenes)
check = s1_passes.repeat_check(passes)
log(f"repeat check on the archive: {check['pairs']} pass pairs, {100 * (check['share_within_tolerance'] or 0):.1f} % within "
    f"{check['tolerance_s']:.0f} s of a 12-day multiple, median drift {check['median_abs_drift_s']} s -> {check['verdict']}")
pred = s1_passes.predict_passes(passes, win_start, horizon_h=args.horizon_hours + args.lookback_hours)
footprints = pyogrio.read_dataframe(DATA_DIR / "s1_footprints.gpkg", layer="s1_footprints_4326")
pred_g = s1_passes.attach_footprints(pred, footprints, aoi) if len(pred) else gpd.GeoDataFrame(pred, geometry=[], crs=CRS_GEO)
if len(pred_g):
    pred_g = pred_g[pred_g.geometry.notna() & (pred_g.aoi_overlap_km2.fillna(0) > 0)].copy()
log(f"repeat-cycle prediction: {len(pred_g)} passes over the AOI from {win_start:%Y-%m-%d %H:%M} to {win_end:%Y-%m-%d %H:%M} UTC")

fetch_log = []
if args.fetch_plan and not args.no_plan:
    try:
        fetch_log = s1_passes.fetch_plan(PLAN_DIR, win_start, win_end, log=log)
        log(f"plan fetch: {sum(f['status'] == 'downloaded' for f in fetch_log)} downloaded, "
            f"{sum(f['status'] == 'cached' for f in fetch_log)} cached, {sum(f['status'] == 'failed' for f in fetch_log)} failed")
    except Exception as exc:  # the cached files still give a plan
        log(f"plan fetch failed ({type(exc).__name__}: {exc}); using the cached files")

page_accessed = None  # last successful read of the ESA acquisition-plan page (data/cache/s1_plan/fetch_log.json)
try:
    page_accessed = json.loads((PLAN_DIR / "fetch_log.json").read_text()).get("page_accessed_utc")
except (FileNotFoundError, ValueError, OSError):
    pass
plan_rows = gpd.GeoDataFrame(columns=["start_utc", "stop_utc", "mission", "orbit_rel", "pass_dir", "source", "mode", "polarisation", "geometry"],
                             geometry="geometry", crs=CRS_GEO)
plan_files = []
if not args.no_plan and PLAN_DIR.exists():
    parsed = []
    for k in sorted(PLAN_DIR.glob("*.kml")):
        w = s1_passes.plan_file_window(k.name)
        if w is None or w[2] < win_start or w[1] > win_end:
            continue  # outside the window: not needed
        try:
            plan = s1_passes.parse_acquisition_kml(k)
        except Exception as exc:
            log(f"could not parse {k.name}: {type(exc).__name__}: {exc}")
            continue
        parsed.append((k.name, plan))
        fetched = next((f for f in fetch_log if f["file"] == k.name), None)
        plan_files.append({"file": k.name, "url": f"https://sentinels.copernicus.eu/documents/d/sentinel/{k.stem}",
                           "mission": w[0], "plan_window_utc": [w[1].strftime("%Y-%m-%dT%H:%M:%SZ"), w[2].strftime("%Y-%m-%dT%H:%M:%SZ")],
                           "segments": int(len(plan)),
                           "fetched_utc": (fetched or {}).get("fetched_utc") or pd.Timestamp(k.stat().st_mtime, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")})
    allplan = s1_passes.select_plan_segments(parsed)
    newest = {}
    for f in plan_files:
        if f["mission"] not in newest or f["plan_window_utc"][0] > newest[f["mission"]]:
            newest[f["mission"]] = f["plan_window_utc"][0]
    for f in plan_files:
        f["segments_used"] = int((allplan.plan_file == f["file"]).sum()) if len(allplan) else 0
        if f["plan_window_utc"][0] == newest[f["mission"]]:
            f["role"] = "newest file of its mission: wins inside its window"
        elif f["segments_used"]:
            f["role"] = "older file: used only outside the windows of newer files"
        else:
            f["role"] = "older file: fully superseded inside this window"
    hits = []
    for mission, pm in allplan.groupby("mission"):
        h = s1_passes.plan_passes_over(gpd.GeoDataFrame(pm, geometry="geometry", crs=CRS_GEO), aoi, win_start, win_end, mission)
        if len(h):
            hits.append(h)
    if hits:
        plan_rows = gpd.GeoDataFrame(pd.concat(hits, ignore_index=True), geometry="geometry", crs=CRS_GEO)
        dirs = passes.drop_duplicates(["mission", "orbit_rel"]).set_index(["mission", "orbit_rel"]).pass_dir
        plan_rows["pass_dir"] = [dirs.get((m, int(r)), None) if pd.notna(r) else None for m, r in zip(plan_rows.mission, plan_rows.orbit_rel)]
        inter = plan_rows.geometry.intersection(aoi)
        plan_rows["aoi_overlap_km2"] = gpd.GeoSeries(inter, crs=CRS_GEO).to_crs(CRS_EQUAL_AREA).area.div(1e6).round(1).values
        plan_rows = plan_rows[plan_rows.aoi_overlap_km2 > 0].copy()
    log(f"ESA acquisition plan: {len(allplan)} segments selected from {len(plan_files)} files, {len(plan_rows)} over the AOI in the window")
else:
    log("acquisition plan skipped (--no-plan or data/cache/s1_plan missing); repeat-cycle prediction only")


def _match(a: pd.DataFrame, b: pd.DataFrame, tol_min: float = 30.0) -> list[bool]:
    """Does each row of `a` have a row of `b` (same mission) starting within `tol_min`?"""
    out = []
    for _, r in a.iterrows():
        same = b[(b.mission == r.mission)] if len(b) else b
        out.append(bool(len(same) and (abs((same.start_utc - r.start_utc).dt.total_seconds()) <= tol_min * 60).any()))
    return out


if len(pred_g):
    pred_g["in_esa_plan"] = _match(pred_g, plan_rows) if len(plan_rows) else None
if len(plan_rows):
    plan_rows["in_repeat_prediction"] = _match(plan_rows, pred_g) if len(pred_g) else None


def _to_multi(geom):
    if geom is None:
        return None
    return MultiPolygon([geom]) if isinstance(geom, Polygon) else geom


def _reach_under(geom) -> tuple[int, float | None, float | None]:
    """(AOI cells under the footprint, share of them where live AIS was heard, mean hour share) on the model grid."""
    if geom is None or share is None:
        return 0, None, None
    inter = geom.intersection(aoi)
    if inter.is_empty:
        return 0, None, None
    cm = features.rasterize([(inter, 1)], out_shape=shape, transform=transform, dtype="uint8", all_touched=False).astype(bool) & mask
    n = int(cm.sum())
    if not n:
        return 0, None, None
    return n, round(float((share[cm] > 0).mean()), 4), round(float(share[cm].mean()), 4)


frames = []
if len(plan_rows):
    pr = plan_rows.copy()
    pr["note"] = s1_passes.PASS_NOTE_PLAN
    frames.append(pr)
if len(pred_g):
    rp = pred_g.copy()
    rp["note"] = s1_passes.PASS_NOTE_REPEAT
    frames.append(rp)
pass_cols = ["start_utc", "stop_utc", "mission", "orbit_rel", "pass_dir", "source", "mode", "polarisation", "aoi_overlap_km2",
             "basis_start_utc", "cycles_ahead", "n_scenes", "product_ids", "in_esa_plan", "in_repeat_prediction", "plan_file",
             "pass_group", "status", "aoi_parts", "aoi_overlap_bbox", "aoi_cells_under", "ais_heard_share", "ais_reach_mean",
             "note", "geometry"]
if frames:
    nxt = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=CRS_GEO)
    for c in pass_cols:
        if c not in nxt:
            nxt[c] = None
    nxt["start_utc"] = pd.to_datetime(nxt.start_utc, utc=True)
    nxt["stop_utc"] = pd.to_datetime(nxt.stop_utc, utc=True)
    nxt = nxt[(nxt.stop_utc >= win_start) & (nxt.start_utc <= win_end)]
    nxt = nxt[pass_cols].sort_values(["start_utc", "source"], kind="stable").reset_index(drop=True)
    nxt["geometry"] = nxt.geometry.map(_to_multi)
    nxt = gpd.GeoDataFrame(nxt, geometry="geometry", crs=CRS_GEO)
    nxt["pass_group"] = s1_passes.group_passes(nxt)
    nxt["status"] = np.where(nxt.stop_utc < now, "past", np.where(nxt.start_utc <= now, "in_progress", "upcoming"))
    parts_l, bbox_l, cells_l, heard_l, mean_l = [], [], [], [], []
    for geom in nxt.geometry:
        inter = geom.intersection(aoi) if geom is not None else None
        parts_l.append("; ".join(s1_passes.area_names(inter, aoi_parts)))
        bbox_l.append(",".join(f"{v:.4f}" for v in inter.bounds) if inter is not None and not inter.is_empty else None)
        n, heard, mean = _reach_under(geom)
        cells_l.append(n)
        heard_l.append(heard)
        mean_l.append(mean)
    nxt["aoi_parts"], nxt["aoi_overlap_bbox"], nxt["aoi_cells_under"] = parts_l, bbox_l, cells_l
    nxt["ais_heard_share"], nxt["ais_reach_mean"] = heard_l, mean_l
    b = nxt.geometry.bounds.round(4)
    nxt["bbox"] = [f"{r.minx},{r.miny},{r.maxx},{r.maxy}" for r in b.itertuples()]
    for c in ("start_utc", "stop_utc", "basis_start_utc"):
        nxt[c] = pd.to_datetime(nxt[c], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    for c in ("orbit_rel", "cycles_ahead", "n_scenes"):
        nxt[c] = pd.to_numeric(nxt[c], errors="coerce").astype("float").astype(object).where(pd.notna(nxt[c]), None)
    for c in ("in_esa_plan", "in_repeat_prediction"):
        nxt[c] = nxt[c].map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else str(bool(v)))
    for c in ("mode", "polarisation", "plan_file", "pass_dir", "product_ids"):
        nxt[c] = ais.as_text(nxt[c])
    nxt["ais_note"] = "ais_heard_share = share of AOI cells under the footprint where aisstream heard any AIS; " + DARK_CAVEAT_SHORT
    layers += write_dual_crs(nxt, GPKG, "s1_next_passes", utm_crs=CRS_UTM_REGIONAL)
else:
    nxt = gpd.GeoDataFrame(columns=pass_cols, geometry="geometry", crs=CRS_GEO)
    nxt["bbox"] = []
log(f"s1_next_passes: {len(nxt)} rows ({len(plan_rows)} from the ESA plan, {len(pred_g)} from the repeat cycle), "
    f"{nxt.pass_group.nunique() if len(nxt) else 0} distinct passes")


def _num(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else v


groups = []
for gid, g in nxt.groupby("pass_group", sort=False) if len(nxt) else []:
    plan_g = g[g.source == "esa_plan"]
    ref = plan_g if len(plan_g) else g
    bbs = [[float(x) for x in v.split(",")] for v in ref.aoi_overlap_bbox if v]
    union_bb = [min(b_[0] for b_ in bbs), min(b_[1] for b_ in bbs), max(b_[2] for b_ in bbs), max(b_[3] for b_ in bbs)] if bbs else None
    groups.append({"pass_group": gid, "start_utc": ref.start_utc.min(), "stop_utc": ref.stop_utc.max(), "mission": g.mission.iloc[0],
                   "relative_orbit": int(g.orbit_rel.dropna().iloc[0]) if g.orbit_rel.notna().any() else None,
                   "pass_dir": next((d for d in g.pass_dir if d), None), "sources": sorted(set(g.source)),
                   "status": ref.status.iloc[0], "aoi_overlap_km2": round(float(ref.aoi_overlap_km2.sum()), 1),
                   "aoi_parts": sorted({p for s in ref.aoi_parts if s for p in s.split("; ")}),
                   "aoi_overlap_bbox": union_bb,
                   "ais_heard_share": _num(ref.ais_heard_share.max()), "rows": int(len(g))})
groups.sort(key=lambda x: x["start_utc"])

passes_out = {
    "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "window_start_utc": win_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "window_end_utc": win_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "lookback_hours": args.lookback_hours,
    "horizon_hours": args.horizon_hours,
    "aoi": DEFAULT_AOI,
    "primary_source": "esa_plan" if len(plan_rows) else "repeat_cycle",
    "sources": {
        "esa_plan": {"page": s1_passes.ACQ_PLAN_PAGE, "page_accessed_utc": page_accessed,
                     "files": plan_files,
                     "rule": "per mission, the newest plan file (latest window start) wins inside its window; older files fill only "
                             "outside newer windows",
                     "note": "ESA's published acquisition plan (KML). Plans can still change." if plan_files else "not available in this run"},
        "repeat_cycle": {"basis": "data/s1_scenes.csv and data/s1_footprints.gpkg (Sentinel-1C/1D IW GRD over the AOI)",
                         "rule": "same mission, relative orbit and time of day, 12 days after the latest archived pass",
                         "repeat_check": check, "reference": s1_passes.SENTIWIKI_S1,
                         "reference_quote": "Sentinel-1 is in a near-polar, sun-synchronous orbit with a 12 day repeat cycle and 175 orbits per cycle "
                                            "for a single satellite. ... The reference orbit will be maintained within an Earth-fixed orbital tube of a "
                                            "diameter of 120 m (RMS) during normal operation.",
                         "note": s1_passes.PASS_NOTE_REPEAT},
    },
    "fields": {"pass_group": "same physical pass across sources and plan segments: one mission, time spans within 10 min (id = mission, relative orbit and start of the first row)",
               "status": "past, in_progress or upcoming at generated_utc",
               "aoi_parts": "Natural Earth marine areas of the AOI the footprint covers by at least 100 km2",
               "aoi_overlap_bbox": "lon/lat bbox of the footprint inside the AOI",
               "ais_heard_share": "share of the 0.25 degree AOI cells under the footprint where the live aisstream feed heard at "
                                  "least one position during the recording (data/outputs/small/ais_reach_share_4326.tif); a low "
                                  "value means most radar contacts will be no_coverage, not dark"},
    "n_passes": int(len(nxt)),
    "n_pass_groups": len(groups),
    "passes": [
        {"start_utc": r.start_utc, "stop_utc": r.stop_utc, "mission": r.mission,
         "relative_orbit": int(r.orbit_rel) if r.orbit_rel is not None and not pd.isna(r.orbit_rel) else None,
         "pass_dir": r.pass_dir, "source": r.source, "mode": r.mode, "polarisation": r.polarisation,
         "footprint_bbox": [float(x) for x in r.bbox.split(",")], "aoi_overlap_km2": r.aoi_overlap_km2,
         "basis_pass_start_utc": r.basis_start_utc, "cycles_ahead": int(r.cycles_ahead) if r.cycles_ahead is not None and not pd.isna(r.cycles_ahead) else None,
         "in_esa_plan": r.in_esa_plan, "in_repeat_prediction": r.in_repeat_prediction,
         "pass_group": r.pass_group, "status": r.status, "plan_file": r.plan_file,
         "aoi_parts": r.aoi_parts.split("; ") if r.aoi_parts else [],
         "aoi_overlap_bbox": [float(x) for x in r.aoi_overlap_bbox.split(",")] if r.aoi_overlap_bbox else None,
         "aoi_cells_under": int(r.aoi_cells_under), "ais_heard_share": _num(r.ais_heard_share)}
        for r in nxt.itertuples()
    ],
    "pass_groups": groups,
    "use": "These are the windows when live AIS (scripts/26_ais_record.py) can be matched to Sentinel-1 radar contacts with "
           "darkvessel.ais.match. Keep the recorder running through each window and 30 minutes either side.",
    "caveat": DARK_CAVEAT + " A radar contact in a cell where the live feed hears nothing cannot be called dark or matched at all.",
}
s1_passes.write_json_atomic(PASSES_JSON, passes_out)
log(f"wrote {PASSES_JSON.name}: {len(nxt)} rows, {len(groups)} passes, {passes_out['window_start_utc']} to {passes_out['window_end_utc']}")

if args.passes_only:
    if SUMMARY.exists():  # keep the summary's pass counts current
        try:
            summ = json.loads(SUMMARY.read_text())
            summ["s1_next_passes"] = {"n": int(len(nxt)), "pass_groups": len(groups), "esa_plan": int(len(plan_rows)),
                                      "repeat_cycle": int(len(pred_g)), "window_start_utc": passes_out["window_start_utc"],
                                      "window_end_utc": passes_out["window_end_utc"], "generated_utc": passes_out["generated_utc"],
                                      "file": _rel(PASSES_JSON)}
            s1_passes.write_json_atomic(SUMMARY, summ)
        except (ValueError, OSError) as exc:
            log(f"summary not updated: {exc}")
    raise SystemExit(0)

# ---------------------------------------------------------------------------------------------------------------
# about layer and summary JSON
# ---------------------------------------------------------------------------------------------------------------
about = {
    "product": "Live AIS over the South China Sea AOI: latest vessels, tracks, receiver reach and the Sentinel-1 pass windows",
    "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "period": period_text,
    "source": AISSTREAM_TERMS["source"],
    "source_docs": AISSTREAM_TERMS["docs_url"],
    "source_terms": AISSTREAM_TERMS["licence"],
    "source_provenance": AISSTREAM_TERMS["provenance_quote"],
    "positions": int(len(positions)), "mmsi": int(positions.mmsi.nunique()),
    "layers": "vessels_latest_* (last position per MMSI with static data), tracks_* (one simplified line per MMSI), "
              f"s1_next_passes_* (Sentinel-1 passes from {args.lookback_hours:g} h before to {args.horizon_hours:g} h after "
              "generated_utc), each in EPSG:4326 and UTM 49N (EPSG:32649)",
    "reach_rasters": "data/outputs/small/ais_reach_share_*.tif, ais_reach_mmsi_*.tif (0.25 degree; EPSG:4326 and UTM 49N)",
    "s1_passes_note": (s1_passes.PASS_NOTE_PLAN + " " if len(plan_rows) else "") + s1_passes.PASS_NOTE_REPEAT,
    "recording_gaps": "; ".join(f"{g['from_utc']} to {g['to_utc']} ({g['minutes']:.0f} min)" for g in gaps) or "none over 10 min",
    "caveat": DARK_CAVEAT,
    "caveat_full": DARK_CAVEAT,
    "caveat_reach": ais.AIS_REACH_CAVEAT,
    "caveat_gap": "A gap in a vessel's AIS track is not proof of intent: receivers lose class B first, messages collide in busy "
                  "waters, and the feed itself drops messages and has no uptime guarantee.",
    "script": "scripts/28_ais_reach.py",
}
pyogrio.write_dataframe(pd.DataFrame([about]), GPKG, layer="about", driver="GPKG")

summary = ais.summarise(positions, static)
status_now = json.loads(ais.STATUS_PATH.read_text()) if ais.STATUS_PATH.exists() else None
wd_status = ais.AIS_CACHE / "watchdog_status.json"
summary.update({
    "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "period": period_text,
    "recording_gaps_over_10_min": gaps,
    "positions_in_aoi": int(in_aoi.sum()),
    "mmsi_in_aoi": int(positions.mmsi[in_aoi].nunique()),
    "recorder_status": status_now,
    "watchdog_status": (lambda d: {k: d.get(k) for k in ("pid", "started_utc", "checks", "restarts", "streak", "last_restart_utc",
                                                          "last_restart_reason", "last_decision")})(json.loads(wd_status.read_text()))
                       if wd_status.exists() else None,
    "reach": {"grid": "0.25 degree model grid (darkvessel.ocean.grid.model_grid)", "aoi_cells": aoi_cells,
              "aoi_cells_reached": reached, "share_aoi_cells_reached": round(reached / aoi_cells, 4),
              "aoi_cells_reached_half_of_hours_or_more": steady, "rasters": [_rel(p) for p in cogs]},
    "coverage_by_aoi_part": by_part,
    "coverage_by_report_box": by_box,
    "report_box_note": "Reporting boxes are lon/lat boxes for statistics only; they are not boundaries and take no position on any claim.",
    "coverage_by_distance_to_coast": by_dist,
    "tracks": int(len(tracks)),
    "s1_next_passes": {"n": int(len(nxt)), "pass_groups": len(groups), "esa_plan": int(len(plan_rows)), "repeat_cycle": int(len(pred_g)),
                       "window_start_utc": passes_out["window_start_utc"], "window_end_utc": passes_out["window_end_utc"],
                       "generated_utc": passes_out["generated_utc"], "file": _rel(PASSES_JSON)},
    "aisstream_terms": AISSTREAM_TERMS,
    "caveat": DARK_CAVEAT,
    "caveat_reach": ais.AIS_REACH_CAVEAT,
    "caveat_gap": about["caveat_gap"],
    "files": {"gpkg": _rel(GPKG), "layers": layers + ["about"]},
})
if summary.get("recorder_status"):
    summary["recorder_status"].pop("caveat", None)
    summary["recorder_status"].pop("hours_written", None)
s1_passes.write_json_atomic(SUMMARY, summary)
log(f"wrote {GPKG.name} ({GPKG.stat().st_size / 1e6:.1f} MB), {SUMMARY.name}, {PASSES_JSON.name}")
