"""Global Fishing Watch pull over the South China Sea AOI: research build only (CC BY-NC 4.0, noncommercial).

Purpose: fetch GFW's SAR vessel detections, AIS presence, AIS fishing effort, AIS events (gaps, encounters,
loitering, port visits) and vessel identities for one window, write them as research products under
data/research/, and compare GFW's SAR detections with our own Sentinel-1 candidates on the same dates.

Method
- 4Wings report endpoint (POST, custom GeoJSON polygon = the AOI simplified to under 1,000 vertices). SAR: one
  request per day and per matched flag at HIGH resolution (0.01 degree) and HOURLY time steps, so each cell count
  carries the hour of the Sentinel-1 pass; per day and neural_vessel_type at DAILY steps; matched detections also
  grouped by vessel id (gives MMSI, flag, gear type and ship name of GFW's match). AIS presence: LOW resolution
  (0.1 degree) per day, summed per cell (GFW returns one row per vessel, cell and day, so an AOI-wide HIGH grid runs
  to gigabytes and is only requested with --high-presence). Fishing effort: LOW daily grouped by gear type, and
  HIGH over the whole window.
- Events endpoint (POST with geometry), paginated; vessels endpoint for every vessel id seen in the events.
- Every response is cached under data/cache/gfw/ (key = hash of the request, never the token), so reruns skip
  finished work. The 4Wings endpoint allows one report at a time per token; the client runs them in sequence and
  waits on the 'one concurrent report' 429.
- GFW cell values are cell centres (GFW report docs: 'center of the grid cell'); rasters are written on GFW's own
  grid (cell edges at half-multiples of the cell size), and the comparison bins on that grid too.
- Comparison with our radar: GFW's report gives cell counts, not positions, so the comparison is per cell and
  date (0.01 degree with the hour, 0.1 and 0.25 degree by date) inside the sea our scenes tested. Shares are
  reported by our length estimate and by mission (S1C, S1D), with GFW's own AIS matched flag in shared cells.
- Attribution access dates are the fetch dates of the cached responses, not the day of an offline rebuild. Every
  parquet carries use, licence, attribution and caveat in its file metadata (split under 20 MB when needed).

Inputs: .env (GFW_API_TOKEN, git-ignored), data/aoi.gpkg, data/detections_regional.gpkg (for the comparison).
Output (data/research/): gfw_sar_detections.parquet, gfw_sar_detections_by_neural_type.parquet,
  gfw_sar_matched_by_vessel.parquet, gfw_sar_matched_density_*.tif, gfw_sar_unmatched_density_*.tif (0.01 degree),
  gfw_ais_presence_hours_*.tif (0.1 degree), gfw_fishing_hours_*.tif (0.01 degree; all EPSG:4326 and UTM 49N),
  gfw_presence_daily.parquet, gfw_fishing_effort_daily.parquet, gfw_events.gpkg (one layer per event type, dual CRS,
  plus 'about'), gfw_events_<type>*.parquet, gfw_events_about.csv, gfw_events_vessels.parquet (identities of vessels
  in the events; gfw_vessels.parquet belongs to scripts/31), gfw_summary.json, radar_vs_gfw.parquet, radar_vs_gfw.json.
Usage: python scripts/27_gfw_pull.py --steps sar,grids            # 4Wings reports (one at a time per token)
       python scripts/27_gfw_pull.py --steps events,vessels       # can run in parallel with the line above
       python scripts/27_gfw_pull.py --steps outputs,compare      # products from the cache, then the comparison
       Options: --start 2026-09-01 --end 2026-10-08 --vessel-max 40000 --events gaps,encounters,loitering,port_visits
                --offline (build products from data/cache/gfw/ only; no network)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time

import numpy as np
import pandas as pd

import darkvessel  # noqa: F401  (sets PROJ_DATA before rasterio and pyogrio load)
import geopandas as gpd
import pyogrio

from darkvessel.ais import gfw as G
from darkvessel.config import CRS_GEO, CRS_UTM_REGIONAL, DATA_DIR
from darkvessel.io import write_dual_crs
from darkvessel.ocean.grid import write_dual_cog

RESEARCH = DATA_DIR / "research"
LOG = DATA_DIR / "cache" / "gfw" / "pull_state.json"
NEURAL_TYPES = ["Likely Fishing", "Likely non-fishing", "Unknown"]
EVENT_PAGE = 20000      # events per page (20,000 returned in about 30 s on 2026-10-08)
VESSEL_BATCH = 100      # vessel ids per /vessels request (100 accepted on 2026-10-08)
TODAY = dt.date.today().isoformat()


def accessed(*keys: str) -> str:
    """Access date(s) of the named datasets, read from the cache's fetch dates (today when nothing is cached)."""
    return G.accessed_text(G.cache_fetch_dates(), *keys, default=TODAY)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def state_load() -> dict:
    return json.loads(LOG.read_text()) if LOG.exists() else {}


