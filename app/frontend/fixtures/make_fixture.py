"""Build a small development bundle for the SCS Vessel Watch frontend in the contract 6.2 format.

Purpose: the single-file bundle builder (app/build/, round 3) is not written yet, so the frontend needs a bundle
in the exact encodings of app/CONTRACT.md section 6.2 to develop and smoke-test against. This script writes one
from real open-build files, plus a handful of SYNTHETIC leads and synthetic AIS statuses that are flagged as such
in every record (data/leads_open.gpkg does not exist yet and no live contact is matched or unmatched so far).
It is a test fixture, not a data product: meta.fixture.synthetic is true and the page shows a FIXTURE tag.

Method: read a few hundred live and September contacts (CNN scores joined from data/ml/regional_cnn.parquet),
fixed structures, VIIRS lights, aisstream vessels and tracks, planned and processed passes, and the land, AOI,
reporting-box and Marine Regions boundary-line geometry; encode bulk parts as little-endian typed columns in
base64 (i32/u32/i16/u16/u8/f32/bool8/dict8/dict16/time/detid/lightid/ref16/const/str) and geometry in the
`geom` encoding (i32 lon/lat x 10,000, ring and feature offsets); write one JSON object per part.

Inputs (open build only; never data/research/):
  data/live/live_contacts.gpkg (contacts_4326, scenes_4326, about), data/detections_regional.gpkg
  (detections_regional_4326, scenes_processed_4326), data/ml/regional_cnn.parquet, data/structures_regional.gpkg,
  data/viirs_lights.gpkg (viirs_lights_4326), data/ais_live.gpkg (vessels_latest_4326, tracks_4326,
  s1_next_passes_4326, about), data/ais_live_summary.json, data/s1_next_passes.json, data/aoi.gpkg,
  data/eez_marineregions.gpkg (eez_boundaries_4326 only), Natural Earth 10 m land (darkvessel.aoi).
Output: app/frontend/fixtures/bundle_small.json, {"parts": {name: part}} with the 6.2 part objects; under 2 MB.
Usage: /home/user/.mamba/envs/darkvessel/bin/python app/frontend/fixtures/make_fixture.py [--out PATH]
       [--live 300 --regional 300 --structures 100 --lights 300]
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

import darkvessel  # noqa: F401  sets PROJ_DATA before pyogrio and rasterio
import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
from shapely.geometry import box, mapping

from darkvessel.aoi import aoi_gdf, natural_earth_land
from darkvessel.config import DARK_CAVEAT_SHORT, DATA_DIR, REPO_ROOT
from darkvessel.leads.rules import CHANGE_INDICATORS, LAWFUL_EXPLANATIONS
from darkvessel.live.mid import flag_from_mmsi
from darkvessel.ocean.grid import REPORTING_BOXES

CONTRACT_VERSION = "1.2.0"
EPOCH = "2026-09-01T00:00:00Z"
EPOCH_TS = pd.Timestamp(EPOCH)
PRODUCT_CAVEAT = (
    "'Dark' means only that no AIS position was matched to this radar contact. It does not mean illegal. "
    "Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and both satellite and "
    "terrestrial AIS have blind spots: satellite AIS misses messages in busy coastal waters, and shore receivers "
    "cover only the waters within their radio range. Treat every unmatched contact as a lead for review, not as "
    "evidence of wrongdoing. An AIS gap is not proof of intent."
)
BUILD_LABEL = "Open build. Open-licensed sources and live AIS relayed by aisstream.io."
FIXTURE_NOTE = ("Development fixture built by app/frontend/fixtures/make_fixture.py. Real open-build records, plus "
                "SYNTHETIC leads and synthetic AIS statuses (rows with synthetic = true) that exercise the views. "
                "Not a data product.")
CNN_THRESHOLD = 0.631783
AIS_PASS_FIELDS = ["ais_aoi_positions", "ais_aoi_mmsi", "ais_footprint_positions", "ais_footprint_mmsi", "ais_near_footprint_mmsi"]
CNN_MODEL_ID = "verifier_v0_356af0ca"
PRIORITY_MODEL_ID = "priority_v0_fixture"
ACCESS_DATE = dt.date.today().isoformat()

# Source registry (contract section 2), open-build entries only. URLs are re-resolved by the task that runs this
# script; the frontend shows them on the About view and in provenance chips.
SOURCES = [
    {"key": "s1_grd", "name": "Copernicus Sentinel-1C/1D IW GRD, AWS Open Data mirror sentinel-s1-l1c",
     "method": "input to every radar product", "script": "src/darkvessel/s1/aws.py",
     "licence": "Copernicus Sentinel Data Legal Notice: free, full and open",
     "licence_url": "https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice",
     "url": "https://registry.opendata.aws/sentinel-1/", "credit": "Contains modified Copernicus Sentinel data 2026"},
    {"key": "det_regional", "name": "September regional run, 119 scenes, 2026-09-20 to 2026-10-01",
     "method": "CA-CFAR, PFA 1e-6, VV/VH fusion, clutter-zone and near-fixed rules, persistence",
     "script": "scripts/09_run_regional.py", "licence": "derived from s1_grd", "licence_url": None, "url": None,
     "credit": "Contains modified Copernicus Sentinel data 2026"},
    {"key": "det_live", "name": "Live passes", "method": "same detector settings as the regional run",
     "script": "scripts/30_live_pass.py (darkvessel.live)", "licence": "derived from s1_grd", "licence_url": None,
     "url": None, "credit": "Contains modified Copernicus Sentinel data 2026"},
    {"key": "cnn_v0", "name": "CNN verifier verifier_v0 (model id verifier_v0_356af0ca, threshold 0.631783)",
     "method": "64 x 64 px VV/VH chips in dB; held-out precision 0.77 and recall 0.75 on Sentinel-1A/1B labels",
     "script": "scripts/32_cnn_regional.py, darkvessel.live",
     "licence": "trained on Allen Institute for AI Sentinel-1A/1B vessel point labels, Apache-2.0",
     "licence_url": "https://www.apache.org/licenses/LICENSE-2.0.txt",
     "url": "https://raw.githubusercontent.com/allenai/vessel-detection-sentinels/main/README.md", "credit": None},
    {"key": "aisstream", "name": "aisstream.io websocket relay of shore receivers",
     "method": "recorder scripts/26_ais_record.py; matching darkvessel.live", "script": "scripts/26_ais_record.py",
     "licence": "UNVERIFIED: the operator publishes no terms of use (only a privacy policy)",
     "licence_url": "https://aisstream.io/privacypolicy", "url": "https://aisstream.io/documentation",
     "credit": "Live AIS relayed by aisstream.io; terms UNVERIFIED"},
    {"key": "mid_itu", "name": "ITU Table of Maritime Identification Digits", "method": "darkvessel.live.mid",
     "script": "src/darkvessel/live/mid.py", "licence": "ITU public table", "licence_url": None,
     "url": "https://www.itu.int/en/ITU-R/terrestrial/fmd/Pages/mid.aspx", "credit": None},
    {"key": "viirs_dnb", "name": "VIIRS Day/Night Band SDR and GEO, JRR cloud mask (S-NPP, NOAA-20, NOAA-21), NOAA JPSS on AWS",
     "method": "spike detection against the local background, cloud mask, moon illumination",
     "script": "scripts/15_viirs_lights.py", "licence": "NOAA open data: open to the public and can be used as desired",
     "licence_url": "https://registry.opendata.aws/noaa-jpss/", "url": "https://registry.opendata.aws/noaa-jpss/",
     "credit": "NOAA JPSS VIIRS"},
    {"key": "natural_earth", "name": "Natural Earth 10 m land, marine areas, ports", "method": "AOI, coast, land",
     "script": "src/darkvessel/aoi.py", "licence": "public domain",
     "licence_url": "https://www.naturalearthdata.com/about/terms-of-use/", "url": "https://www.naturalearthdata.com/",
     "credit": "Made with Natural Earth"},
    {"key": "marineregions_v12", "name": "Marine Regions Maritime Boundaries v12 (Flanders Marine Institute, VLIZ), doi:10.14284/632",
     "method": "WFS download, clipped to the AOI frame", "script": "scripts/22_static_layers.py", "licence": "CC BY 4.0",
     "licence_url": "https://creativecommons.org/licenses/by/4.0/", "url": "https://doi.org/10.14284/632",
     "credit": "Flanders Marine Institute (2023). Maritime Boundaries Geodatabase, version 12. https://doi.org/10.14284/632"},
    {"key": "esa_acq_plan", "name": "ESA Sentinel-1 acquisition plan KML files", "method": "darkvessel.ais.s1_passes",
     "script": "scripts/28_ais_reach.py", "licence": "ESA public plan; repeat predictions are not ESA's plan",
     "licence_url": None, "url": "https://sentinels.copernicus.eu/copernicus/sentinel-1/acquisition-plans", "credit": None},
    {"key": "analyst", "name": "Owner labels and lead decisions", "method": "the app", "script": "app/frontend",
     "licence": "the owner's", "licence_url": None, "url": None, "credit": None},
    {"key": "app", "name": "values computed by the product (review priority, evidence counts)",
     "method": "priority_v0_fixture: uncalibrated start weights of docs/product_design.md section 4.1",
     "script": "app/frontend/fixtures/make_fixture.py", "licence": "derived", "licence_url": None, "url": None, "credit": None},
    {"key": "fixture_synthetic", "name": "SYNTHETIC fixture values (not observed)",
     "method": "invented by make_fixture.py to exercise the matched, unmatched and lead views; every such row has synthetic = true",
     "script": "app/frontend/fixtures/make_fixture.py", "licence": "none: test data", "licence_url": None, "url": None,
     "credit": None},
]
for s in SOURCES:
    s.setdefault("access_date", ACCESS_DATE)
    s.setdefault("research_only", False)

# Explanation and indicator codes exactly as the lead builder writes them (the frontend maps codes to sentences).
LAWFUL = {t: list(v) for t, v in LAWFUL_EXPLANATIONS.items()}
CHANGE = {t: list(v) for t, v in CHANGE_INDICATORS.items()}
STATES = ["new", "reviewing", "closed_explained", "closed_unexplained", "closed_false_alarm"]
REASONS = ["no carriage requirement", "VMS fleet", "outside AIS reach", "weather or sea clutter", "fixed structure",
           "fishing lights", "pilot or supply transfer", "sea clutter", "rain cell", "ambiguity or sidelobe", "duplicate", "other"]


# ----------------------------------------------------------------------------------------------- encoders
def b64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")


def col_const(v):
    return {"t": "const", "v": v}


def col_str(values) -> dict:
    out = []
    for v in values:
        if v is None or (isinstance(v, float) and np.isnan(v)) or (isinstance(v, str) and v == ""):
            out.append(None)
        else:
            out.append(str(v))
    return {"t": "str", "v": out}


def col_dict(values, dict_values=None) -> dict:
    vals = [None if (v is None or (isinstance(v, float) and np.isnan(v))) else str(v) for v in values]
    d = dict_values if dict_values is not None else sorted({v for v in vals if v is not None})
    idx = {v: i for i, v in enumerate(d)}
    if len(d) < 255:
        na, dtype, t = 255, "u1", "dict8"
    else:
        na, dtype, t = 65535, "<u2", "dict16"
    arr = np.array([na if v is None else idx[v] for v in vals], dtype=dtype)
    return {"t": t, "dict": d, "na": na, "b": b64(arr)}


def col_bool(values) -> dict:
    arr = np.full(len(values), 255, dtype="u1")
    for i, v in enumerate(values):
        if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
            continue
        arr[i] = 1 if bool(v) else 0
    return {"t": "bool8", "na": 255, "b": b64(arr)}


def col_num(values, s: int = 1, prefer=None) -> dict:
    """Narrowest integer type that holds round(value * s), with a reserved null code. Nulls are NaN or None."""
    x = np.array([np.nan if v is None else float(v) for v in values], dtype="f8")
    ok = np.isfinite(x)
    scaled = np.where(ok, np.round(x * s), 0.0)
    lo = scaled[ok].min() if ok.any() else 0
    hi = scaled[ok].max() if ok.any() else 0
    if prefer == "f32":
        arr = np.where(ok, x, np.nan).astype("<f4")
        return {"t": "f32", "b": b64(arr)}
    if lo >= 0 and hi <= 254:
        t, dtype, na = "u8", "u1", 255
    elif lo >= 0 and hi <= 65534:
        t, dtype, na = "u16", "<u2", 65535
    elif -32768 <= lo and hi <= 32766:
        t, dtype, na = "i16", "<i2", 32767
    elif lo >= 0 and hi <= 4294967294:
        t, dtype, na = "u32", "<u4", 4294967295
    else:
        t, dtype, na = "i32", "<i4", 2147483647
    arr = np.where(ok, scaled, na).astype(dtype)
    out = {"t": t, "b": b64(arr)}
    if s != 1:
        out["s"] = s
    out["na"] = na
    return out


def col_time(values) -> dict:
    arr = np.full(len(values), 4294967295, dtype="<u4")
    for i, v in enumerate(values):
        if v is None or v is pd.NaT or (isinstance(v, float) and np.isnan(v)):
            continue
        ts = pd.Timestamp(v)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        arr[i] = int((ts - EPOCH_TS).total_seconds())
    return {"t": "time", "e": EPOCH, "na": 4294967295, "b": b64(arr)}


def col_detid(det_ids) -> dict:
    """`pfx[p] + "_" + pad(q, w[p])`; w is parallel to pfx (one width per prefix)."""
    pfx, widths, p, q = [], [], [], []
    seen = {}
    for d in det_ids:
        head, _, tail = d.rpartition("_")
        if head not in seen:
            seen[head] = len(pfx)
            pfx.append(head)
            widths.append(len(tail))
        p.append(seen[head])
        q.append(int(tail))
    return {"t": "detid", "pfx": pfx, "w": widths, "p": b64(np.array(p, dtype="<u2")), "q": b64(np.array(q, dtype="<u4"))}


def col_lightid(light_ids, satellites, times) -> dict:
    sat_code = {"S-NPP": "SPP", "NOAA-20": "N20", "NOAA-21": "N21"}
    q = []
    for lid, sat, t in zip(light_ids, satellites, times):
        code, stamp, idx = lid.split("_")
        ts = pd.Timestamp(t)
        assert code == sat_code[sat], (lid, sat)
        assert stamp == ts.strftime("%Y%m%dT%H%M%S"), (lid, t)
        q.append(int(idx))
    return {"t": "lightid", "w": 6, "q": b64(np.array(q, dtype="<u4"))}


def col_ref16(indices) -> dict:
    arr = np.array([65535 if (i is None or i < 0) else int(i) for i in indices], dtype="<u2")
    return {"t": "ref16", "to": "vessels", "na": 65535, "b": b64(arr)}


def geom_part(gdf: gpd.GeoDataFrame, kind: str, props: dict | None = None, note: str | None = None) -> dict:
    """Contract 6.2 `geom`: xy i32 lon/lat x 10,000 interleaved; ring = start vertex of each ring or line;
    feat = start ring of each feature. Points: one vertex per feature (ring and feat omitted)."""
    xy, ring, feat = [], [], []
    for geom in gdf.geometry:
        feat.append(len(ring))
        if kind == "point":
            xy.extend([geom.x, geom.y])
            continue
        parts = list(geom.geoms) if geom.geom_type.startswith("Multi") else [geom]
        for part in parts:
            rings = [part.exterior, *part.interiors] if kind == "polygon" else [part]
            for r in rings:
                ring.append(len(xy) // 2)
                coords = np.asarray(r.coords)[:, :2]
                if kind == "polygon" and len(coords) > 1 and np.allclose(coords[0], coords[-1]):
                    coords = coords[:-1]
                xy.extend(coords.ravel().tolist())
    out = {"kind": kind, "n": int(len(gdf)),
           "xy": b64(np.round(np.array(xy, dtype="f8") * 10000).astype("<i4"))}
    if kind != "point":
        out["ring"] = b64(np.array(ring, dtype="<u4"))
        out["feat"] = b64(np.array(feat, dtype="<u4"))
    if props:
        out["props"] = props
    if note:
        out["note"] = note
    return out


# ----------------------------------------------------------------------------------------------- helpers
def read(path, layer, **kw):
    return gpd.read_file(DATA_DIR / path if not str(path).startswith("/") else path, layer=layer, **kw)


def git_hash() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    except Exception:
        return "unknown"


def region_box(lon: float, lat: float) -> str:
    for name, (w, s, e, n) in REPORTING_BOXES.items():
        if w <= lon < e and s <= lat < n:
            return name
    return "other"


def cell_id(lon: float, lat: float) -> str:
    return f"r{int((24.0 - lat) // 0.25)}c{int((lon - 99.0) // 0.25)}"


def to_py(v):
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        return pd.Timestamp(v).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ") if pd.Timestamp(v).tzinfo else pd.Timestamp(v).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def stable_pick(df: pd.DataFrame, n: int, key: str) -> pd.DataFrame:
    """Deterministic sample: order by the md5 of the key column, take the first n."""
    if len(df) <= n:
        return df
    h = df[key].map(lambda s: hashlib.md5(str(s).encode()).hexdigest())
    return df.loc[h.sort_values().index[:n]]


# ----------------------------------------------------------------------------------------------- main build
def build(args) -> dict:
    parts: dict[str, dict] = {}
    aoi = aoi_gdf()
    aoi_geom = aoi.geometry.iloc[0]

    # ---- vessels (aisstream): in the AOI first, then class A with a name, up to --vessels
    ves = read("ais_live.gpkg", "vessels_latest_4326")
    ves["mmsi"] = ves["mmsi"].astype("Int64").astype(str)
    ves = ves[ves["mmsi"].str.len() == 9]  # ship stations only
    named = ves[ves["name"].notna()]
    chosen = pd.concat([named[named["in_aoi"] == True], named[named["in_aoi"] != True]])  # noqa: E712
    ves = chosen.head(args.vessels).reset_index(drop=True)
    vrow = {m: i for i, m in enumerate(ves["mmsi"])}
    tracks = read("ais_live.gpkg", "tracks_4326")
    tracks["mmsi"] = tracks["mmsi"].astype("Int64").astype(str)
    tracks = tracks[tracks["mmsi"].isin(vrow) & (tracks["n_positions"] >= 3)].copy()
    tracks["geometry"] = tracks.geometry.simplify(0.002, preserve_topology=False)
    tracks = tracks.head(args.tracks).reset_index(drop=True)

    # ---- live contacts: all high of the two busiest scenes first, then medium and fixed, up to --live
    live = read("live/live_contacts.gpkg", "contacts_4326")
    scenes_live = read("live/live_contacts.gpkg", "scenes_4326")
    busiest = scenes_live.sort_values("n_contacts", ascending=False)["product_id"].head(2).tolist()
    sub = live[live["scene_id"].isin(busiest)]
    live_sel = pd.concat([
        sub[sub["confidence"] == "high"].sort_values("cnn_score", ascending=False),
        stable_pick(sub[sub["confidence"] == "medium"], max(args.live // 3, 1), "det_id"),
        stable_pick(sub[sub["confidence"] == "fixed"], max(args.live // 6, 1), "det_id"),
    ]).drop_duplicates("det_id").head(args.live).copy()
    live_sel["view"] = "live"
    live_sel["pass_id"] = live_sel["run_id"]
    live_sel["src"] = "det_live"

    # ---- September regional contacts from two scenes, CNN scores joined from regional_cnn.parquet
    reg = read("detections_regional.gpkg", "detections_regional_4326")
    sp = read("detections_regional.gpkg", "scenes_processed_4326")
    cnn = pq.read_table(DATA_DIR / "ml/regional_cnn.parquet",
                        columns=["det_id", "cnn_score", "cnn_vessel", "cnn_chip_valid_frac", "bg_vv_db", "bg_vh_db"]).to_pandas()
    reg = reg.merge(cnn, on="det_id", how="left")
    reg_scenes = sp.sort_values(["high"], ascending=False).head(2)
    reg_sub = reg[reg["scene_idx"].isin(reg_scenes["scene_idx"])]
    reg_sel = pd.concat([
        reg_sub[reg_sub["confidence"] == "high"].sort_values("cnn_score", ascending=False).head(args.regional // 2),
        stable_pick(reg_sub[reg_sub["confidence"] == "medium"], args.regional // 2, "det_id"),
    ]).drop_duplicates("det_id").head(args.regional).copy()
    scene_by_idx = sp.set_index("scene_idx")
    reg_sel["scene_id"] = reg_sel["scene_idx"].map(scene_by_idx["product_id"])
    reg_sel["pass_dir"] = reg_sel["scene_idx"].map(scene_by_idx["pass_dir"])
    reg_sel["orbit_rel"] = reg_sel["scene_idx"].map(scene_by_idx["orbit_rel"])
    reg_sel["run_id"] = "regional_2026-09"
    reg_sel["view"] = "regional"
    reg_sel["ais_status"] = "not_checked"
    reg_sel["src"] = "det_regional"
    # pass id: mission plus start of the first scene of the group (scenes within 10 min)
    sp_sorted = sp.sort_values("start_utc")
    pass_of_scene = {}
    group_start = None
    for _, r in sp_sorted.iterrows():
        t = pd.Timestamp(r["start_utc"])
        if group_start is None or r["mission"] != group_start[0] or (t - group_start[1]).total_seconds() > 600:
            group_start = (r["mission"], t)
        pass_of_scene[r["product_id"]] = f"{group_start[0]}_{group_start[1].strftime('%Y%m%dT%H%M')}"
    reg_sel["pass_id"] = reg_sel["scene_id"].map(pass_of_scene)

    # ---- fixed structures near the two regional scenes
    st = read("structures_regional.gpkg", "structures_regional_4326")
    st = st.merge(cnn, on="det_id", how="left")
    st_sel = stable_pick(st[st["scene_idx"].isin(reg_scenes["scene_idx"])], args.structures, "det_id").copy()
    st_sel["scene_id"] = st_sel["scene_idx"].map(scene_by_idx["product_id"])
    st_sel["pass_dir"] = st_sel["scene_idx"].map(scene_by_idx["pass_dir"])
    st_sel["orbit_rel"] = st_sel["scene_idx"].map(scene_by_idx["orbit_rel"])
    st_sel["run_id"] = "regional_2026-09"
    st_sel["view"] = "regional"
    st_sel["ais_status"] = "not_checked"
    st_sel["pass_id"] = st_sel["scene_id"].map(pass_of_scene)
    st_sel["src"] = "det_regional"

    contacts = pd.concat([live_sel, reg_sel, st_sel], ignore_index=True)
    for c in ["cnn_score", "cnn_vessel", "ais_source", "match_method", "match_dist_m", "match_dt_s", "match_quality",
              "mmsi", "nearest_ais_mmsi", "nearest_ais_dist_m", "nearest_ais_dt_s", "n_ais_10km", "ais_reach",
              "dark_lead", "pol_class", "n_pixels", "low_reason", "n_low_1km", "near_fixed_m", "match_gate_m",
              "ais_sog_kn", "length_ratio", "ais_class", "cnn_chip_valid_frac", "ais_footprint_positions",
              "ais_recorded_hours", "bg_vv_db", "bg_vh_db"]:
        if c not in contacts:
            contacts[c] = np.nan
    contacts["synthetic"] = False
    contacts["vessel_ref"] = -1
    contacts["nearest_ref"] = -1
    contacts["mmsi"] = contacts["mmsi"].map(lambda v: None if pd.isna(v) else str(int(v)))
    contacts["nearest_ais_mmsi"] = contacts["nearest_ais_mmsi"].map(lambda v: None if pd.isna(v) else str(int(v)))
    for i, r in contacts.iterrows():
        if r["nearest_ais_mmsi"] in vrow:
            contacts.at[i, "nearest_ref"] = vrow[r["nearest_ais_mmsi"]]

    # ---- SYNTHETIC statuses and leads (clearly flagged): 3 matched contacts, L1 leads on live high contacts, 1 L7 lead
    live_high = contacts[(contacts["view"] == "live") & (contacts["confidence"] == "high")].sort_values("cnn_score", ascending=False)
    aoi_vessels = ves[(ves["in_aoi"] == True) & ves["name"].notna() & ves["length_m"].notna()].head(3)  # noqa: E712
    records_contacts: dict[str, dict] = {}
    for k, (ci, vrow_i) in enumerate(zip(live_high.index[:3], aoi_vessels.index)):
        v = ves.loc[vrow_i]
        contacts.loc[ci, ["ais_status", "ais_source", "match_method", "match_quality", "synthetic"]] = [
            "matched", "aisstream", "track_interp_hungarian", ["high", "medium", "low"][k], True]
        contacts.at[ci, "match_dist_m"] = [180.0, 650.0, 1400.0][k]
        contacts.at[ci, "match_dt_s"] = [45.0, 300.0, 1500.0][k]
        contacts.at[ci, "match_gate_m"] = 2000.0
        contacts.at[ci, "mmsi"] = v["mmsi"]
        contacts.at[ci, "vessel_ref"] = int(vrow_i)
        contacts.at[ci, "nearest_ref"] = int(vrow_i)
        contacts.at[ci, "nearest_ais_mmsi"] = v["mmsi"]
        contacts.at[ci, "nearest_ais_dist_m"] = contacts.at[ci, "match_dist_m"]
        contacts.at[ci, "nearest_ais_dt_s"] = contacts.at[ci, "match_dt_s"]
        contacts.at[ci, "n_ais_10km"] = 1 + k
        contacts.at[ci, "ais_reach"] = 0.6
        contacts.at[ci, "length_ratio"] = float(v["length_m"]) / float(contacts.at[ci, "length_est_m"]) if contacts.at[ci, "length_est_m"] else np.nan
        contacts.at[ci, "ais_class"] = v["ais_class"]
        contacts.at[ci, "dark_lead"] = False
        records_contacts[contacts.at[ci, "det_id"]] = {
            "extra": {"synthetic_note": "SYNTHETIC AIS match for frontend development; the real contact has no AIS coverage (no AIS heard near it)."},
            "prov": {"ais_status": "fixture_synthetic", "mmsi": "fixture_synthetic", "match_dist_m": "fixture_synthetic",
                     "match_dt_s": "fixture_synthetic", "match_quality": "fixture_synthetic"}}

    lead_rows = []
    lead_records = {}
    passes_json = json.load(open(DATA_DIR / "s1_next_passes.json"))
    groups = passes_json["pass_groups"]
    now = pd.Timestamp(passes_json["generated_utc"])

    def next_look(lon, lat):
        best = None
        for g in groups:
            if g["status"] != "upcoming":
                continue
            w, s, e, n = g["aoi_overlap_bbox"]
            if w <= lon <= e and s <= lat <= n:
                t = pd.Timestamp(g["start_utc"])
                if best is None or t < best:
                    best = t
        return best

    unmatched_idx = [i for i in live_high.index[3:] if contacts.at[i, "cnn_score"] >= 0.5][: args.leads]
    states_cycle = ["new", "new", "reviewing", "new", "closed_explained", "new", "new", "new"]
    nearest_pool = ves.index.tolist()
    for k, ci in enumerate(unmatched_idx):
        r = contacts.loc[ci]
        contacts.loc[ci, ["ais_status", "ais_source", "synthetic", "dark_lead"]] = ["unmatched", "aisstream", True, True]
        nref = nearest_pool[(k * 7) % len(nearest_pool)]
        contacts.at[ci, "nearest_ref"] = int(nref)
        contacts.at[ci, "nearest_ais_mmsi"] = ves.at[nref, "mmsi"]
        contacts.at[ci, "nearest_ais_dist_m"] = 2500.0 + 900.0 * k
        contacts.at[ci, "nearest_ais_dt_s"] = 120.0 + 60.0 * k
        contacts.at[ci, "n_ais_10km"] = 1 + (k % 3)
        contacts.at[ci, "ais_reach"] = round(0.35 + 0.05 * (k % 5), 2)
        det_id = r["det_id"]
        lead_id = f"L1-{det_id}"
        box_name = region_box(r["lon"], r["lat"])
        cnn_pts = int(round(30 * min(1.0, max(0.0, (float(r["cnn_score"]) - CNN_THRESHOLD) / (1 - CNN_THRESHOLD)))))
        both = r["confidence"] == "high"
        ev = min(30, cnn_pts if both else cnn_pts // 2)
        reach_pts = int(round(20 * float(contacts.at[ci, "ais_reach"])))
        corr = 0
        persist = 15 if int(r.get("persist_dates") or 0) > 0 else 0
        area = 0
        priority = min(100, ev + corr + reach_pts + persist + area)
        state = states_cycle[k % len(states_cycle)]
        nl = next_look(r["lon"], r["lat"])
        lead_rows.append({"lead_id": lead_id, "lead_type": "L1", "primary_type": "contacts", "primary": int(ci),
                          "priority": priority, "state": state, "reason": "outside AIS reach" if state == "closed_explained" else None,
                          "region_box": box_name, "time_utc": r["acq_utc"], "next_look_utc": nl, "lon": r["lon"], "lat": r["lat"],
                          "f_evidence": ev, "f_corroboration": corr, "f_reach": reach_pts, "f_persistence": persist, "f_area": area,
                          "synthetic": True})
        history = []
        if state != "new":
            history.append({"lead_id": lead_id, "time_utc": "2026-10-09T09:00:00Z", "user": "fixture", "from_state": "new",
                            "to_state": "reviewing", "reason": None, "note": "opened (synthetic history)", "build": "open",
                            "app_version": "fixture"})
        if state == "closed_explained":
            history.append({"lead_id": lead_id, "time_utc": "2026-10-09T09:05:00Z", "user": "fixture", "from_state": "reviewing",
                            "to_state": "closed_explained", "reason": "outside AIS reach",
                            "note": "ais_reach 0.35 (synthetic history)", "build": "open", "app_version": "fixture"})
        lead_records[lead_id] = {
            "title": f"SYNTHETIC lead (fixture): Unmatched radar contact in AIS reach, {int(round(float(r['length_est_m'] or 0)))} m, {box_name}",
            "evidence": [{"type": "contact", "id": det_id, "role": "primary"},
                         {"type": "vessel", "id": f"mmsi:{ves.at[nref, 'mmsi']}", "role": "nearest_ais"},
                         {"type": "pass", "id": r["pass_id"], "role": "pass"}],
            "history": history,
            "lawful_explanations": LAWFUL["L1"],
            "change_indicators": CHANGE["L1"],
            "weather": "weather unknown: live passes have no weather join yet (0 weather points)",
            "synthetic_note": "SYNTHETIC lead for frontend development. The real contact has no AIS coverage and is not a dark lead candidate.",
            "prov": {"priority": "app", "factors": "app", "ais_status": "fixture_synthetic"}}
        records_contacts.setdefault(det_id, {"extra": {}, "prov": {}})
        records_contacts[det_id]["extra"]["synthetic_note"] = "SYNTHETIC unmatched status for frontend development; the real contact has no AIS coverage (no AIS heard near it)."
        records_contacts[det_id]["prov"].update({"ais_status": "fixture_synthetic", "nearest_ais_mmsi": "fixture_synthetic",
                                                 "nearest_ais_dist_m": "fixture_synthetic", "n_ais_10km": "fixture_synthetic",
                                                 "ais_reach": "fixture_synthetic"})
        records_contacts[det_id]["lead_ids"] = [lead_id]
    for det_id in records_contacts:
        records_contacts[det_id].setdefault("lead_ids", [])

    # ---- lights: clear-sky lights of the two most recent nights near the live scenes, then a stable sample
    li = read("viirs_lights.gpkg", "viirs_lights_4326")
    nights = sorted(li["night"].unique())[-2:]
    li_near = li[li["night"].isin(nights) & (li["lon"] > 99) & (li["lon"] < 104) & (li["lat"] > 6) & (li["lat"] < 14)]
    li_sel = pd.concat([li_near, stable_pick(li[li["night"].isin(nights)], args.lights, "light_id")]).drop_duplicates("light_id").head(args.lights).copy()
    li_sel = li_sel.sort_values("time_utc").reset_index(drop=True)
    # L7 synthetic lead on one clear light
    clear = li_sel[li_sel["quality"] == "clear"]
    if len(clear):
        lr = clear.iloc[0]
        lead_id = f"L7-{lr['light_id']}"
        lead_rows.append({"lead_id": lead_id, "lead_type": "L7", "primary_type": "lights", "primary": int(clear.index[0]),
                          "priority": 20, "state": "new", "reason": None, "region_box": region_box(lr["lon"], lr["lat"]),
                          "time_utc": lr["time_utc"], "next_look_utc": next_look(lr["lon"], lr["lat"]), "lon": lr["lon"], "lat": lr["lat"],
                          "f_evidence": 10, "f_corroboration": 0, "f_reach": 10, "f_persistence": 0, "f_area": 0, "synthetic": True})
        lead_records[lead_id] = {
            "title": f"SYNTHETIC lead (fixture): Lit activity where radar does not look, {region_box(lr['lon'], lr['lat'])}",
            "evidence": [{"type": "light", "id": lr["light_id"], "role": "primary"}], "history": [],
            "lawful_explanations": LAWFUL["L7"],
            "change_indicators": CHANGE["L7"],
            "synthetic_note": "SYNTHETIC lead for frontend development.", "prov": {"priority": "app"}}

    # ---------------------------------------------------------------------------------------- encode contacts
    c = contacts.reset_index(drop=True)
    pass_ids = sorted(c["pass_id"].dropna().unique().tolist())
    cols = {
        "det_id": col_detid(c["det_id"].tolist()),
        "run_id": col_dict(c["run_id"]),
        "mission": col_dict(c["mission"], ["S1C", "S1D"]),
        "acq_utc": col_time(c["acq_utc"]),
        "lon": col_num(c["lon"], 100000),
        "lat": col_num(c["lat"], 100000),
        "length_est_m": col_num(c["length_est_m"], 10),
        "confidence": col_dict(c["confidence"], ["high", "medium", "fixed", "low"]),
        "cnn_score": col_num(c["cnn_score"], 250),
        "cnn_vessel": col_bool(c["cnn_vessel"]),
        "ais_status": col_dict(c["ais_status"], ["matched", "unmatched", "no_coverage", "not_checked"]),
        "ais_source": col_dict(c["ais_source"], ["aisstream", "gfw"]),
        "match_method": col_dict(c["match_method"]),
        "match_dist_m": col_num(c["match_dist_m"], 1),
        "match_dt_s": col_num(c["match_dt_s"], 1),
        "match_quality": col_dict(c["match_quality"], ["high", "medium", "low"]),
        "vessel_ref": col_ref16(c["vessel_ref"]),
        "nearest_ref": col_ref16(c["nearest_ref"]),
        "nearest_ais_dist_m": col_num(c["nearest_ais_dist_m"], 1),
        "nearest_ais_dt_s": col_num(c["nearest_ais_dt_s"], 1),
        "n_ais_10km": col_num(c["n_ais_10km"], 1),
        "ais_reach": col_num(c["ais_reach"], 250),
        "research_only": col_const(False),
        "caveat": col_const(PRODUCT_CAVEAT),
        "view": col_dict(c["view"], ["regional", "live", "camau"]),
        "dark_lead": col_bool(c["dark_lead"]),
        "scene_id": col_dict(c["scene_id"]),
        "pass_id": col_dict(c["pass_id"], pass_ids),
        "pass_dir": col_dict(c["pass_dir"], ["ASCENDING", "DESCENDING"]),
        "orbit_rel": col_num(c["orbit_rel"], 1),
        "inc_angle_deg": col_num(c["inc_angle_deg"], 10),
        "pol_class": col_dict(c["pol_class"]),
        "n_pixels": col_num(c["n_pixels"], 1),
        "scr_vv_db": col_num(c["scr_vv_db"], 10),
        "scr_vh_db": col_num(c["scr_vh_db"], 10),
        "persist_dates": col_num(c["persist_dates"], 1),
        "persist_dates_checked": col_num(c["persist_dates_checked"], 1),
        "n_low_1km": col_num(c["n_low_1km"], 1),
        "near_fixed_m": col_num(c["near_fixed_m"], 1),
        "match_gate_m": col_num(c["match_gate_m"], 1),
        "length_ratio": col_num(c["length_ratio"], 100),
        "ais_class": col_dict(c["ais_class"], ["A", "B"]),
        "ais_footprint_positions": col_num(c["ais_footprint_positions"], 1),
        "ais_recorded_hours": col_num(c["ais_recorded_hours"], 1),
        "cnn_chip_valid_frac": col_num(c["cnn_chip_valid_frac"], 250),
        "bg_vv_db": col_num(c["bg_vv_db"], 10),
        "bg_vh_db": col_num(c["bg_vh_db"], 10),
        "cnn_threshold": col_const(CNN_THRESHOLD),
        "cnn_model_id": col_const(CNN_MODEL_ID),
        "src": col_dict(c["src"], ["det_live", "det_regional", "det_camau"]),
        "synthetic": col_bool(c["synthetic"]),
    }
    parts["contacts"] = {"type": "contacts", "n": int(len(c)), "columns": cols,
                         "prov": {"cnn_score": "cnn_v0", "cnn_vessel": "cnn_v0", "cnn_threshold": "cnn_v0", "cnn_model_id": "cnn_v0",
                                  "cnn_chip_valid_frac": "cnn_v0", "ais_status": "aisstream", "ais_source": "aisstream",
                                  "match_method": "aisstream", "match_dist_m": "aisstream", "match_dt_s": "aisstream",
                                  "match_quality": "aisstream", "mmsi": "aisstream", "imo": "aisstream", "vessel_name": "aisstream",
                                  "call_sign": "aisstream", "flag": "mid_itu", "ship_type": "aisstream", "length_ais_m": "aisstream",
                                  "identity_source": "aisstream", "nearest_ais_mmsi": "aisstream", "nearest_ais_dist_m": "aisstream",
                                  "nearest_ais_dt_s": "aisstream", "n_ais_10km": "aisstream", "ais_reach": "aisstream",
                                  "dark_lead": "aisstream", "lead_ids": "app"},
                         "records": records_contacts,
                         "note": "Bulk columns of the D1 fields plus extensions; identity by reference (vessel_ref, nearest_ref)."}

    # ---------------------------------------------------------------------------------------- encode vessels
    v = ves
    vcols = {
        "vessel_key": col_str(["mmsi:" + m for m in v["mmsi"]]),
        "mmsi": col_str(v["mmsi"]),
        "mid": col_num(v["mid"], 1),
        "flag": col_str([flag_from_mmsi(m) for m in v["mmsi"]]),
        "name": col_str(v["name"]),
        "call_sign": col_str(v["callsign"]),
        "imo": col_str([None if pd.isna(x) else str(int(float(x))) for x in v["imo"]]),
        "ais_class": col_dict(v["ais_class"], ["A", "B"]),
        "ship_type": col_str(v["ship_type_label"]),
        "ship_type_code": col_num(v["ship_type"], 1),
        "gear_type": col_const(None),
        "identity_kind": col_const(None),
        "length_m": col_num(v["length_m"], 10),
        "width_m": col_num(v["width_m"], 10),
        "length_ais_m": col_num(v["length_m"], 10),
        "tonnage_gt": col_const(None),
        "identity_source": col_const("aisstream static message"),
        "destination": col_str(v["destination"]),
        "eta": col_str(v["eta"]),
        "first_seen_utc": col_time(v["first_seen_utc"]),
        "last_seen_utc": col_time(v["last_seen_utc"]),
        "static_seen_utc": col_time(v["static_seen_utc"]),
        "n_positions": col_num(v["n_positions"], 1),
        "last_lon": col_num(v["lon"], 100000),
        "last_lat": col_num(v["lat"], 100000),
        "sog_kn": col_num(v["sog_kn"], 10),
        "cog_deg": col_num(v["cog_deg"], 10),
        "heading": col_num(v["heading"], 1),
        "nav_status_label": col_str(v["nav_status_label"]),
        "in_aoi": col_bool(v["in_aoi"]),
        "ever_in_aoi": col_bool(v["ever_in_aoi"]),
        "gear_beacon_like": col_bool(v["gear_beacon_like"]),
        "stub": col_const(False),
        "identity_note": col_const("Identity fields are self-reported by the transponder; they can be wrong, reused or spoofed."),
        "research_only": col_const(False),
        "caveat": col_const(PRODUCT_CAVEAT),
        "src": col_const("aisstream"),
    }
    track_ref = [vrow[m] for m in tracks["mmsi"]]
    parts["vessels"] = {"type": "vessels", "n": int(len(v)), "columns": vcols,
                        "prov": {"flag": "mid_itu"},
                        "tracks": geom_part(tracks, "line",
                                            props={"vessel_ref": col_ref16(track_ref),
                                                   "start_utc": col_time(tracks["start_utc"]), "end_utc": col_time(tracks["end_utc"]),
                                                   "n_positions": col_num(tracks["n_positions"], 1)},
                                            note="simplified for display at 0.002 degree; the GeoPackage holds the recorded positions"),
                        "note": "flag = ITU country of the MID (darkvessel.live.mid), as claimed by the transponder; the gap note is 'An AIS gap is not proof of intent.'"}

    # ---------------------------------------------------------------------------------------- encode lights
    L = li_sel
    lcols = {
        "light_id": col_lightid(L["light_id"].tolist(), L["satellite"].tolist(), L["time_utc"].tolist()),
        "satellite": col_dict(L["satellite"], ["S-NPP", "NOAA-20", "NOAA-21"]),
        "time_utc": col_time(L["time_utc"]),
        "night": col_dict(L["night"]),
        "lon": col_num(L["lon"], 100000),
        "lat": col_num(L["lat"], 100000),
        "radiance_nw": col_num(L["radiance_nw"], 100),
        "spike_nw": col_num(L["spike_nw"], 100),
        "isolation": col_num(L["isolation"], 100),
        "quality": col_dict(L["quality"], ["clear", "under_cloud"]),
        "class": col_dict(L["class"]),
        "nights_seen_500m": col_num(L["nights_seen_500m"], 1),
        "clear_nights_cell": col_num(L["clear_nights_cell"], 1),
        "moon_illum_pct": col_num(L["moon_illum_pct"], 10),
        "satlas_infra_m": col_num(L["satlas_infra_m"], 1),
        "s1_passes_90d": col_num(L["s1_passes_90d"], 1),
        "caveat": col_const(PRODUCT_CAVEAT),
        "src": col_const("viirs_dnb"),
    }
    parts["lights"] = {"type": "lights", "n": int(len(L)), "columns": lcols, "prov": {"satlas_infra_m": "satlas", "s1_passes_90d": "s1_grd"},
                       "records": {}}

    # ---------------------------------------------------------------------------------------- encode leads
    ld = pd.DataFrame(lead_rows)
    if len(ld):
        parts["leads"] = {"type": "leads", "n": int(len(ld)), "columns": {
            "lead_type": col_dict(ld["lead_type"], ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]),
            "primary_type": col_dict(ld["primary_type"], ["contacts", "lights", "cells", "vessels"]),
            "primary": {"t": "u32", "b": b64(ld["primary"].to_numpy(dtype="<u4")), "na": 4294967295},
            "priority": col_num(ld["priority"], 1),
            "state": col_dict(ld["state"], STATES),
            "reason": col_dict(ld["reason"], REASONS),
            "region_box": col_dict(ld["region_box"], list(REPORTING_BOXES) + ["other"]),
            "time_utc": col_time(ld["time_utc"]),
            "next_look_utc": col_time(ld["next_look_utc"]),
            "lon": col_num(ld["lon"], 100000),
            "lat": col_num(ld["lat"], 100000),
            "f_evidence": col_num(ld["f_evidence"], 1),
            "f_corroboration": col_num(ld["f_corroboration"], 1),
            "f_reach": col_num(ld["f_reach"], 1),
            "f_persistence": col_num(ld["f_persistence"], 1),
            "f_area": col_num(ld["f_area"], 1),
            "priority_model_id": col_const(PRIORITY_MODEL_ID),
            "calibrated": col_const(False),
            "research_only": col_const(False),
            "caveat": col_const(PRODUCT_CAVEAT),
            "src": col_const("app"),
            "synthetic": col_bool(ld["synthetic"]),
        }, "factors": [
            {"factor": "evidence_quality", "column": "f_evidence", "max_points": 30, "source": "cnn_v0"},
            {"factor": "corroboration", "column": "f_corroboration", "max_points": 25, "source": "app"},
            {"factor": "ais_reach", "column": "f_reach", "max_points": 20, "source": "aisstream"},
            {"factor": "persistence", "column": "f_persistence", "max_points": 15, "source": "det_live"},
            {"factor": "area_weight", "column": "f_area", "max_points": 10, "source": "analyst"},
        ], "records": lead_records, "lawful_explanations": LAWFUL,
            "note": "lead_id = <lead_type>-<primary id>; title in records, else built by the frontend"}

    # ---------------------------------------------------------------------------------------- passes (GeoJSON)
    plan = read("ais_live.gpkg", "s1_next_passes_4326")
    plan_fp = plan.dissolve(by="pass_group") if "pass_group" in plan else None
    feats = []
    for g in groups:
        geom = None
        if plan_fp is not None and g["pass_group"] in plan_fp.index:
            geom = plan_fp.loc[g["pass_group"]].geometry.simplify(0.01)
        feats.append({"type": "Feature", "geometry": mapping(geom) if geom is not None else None, "properties": {
            "pass_id": g["pass_group"], "mission": g["mission"], "relative_orbit": g["relative_orbit"], "pass_dir": g["pass_dir"],
            "start_utc": g["start_utc"], "stop_utc": g["stop_utc"], "status": g["status"], "sources": g["sources"],
            "aoi_overlap_km2": g["aoi_overlap_km2"], "aoi_parts": g["aoi_parts"], "aoi_overlap_bbox": g["aoi_overlap_bbox"],
            "ais_heard_share": g.get("ais_heard_share"), "scenes": [], "processed": False, "n_contacts": None,
            "caveat": PRODUCT_CAVEAT, "src": "esa_acq_plan",
            "prov": {"ais_heard_share": "aisstream", **{k: "aisstream" for k in AIS_PASS_FIELDS}},
            "note": "a repeat_cycle prediction is not ESA's plan"}})
    # processed live pass from scenes_4326
    sl = scenes_live
    for run_id, grp in sl.groupby("run_id"):
        n_status = {"matched": int(grp["n_matched"].sum()), "unmatched": int(grp["n_unmatched"].sum()),
                    "no_coverage": int(grp["n_no_coverage"].sum()), "not_checked": 0}
        feats.append({"type": "Feature", "geometry": mapping(grp.geometry.union_all().simplify(0.01)), "properties": {
            "pass_id": run_id, "mission": grp["mission"].iloc[0], "relative_orbit": int(grp["orbit_rel"].iloc[0]),
            "pass_dir": grp["pass_dir"].iloc[0], "start_utc": to_py(pd.Timestamp(grp["start_utc"].min())),
            "stop_utc": to_py(pd.Timestamp(grp["stop_utc"].max())), "status": "past", "sources": ["processed"],
            "aoi_overlap_km2": float(grp["aoi_overlap_km2"].sum()), "aoi_parts": ["Gulf of Thailand"],
            "scenes": grp["product_id"].tolist(), "processed": True, "n_contacts": n_status,
            "ais_aoi_positions": int(grp["ais_aoi_positions"].sum()), "ais_aoi_mmsi": int(grp["ais_aoi_mmsi"].max()),
            "ais_footprint_positions": int(grp["ais_footprint_positions"].sum()), "ais_footprint_mmsi": int(grp["ais_footprint_mmsi"].max()),
            "ais_near_footprint_mmsi": int(grp["ais_near_footprint_mmsi"].max()),
            "scene_counts": [{"product_id": r["product_id"], "n_contacts": int(r["n_contacts"]), "n_high": int(r["n_high"]),
                              "n_medium": int(r["n_medium"]), "n_fixed": int(r["n_fixed"]), "ais_aoi_positions": int(r["ais_aoi_positions"]),
                              "ais_aoi_mmsi": int(r["ais_aoi_mmsi"])} for _, r in grp.iterrows()],
            "caveat": PRODUCT_CAVEAT, "src": "det_live", "prov": {k: "aisstream" for k in AIS_PASS_FIELDS}}})
    # the two regional passes of the fixture scenes
    for pid in sorted(set(reg_sel["pass_id"])):
        scenes_of = [s for s, p in pass_of_scene.items() if p == pid]
        grp = sp[sp["product_id"].isin(scenes_of)]
        in_fixture = c[(c["pass_id"] == pid)]
        feats.append({"type": "Feature", "geometry": mapping(grp.geometry.union_all().simplify(0.01)), "properties": {
            "pass_id": pid, "mission": grp["mission"].iloc[0], "relative_orbit": int(grp["orbit_rel"].iloc[0]),
            "pass_dir": grp["pass_dir"].iloc[0], "start_utc": to_py(pd.Timestamp(grp["start_utc"].min())),
            "stop_utc": to_py(pd.Timestamp(grp["start_utc"].max()) + pd.Timedelta(seconds=25)), "status": "past",
            "sources": ["processed"], "aoi_overlap_km2": None, "aoi_parts": None, "scenes": scenes_of, "processed": True,
            "n_contacts": {"matched": 0, "unmatched": 0, "no_coverage": 0, "not_checked": int(len(in_fixture))},
            "fixture_note": f"only {len(in_fixture)} of this pass's contacts are in the fixture",
            "caveat": PRODUCT_CAVEAT, "src": "det_regional", "prov": {}}})
    parts["passes"] = {"type": "FeatureCollection", "n": len(feats), "features": feats,
                       "note": "pass_id = pass_group (plan), run_id (live), mission plus first scene start (regional); footprints simplified at 0.01 degree"}

    # ---------------------------------------------------------------------------------------- geo
    west, south, east, north = aoi.total_bounds
    frame = box(west - 1, south - 1, east + 1, north + 1)
    land = natural_earth_land(bbox=(west - 1, south - 1, east + 1, north + 1))
    land = gpd.GeoDataFrame(geometry=[land.geometry.union_all().intersection(frame).simplify(0.01, preserve_topology=True)], crs="EPSG:4326").explode(index_parts=False)
    land = land[land.geometry.geom_type == "Polygon"].reset_index(drop=True)
    boxes = gpd.GeoDataFrame({"name": list(REPORTING_BOXES)}, geometry=[box(*b) for b in REPORTING_BOXES.values()], crs="EPSG:4326")
    eez_lines = read("eez_marineregions.gpkg", "eez_boundaries_4326")
    eez_lines["geometry"] = eez_lines.geometry.simplify(0.005)
    eez_lines = eez_lines[~eez_lines.geometry.is_empty].reset_index(drop=True)
    fps = pd.concat([
        gpd.GeoDataFrame({"product_id": sl["product_id"], "pass_id": sl["run_id"], "mission": sl["mission"], "start_utc": sl["start_utc"]},
                         geometry=sl.geometry.simplify(0.01), crs="EPSG:4326"),
        gpd.GeoDataFrame({"product_id": sp["product_id"], "pass_id": sp["product_id"].map(pass_of_scene), "mission": sp["mission"],
                          "start_utc": sp["start_utc"]}, geometry=sp.geometry.simplify(0.01), crs="EPSG:4326"),
    ], ignore_index=True)
    fps = fps[fps["pass_id"].isin(set(reg_sel["pass_id"]) | set(sl["run_id"]))].reset_index(drop=True)
    aoi_simple = gpd.GeoDataFrame({"label": aoi["label"]}, geometry=aoi.geometry.simplify(0.005), crs="EPSG:4326").explode(index_parts=False).reset_index(drop=True)
    parts["geo"] = {"type": "geo", "layers": {
        "land": geom_part(land, "polygon", note="Natural Earth 10 m land, clipped to the AOI frame plus 1 degree, simplified at 0.01 degree for display"),
        "aoi": geom_part(aoi_simple, "polygon", props={"label": col_str(aoi_simple["label"])}, note="simplified at 0.005 degree for display"),
        "reporting_boxes": geom_part(boxes, "polygon", props={"name": col_str(boxes["name"])},
                                     note="reporting box: for statistics only, not a boundary"),
        "eez_boundaries": geom_part(eez_lines, "line", props={"line_name": col_str(eez_lines["line_name"]),
                                                              "line_type": col_dict(eez_lines["line_type"]),
                                                              "length_km": col_num(eez_lines["length_km"], 10)},
                                    note="Marine Regions Maritime Boundaries (v12, world, 2023) lines as published, simplified at 0.005 degree for display; the GeoPackage holds the published geometry"),
        "footprints": geom_part(fps, "polygon", props={"product_id": col_str(fps["product_id"]), "pass_id": col_str(fps["pass_id"]),
                                                       "mission": col_dict(fps["mission"], ["S1C", "S1D"]), "start_utc": col_time(fps["start_utc"])},
                                note="scene footprints of the passes in this fixture, simplified at 0.01 degree"),
    }, "eez_statement": ("Lines and polygons as published by Marine Regions (Flanders Marine Institute, VLIZ), World EEZ v12, "
                         "CC BY 4.0, doi:10.14284/632. In this sea many zones overlap or are disputed; the source marks them. "
                         "This product takes no position on any boundary or claim."),
        "eez_disclaimer": ("VLIZ expresses no opinion about the legal state neither of any country, territory or area nor "
                           "concerning its delimitation, frontier or borders. The data has no legal value whatsoever.")}

    # ---------------------------------------------------------------------------------------- meta
    summ = json.load(open(DATA_DIR / "ais_live_summary.json"))
    live_about = read("live/live_contacts.gpkg", "about").iloc[0].to_dict()
    gaps = summ.get("recording_gaps_over_10_min", [])
    meta = {
        "contract_version": CONTRACT_VERSION, "build": "open", "build_label": BUILD_LABEL, "caveat": PRODUCT_CAVEAT,
        "caveat_short": DARK_CAVEAT_SHORT, "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_hash": git_hash(), "priority_model_id": PRIORITY_MODEL_ID, "app_version": "fixture",
        "sources": SOURCES,
        "files": [
            {"path": "data/live/live_contacts.gpkg", "layer": "contacts_4326", "status": "existing", "rows": int(len(live)), "in_bundle": int(len(live_sel))},
            {"path": "data/detections_regional.gpkg", "layer": "detections_regional_4326", "status": "existing", "rows": int(len(reg)), "in_bundle": int(len(reg_sel))},
            {"path": "data/structures_regional.gpkg", "layer": "structures_regional_4326", "status": "existing", "rows": int(len(st)), "in_bundle": int(len(st_sel))},
            {"path": "data/viirs_lights.gpkg", "layer": "viirs_lights_4326", "status": "existing", "rows": int(len(li)), "in_bundle": int(len(li_sel))},
            {"path": "data/ais_live.gpkg", "layer": "vessels_latest_4326", "status": "existing", "rows": int(len(chosen)), "in_bundle": int(len(ves))},
            {"path": "data/s1_next_passes.json", "layer": "pass_groups", "status": "existing", "rows": len(groups), "in_bundle": len(groups)},
            {"path": "data/leads_open.gpkg", "layer": "leads_4326", "status": "missing", "rows": 0, "in_bundle": 0},
            {"path": "data/events_open.gpkg", "layer": "events_4326", "status": "missing", "rows": 0, "in_bundle": 0},
            {"path": "data/ocean_static_cells.parquet", "layer": None, "status": "existing", "rows": 5116, "in_bundle": 0},
        ],
        "counts": {"contacts": int(len(c)), "vessels": int(len(ves)), "lights": int(len(li_sel)), "leads": int(len(ld)), "passes": len(feats),
                   "events": 0, "cells": 0},
        "ais_recording": {"period_start_utc": summ.get("period_start_utc"), "period_end_utc": summ.get("period_end_utc"),
                          "hours_recorded": summ.get("hours_recorded"), "gaps": gaps, "positions": summ.get("positions"),
                          "mmsi_count": summ.get("mmsi_count"), "generated_utc": summ.get("generated_utc"),
                          "note": "aisstream relays shore receivers: dense near Hong Kong, the Singapore and Bangka straits and the Philippines, thin off Vietnam"},
        "live_rules": {k: live_about.get(k) for k in ["no_coverage_rule", "ais_status_rule", "match_quality_rule", "dark_lead_rule", "distance_gate", "ais_window", "cnn"]},
        "data_credit": "Contains modified Copernicus Sentinel data 2026",
        "dropped": [{"part": "chips", "reason": "no chip cache in the fixture"}, {"part": "cells", "reason": "not in the fixture"},
                    {"part": "rasters", "reason": "not in the fixture"}, {"part": "camau", "reason": "not in the fixture"},
                    {"part": "events", "reason": "data/events_open.gpkg pending"}],
        "fixture": {"synthetic": True, "note": FIXTURE_NOTE, "synthetic_counts": {"matched_contacts": 3, "unmatched_contacts": len(unmatched_idx), "leads": int(len(ld))}},
        "parts": {},
    }
    parts["meta"] = meta
    sizes = {k: len(json.dumps(v, separators=(",", ":"))) for k, v in parts.items() if k != "meta"}
    meta["parts"] = sizes
    return parts


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path(__file__).with_name("bundle_small.json"))
    ap.add_argument("--live", type=int, default=300)
    ap.add_argument("--regional", type=int, default=300)
    ap.add_argument("--structures", type=int, default=100)
    ap.add_argument("--lights", type=int, default=300)
    ap.add_argument("--vessels", type=int, default=400)
    ap.add_argument("--tracks", type=int, default=150)
    ap.add_argument("--leads", type=int, default=8)
    args = ap.parse_args()
    parts = build(args)
    out = {"format": "scs-bundle-parts", "contract_version": CONTRACT_VERSION, "parts": parts}
    args.out.write_text(json.dumps(out, separators=(",", ":"), default=to_py))
    size = args.out.stat().st_size
    print(f"wrote {args.out} ({size / 1e6:.2f} MB)")
    for k, v in sorted(parts["meta"]["parts"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:10s} {v / 1e6:.3f} MB")
    print("  counts", parts["meta"]["counts"])
    if size > 2_000_000:
        print("FIXTURE OVER 2 MB", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
