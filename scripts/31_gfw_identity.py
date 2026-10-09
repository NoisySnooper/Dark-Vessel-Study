"""AIS status and identity of the September regional radar contacts from Global Fishing Watch data: research build
only (CC BY-NC 4.0, noncommercial).

Purpose: give every contact of data/detections_regional.gpkg (78,615 candidates, 2026-09-20 to 2026-10-01) exactly one
of matched, unmatched or no_coverage, an identity (MMSI, name, call sign, IMO, flag, type, length) where GFW's data
support one, and the evidence for the rest, in the D1 schema of docs/PROJECT_BOARD.md.

Method (rules in darkvessel.ais.gfw_identity; the output's 'about' layer repeats them)
- Passes: the processed scenes grouped by mission and time (29 passes). For each pass one 4Wings report of GFW AIS
  presence (HIGH, 0.01 degree; HOURLY; grouped by vessel id) over the pass footprint for the pass hour and the hour
  on each side. The token allows one report at a time, so reports run in sequence and never from two processes.
- (a) GFW's SAR detections of the same scenes (cached by scripts/27_gfw_pull.py, step sar) paired by 0.01 degree cell
  (plus 8 neighbours) and 30 minutes, one to one; GFW's own AIS match gives the identity (match_method
  gfw_sar_cell_hour). A GEAR identity (an AIS net or gear buoy) is kept as GFW's match but flagged identity_kind =
  gear with low quality. (b) For contacts with no SAR pair, GFW AIS presence vessels whose hourly track, interpolated
  to the scene time, lies within 3 km and whose speed proxy is at most 10 km/h (gfw_presence_cell_hour, medium only
  for near-stationary vessels within 1 km, else low; GEAR buoys excluded). The thresholds come from a calibration of
  the presence tracks against the rule (a) matches, written to the summary. (c) no_coverage when GFW's AIS presence
  grid (LOW, DAILY, script 27 step grids) shows zero hours in the 0.3 degree block around the contact over the whole
  window; unmatched otherwise. (d) Evidence on every row: nearest AIS vessel and hour offset, vessels and gear buoys
  within 10 km, GFW gap events within 50 km and 24 h, encounters and loitering within 10 km and 24 h, GFW neural
  vessel type, AIS reach.
- Identity records of the matched vessels (and of every GFW-matched SAR detection in our passes, for the length
  check) from the vessels endpoint in batches of 100 (ids already in any cached batch are served from the cache);
  registry length where GFW publishes one.
- GFW cell values are cell centres (GFW docs); the data check (alignment check in the summary) confirms it.
- Access dates in attributions are the fetch dates of the cached responses, not the day of a rebuild.
- Checkpoints: every API response is cached under data/cache/gfw/; the step state is in pull_state.json; any step
  can be rerun from the cache with --offline.

Inputs: data/detections_regional.gpkg; data/cache/gfw/ (script 27 steps sar and grids);
  data/research/gfw_events_{gaps,encounters,loitering}.parquet (script 27 step outputs; evidence only, optional);
  data/ml/regional_cnn.parquet (CNN verifier scores of every regional contact, read-only, optional; falls back to
  data/ml/shared_cells_cnn.parquet, shared cells only).
Output (data/research/): regional_identity.gpkg (contacts_4326, contacts_utm49n, about; over 20 MB, rebuilt offline in
  minutes, so it stays out of git), regional_identity.parquet (canonical, licence and attribution in the file
  metadata), regional_identity_summary.json, gfw_vessels.parquet, gfw_presence_passes.parquet.
Usage: python scripts/31_gfw_identity.py --steps presence,identify,vessels,outputs
       python scripts/31_gfw_identity.py --steps outputs --offline        # rebuild the products from the cache
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
from darkvessel.ais import gfw_compare as C
from darkvessel.ais import gfw_identity as I
from darkvessel.config import CRS_GEO, CRS_UTM_REGIONAL, DARK_CAVEAT, DATA_DIR
from darkvessel.io import write_dual_crs

RESEARCH = DATA_DIR / "research"
STATE = DATA_DIR / "cache" / "gfw" / "pull_state.json"
NEEDED_IDS = DATA_DIR / "cache" / "gfw" / "identity_needed_vessel_ids.json"
NEURAL_TYPES = ["Likely Fishing", "Likely non-fishing", "Unknown"]
VESSEL_BATCH = 100
TODAY = dt.date.today().isoformat()
FETCH_DATES = G.cache_fetch_dates()   # when each dataset was actually pulled (attribution access dates)


def accessed(*keys: str) -> str:
    """Access date(s) of the named datasets from the cache, else today."""
    return G.accessed_text(FETCH_DATES, *keys, default=TODAY)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def state_load() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def state_save(s: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    merged = state_load()
    merged.update(s)
    STATE.write_text(json.dumps(merged, indent=1, default=str))


def days(start: str, end: str) -> list[str]:
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    return [(d0 + dt.timedelta(n)).isoformat() for n in range((d1 - d0).days + 1)]


def next_day(d: str) -> str:
    return (dt.date.fromisoformat(d) + dt.timedelta(1)).isoformat()


def aoi():
    """The AOI GeoJSON exactly as script 27 sends it, so both scripts share the report cache keys."""
    geom = gpd.read_file(DATA_DIR / "aoi.gpkg", layer="aoi_4326").geometry.iloc[0]
    gj, tol = G.aoi_geojson(geom, max_vertices=1000)
    return gj, tol


def load_contacts():
    det = pyogrio.read_dataframe(DATA_DIR / "detections_regional.gpkg", layer="detections_regional_4326", read_geometry=False)
    det["ts"] = pd.to_datetime(det.acq_utc, utc=True)
    det["date"] = det.ts.dt.strftime("%Y-%m-%d")
    scenes = pyogrio.read_dataframe(DATA_DIR / "detections_regional.gpkg", layer="scenes_processed_4326")
    passes = I.passes_from_scenes(scenes)
    det["pass_id"] = I.assign_pass(det, passes)
    if det.pass_id.isna().any():
        raise RuntimeError(f"{int(det.pass_id.isna().sum())} contacts without a pass")
    return det.reset_index(drop=True), passes


# -- cached GFW frames ------------------------------------------------------------------------------------------------
def sar_hourly(c: G.GFWClient, gj: dict, dates: list[str]) -> pd.DataFrame:
    frames = []
    for d in dates:
        for m in ("true", "false"):
            try:
                f = G.report_to_frame(c.report(G.DATASETS["sar"], (d, next_day(d)), gj, "HIGH", "HOURLY", filters=f"matched='{m}'"))
            except G.GFWError as e:
                log("sar hourly", d, m, "missing from cache:", str(e)[:80])
                continue
            if not f.empty:
                f["matched"] = m == "true"
                frames.append(f)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def sar_by_vessel(c: G.GFWClient, gj: dict, dates: list[str]) -> pd.DataFrame:
    """GFW-matched SAR detections grouped by vessel id (DAILY x HIGH): one row per vessel, cell and day."""
    frames = []
    for d in dates:
        try:
            f = G.report_to_frame(c.report(G.DATASETS["sar"], (d, next_day(d)), gj, "HIGH", "DAILY", filters="matched='true'", group_by="VESSEL_ID"))
        except G.GFWError:
            continue
        if not f.empty:
            frames.append(f)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def sar_neural(c: G.GFWClient, gj: dict, dates: list[str]) -> pd.DataFrame:
    frames = []
    for d in dates:
        for nt in NEURAL_TYPES:
            try:
                f = G.report_to_frame(c.report(G.DATASETS["sar"], (d, next_day(d)), gj, "HIGH", "DAILY", filters=f"neural_vessel_type='{nt}'"))
            except G.GFWError:
                continue
            if not f.empty:
                f["neural_vessel_type"] = nt
                frames.append(f)
    if not frames:
        return pd.DataFrame(columns=["lon", "lat", "date", "detections", "neural_vessel_type"])
    n = pd.concat(frames, ignore_index=True)
    n["date"] = pd.to_datetime(n.date, utc=True, errors="coerce").dt.strftime("%Y-%m-%d")
    return n[["lon", "lat", "date", "detections", "neural_vessel_type"]]


def presence_passes(c: G.GFWClient, passes, st: dict | None = None) -> pd.DataFrame:
    """One HOURLY x HIGH x VESSEL_ID presence report per pass (network when `c` is online, else cache only)."""
    frames = []
    for p in passes.itertuples():
        gj, tol = G.aoi_geojson(p.geometry, max_vertices=1000)
        rng = I.pass_window(p.t0, p.t1)
        t = time.time()
        try:
            resp = c.report(G.DATASETS["presence"], rng, gj, "HIGH", "HOURLY", group_by="VESSEL_ID")
        except G.GFWError as e:
            log("presence", p.pass_id, "failed:", str(e)[:160])
            if st is not None:
                st.setdefault("presence_pass_errors", {})[p.pass_id] = str(e)[:200]
            continue
        f = I.presence_rows(G.report_to_frame(resp), p.pass_id)
        frames.append(f)
        if not c.offline:
            log("presence", p.pass_id, rng, "rows", len(f), "vessels", f.vessel_id.nunique(), round(time.time() - t, 1), "s",
                c.last_headers.get("x-ratelimit-daily-current-usage", ""))
    if not frames:
        return pd.DataFrame(columns=["pass_id", "lon", "lat", "hour_ts", "hours"] + list(I._ID_FIELDS.values()))
    return pd.concat(frames, ignore_index=True)


def load_events() -> dict:
    out = {}
    for key in ("gaps", "encounters", "loitering"):
        parts = sorted(RESEARCH.glob(f"gfw_events_{key}.parquet")) + sorted(RESEARCH.glob(f"gfw_events_{key}_part*of*.parquet"))
        if parts:
            out[key] = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
        else:
            log("events", key, "not found (run scripts/27_gfw_pull.py --steps outputs); evidence column stays 0")
    return out


def load_cnn() -> tuple[pd.DataFrame | None, str | None]:
    """CNN verifier scores, read-only: data/ml/regional_cnn.parquet (every regional contact, docs/ml_verifier.md) when it
    exists, else data/ml/shared_cells_cnn.parquet (shared cells only). Returns (det_id, cnn_score, cnn_vessel) and the path."""
    for p in (DATA_DIR / "ml" / "regional_cnn.parquet", DATA_DIR / "ml" / "shared_cells_cnn.parquet"):
        if p.exists():
            cnn = pd.read_parquet(p, columns=["det_id", "cnn_score", "cnn_vessel"]).drop_duplicates("det_id")
            return cnn, str(p.relative_to(DATA_DIR.parent)) if p.is_relative_to(DATA_DIR.parent) else str(p)
    return None, None


# -- the build -----------------------------------------------------------------------------------------------------
def build(det: pd.DataFrame, passes, c_off: G.GFWClient, gj: dict, window: tuple[str, str], vessels: pd.DataFrame | None,
          events: dict, cnn: pd.DataFrame | None) -> tuple[pd.DataFrame, dict, set]:
    """Every rule applied to every contact from the cache. Returns (table, metadata, vessel ids whose identity the
    table needs). With vessels=None the pairing runs without lengths and the needed ids are the result."""
    meta = {}
    dates = sorted(det.date.unique())
    t = time.time()
    hourly = sar_hourly(c_off, gj, dates)
    sar = I.sar_detections(hourly, sar_by_vessel(c_off, gj, dates))
    meta["gfw_sar_detections_on_run_dates"] = int(len(sar))
    meta["gfw_sar_matched_on_run_dates"] = int(sar.matched.sum()) if len(sar) else 0
    meta["gfw_sar_matched_in_multi_detection_cells"] = int((sar.matched & (sar.n_in_cell > 1)).sum()) if len(sar) else 0
    meta["gfw_sar_matched_identity_recovered_from_by_vessel_report"] = int(sar.ambiguous_cell.sum()) if len(sar) else 0
    align = I.alignment_check(det[["lon", "lat", "ts"]], sar)
    meta["cell_alignment_check"] = align
    if align.get("shift_applied_deg"):
        sar = I.shift_cells(sar, align["shift_applied_deg"])
    log("sar detections", len(sar), "alignment", align.get("chosen"), round(time.time() - t), "s")

    vlen = None
    if vessels is not None and not vessels.empty and "length_m" in vessels:
        vlen = vessels.drop_duplicates("vessel_id").set_index("vessel_id").length_m.dropna()
    pa = I.pair_sar(det[["lon", "lat", "ts", "length_est_m"]], sar, vessel_length=vlen)
    out = det[["det_id", "scene_idx", "pass_id", "mission", "acq_utc", "ts", "date", "lon", "lat", "length_est_m", "confidence"]].copy()
    for col in ("gfw_sar_pair", "match_dist_m", "match_dt_s", "gfw_sar_n_cand", "gfw_sar_n_rivals", "gfw_sar_ambiguous_cell"):
        out[col] = pa[col].values
    sar_idx = np.clip(pa.gfw_sar_idx.values, 0, None)
    vid_at = pd.Series(sar.vessel_id.values[sar_idx]) if len(sar) else pd.Series([None] * len(out))
    has_sar_match = (pa.gfw_sar_pair.values == "matched") & vid_at.notna().values
    no_identity = (pa.gfw_sar_pair.values == "matched") & ~has_sar_match
    out.loc[no_identity, "gfw_sar_pair"] = "matched_no_identity"
    out["gfw_vessel_id"] = np.where(has_sar_match, vid_at.values, None)
    out.loc[~has_sar_match, "gfw_vessel_id"] = None
    out["match_method"] = np.where(has_sar_match, I.MATCH_SAR, None)
    out.loc[~has_sar_match, "match_method"] = None
    len_ais = pa.length_ais_m.values
    out["match_quality"] = None
    gear_at = (I.is_gear(sar.gfw_vessel_type.values[sar_idx], sar.gfw_geartype.values[sar_idx]) if len(sar) else np.zeros(len(out), bool)) & has_sar_match
    q = I.sar_quality(pa.match_dist_m.values, pa.gfw_sar_n_cand.values, pa.gfw_sar_n_rivals.values, det.length_est_m.values, len_ais,
                      pa.gfw_sar_ambiguous_cell.values, gear=gear_at)
    out.loc[has_sar_match, "match_quality"] = q[has_sar_match]
    meta["gfw_sar_matched_to_gear_buoy"] = int(gear_at.sum())
    out.loc[~has_sar_match, ["match_dist_m", "match_dt_s"]] = np.nan
    log("sar pairs: matched", int(has_sar_match.sum()), "matched without identity", int(no_identity.sum()),
        "unmatched pairs", int((pa.gfw_sar_pair.values == "unmatched").sum()))
    # vessel ids whose identity the length check needs: every GFW-matched detection within a pass window
    needed = set()
    if len(sar):
        pass_t0 = passes.t0.values.astype("datetime64[s]").astype("int64")
        pass_t1 = passes.t1.values.astype("datetime64[s]").astype("int64")
        s_ts = sar.ts.values.astype("datetime64[s]").astype("int64")
        in_pass = np.zeros(len(sar), bool)
        for a, b in zip(pass_t0, pass_t1):
            in_pass |= (s_ts >= a - 1800) & (s_ts <= b + 1800)
        needed |= set(sar.vessel_id[sar.matched.values & in_pass].dropna())

    # (b) presence per pass, (d) nearest vessels
    pres = presence_passes(c_off, passes)
    meta["presence_rows"] = int(len(pres))
    meta["presence_passes_with_rows"] = int(pres.pass_id.nunique()) if len(pres) else 0
    out["pres_vessel_id"], out["nearest_ais_vessel_id"], out["nearest_ais_mmsi"], out["nearest_ais_name"] = None, None, None, None
    out["nearest_ais_dist_m"], out["nearest_ais_dt_s"], out["n_ais_10km"], out["n_gear_10km"] = np.nan, np.nan, 0, 0
    out["pres_speed_kmh"], out["pres_n_cells"], out["pres_n_cand"] = np.nan, 0, 0
    identity_rows = [sar.loc[sar.matched & sar.vessel_id.notna(), list(I._ID_FIELDS.values())]]
    cal_contacts, cal_presence = [], []
    t = time.time()
    for p in passes.itertuples():
        sel = np.flatnonzero((out.pass_id == p.pass_id).values)
        pp = pres[pres.pass_id == p.pass_id].reset_index(drop=True)
        if not len(sel) or pp.empty:
            continue
        sub = out.iloc[sel]
        gear_rows = I.is_gear(pp.gfw_vessel_type.values, pp.gfw_geartype.values)
        near = I.nearest_presence(sub[["lon", "lat", "ts"]], pp[~gear_rows].reset_index(drop=True))   # vessels, not gear buoys
        for col in near.columns:
            out.iloc[sel, out.columns.get_loc(col)] = near[col].values
        if gear_rows.any():
            out.iloc[sel, out.columns.get_loc("n_gear_10km")] = I.nearest_presence(sub[["lon", "lat", "ts"]], pp[gear_rows].reset_index(drop=True)).n_ais_10km.values
        # the rule (a) matches of this pass calibrate rule (b): where was the GFW-identified vessel's hourly cell?
        ref = sub[has_sar_match[sel] & ~gear_at[sel]]
        if len(ref):
            cal_contacts.append(ref[["lon", "lat", "ts", "gfw_vessel_id"]].rename(columns={"gfw_vessel_id": "vessel_id"}).assign(pass_id=p.pass_id))
            cal_presence.append(pp)
        # GFW-matched SAR vessels of this pass are already placed by GFW's matcher: not presence candidates
        s_in = sar[(sar.ts >= p.t0 - pd.Timedelta(minutes=30)) & (sar.ts <= p.t1 + pd.Timedelta(minutes=30)) & sar.matched]
        exclude = set(s_in.vessel_id.dropna())
        free = sel[(sub.gfw_sar_pair.isna() | (sub.gfw_sar_pair == "matched_no_identity")).values]
        if len(free):
            pb = I.pair_presence(out.iloc[free][["lon", "lat", "ts"]], pp, exclude_vessels=exclude)
            got = pb.pres_vessel_id.notna().values
            idx = free[got]
            out.iloc[idx, out.columns.get_loc("pres_vessel_id")] = pb.pres_vessel_id.values[got]
            out.iloc[idx, out.columns.get_loc("gfw_vessel_id")] = pb.pres_vessel_id.values[got]
            out.iloc[idx, out.columns.get_loc("match_method")] = I.MATCH_PRESENCE
            out.iloc[idx, out.columns.get_loc("match_quality")] = pb.match_quality.values[got]
            out.iloc[idx, out.columns.get_loc("match_dist_m")] = pb.match_dist_m.values[got]
            out.iloc[idx, out.columns.get_loc("match_dt_s")] = pb.match_dt_s.values[got]
            out.iloc[idx, out.columns.get_loc("pres_speed_kmh")] = pb.pres_speed_kmh.values[got]
            out.iloc[idx, out.columns.get_loc("pres_n_cells")] = pb.pres_n_cells.values[got]
            out.iloc[free, out.columns.get_loc("pres_n_cand")] = pb.pres_n_cand.values
            out.iloc[free, out.columns.get_loc("gfw_sar_n_cand")] = 0
            identity_rows.append(pp[pp.vessel_id.isin(set(pb.pres_vessel_id.dropna()))][list(I._ID_FIELDS.values())])
    log("presence pairs", int(out.pres_vessel_id.notna().sum()), "nearest vessel known for", int(out.nearest_ais_mmsi.notna().sum()),
        round(time.time() - t), "s")
    if cal_contacts:
        meta["presence_calibration"] = I.presence_calibration(pd.concat(cal_contacts, ignore_index=True), pd.concat(cal_presence, ignore_index=True))
        log("presence calibration on", meta["presence_calibration"].get("n_reference"), "GFW-identified contacts")
    needed |= set(out.gfw_vessel_id.dropna())

    # (c) status, reach
    all_days = days(*window)
    low = I.presence_daily(c_off, gj, all_days)
    n_days = int(low.date.nunique()) if len(low) else 0
    meta["presence_low_daily"] = {"days_with_data": n_days, "first": low.date.min() if n_days else None, "last": low.date.max() if n_days else None,
                                  "cells_rows": int(len(low))}
    h_day, h_win = I.presence_block_hours(out[["lon", "lat", "date"]], low)
    out["ais_presence_h_day"], out["ais_presence_h_window"] = h_day, h_win
    out["ais_reach"] = I.ais_reach(out[["lon", "lat"]], low, n_days)
    has_vessel = out.gfw_vessel_id.notna().values
    out["ais_status"] = I.ais_status(has_vessel, h_win)
    log("status", out.ais_status.value_counts().to_dict())

    # (d) events and neural type
    gaps = events.get("gaps")
    if gaps is not None and not gaps.empty:
        cnt, near_km = I.gaps_nearby(out[["lon", "lat", "ts"]], gaps, 50, 24)
    else:
        cnt, near_km = np.zeros(len(out), int), np.full(len(out), np.nan)
    out["n_gfw_gaps_50km_24h"], out["nearest_gfw_gap_km"] = cnt, near_km
    for key, col in (("encounters", "n_gfw_encounters_10km_24h"), ("loitering", "n_gfw_loitering_10km_24h")):
        ev = events.get(key)
        out[col] = I.events_nearby(out[["lon", "lat", "ts"]], ev, 10, 24) if ev is not None else 0
    neural = sar_neural(c_off, gj, dates)
    if align.get("shift_applied_deg") and len(neural):
        neural = I.shift_cells(neural, align["shift_applied_deg"])
    typed = C.assign_gfw_type(out[["det_id", "lon", "lat", "date"]].reset_index(drop=True), neural, max_dist_m=1000.0)
    out["gfw_neural_type"] = typed.gfw_type.values

    # identity
    ident = pd.concat(identity_rows, ignore_index=True).dropna(subset=["vessel_id"]).drop_duplicates("vessel_id") if identity_rows else pd.DataFrame(columns=list(I._ID_FIELDS.values()))
    merged = I.merge_identity(ident, vessels).set_index("vessel_id")
    for col in ("mmsi", "imo", "vessel_name", "call_sign", "flag", "ship_type", "length_ais_m", "identity_source", "gfw_geartype"):
        out[col] = out.gfw_vessel_id.map(merged[col]) if col in merged else None
    out.loc[~has_vessel, "identity_source"] = None
    gear = I.is_gear(out.ship_type.values, out.gfw_geartype.values) & has_vessel
    out["identity_kind"] = np.where(~has_vessel, None, np.where(gear, "gear", np.where(out.ship_type.notna(), "vessel", "unknown")))
    out.loc[~has_vessel, "identity_kind"] = None
    out.loc[gear, "match_quality"] = "low"   # a buoy is not the vessel's identity, whatever the method
    meta["matched_gear_identity"] = int(gear.sum())
    out["run_id"], out["ais_source"], out["research_only"], out["caveat"] = I.RUN_ID, "gfw", True, DARK_CAVEAT
    if cnn is not None:
        m = out[["det_id"]].merge(cnn, on="det_id", how="left")
        out["cnn_score"], out["cnn_vessel"] = m.cnn_score.values, m.cnn_vessel.astype("boolean").values
    else:
        out["cnn_score"], out["cnn_vessel"] = np.nan, pd.array([None] * len(out), dtype="boolean")
    out["n_ais_10km"] = out.n_ais_10km.fillna(0).astype(int)
    for c in ("match_dist_m", "match_dt_s", "nearest_ais_dist_m", "nearest_ais_dt_s", "ais_reach", "length_ais_m"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    meta["identity_vessels_used"] = merged.reset_index()
    return out, meta, needed


def hand_check_sample(df: pd.DataFrame, sar_raw: pd.DataFrame, pres: pd.DataFrame, n_high: int = 10, n_other: int = 3) -> list[dict]:
    """A stratified sample for the hand check: n_high SAR matches of quality high (one per pass where possible), then
    n_other each of SAR low, presence medium and presence low, with at least one GEAR identity and one moving vessel
    (speed proxy over 3 km/h) among the presence picks when they exist. Each row carries the raw GFW report rows it
    paired with (SAR: the matched cell row of that vessel on that date; presence: the vessel's hourly cells in the pass)."""
    def pick(sel, n, sort_cols):
        m = df[sel].sort_values(sort_cols)
        p = m.groupby("pass_id", sort=True).head(1).head(n)
        if len(p) < n:
            p = pd.concat([p, m[~m.det_id.isin(p.det_id)].head(n - len(p))])
        return p.assign(sample_group=None)

    groups = [("sar_high", (df.match_method == I.MATCH_SAR) & (df.match_quality == "high"), n_high),
              ("sar_low", (df.match_method == I.MATCH_SAR) & (df.match_quality == "low"), n_other),
              ("presence_medium", (df.match_method == I.MATCH_PRESENCE) & (df.match_quality == "medium"), n_other),
              ("presence_low", (df.match_method == I.MATCH_PRESENCE) & (df.match_quality == "low"), n_other)]
    picks = []
    for name, sel, n in groups:
        p = pick(sel, n, ["pass_id", "det_id"])
        p["sample_group"] = name
        picks.append(p)
    extra = [("gear_identity", (df.identity_kind == "gear") & (df.match_method == I.MATCH_SAR)),
             ("presence_moving", (df.match_method == I.MATCH_PRESENCE) & (df.pres_speed_kmh > 3))]
    chosen_ids = set(pd.concat(picks).det_id)
    for name, sel in extra:
        if not (sel & df.det_id.isin(chosen_ids)).any() and sel.any():
            p = df[sel].sort_values(["pass_id", "det_id"]).head(1).assign(sample_group=name)
            picks.append(p), chosen_ids.update(p.det_id)
    rows = []
    for r in pd.concat(picks).itertuples():
        raw = []
        if r.match_method == I.MATCH_SAR and "vesselId" in sar_raw:
            g = sar_raw[sar_raw.vesselId == r.gfw_vessel_id]
            g = g[pd.to_datetime(g.entryTimestamp, utc=True, errors="coerce").dt.strftime("%Y-%m-%d") == r.date] if len(g) else g
            raw = [{"lon_cell": float(x.lon), "lat_cell": float(x.lat), "entryTimestamp": x.entryTimestamp, "date": x.date, "mmsi": x.mmsi,
                    "shipName": x.shipName, "vesselType": getattr(x, "vesselType", None)} for x in g.head(3).itertuples()]
        elif r.match_method == I.MATCH_PRESENCE and len(pres):
            g = pres[(pres.pass_id == r.pass_id) & (pres.vessel_id == r.gfw_vessel_id)].sort_values("hour_ts")
            raw = [{"lon_cell": float(x.lon), "lat_cell": float(x.lat), "hour": pd.Timestamp(x.hour_ts).strftime("%Y-%m-%dT%H:%M"), "hours": float(x.hours),
                    "mmsi": x.mmsi, "shipName": x.ship_name, "vesselType": x.gfw_vessel_type} for x in g.itertuples()]
        rows.append({"sample_group": r.sample_group, "det_id": r.det_id, "pass_id": r.pass_id, "acq_utc": r.acq_utc, "lon": round(r.lon, 4), "lat": round(r.lat, 4),
                     "length_est_m": r.length_est_m, "confidence": r.confidence, "match_method": r.match_method, "match_quality": r.match_quality,
                     "identity_kind": r.identity_kind, "gfw_vessel_id": r.gfw_vessel_id, "mmsi": r.mmsi, "vessel_name": r.vessel_name, "flag": r.flag,
                     "ship_type": r.ship_type, "length_ais_m": r.length_ais_m, "match_dist_m": r.match_dist_m, "match_dt_s": r.match_dt_s,
                     "pres_speed_kmh": r.pres_speed_kmh, "pres_n_cells": r.pres_n_cells, "gfw_raw_rows": raw})
    return rows


def cols_for_about(df: pd.DataFrame) -> list[str]:
    return [c for c in I.D1_COLUMNS + I.EVIDENCE_COLUMNS if c in df.columns]


def write_outputs(df: pd.DataFrame, about: pd.DataFrame, vessels_used: pd.DataFrame, summary: dict, tags: dict):
    """GeoPackage (dual CRS plus about), parquet (canonical, `tags` in the file metadata), vessels parquet, summary JSON.
    Every GeoPackage row carries the full dark caveat (D1); the file is over 20 MB and is rebuilt offline, so it is not
    for git. The parquet keeps the same table with licence, attribution and caveat in its metadata."""
    RESEARCH.mkdir(parents=True, exist_ok=True)
    cols = [c for c in I.D1_COLUMNS + I.EVIDENCE_COLUMNS if c in df.columns]
    gpkg = RESEARCH / "regional_identity.gpkg"
    if gpkg.exists():
        gpkg.unlink()
    g = df[cols].copy()
    g["cnn_vessel"] = g.cnn_vessel.astype("boolean").astype("Int16")   # 1 accepts, 0 rejects, null not scored (bool in the parquet)
    g["acq_utc"] = g.acq_utc.astype(str)
    for c in g.columns:
        if g[c].dtype == object:
            g[c] = g[c].where(g[c].notna(), None)
    gdf = gpd.GeoDataFrame(g, geometry=gpd.points_from_xy(g.lon, g.lat), crs=CRS_GEO)
    write_dual_crs(gdf, gpkg, "contacts", utm_crs=CRS_UTM_REGIONAL, spatial_index=False)
    pyogrio.write_dataframe(about, gpkg, layer="about", driver="GPKG")
    written = G.write_parquet_parts(df[cols], RESEARCH / "regional_identity.parquet", {**tags, "table": "AIS status and identity of every contact of the September regional run (D1 schema plus evidence columns)"})
    written += G.write_parquet_parts(vessels_used, RESEARCH / "gfw_vessels.parquet", {**tags, "table": "GFW identity records of the vessel ids used by regional_identity"})
    (RESEARCH / "regional_identity_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    for p in [gpkg] + written + [RESEARCH / "regional_identity_summary.json"]:
        log("wrote", p.name, round(p.stat().st_size / 1e6, 1), "MB")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--steps", default="presence,identify,vessels,outputs")
    ap.add_argument("--start", default="2026-09-01", help="window of the AIS presence grid (script 27 step grids)")
    ap.add_argument("--end", default="2026-10-08")
    ap.add_argument("--offline", action="store_true", help="cache only, no network")
    a = ap.parse_args()
    steps = a.steps.split(",")
    gj, tol = aoi()
    det, passes = load_contacts()
    log("contacts", len(det), "passes", len(passes), "AOI tolerance", tol)
    st = state_load()
    c_off = G.GFWClient(token="unused", offline=True)
    c = c_off if a.offline else G.GFWClient()
    window = (a.start, a.end)
    if "presence" in steps:
        pres = presence_passes(c, passes, st)
        pres["use"], pres["licence"] = G.RESEARCH_TAG, G.LICENCE_URL
        RESEARCH.mkdir(parents=True, exist_ok=True)
        pres_ver = st.get("presence_dataset_version", G.DATASETS["presence"])
        G.write_parquet_parts(pres, RESEARCH / "gfw_presence_passes.parquet",
                              G.research_tags(pres_ver, (str(passes.t0.min())[:10], str(passes.t1.max())[:10]), G.accessed_text(G.cache_fetch_dates(), pres_ver, default=TODAY),
                                              table="GFW AIS presence per radar pass: one row per vessel, 0.01 degree cell and hour (HOURLY, HIGH, by vessel id)"))
        st["identity_presence_passes"] = int(pres.pass_id.nunique())
        state_save(st)
        log("presence passes", st["identity_presence_passes"], "rows", len(pres), "wrote gfw_presence_passes.parquet")
    events, (cnn, cnn_source) = load_events(), load_cnn()
    log("cnn scores from", cnn_source or "none", 0 if cnn is None else len(cnn))
    if "identify" in steps:
        _, meta, needed = build(det, passes, c_off, gj, window, None, events, cnn)
        NEEDED_IDS.write_text(json.dumps(sorted(needed)))
        st["identity_needed_vessel_ids"] = len(needed)
        state_save(st)
        log("needed vessel ids", len(needed), "->", NEEDED_IDS.name)
    if "vessels" in steps:
        ids = json.loads(NEEDED_IDS.read_text()) if NEEDED_IDS.exists() else []
        t = time.time()
        entries = c.vessels(ids, batch=VESSEL_BATCH, skip_missing=a.offline,
                            progress=lambda i, n: log("vessels", i, "of", n) if i % 2000 < VESSEL_BATCH else None)
        st["identity_vessels_fetched"] = len(ids)
        state_save(st)
        log("vessels", len(entries), "entries for", len(ids), "ids in", round(time.time() - t), "s")
    if "outputs" in steps:
        ids = json.loads(NEEDED_IDS.read_text()) if NEEDED_IDS.exists() else []
        vessels = G.vessels_to_frame(c_off.vessels(ids, batch=VESSEL_BATCH, skip_missing=True)) if ids else None
        log("vessel identities from cache:", 0 if vessels is None else len(vessels))
        df, meta, _ = build(det, passes, c_off, gj, window, vessels, events, cnn)
        versions = {k: st.get(f"{k}_dataset_version", G.DATASETS[k]) for k in ("sar", "presence", "gaps", "encounters", "loitering")}
        versions["vessels"] = (vessels.dataset_version.dropna().iloc[0] if vessels is not None and len(vessels) and vessels.dataset_version.notna().any()
                               else G.DATASETS["vessels"])
        acc = {k: accessed(v, *(("events",) if k in ("gaps", "encounters", "loitering", "port_visits") else ())) for k, v in versions.items()}
        acc_all = accessed(*versions.values())
        summary = {"generated_utc": pd.Timestamp.now("UTC").isoformat(), "run_id": I.RUN_ID, "use": G.RESEARCH_TAG, "licence": G.LICENCE,
                   "licence_url": G.LICENCE_URL, "terms_url": G.TERMS_URL, "accessed": acc_all, "accessed_by_dataset": acc,
                   "attribution": {k: G.attribution(v, window, acc[k]) for k, v in versions.items()},
                   "caveat": DARK_CAVEAT, "gfw_caveat": G.GFW_CAVEAT, "cnn_source": cnn_source,
                   "datasets": versions, "window_presence_grid": list(window), "run_dates": sorted(det.date.unique()),
                   "passes": [{"pass_id": p.pass_id, "mission": p.mission, "t0": p.t0.isoformat(), "t1": p.t1.isoformat(), "n_scenes": p.n_scenes,
                               "presence_window": list(I.pass_window(p.t0, p.t1))} for p in passes.itertuples()],
                   "rules": I.RULES, "sources": G.SOURCES, **{k: v for k, v in meta.items() if k != "identity_vessels_used"}}
        summary.update(I.summarize(df))
        sar_raw = sar_hourly(c_off, gj, sorted(det.date.unique()))
        pres_all = presence_passes(c_off, passes)
        summary["hand_check_sample"] = hand_check_sample(df, sar_raw, pres_all)
        vessels_used = meta["identity_vessels_used"].copy()
        if vessels is not None and len(vessels):
            vessels_used = vessels_used.merge(vessels.drop(columns=[c for c in ("ssvid", "shipname", "callsign", "imo", "flag") if c in vessels]),
                                              on="vessel_id", how="left")
        vessels_used["identity_kind"] = np.where(I.is_gear(vessels_used.ship_type.values, vessels_used.gfw_geartype.values), "gear",
                                                 np.where(vessels_used.ship_type.notna(), "vessel", "unknown"))
        vessels_used["use"], vessels_used["licence"] = G.RESEARCH_TAG, G.LICENCE_URL
        about = I.about_rows(versions, acc, window, extra={"aoi_simplify_tolerance_deg": tol, "n_contacts": int(len(df)),
                                                           "n_passes": int(len(passes)), "cell_alignment": json.dumps(meta["cell_alignment_check"]),
                                                           "presence_calibration": json.dumps(meta.get("presence_calibration", {})),
                                                           "cnn_vessel_in_gpkg": "1 = CNN verifier accepts, 0 = rejects, null = not scored (boolean in the parquet)",
                                                           "cnn_source": cnn_source or "none",
                                                           "columns_gpkg": ", ".join(cols_for_about(df))})
        tags = G.research_tags(versions["sar"], window, acc_all, datasets=json.dumps(versions), accessed_by_dataset=json.dumps(acc),
                               run_id=I.RUN_ID, dark_caveat=DARK_CAVEAT)
        write_outputs(df, about, vessels_used, summary, tags)
        log("by status", summary["by_status"], "by method", summary["by_method_quality"])
    state_save(st)


if __name__ == "__main__":
    sys.exit(main())