def state_save(s: dict):
    """Merge with the file on disk (two step groups may run in parallel) and write."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    merged = state_load()
    merged.update(s)
    LOG.write_text(json.dumps(merged, indent=1, default=str))


def days(start: str, end: str) -> list[str]:
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    return [(d0 + dt.timedelta(n)).isoformat() for n in range((d1 - d0).days + 1)]


def next_day(d: str) -> str:
    return (dt.date.fromisoformat(d) + dt.timedelta(1)).isoformat()


def aoi():
    geom = gpd.read_file(DATA_DIR / "aoi.gpkg", layer="aoi_4326").geometry.iloc[0]
    gj, tol = G.aoi_geojson(geom, max_vertices=1000)
    return geom, gj, tol


# ---------------------------------------------------------------------------------------------------------------
def step_sar(c: G.GFWClient, gj: dict, start: str, end: str, st: dict, refresh_empty: bool = False):
    """SAR detections per day: matched true/false at HOURLY x HIGH, neural type at DAILY x HIGH, matched grouped by vessel.
    `refresh_empty` refetches days whose cached report had no cells (GFW publishes SAR about 3 to 5 days late)."""
    ds = G.DATASETS["sar"]
    last_with_data = None
    for d in days(start, end):
        rng = (d, next_day(d))
        n_day = 0
        for m in ("true", "false"):
            resp = c.report(ds, rng, gj, "HIGH", "HOURLY", filters=f"matched='{m}'", refresh_empty=refresh_empty)
            n = sum(int(x.get("detections", 0) or 0) for e in resp.get("entries", []) for v in e.values() for x in v or [])
            n_day += n
        for nt in NEURAL_TYPES:
            c.report(ds, rng, gj, "HIGH", "DAILY", filters=f"neural_vessel_type='{nt}'", refresh_empty=refresh_empty)
        c.report(ds, rng, gj, "HIGH", "DAILY", filters="matched='true'", group_by="VESSEL_ID", refresh_empty=refresh_empty)
        if n_day:
            last_with_data = d
        log("sar", d, "detections", n_day, c.last_headers.get("x-ratelimit-daily-current-usage", ""))
    st["sar_last_day_with_data"] = last_with_data
    st["sar_dataset_version"] = c.dataset(ds)["id"]
    state_save(st)


PRESENCE_CHECKPOINT = DATA_DIR / "cache" / "gfw" / "presence_low_daily.parquet"


def step_grids(c: G.GFWClient, gj: dict, start: str, end: str, st: dict, refresh_empty: bool = False, high_presence: bool = False):
    """AIS presence and fishing effort. Presence: LOW (0.1 degree) DAILY, one report per day, summed per cell (the AIS
    status rule of the identity step reads these). Fishing effort: LOW daily grouped by gear type, then HIGH over the
    window. The presence dataset accepts only vessel_id, flag and mmsi groupings (datasets endpoint), so it is not
    grouped. GFW returns presence as one row per vessel and cell, so an AOI-wide HIGH report over the window would run
    to gigabytes; it is only requested with `high_presence`. Reports run strictly one after the other."""
    from darkvessel.ais.gfw_identity import presence_daily

    rng = (start, next_day(end))
    for key in ("presence", "fishing_effort"):
        meta = c.dataset(G.DATASETS[key])
        st[f"{key}_dataset_version"], st[f"{key}_end_date"] = meta["id"], meta.get("endDate")
    t = time.time()
    lo = presence_daily(c, gj, days(start, end), refresh_empty=refresh_empty, log=log, checkpoint=PRESENCE_CHECKPOINT)
    log("presence LOW DAILY cell-days", len(lo), "days with data", lo.date.nunique() if len(lo) else 0, round(time.time() - t), "s")
    t = time.time()
    r = c.report(G.DATASETS["fishing_effort"], rng, gj, "LOW", "DAILY", group_by="GEARTYPE")
    log("fishing_effort LOW DAILY by geartype rows", sum(len(v or []) for e in r.get("entries", []) for v in e.values()), round(time.time() - t), "s")
    for key in ("fishing_effort",) + (("presence",) if high_presence else ()):
        t = time.time()
        try:
            r = c.report(G.DATASETS[key], rng, gj, "HIGH", "ENTIRE")
            log(key, "HIGH ENTIRE cells", sum(len(v or []) for e in r.get("entries", []) for v in e.values()), round(time.time() - t), "s")
        except G.GFWError as e:
            log(key, "HIGH ENTIRE failed:", str(e)[:200])
            st[f"{key}_high_entire_error"] = str(e)[:300]
    state_save(st)


def step_events(c: G.GFWClient, gj: dict, start: str, end: str, st: dict, which: list[str]):
    rng_end = next_day(end)
    for key in which:
        ds = G.DATASETS[key]
        st[f"{key}_dataset_version"] = c.dataset(ds)["id"]
        t = time.time()
        ev = c.events(ds, start, rng_end, gj, limit=EVENT_PAGE, progress=lambda d, n, tot: log("events", d, n, "of", tot))
        st[f"events_{key}_n"] = len(ev)
        log("events", key, len(ev), "in", round(time.time() - t), "s")
        state_save(st)


def events_frames(c: G.GFWClient, gj: dict, start: str, end: str, which: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for key in which:
        try:
            ev = c.events(G.DATASETS[key], start, next_day(end), gj, limit=EVENT_PAGE)
        except G.GFWError as e:
            log("events", key, "not available from cache:", str(e)[:120])
            continue
        out[key] = G.events_to_frame(ev)
    return out


def step_vessels(c: G.GFWClient, gj: dict, start: str, end: str, st: dict, which: list[str], vessel_max: int):
    frames = events_frames(c, gj, start, end, which)
    ids = []
    for key in ("gaps", "encounters", "loitering", "port_visits"):  # priority order, dark-vessel relevant first
        f = frames.get(key)
        if f is None or f.empty:
            continue
        ids += f.vessel_id.dropna().tolist()
        if "encounter_vessel_id" in f:
            ids += f.encounter_vessel_id.dropna().tolist()
    uniq = list(dict.fromkeys(ids))
    st["vessels_unique_in_events"] = len(uniq)
    todo = uniq[:vessel_max]
    log("vessels: unique ids", len(uniq), "fetching", len(todo))
    t = time.time()
    c.vessels(todo, batch=VESSEL_BATCH, progress=lambda i, n: log("vessels", i, "of", n) if i % 5000 < VESSEL_BATCH else None)
    st["vessels_fetched"] = len(todo)
    log("vessels done in", round(time.time() - t), "s")
    state_save(st)


# ---------------------------------------------------------------------------------------------------------------
def sar_frames(c: G.GFWClient, gj: dict, start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(hourly cells with matched flag, daily cells by neural type, matched cells grouped by vessel) from the cache."""
    ds = G.DATASETS["sar"]
    hourly, neural, byves = [], [], []
    for d in days(start, end):
        rng = (d, next_day(d))
        for m in ("true", "false"):
            try:
                f = G.report_to_frame(c.report(ds, rng, gj, "HIGH", "HOURLY", filters=f"matched='{m}'"))
            except G.GFWError:
                continue
            if not f.empty:
                f["matched"] = m == "true"
                hourly.append(f)
        for nt in NEURAL_TYPES:
            try:
                f = G.report_to_frame(c.report(ds, rng, gj, "HIGH", "DAILY", filters=f"neural_vessel_type='{nt}'"))
            except G.GFWError:
                continue
            if not f.empty:
                f["neural_vessel_type"] = nt
                neural.append(f)
        try:
            f = G.report_to_frame(c.report(ds, rng, gj, "HIGH", "DAILY", filters="matched='true'", group_by="VESSEL_ID"))
        except G.GFWError:
            continue
        if not f.empty:
            byves.append(f)
    h = pd.concat(hourly, ignore_index=True) if hourly else pd.DataFrame()
    n = pd.concat(neural, ignore_index=True) if neural else pd.DataFrame()
    v = pd.concat(byves, ignore_index=True) if byves else pd.DataFrame()
    if not h.empty:
        ts = pd.to_datetime(h.date, utc=True, errors="coerce")
        h["date"], h["hour"] = ts.dt.strftime("%Y-%m-%d"), ts.dt.hour
    return h, n, v


def gfw_grid(res: float):
    """(transform, shape) of GFW's cell grid over the AOI at `res` degrees: the project grid of the same resolution
    (darkvessel.ocean.grid.grid, edges at whole multiples of res) moved half a cell west and north and widened by one
    cell, so that each GFW cell centre (a whole multiple of res) sits at the centre of a raster cell. On the project
    grid the centres would fall on cell edges and float noise would decide the cell."""
    from rasterio.transform import from_origin

    from darkvessel.ocean.grid import grid as ocean_grid

    tr, (h, w) = ocean_grid(res)
    return from_origin(tr.c - res / 2, tr.f + res / 2, res, res), (h + 1, w + 1)


def grid_fill(frame: pd.DataFrame, value_col: str, tr=None, shape=None) -> np.ndarray:
    """Sum of `value_col` per grid cell (NaN where nothing), on GFW's 0.01 degree grid over the AOI by default."""
    from darkvessel.ocean.grid import cell_index

    if tr is None:
        tr, shape = gfw_grid(0.01)
    arr = np.zeros(shape, np.float64)
    if not frame.empty:
        row, col, inside = cell_index(tr, shape, frame.lon.values, frame.lat.values)
        v = pd.to_numeric(frame[value_col], errors="coerce").fillna(0).values
        np.add.at(arr, (row[inside], col[inside]), v[inside])
    arr[arr == 0] = np.nan
    return arr


def step_outputs(c: G.GFWClient, gj: dict, tol: float, start: str, end: str, st: dict, which: list[str], vessel_max: int = 40000):
    RESEARCH.mkdir(parents=True, exist_ok=True)
    tr, shape = gfw_grid(0.01)
    rng = (start, next_day(end))
    summary = {"generated_utc": pd.Timestamp.now("UTC").isoformat(), "use": G.RESEARCH_TAG, "licence": G.LICENCE,
               "licence_url": G.LICENCE_URL, "terms_url": G.TERMS_URL, "caveat": G.GFW_CAVEAT, "sources": G.SOURCES,
               "window_requested": {"start": start, "end": end}, "aoi": "data/aoi.gpkg aoi_4326, simplified tolerance_deg=%s" % tol,
               "api_base": G.BASE_URL, "datasets": {}, "counts": {}, "attribution": {}, "notes": []}
    # SAR
    h, n, v = sar_frames(c, gj, start, end)
    sar_ver = st.get("sar_dataset_version") or (h.dataset_version.iloc[0] if not h.empty else G.DATASETS["sar"])
    if not h.empty:
        last = h.loc[h.detections > 0, "date"].max()
        sar_acc = accessed(sar_ver, G.DATASETS["sar"])
        sar_tags = G.research_tags(sar_ver, (start, last), sar_acc, cell="0.01 degree cells (lon, lat = cell centre)")
        h["res_deg"] = 0.01
        h["use"], h["licence"] = G.RESEARCH_TAG, G.LICENCE_URL
        keep = [x for x in ["dataset_version", "date", "hour", "lat", "lon", "detections", "matched", "res_deg", "use", "licence"] if x in h]
        G.write_parquet_parts(h[keep], RESEARCH / "gfw_sar_detections.parquet", {**sar_tags, "table": "GFW SAR detections per 0.01 degree cell and hour with GFW's matched flag"})
        if not n.empty:
            G.write_parquet_parts(n[[x for x in ["dataset_version", "date", "lat", "lon", "detections", "neural_vessel_type"] if x in n]],
                                  RESEARCH / "gfw_sar_detections_by_neural_type.parquet", {**sar_tags, "table": "GFW SAR detections per 0.01 degree cell and day by GFW neural vessel type"})
        if not v.empty:
            G.write_parquet_parts(v, RESEARCH / "gfw_sar_matched_by_vessel.parquet", {**sar_tags, "table": "GFW-matched SAR detections per 0.01 degree cell and day grouped by GFW vessel id"})
        summary["datasets"]["sar"] = {"id": sar_ver, "resolution_deg": 0.01, "temporal": "HOURLY", "first_date": h.date.min(),
                                      "last_date_with_detections": last, "lag_days_at_run": (dt.date.fromisoformat(TODAY) - dt.date.fromisoformat(last)).days}
        summary["counts"]["sar_detections"] = int(h.detections.sum())
        summary["counts"]["sar_matched"] = int(h.loc[h.matched, "detections"].sum())
        summary["counts"]["sar_unmatched"] = int(h.loc[~h.matched, "detections"].sum())
        summary["counts"]["sar_by_day"] = h.groupby("date").detections.sum().astype(int).to_dict()
        if not n.empty:
            summary["counts"]["sar_by_neural_type"] = n.groupby("neural_vessel_type").detections.sum().astype(int).to_dict()
        if not v.empty:
            summary["counts"]["sar_matched_vessels"] = int(v.vesselId.nunique()) if "vesselId" in v else None
            if "flag" in v:
                summary["counts"]["sar_matched_by_flag_top"] = v.groupby("flag").detections.sum().sort_values(ascending=False).head(15).astype(int).to_dict()
        summary["attribution"]["sar"] = G.attribution(sar_ver, (start, last), sar_acc)
        tags = G.research_tags(sar_ver, (start, last), sar_acc, window=f"{start} to {last}", cell="0.01 degree, sum of detections (GFW grid)")
        for flag, name in ((True, "gfw_sar_matched_density"), (False, "gfw_sar_unmatched_density")):
            arr = grid_fill(h[h.matched == flag], "detections")
            paths = write_dual_cog(arr.astype(np.float32), tr, name, "detections per 0.01 degree cell over the window",
                                   f"Global Fishing Watch {sar_ver}, matched={str(flag).lower()}", out_dir=RESEARCH,
                                   tags={**tags, "matched": str(flag).lower()}, resampling=__import__("rasterio.warp").warp.Resampling.nearest)
            log("wrote", [p.name for p in paths])
    else:
        summary["notes"].append("no SAR detections cached for the window")
    # presence and fishing effort (presence LOW DAILY comes per day, ungrouped: the dataset has no geartype grouping)
    from darkvessel.ais.gfw_identity import presence_daily

    for key, name, col in (("presence", "gfw_ais_presence_hours", "hours"), ("fishing_effort", "gfw_fishing_hours", "hours")):
        ds = G.DATASETS[key]
        offline_c = G.GFWClient(token="unused", cache_dir=c.cache_dir, offline=True)
        try:
            hi = G.report_to_frame(offline_c.report(ds, rng, gj, "HIGH", "ENTIRE"))
        except G.GFWError as e:
            hi = pd.DataFrame()
            summary["notes"].append(f"{key}: HIGH ENTIRE grid not cached ({str(e)[:80]}); no density raster written")
        if key == "presence":
            lo = presence_daily(offline_c, gj, days(start, end), checkpoint=PRESENCE_CHECKPOINT)
        else:
            try:
                lo = G.report_to_frame(offline_c.report(ds, rng, gj, "LOW", "DAILY", group_by="GEARTYPE"), "LOW")
            except G.GFWError as e:
                lo = pd.DataFrame()
                summary["notes"].append(f"{key}: LOW DAILY by geartype not cached ({str(e)[:80]})")
        ver = st.get(f"{key}_dataset_version") or (hi.dataset_version.iloc[0] if not hi.empty else ds)
        end_eff = st.get(f"{key}_end_date") or end
        end_eff = str(end_eff)[:10]
        summary["datasets"][key] = {"id": ver, "resolution_deg": [0.01, 0.1], "dataset_end_date": end_eff, "window": f"{start} to {min(end, end_eff)}"}
        summary["counts"][f"{key}_hours"] = round(float(hi[col].sum()), 1) if not hi.empty else None
        if not lo.empty:
            if "geartype" in lo:
                summary["counts"][f"{key}_hours_by_geartype"] = lo.groupby("geartype")[col].sum().round(1).sort_values(ascending=False).to_dict()
            summary["counts"][f"{key}_hours_by_day"] = lo.groupby("date")[col].sum().round(1).to_dict()
            summary["counts"][f"{key}_low_cells_with_data"] = int(lo[lo[col] > 0].groupby(["lon", "lat"]).ngroups)
            lo["use"], lo["licence"] = G.RESEARCH_TAG, G.LICENCE_URL
            G.write_parquet_parts(lo, RESEARCH / f"gfw_{key}_daily.parquet", G.research_tags(ver, (start, min(end, end_eff)), accessed(ver, ds),
                                  table=f"GFW {key} per 0.1 degree cell (lon, lat = cell centre) and day" + (" by gear type" if "geartype" in lo else ", summed over vessels")))
        summary["attribution"][key] = G.attribution(ver, (start, min(end, end_eff)), accessed(ver, ds))
        if hi.empty and key == "presence" and not lo.empty:
            # no AOI-wide HIGH grid for presence (per-vessel rows make it too large): 0.1 degree raster from the daily sums
            tr10, shape10 = gfw_grid(0.1)
            tags = G.research_tags(ver, (start, min(end, end_eff)), accessed(ver, ds), cell="0.1 degree, sum of AIS presence hours over the window (GFW grid)")
            arr = grid_fill(lo.groupby(["lon", "lat"], as_index=False)[col].sum(), col, tr10, shape10)
            paths = write_dual_cog(arr.astype(np.float32), tr10, name, "AIS presence hours per 0.1 degree cell over the window",
                                   f"Global Fishing Watch {ver}", out_dir=RESEARCH, tags=tags,
                                   resampling=__import__("rasterio.warp").warp.Resampling.nearest)
            log("wrote", [p.name for p in paths])
            summary["datasets"][key]["raster_resolution_deg"] = 0.1
            continue
        if hi.empty:
            continue
        tags = G.research_tags(ver, (start, min(end, end_eff)), accessed(ver, ds), cell="0.01 degree, sum of hours over the window (GFW grid)")
        arr = grid_fill(hi, col)
        paths = write_dual_cog(arr.astype(np.float32), tr, name, "hours per 0.01 degree cell over the window",
                               f"Global Fishing Watch {ver}", out_dir=RESEARCH, tags=tags,
                               resampling=__import__("rasterio.warp").warp.Resampling.nearest)
        log("wrote", [p.name for p in paths])
    # events
    frames = events_frames(c, gj, start, end, which)
    gpkg = RESEARCH / "gfw_events.gpkg"
    if gpkg.exists():
        gpkg.unlink()
    about_rows = []
    for key, f in frames.items():
        ver = st.get(f"{key}_dataset_version", G.DATASETS[key])
        summary["datasets"][key] = {"id": ver, "window": f"{start} to {end}", "note": "events that overlap the window and the AOI, as the API returns them"}
        summary["counts"][f"events_{key}"] = int(len(f))
        if f.empty:
            continue
        f = f.copy()
        # per-row text stays short (the licence, attribution and caveat are in the file metadata and the about layer);
        # the bounding box columns go (the position stays), which with zstd keeps the big tables small
        f = f.drop(columns=[c for c in f.columns if c.startswith("bbox_")])
        f["use"] = G.RESEARCH_TAG
        f["overlaps_window_only"] = (f.start < pd.Timestamp(start, tz="UTC")) | (f.end > pd.Timestamp(next_day(end), tz="UTC"))
        f["starts_in_window"] = f.start >= pd.Timestamp(start, tz="UTC")
        for ccol in ("start", "end"):
            f[ccol] = f[ccol].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        ev_acc = accessed("events")
        ev_tags = G.research_tags(ver, (start, end), ev_acc, table=f"GFW {key} events overlapping the window and the AOI, as the API returns them")
        parts = G.write_parquet_parts(f, RESEARCH / f"gfw_events_{key}.parquet", ev_tags)  # the full table, split under 20 MB
        log("wrote", [p.name for p in parts])
        summary["counts"][f"events_{key}_start_in_window"] = int(f.starts_in_window.sum())
        if key == "port_visits":
            # hundreds of thousands of visits: the GeoPackage gets one point per anchorage; the table has every visit
            agg = f.groupby(["port_id", "port_name", "port_flag"], dropna=False).agg(
                n_visits=("event_id", "size"), n_vessels=("vessel_id", "nunique"), lon=("lon", "mean"), lat=("lat", "mean"),
                n_start_in_window=("starts_in_window", "sum")).reset_index()
            agg["use"] = G.RESEARCH_TAG
            g = gpd.GeoDataFrame(agg, geometry=gpd.points_from_xy(agg.lon, agg.lat), crs=CRS_GEO)
            layer_note = "one point per anchorage with visit and vessel counts; every visit is in gfw_events_port_visits*.parquet"
        elif key == "loitering":
            # hundreds of thousands of loitering events (many are stationary AIS beacons): the GeoPackage gets 0.1 degree
            # cell counts of the events that start in the window; every event is in the parquet
            sel = f[f.starts_in_window].copy()
            sel["cx"], sel["cy"] = G.cell_key(sel.lon, sel.lat, 0.1)
            agg = sel.groupby(["cx", "cy"]).agg(n_events=("event_id", "size"), n_vessels=("vessel_id", "nunique"),
                                                 total_hours=("loitering_total_time_h", "sum"),
                                                 median_speed_kn=("loitering_avg_speed_kn", "median")).reset_index()
            agg["lon"], agg["lat"] = G.cell_centre(agg.cx.values, agg.cy.values, 0.1)
            agg["total_hours"] = agg.total_hours.round(1)
            agg["use"] = G.RESEARCH_TAG
            g = gpd.GeoDataFrame(agg.drop(columns=["cx", "cy"]), geometry=gpd.points_from_xy(agg.lon, agg.lat), crs=CRS_GEO)
            layer_note = "0.1 degree cell counts of loitering events that start inside the window; every event is in gfw_events_loitering*.parquet"
        else:
            g = gpd.GeoDataFrame(f, geometry=gpd.points_from_xy(f.lon, f.lat), crs=CRS_GEO)
            layer_note = "every event returned for the window and AOI"
        write_dual_crs(g, gpkg, f"gfw_{key}", utm_crs=CRS_UTM_REGIONAL, spatial_index=False)
        if key == "gaps" and {"off_lon", "off_lat", "on_lon", "on_lat"} <= set(f.columns):
            from shapely.geometry import LineString
            ok = f[["off_lon", "off_lat", "on_lon", "on_lat"]].notna().all(axis=1)
            lines = gpd.GeoDataFrame(f[ok], geometry=[LineString([(a, b), (c_, d)]) for a, b, c_, d in
                                                     f.loc[ok, ["off_lon", "off_lat", "on_lon", "on_lat"]].values], crs=CRS_GEO)
            if len(lines):
                write_dual_crs(lines, gpkg, "gfw_gaps_off_to_on", utm_crs=CRS_UTM_REGIONAL, spatial_index=False)
        about_rows.append({"layer": f"gfw_{key}", "dataset": ver, "n_events": int(len(f)), "n_rows_in_layer": int(len(g)), "layer_holds": layer_note,
                           "attribution": G.attribution(ver, (start, end), ev_acc)})
        summary["attribution"][key] = G.attribution(ver, (start, end), ev_acc)
        if key == "gaps" and "gap_intentional_disabling" in f:
            summary["counts"]["gaps_flagged_intentional_by_gfw"] = int(pd.Series(f.gap_intentional_disabling).astype(str).str.lower().eq("true").sum())
        if key == "encounters" and "encounter_type" in f:
            summary["counts"]["encounters_by_type"] = f.encounter_type.value_counts().to_dict()
        if key == "port_visits" and "port_name" in f:
            summary["counts"]["port_visits_top_ports"] = f.port_name.value_counts().head(15).to_dict()
        summary["counts"][f"events_{key}_overlap_only"] = int(f.overlaps_window_only.sum())
    if about_rows:
        about = pd.DataFrame(about_rows)
        about["use"], about["licence"], about["licence_url"], about["terms_url"], about["caveat"] = G.RESEARCH_TAG, G.LICENCE, G.LICENCE_URL, G.TERMS_URL, G.GFW_CAVEAT
        about["row_text_note"] = ("Rows carry only the short research tag; licence, attribution and caveat are in this table and in the parquet "
                                  "file metadata. Bounding-box columns were dropped from the tables; the event position is kept.")
        about["gap_note"] = ("An AIS gap event is not proof of intent. GFW flags 'intentionalDisabling' by rules (gap >= 12 h, start "
                             ">= 50 nm from shore, good satellite reception, >= 14 positions in the 12 h before); GFW calls the "
                             "dataset a prototype. Events whose start or end lies outside the window are kept and flagged "
                             "overlaps_window_only.")
        about.to_csv(RESEARCH / "gfw_events_about.csv", index=False)
        pyogrio.write_dataframe(about, gpkg, layer="about", driver="GPKG")
    # vessels
    ids = []
    for f in frames.values():
        if not f.empty:
            ids += f.vessel_id.dropna().tolist()
            if "encounter_vessel_id" in f:
                ids += f.encounter_vessel_id.dropna().tolist()
    uniq = list(dict.fromkeys(ids))
    offline = G.GFWClient(token="unused", cache_dir=c.cache_dir, offline=True)
    entries = offline.vessels(uniq[:vessel_max], batch=VESSEL_BATCH, skip_missing=True)  # same batches as step_vessels
    vf = G.vessels_to_frame(entries)
    if not vf.empty:
        vf["use"], vf["licence"] = G.RESEARCH_TAG, G.LICENCE_URL
        ves_ver = st.get("vessels_dataset_version") or (vf.dataset_version.dropna().iloc[0] if vf.dataset_version.notna().any() else G.DATASETS["vessels"])
        G.write_parquet_parts(vf, RESEARCH / "gfw_events_vessels.parquet",   # gfw_vessels.parquet is the identity step's file
                              G.research_tags(ves_ver, (start, end), accessed(G.DATASETS["vessels"]), table="GFW identity records of the vessels in the events"))
        summary["attribution"]["vessels"] = G.attribution(ves_ver, (start, end), accessed(G.DATASETS["vessels"]))
        summary["counts"]["vessels_in_events"] = len(uniq)
        summary["counts"]["vessels_with_identity"] = int(len(vf))
        summary["counts"]["vessels_by_flag_top"] = vf.flag.value_counts().head(15).to_dict()
        summary["counts"]["vessels_by_shiptype"] = vf.shiptype.value_counts().head(15).to_dict()
        summary["counts"]["vessels_with_length"] = int(vf.length_m.notna().sum())
        summary["datasets"]["vessels"] = {"id": st.get("vessels_dataset_version", G.DATASETS["vessels"])}
    summary["rate_limit_headers_last"] = c.last_headers
    summary["accessed"] = accessed(*[v for v in G.DATASETS.values()], "events")
    summary["notes"] += ["GFW report cells are sums per 0.01 degree cell; GFW does not publish individual detection positions through the report endpoint, so the SAR product is cell counts with the hour of the pass.",
                         "Cell coordinates are cell centres (GFW report docs); rasters use GFW's grid (cell edges at half-multiples of the cell size).",
                         "GFW's 'matched' is GFW's AIS match, 'unmatched' is GFW's dark count; neither is ours. Dark does not mean illegal.",
                         "Loitering and port-visit events can span years (a stationary AIS beacon); overlaps_window_only marks events not fully inside the window."]
    (RESEARCH / "gfw_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    log("wrote gfw_summary.json")


# ---------------------------------------------------------------------------------------------------------------
LENGTH_BINS = [(0, 25), (25, 50), (50, 100), (100, 1000)]


def step_compare(c: G.GFWClient, gj: dict, start: str, end: str, st: dict):
    """Our regional candidates against GFW SAR cell counts, same dates, inside the sea our scenes tested."""
    h, _, _ = sar_frames(c, gj, start, end)
    if h.empty:
        log("compare: no GFW SAR cells cached")
        return
    det = pyogrio.read_dataframe(DATA_DIR / "detections_regional.gpkg", layer="detections_regional_4326")
    det["ts"] = pd.to_datetime(det.acq_utc, utc=True)
    det["date"], det["hour"] = det.ts.dt.strftime("%Y-%m-%d"), det.ts.dt.hour
    scenes = pyogrio.read_dataframe(DATA_DIR / "detections_regional.gpkg", layer="scenes_processed_4326")
    tcol = [x for x in scenes.columns if "acq" in x.lower() or "start" in x.lower() or "time" in x.lower()]
    scenes["date"] = pd.to_datetime(scenes[tcol[0]], utc=True).dt.strftime("%Y-%m-%d") if tcol else None
    our_dates = sorted(det.date.unique())
    # GFW cells inside our scene footprints of the same date (cell centre within footprint, 1 km shore buffer on both sides)
    g = h[h.date.isin(our_dates)].copy()
    gp = gpd.GeoDataFrame(g, geometry=gpd.points_from_xy(g.lon, g.lat), crs=CRS_GEO)
    def inside(frame_scenes):
        keep = np.zeros(len(gp), bool)
        for d in our_dates:
            fp = frame_scenes[frame_scenes.date == d]
            if fp.empty:
                continue
            geom = fp.geometry.union_all() if hasattr(fp.geometry, "union_all") else fp.geometry.unary_union
            idx = gp.index[(gp.date == d).values]
            keep[gp.index.get_indexer(idx)] = gp.loc[idx].within(geom).values
        return gp[keep].drop(columns="geometry")

    g_in = inside(scenes)
    g_in_mission = {m: inside(scenes[scenes.mission == m]) for m in sorted(scenes.mission.unique())}
    g_all = gp.drop(columns="geometry")
    out = {"generated_utc": pd.Timestamp.now("UTC").isoformat(), "use": G.RESEARCH_TAG, "licence": G.LICENCE, "licence_url": G.LICENCE_URL,
           "caveat": G.GFW_CAVEAT, "method": "cell-date counts on GFW's grid (GFW reports cell centres, not positions); 0.01 degree cells also keyed by hour",
           "ours": {"file": "data/detections_regional.gpkg detections_regional_4326", "n": int(len(det)), "dates": our_dates,
                    "classes": "high and medium candidates (low class and fixed structures excluded)"},
           "gfw": {"dataset": st.get("sar_dataset_version", G.DATASETS["sar"]), "n_on_our_dates_aoi": int(g_all.detections.sum()),
                   "n_on_our_dates_in_our_scenes": int(g_in.detections.sum())}, "by_resolution": {}, "by_length_m": {}, "by_mission": {},
           "statement": ("GFW detections are another detector's output on the same Sentinel-1 scenes, not truth. GFW reports "
                         "industrial vessels and misses most under 15 m (GFW data caveats; Paolo et al. 2024 report >70 % at 25 m, "
                         ">90 % at 50 m, 60 % at 15 to 20 m by calibration). Agreement in a cell means both detectors saw "
                         "something there in that pass; it is not an object-level match. GFW's matched flag is AIS status by "
                         "GFW's matcher, research only. Dark does not mean illegal.")}
    frames = []
    for res, by_hour in ((0.01, True), (0.1, False), (0.25, False)):
        j = G.cell_date_match(det[["lon", "lat", "date", "hour"]], g_in[["lon", "lat", "date", "hour", "detections", "matched"]], res, by_hour=by_hour)
        out["by_resolution"][str(res)] = G.match_summary(j)
        frames.append(j)
        if res == 0.1:
            for lo, hi in LENGTH_BINS:
                sel = det[(det.length_est_m >= lo) & (det.length_est_m < hi)]
                jj = G.cell_date_match(sel[["lon", "lat", "date", "hour"]], g_in[["lon", "lat", "date", "hour", "detections", "matched"]], res)
                s = G.match_summary(jj)
                out["by_length_m"][f"{lo}-{hi}"] = {"n_ours": s["n_ours"], "ours_in_cells_with_gfw": s["ours_in_cells_with_gfw"], "ours_paired_share": s["ours_paired_share"],
                                                    "gfw_matched_share_in_shared_cells": s["gfw_matched_share_in_shared_cells"]}
            for mis in sorted(det.mission.unique()):
                sel = det[det.mission == mis]
                gm = g_in_mission.get(mis, g_in.iloc[0:0])
                jj = G.cell_date_match(sel[["lon", "lat", "date", "hour"]], gm[["lon", "lat", "date", "hour", "detections", "matched"]], res)
                out["by_mission"][mis] = G.match_summary(jj)
                out["by_mission"][mis]["note"] = "GFW detections inside this mission's scene footprints on the same dates"
            # cell convention: GFW's value is the cell centre (report docs, 'center of the grid cell'; the identity step's data
            # check agrees). Both sides are binned on GFW's grid (cell_key). The number under the old corner-anchored floor
            # is kept only to show what the misalignment cost.
            cols = ["lon", "lat", "date", "hour", "detections", "matched"]
            o_floor, g_floor = det[["lon", "lat", "date", "hour"]].copy(), g_in[cols].copy()
            for fr in (o_floor, g_floor):   # floor binning = GFW grid binning of points moved by half a cell
                fr["lon"], fr["lat"] = fr.lon - 0.005, fr.lat - 0.005
            out["cell_convention_0.01deg"] = {
                "gfw_grid_centre_anchored (used)": G.match_summary(G.cell_date_match(det[["lon", "lat", "date", "hour"]], g_in[cols], 0.01, by_hour=True))["ours_in_cells_with_gfw"],
                "corner_anchored_floor (old, wrong)": G.match_summary(G.cell_date_match(o_floor, g_floor, 0.01, by_hour=True))["ours_in_cells_with_gfw"],
                "note": "share of our candidates in a 0.01 degree cell-hour with a GFW detection; GFW's lon/lat is the cell centre "
                        "(https://globalfishingwatch.org/our-apis/documentation/docs/v3/4wings/report), so cells are centred on whole multiples of 0.01"}
            for conf in sorted(det.confidence.unique()):
                sel = det[det.confidence == conf]
                jj = G.cell_date_match(sel[["lon", "lat", "date", "hour"]], g_in[["lon", "lat", "date", "hour", "detections", "matched"]], res)
                out.setdefault("by_confidence", {})[conf] = G.match_summary(jj)
                out["by_confidence"][conf]["note"] = "ours restricted to this class; GFW side is all GFW detections in our scenes"
    allj = pd.concat(frames, ignore_index=True)
    allj["use"], allj["licence"] = G.RESEARCH_TAG, G.LICENCE_URL
    sar_ver = st.get("sar_dataset_version", G.DATASETS["sar"])
    out["attribution"] = G.attribution(sar_ver, (our_dates[0], our_dates[-1]), accessed(sar_ver, G.DATASETS["sar"]))
    out["accessed"] = accessed(sar_ver, G.DATASETS["sar"])
    G.write_parquet_parts(allj, RESEARCH / "radar_vs_gfw.parquet", G.research_tags(sar_ver, (our_dates[0], our_dates[-1]), out["accessed"],
                          table="our radar candidates against GFW SAR cell counts per cell and date (and hour at 0.01 degree), GFW grid"))
    (RESEARCH / "radar_vs_gfw.json").write_text(json.dumps(out, indent=1, default=str))
    log("compare:", json.dumps(out["by_resolution"], indent=None)[:600])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2026-09-01")
    ap.add_argument("--end", default=TODAY)
    ap.add_argument("--steps", default="sar,grids,events,vessels,outputs,compare")
    ap.add_argument("--events", default="gaps,encounters,loitering,port_visits")
    ap.add_argument("--vessel-max", type=int, default=40000)
    ap.add_argument("--offline", action="store_true", help="products from the cache only, no network")
    ap.add_argument("--refresh-empty", action="store_true", help="refetch cached reports that held no cells (late GFW days)")
    ap.add_argument("--high-presence", action="store_true", help="also request the AOI-wide HIGH presence grid (very large)")
    a = ap.parse_args()
    steps = a.steps.split(",")
    which = a.events.split(",")
    geom, gj, tol = aoi()
    log("AOI simplified with tolerance", tol, "deg")
    st = state_load()
    st.update({"aoi_simplify_tolerance_deg": tol, "window": [a.start, a.end]})
    c = G.GFWClient(token="unused", offline=True) if a.offline else G.GFWClient()
    if "sar" in steps:
        step_sar(c, gj, a.start, a.end, st, refresh_empty=a.refresh_empty)
    if "grids" in steps:
        step_grids(c, gj, a.start, a.end, st, refresh_empty=a.refresh_empty, high_presence=a.high_presence)
    if "events" in steps:
        step_events(c, gj, a.start, a.end, st, which)
    if "vessels" in steps:
        step_vessels(c, gj, a.start, a.end, st, which, a.vessel_max)
    if "outputs" in steps:
        step_outputs(c, gj, tol, a.start, a.end, st, which, a.vessel_max)
    if "compare" in steps:
        step_compare(c, gj, a.start, a.end, st)
    state_save(st)


if __name__ == "__main__":
    sys.exit(main())
