"""Build a small development bundle for the SCS Vessel Watch frontend in the contract 6.2 format.

Purpose: a small bundle in the exact encodings of app/CONTRACT.md section 6.2 and the layout of the README readings
1 to 16 (board D5.2: normative for the bundle builder), to develop and smoke-test the frontend against. Real open-build
records only, nothing synthetic: the Pearl River live pass of 2026-10-10 (live_S1D_20261010T1032, the first pass with AIS
in its footprint) with every matched contact (high, medium and low quality, with the hand-check note), the contacts of
its highest-priority real L1 leads (a stale lead, whose contact is no longer an unambiguous dark lead, is left out),
ambiguous contacts with their candidate MMSIs, fixed and no_coverage contacts (the hand-checked ones included) and
its AIS-only vessels; samples of the other live passes (all no_coverage), the September run, VIIRS lights, aisstream
vessels, real L1 and L7 leads from data/leads_open.gpkg, and the board D5 shapes: `object_context` of contacts and
lights (D5.3, a columnar block per part), a `cells` part with the newest night, the Marine Regions attributes and
`expected_activity` (D5.4 rows in the cell records, counts per target), and a `rasters` part (context overlays as
WebP, shipping as presence only). It is a test fixture, not a data product: the page shows a FIXTURE tag.

Method: read a few hundred live and September contacts (CNN scores joined from data/ml/regional_cnn.parquet),
the Pearl River AIS-only layer and the open leads,
fixed structures, VIIRS lights, aisstream vessels and tracks, planned and processed passes, and the land, AOI,
reporting-box and Marine Regions boundary-line geometry; encode bulk parts as little-endian typed columns in
base64 (i32/u32/i16/u16/u8/f32/bool8/dict8/dict16/time/detid/lightid/ref16/const/str) and geometry in the
`geom` encoding (i32 lon/lat x 10,000, ring and feature offsets); write one JSON object per part.

Inputs (open build only; never data/research/):
  data/live/live_contacts.gpkg (contacts_4326, scenes_4326, about), data/live/live_S1D_20261010T1032.gpkg
  (ais_only_4326), data/leads_open.gpkg (leads_4326), data/detections_regional.gpkg
  (detections_regional_4326, scenes_processed_4326), data/ml/regional_cnn.parquet, data/structures_regional.gpkg,
  data/viirs_lights.gpkg (viirs_lights_4326), data/ais_live.gpkg (vessels_latest_4326, tracks_4326,
  s1_next_passes_4326, about), data/ais_live_summary.json, data/s1_next_passes.json, data/aoi.gpkg,
  data/eez_marineregions.gpkg (eez_boundaries_4326 only), Natural Earth 10 m land (darkvessel.aoi),
  data/ocean_context_objects.parquet, data/ocean_static_cells.parquet, data/ocean_daily_cells.parquet,
  data/expected_activity.parquet and .json, data/outputs/small/*_4326.tif (overlays, AIS reach, look probability).
Output: app/frontend/fixtures/bundle_small.json, {"parts": {name: part}} with the 6.2 part objects; under about 1 MB.
Usage: /home/user/.mamba/envs/darkvessel/bin/python app/frontend/fixtures/make_fixture.py [--out PATH]
       [--leads 40 --ambiguous 12 --no-coverage 30 --ais-only 100 --l7 8 --live-other 60 --regional 300 --lights 300]
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import subprocess
import sys
import warnings
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
from darkvessel.ocean.grid import OCEAN_CAVEAT, REPORTING_BOXES

CONTRACT_VERSION = "1.3.0"
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
FIXTURE_NOTE = ("Development fixture built by app/frontend/fixtures/make_fixture.py: a small subset of the real open-build "
                "files (live passes with the Pearl River pass of 2026-10-10, the September run, VIIRS lights, aisstream "
                "vessels, open leads, ocean context). Nothing is synthetic. Not a data product.")
# The first live pass with AIS in its footprint (Pearl River mouth and Hong Kong, S1D 2026-10-10 10:32 UTC).
PR_RUN = "live_S1D_20261010T1032"
AISSTREAM_LABEL = "live AIS relayed by aisstream.io; terms UNVERIFIED"  # board D4.7
# Identity strings of a matched live contact, as the live file states them (README reading 16: the record wins).
LIVE_IDENTITY_FIELDS = ["mmsi", "vessel_name", "call_sign", "imo", "flag", "ship_type", "length_ais_m", "identity_source"]
# Live matching evidence that is not a bulk column (contract 1.3.0 contact fields, darkvessel.live.schema).
LIVE_EVIDENCE_FIELDS = ["review_note", "match_ambiguous", "ambiguous_mmsi", "match_alt_dist_m", "az_time_utc", "az_shift_m",
                        "match_dist_uncorr_m", "velocity_source", "pred_method", "ais_sog_kn"]
CNN_THRESHOLD = 0.631783
AIS_ONLY_FIELDS = ["mmsi", "vessel_name", "call_sign", "imo", "flag", "ship_type", "ais_class", "length_ais_m", "sog_kn", "lon", "lat",
                   "scene_id", "pred_method", "pred_dt_s", "n_reports", "on_tested_sea", "dist_coast_km", "nearest_object_m",
                   "nearest_object_class", "ambiguous_det_id", "oversized_det_id", "identity_source"]
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
    {"key": "gebco_2026", "name": "GEBCO_2026 Grid", "method": "depth on the 0.01 degree grid", "script": "scripts/22_static_layers.py",
     "licence": "public domain; acknowledge the source; not for navigation", "licence_url": "https://www.gebco.net/data-products/gridded-bathymetry-data",
     "url": "https://www.gebco.net/", "credit": "GEBCO Compilation Group (2026) GEBCO 2026 Grid"},
    {"key": "wpi", "name": "NGA World Port Index (Pub 150)", "method": "distance to the nearest major port", "script": "scripts/22_static_layers.py",
     "licence": "NGA claims no copyright in posted products; no endorsement", "licence_url": None, "url": "https://msi.nga.mil/Publications/WPI", "credit": None},
    {"key": "worldbank_density", "name": "World Bank and IMF Global Shipping Traffic Density", "method": "presence only (published value above 0); never a count",
     "script": "scripts/22_static_layers.py", "licence": "CC BY 4.0", "licence_url": "https://creativecommons.org/licenses/by/4.0/",
     "url": "https://datacatalog.worldbank.org/search/dataset/0037580/Global-Shipping-Traffic-Density", "credit": "World Bank / IMF"},
    {"key": "mur_sst", "name": "MUR v4.1 SST (JPL PO.DAAC) via NOAA CoastWatch ERDDAP", "method": "daily 09 UTC analysis; gradient and fronts", "script": "scripts/23_daily_ocean.py",
     "licence": "free of charge under the PO.DAAC data policy", "licence_url": None, "url": "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.html", "credit": None},
    {"key": "chl_dineof", "name": "NOAA CoastWatch chlorophyll-a, DINEOF gap-filled", "method": "daily, log10 mg m-3", "script": "scripts/23_daily_ocean.py",
     "licence": "CC0-1.0 or NOAA no-copyright text, per dataset", "licence_url": None, "url": "https://coastwatch.noaa.gov/", "credit": None},
    {"key": "rtofs", "name": "NOAA RTOFS global nowcast 2-D diagnostics", "method": "currents and mixed layer", "script": "scripts/23_daily_ocean.py",
     "licence": "NOAA open data", "licence_url": None, "url": "https://registry.opendata.aws/noaa-rtofs/", "credit": None},
    {"key": "gfs_wave", "name": "NOAA GFS-Wave 0.25 degree significant wave height", "method": "18 UTC analyses", "script": "scripts/23_daily_ocean.py",
     "licence": "NOAA open data", "licence_url": None, "url": "https://registry.opendata.aws/noaa-gfs-bdp-pds/", "credit": None},
    {"key": "gfs_wind", "name": "NOAA GFS 0.25 degree 10 m wind", "method": "nearest cycle and step", "script": "scripts/16_weather_context.py",
     "licence": "NOAA open data", "licence_url": None, "url": "https://registry.opendata.aws/noaa-gfs-bdp-pds/", "credit": None},
    {"key": "expected_activity", "name": "Expected-activity model expected_activity_v1", "method": "gradient-boosted trees with leave-one-week-out expectation; calm-weather anomaly flags",
     "script": "scripts/34_expected_activity.py", "licence": "derived", "licence_url": None, "url": None, "credit": None},
    {"key": "esa_acq_plan", "name": "ESA Sentinel-1 acquisition plan KML files", "method": "darkvessel.ais.s1_passes",
     "script": "scripts/28_ais_reach.py", "licence": "ESA public plan; repeat predictions are not ESA's plan",
     "licence_url": None, "url": "https://sentinels.copernicus.eu/copernicus/sentinel-1/acquisition-plans", "credit": None},
    {"key": "analyst", "name": "Owner labels and lead decisions", "method": "the app", "script": "app/frontend",
     "licence": "the owner's", "licence_url": None, "url": None, "credit": None},
    {"key": "app", "name": "values computed by the product (review priority, evidence counts)",
     "method": "priority_v0_fixture: uncalibrated start weights of docs/product_design.md section 4.1",
     "script": "app/frontend/fixtures/make_fixture.py", "licence": "derived", "licence_url": None, "url": None, "credit": None},
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


def live_vessel_rows(mmsis: set, live_sel: pd.DataFrame, ais_only: pd.DataFrame) -> pd.DataFrame:
    """Vessel rows (vessels_latest_4326 columns) for MMSIs of a live pass that the vessels layer does not hold: identity
    from the matched contact or the AIS-only row of the live file, else a stub with the MMSI and its MID only."""
    rows = []
    for m in sorted(mmsis):
        r = {"mmsi": m, "mid": int(m[:3]) if m[:3].isdigit() else None, "in_aoi": True, "ever_in_aoi": True, "gear_beacon_like": False}
        hit = live_sel[(live_sel["mmsi"] == m) & (live_sel["ais_status"] == "matched")]
        ao = ais_only[ais_only["mmsi"] == m]
        if len(hit):
            h = hit.iloc[0]
            r.update({"name": h.get("vessel_name"), "callsign": h.get("call_sign"), "imo": h.get("imo"), "ship_type_label": h.get("ship_type"),
                      "length_m": h.get("length_ais_m"), "ais_class": h.get("ais_class"), "sog_kn": h.get("ais_sog_kn"),
                      "identity_source": h.get("identity_source"), "stub": False, "last_seen_utc": h.get("acq_utc")})
        elif len(ao):
            a = ao.iloc[0]
            r.update({"name": a.get("vessel_name"), "callsign": a.get("call_sign"), "imo": a.get("imo"), "ship_type_label": a.get("ship_type"),
                      "length_m": a.get("length_ais_m"), "ais_class": a.get("ais_class"), "sog_kn": a.get("sog_kn"), "lon": a.get("lon"),
                      "lat": a.get("lat"), "identity_source": a.get("identity_source"), "stub": False, "last_seen_utc": a.get("acq_utc")})
        else:
            r.update({"stub": True, "identity_source": "MMSI only: heard by the live feed as the nearest AIS vessel of a contact; no identity in this fixture"})
        rows.append(r)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------------------------- main build
def build(args) -> dict:
    parts: dict[str, dict] = {}
    aoi = aoi_gdf()
    aoi_geom = aoi.geometry.iloc[0]

    # ---- live contacts. The Pearl River pass (PR_RUN, the first pass with AIS in its footprint): every matched contact
    # (high, medium and low quality, with the hand-check note), the unmatched contacts of the highest-priority real L1
    # leads, ambiguous contacts (candidates listed, never leads), fixed unmatched returns and no_coverage contacts. Every
    # other processed live pass (no AIS heard near it, all no_coverage): a small sample for the coverage result.
    live = read("live/live_contacts.gpkg", "contacts_4326")
    scenes_live = read("live/live_contacts.gpkg", "scenes_4326")
    live["mmsi"] = live["mmsi"].map(lambda v: None if pd.isna(v) else str(int(v)))
    live["nearest_ais_mmsi"] = live["nearest_ais_mmsi"].map(lambda v: None if pd.isna(v) else str(int(v)))
    leads_open = gpd.read_file(DATA_DIR / "leads_open.gpkg", layer="leads_4326", ignore_geometry=True)
    pr = live[live["run_id"] == PR_RUN]
    # An L1 lead stands only on an unmatched, unambiguous dark-lead contact. A leads file older than the live file's
    # rematch can cite a contact that is now ambiguous or matched: such a lead is stale and stays out of the fixture.
    standing = pr[(pr["ais_status"] == "unmatched") & ~pr["match_ambiguous"].fillna(False).astype(bool)
                  & pr["dark_lead"].fillna(False).astype(bool)]["det_id"]
    pr_l1_all = leads_open[(leads_open["lead_type"] == "L1") & leads_open["det_id"].isin(pr["det_id"])]
    stale = pr_l1_all[~pr_l1_all["det_id"].isin(standing)]
    if len(stale):
        print(f"  {len(stale)} stale L1 leads left out (primary contact no longer an unambiguous dark lead): "
              + ", ".join(stale["lead_id"].head(5)))
    pr_l1 = pr_l1_all[pr_l1_all["det_id"].isin(standing)].sort_values(["priority", "lead_id"], ascending=[False, True]).head(args.leads)
    pr_unm = pr[pr["ais_status"] == "unmatched"]
    amb = pr_unm["match_ambiguous"].fillna(False).astype(bool)
    pr_sel = pd.concat([
        pr[pr["ais_status"] == "matched"],
        pr[pr["det_id"].isin(pr_l1["det_id"])],
        stable_pick(pr_unm[amb], args.ambiguous, "det_id"),
        stable_pick(pr_unm[~amb & (pr_unm["confidence"] == "fixed")], 8, "det_id"),
        stable_pick(pr_unm[~amb & pr_unm["dark_lead"].fillna(False).astype(bool) & ~pr_unm["det_id"].isin(pr_l1["det_id"])], 6, "det_id"),
        stable_pick(pr[(pr["ais_status"] == "no_coverage") & (pr["confidence"] == "high")], args.no_coverage, "det_id"),
        pr[(pr["ais_status"] == "no_coverage") & pr["review_note"].notna()],  # hand-checked: notes may say "not dark"
    ]).drop_duplicates("det_id")
    other = live[live["run_id"] != PR_RUN]
    other_sel = []
    for run_id, g in other.groupby("run_id"):
        other_sel.append(pd.concat([
            g[g["confidence"] == "high"].sort_values("cnn_score", ascending=False).head(args.live_other // 2),
            stable_pick(g[g["confidence"] == "medium"], args.live_other // 3, "det_id"),
            stable_pick(g[g["confidence"] == "fixed"], max(args.live_other // 6, 1), "det_id"),
        ]).drop_duplicates("det_id"))
    live_sel = pd.concat([pr_sel, *other_sel]).copy()
    live_sel["view"] = "live"
    live_sel["pass_id"] = live_sel["run_id"]
    live_sel["src"] = "det_live"
    # the Pearl River AIS-only vessels: tested sea, ambiguous and oversized first, then a stable sample
    ais_only = read(f"live/{PR_RUN}.gpkg", "ais_only_4326")
    ais_only["mmsi"] = ais_only["mmsi"].map(lambda v: None if pd.isna(v) else str(int(v)))
    ao_key = ais_only["on_tested_sea"].fillna(False).astype(bool) | ais_only["ambiguous_det_id"].notna() | ais_only["oversized_det_id"].notna()
    ais_only_sel = pd.concat([ais_only[ao_key], stable_pick(ais_only[~ao_key], max(0, args.ais_only - int(ao_key.sum())), "mmsi")]).head(args.ais_only)

    # ---- vessels (aisstream): every MMSI a selected live contact or AIS-only vessel names, then the AOI, up to --vessels
    ves = read("ais_live.gpkg", "vessels_latest_4326")
    ves["mmsi"] = ves["mmsi"].astype("Int64").astype(str)
    ves = ves[ves["mmsi"].str.len() == 9]  # ship stations only
    named_mmsi = set(live_sel["mmsi"].dropna()) | set(live_sel["nearest_ais_mmsi"].dropna()) | set(ais_only_sel["mmsi"].dropna())
    for v in live_sel["ambiguous_mmsi"].dropna():
        named_mmsi |= {x.strip().removesuffix(".0") for x in str(v).replace(",", ";").split(";") if x.strip()}
    named = ves[ves["name"].notna()]
    chosen = pd.concat([ves[ves["mmsi"].isin(named_mmsi)], named[named["in_aoi"] == True], named[named["in_aoi"] != True]])  # noqa: E712
    chosen = chosen.drop_duplicates("mmsi")
    ves = chosen.head(max(args.vessels, int(ves["mmsi"].isin(named_mmsi).sum()))).reset_index(drop=True)
    ves["stub"] = False
    ves["identity_source"] = "aisstream static message"
    # data/ais_live.gpkg vessels_latest holds only the first recording day (to 2026-10-08 23:37 UTC), so the vessels of
    # the Pearl River pass are not in it. Their rows are built from what the live file itself states (the matched
    # contact's identity, or the AIS-only row); a vessel known only as a nearest MMSI is a stub (MMSI and MID flag only).
    ves = pd.concat([ves, live_vessel_rows(named_mmsi - set(ves["mmsi"]), live_sel, ais_only_sel)], ignore_index=True)
    vrow = {m: i for i, m in enumerate(ves["mmsi"])}
    tracks = read("ais_live.gpkg", "tracks_4326")
    tracks["mmsi"] = tracks["mmsi"].astype("Int64").astype(str)
    tracks = tracks[tracks["mmsi"].isin(vrow) & (tracks["n_positions"] >= 3)].copy()
    tracks["geometry"] = tracks.geometry.simplify(0.002, preserve_topology=False)
    tracks = pd.concat([tracks[tracks["mmsi"].isin(named_mmsi)], tracks[~tracks["mmsi"].isin(named_mmsi)]]).head(args.tracks).reset_index(drop=True)

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
    contacts["mmsi"] = contacts["mmsi"].map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else str(int(float(v))))
    contacts["nearest_ais_mmsi"] = contacts["nearest_ais_mmsi"].map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else str(int(float(v))))
    for i, r in contacts.iterrows():
        if r["nearest_ais_mmsi"] in vrow:
            contacts.at[i, "nearest_ref"] = vrow[r["nearest_ais_mmsi"]]
        if r["ais_status"] == "matched" and r["mmsi"] in vrow:
            contacts.at[i, "vessel_ref"] = vrow[r["mmsi"]]

    # ---- live identity and hand-check fields that are not bulk columns go to the records (README reading 16): the
    # identity strings of a match as the live file states them (they win over the vessel row), the hand-check note, the
    # ambiguity candidates and the azimuth-shift diagnostics, and the board D4.7 label on every aisstream identity.
    records_contacts: dict[str, dict] = {}
    for _, r in contacts[contacts["view"] == "live"].iterrows():
        rec: dict = {}
        if r["ais_status"] == "matched":
            for f in LIVE_IDENTITY_FIELDS:
                v = r.get(f)
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    continue
                rec[f] = str(int(float(v))) if f == "imo" else (float(v) if f == "length_ais_m" else v)
        for f in LIVE_EVIDENCE_FIELDS:
            v = r.get(f)
            if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)) or (f == "match_ambiguous" and not bool(v)):
                continue
            if f == "ambiguous_mmsi":
                v = ";".join(x.strip().removesuffix(".0") for x in str(v).replace(",", ";").split(";") if x.strip())
            elif f == "az_time_utc":
                v = to_py(pd.Timestamp(v))
            elif isinstance(v, (np.floating, float)):
                v = round(float(v), 1)
            elif isinstance(v, (np.bool_, bool)):
                v = bool(v)
            rec[f] = v
        if r.get("ais_source") == "aisstream" and (r["mmsi"] or r["nearest_ais_mmsi"]):
            rec["identity_label"] = AISSTREAM_LABEL
        if rec:
            records_contacts[r["det_id"]] = rec

    # ---- leads: the real open leads (data/leads_open.gpkg) of the selected Pearl River contacts (L1) and the
    # highest-priority L7 coverage leads (primary: a cell). Codes as the lead builder writes them.
    passes_json = json.load(open(DATA_DIR / "s1_next_passes.json"))
    groups = passes_json["pass_groups"]
    row_of_det = {d: i for i, d in enumerate(contacts["det_id"])}
    l7 = leads_open[leads_open["lead_type"] == "L7"].sort_values(["priority", "lead_id"], ascending=[False, True]).head(args.l7)
    lead_rows = []
    lead_records = {}
    for _, r in pd.concat([pr_l1, l7]).iterrows():
        is_l1 = r["lead_type"] == "L1"
        if is_l1 and r["det_id"] not in row_of_det:
            continue
        lead_rows.append({"lead_id": r["lead_id"], "lead_type": r["lead_type"], "primary_type": "contacts" if is_l1 else "cells",
                          "primary": row_of_det[r["det_id"]] if is_l1 else -1, "primary_cell": None if is_l1 else r["primary_id"],
                          "priority": int(r["priority"]), "state": r["state"], "reason": r["reason"], "region_box": r["region_box"] or "other",
                          "time_utc": r["time_utc"], "next_look_utc": r["next_look_utc"], "lon": float(r["lon"]), "lat": float(r["lat"]),
                          "f_evidence": int(r["pts_evidence_quality"]), "f_corroboration": int(r["pts_corroboration"]),
                          "f_reach": int(r["pts_ais_reach"]), "f_persistence": int(r["pts_persistence"]), "f_area": int(r["pts_area_weight"]),
                          "priority_model_id": r["priority_model_id"], "synthetic": False})
        lead_records[r["lead_id"]] = {
            "title": r["title"], "evidence": json.loads(r["evidence"]), "history": json.loads(r["history"] or "[]"),
            "factors": json.loads(r["factors"]), "lawful_explanations": json.loads(r["lawful_explanations"]),
            "change_indicators": json.loads(r["change_indicators"]), "prov": json.loads(r["prov"] or "{}")}
        if is_l1:
            records_contacts.setdefault(r["det_id"], {})["lead_ids"] = [r["lead_id"]]
    for det_id in records_contacts:
        records_contacts[det_id].setdefault("lead_ids", [])

    # ---- lights: clear-sky lights of the two most recent nights near the live scenes, then a stable sample
    li = read("viirs_lights.gpkg", "viirs_lights_4326")
    nights = sorted(li["night"].unique())[-2:]
    li_near = li[li["night"].isin(nights) & (li["lon"] > 99) & (li["lon"] < 104) & (li["lat"] > 6) & (li["lat"] < 14)]
    li_sel = pd.concat([li_near, stable_pick(li[li["night"].isin(nights)], args.lights, "light_id")]).drop_duplicates("light_id").head(args.lights).copy()
    li_sel = li_sel.sort_values("time_utc").reset_index(drop=True)

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
    }
    parts["contacts"] = {"type": "contacts", "n": int(len(c)), "columns": cols,
                         "prov": {"cnn_score": "cnn_v0", "cnn_vessel": "cnn_v0", "cnn_threshold": "cnn_v0", "cnn_model_id": "cnn_v0",
                                  "cnn_chip_valid_frac": "cnn_v0", "ais_status": "aisstream", "ais_source": "aisstream",
                                  "match_method": "aisstream", "match_dist_m": "aisstream", "match_dt_s": "aisstream",
                                  "match_quality": "aisstream", "mmsi": "aisstream", "imo": "aisstream", "vessel_name": "aisstream",
                                  "call_sign": "aisstream", "flag": "mid_itu", "ship_type": "aisstream", "length_ais_m": "aisstream",
                                  "identity_source": "aisstream", "nearest_ais_mmsi": "aisstream", "nearest_ais_dist_m": "aisstream",
                                  "nearest_ais_dt_s": "aisstream", "n_ais_10km": "aisstream", "ais_reach": "aisstream",
                                  "dark_lead": "aisstream", "lead_ids": "app", "review_note": "analyst", "match_ambiguous": "aisstream",
                                  "ambiguous_mmsi": "aisstream", "match_alt_dist_m": "aisstream", "az_time_utc": "det_live",
                                  "az_shift_m": "det_live", "match_dist_uncorr_m": "aisstream", "velocity_source": "aisstream",
                                  "pred_method": "aisstream", "ais_sog_kn": "aisstream", "identity_label": "aisstream", "match_gate_m": "aisstream"},
                         "records": records_contacts,
                         "note": "Bulk columns of the D1 fields plus extensions; identity by reference (vessel_ref, nearest_ref)."}
    ctx_c, n_ctx_c = context_block(c["det_id"].tolist(), ("radar", "radar_detail"), OCEAN_CAVEAT)
    if ctx_c:
        parts["contacts"]["object_context"] = ctx_c

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
        "identity_source": col_str(v["identity_source"]),
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
        "stub": col_bool(v["stub"]),
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
    ctx_l, n_ctx_l = context_block(L["light_id"].tolist(), ("viirs",), OCEAN_CAVEAT)
    if ctx_l:
        parts["lights"]["object_context"] = ctx_l

    # ---------------------------------------------------------------------------------------- cells and rasters (round 3)
    obj_cells = [cell_id(x, y) for x, y in zip(c["lon"], c["lat"])] + [cell_id(x, y) for x, y in zip(L["lon"], L["lat"])]
    anomaly_cells = []
    try:
        an = gpd.read_file(DATA_DIR / "expected_activity_anomalies.gpkg", layer="expected_activity_anomalies_4326")
        if "cell_id" in an:
            anomaly_cells = an["cell_id"].astype(str).drop_duplicates().head(12).tolist()
    except Exception:  # noqa: BLE001  the anomaly file is optional for the fixture
        anomaly_cells = []
    lead_cells = [r["primary_cell"] or cell_id(r["lon"], r["lat"]) for r in lead_rows]
    order = list(dict.fromkeys(lead_cells + anomaly_cells + ["r13c64"] + obj_cells))
    parts["cells"] = cells_part(order, order, OCEAN_CAVEAT)
    cell_row = {cid: i for i, cid in enumerate(parts["cells"].pop("_order"))}
    for r in lead_rows:  # an L7 lead's primary is a row of the cells part
        if r["primary_cell"] is not None:
            r["primary"] = cell_row.get(r["primary_cell"], 4294967295)
    parts["rasters"] = rasters_part()

    # ---------------------------------------------------------------------------------------- encode leads
    ld = pd.DataFrame(lead_rows)
    if len(ld):
        parts["leads"] = {"type": "leads", "n": int(len(ld)), "columns": {
            "lead_type": col_dict(ld["lead_type"], ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]),
            "primary_type": col_dict(ld["primary_type"], ["contacts", "lights", "cells", "vessels"]),
            "primary": {"t": "u32", "b": b64(ld["primary"].astype("int64").to_numpy().astype("<u4")), "na": 4294967295},
            "priority": col_num(ld["priority"], 1),
            "state": col_dict(ld["state"], STATES),
            "reason": col_dict(ld["reason"], REASONS),
            "region_box": col_dict(ld["region_box"], list(dict.fromkeys(list(REPORTING_BOXES) + ["other"] + ld["region_box"].dropna().astype(str).tolist()))),
            "time_utc": col_time(ld["time_utc"]),
            "next_look_utc": col_time(ld["next_look_utc"]),
            "lon": col_num(ld["lon"], 100000),
            "lat": col_num(ld["lat"], 100000),
            "f_evidence": col_num(ld["f_evidence"], 1),
            "f_corroboration": col_num(ld["f_corroboration"], 1),
            "f_reach": col_num(ld["f_reach"], 1),
            "f_persistence": col_num(ld["f_persistence"], 1),
            "f_area": col_num(ld["f_area"], 1),
            "priority_model_id": col_dict(ld["priority_model_id"]),
            "calibrated": col_const(False),
            "research_only": col_const(False),
            "caveat": col_const(PRODUCT_CAVEAT),
            "src": col_const("app"),
        }, "factors": [
            {"factor": "evidence_quality", "column": "f_evidence", "max_points": 30, "source": "cnn_v0"},
            {"factor": "corroboration", "column": "f_corroboration", "max_points": 25, "source": "app"},
            {"factor": "ais_reach", "column": "f_reach", "max_points": 20, "source": "aisstream"},
            {"factor": "persistence", "column": "f_persistence", "max_points": 15, "source": "det_live"},
            {"factor": "area_weight", "column": "f_area", "max_points": 10, "source": "analyst"},
        ], "records": lead_records, "lawful_explanations": LAWFUL, "change_indicators": CHANGE,
            "note": ("lead_id = <lead_type>-<primary id>; records hold the title, evidence, history, the full factor list with "
                     "each factor's value text (wins over the point columns, README reading 6) and the codes")}

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
    # processed live passes from scenes_4326 (real counts); the Pearl River pass carries its AIS-only vessels
    sl = scenes_live
    for run_id, grp in sl.groupby("run_id"):
        n_status = {"matched": int(grp["n_matched"].sum()), "unmatched": int(grp["n_unmatched"].sum()),
                    "no_coverage": int(grp["n_no_coverage"].sum()), "not_checked": 0}
        fp = grp.geometry.union_all()
        ao = ais_only_sel if run_id == PR_RUN else ais_only_sel.iloc[0:0]
        ao_rows = [{k: to_py(v) for k, v in r.items() if k in AIS_ONLY_FIELDS and not (v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)))}
                   for r in ao.drop(columns="geometry").to_dict("records")]
        for r in ao_rows:
            r["vessel_key"] = f"mmsi:{r['mmsi']}" if r.get("mmsi") else None
            r["identity_label"] = AISSTREAM_LABEL
        n_in_fixture = int((c["pass_id"] == run_id).sum())
        feats.append({"type": "Feature", "geometry": mapping(fp.simplify(0.01)), "properties": {
            "pass_id": run_id, "mission": grp["mission"].iloc[0], "relative_orbit": int(grp["orbit_rel"].iloc[0]),
            "pass_dir": grp["pass_dir"].iloc[0], "start_utc": to_py(pd.Timestamp(grp["start_utc"].min())),
            "stop_utc": to_py(pd.Timestamp(grp["stop_utc"].max())), "status": "past", "sources": ["processed"],
            "aoi_overlap_km2": float(grp["aoi_overlap_km2"].sum()),
            "aoi_parts": [str(lab) for lab, g in zip(aoi["label"], aoi.geometry) if g.intersects(fp)] or None,
            "scenes": grp["product_id"].tolist(), "processed": True, "n_contacts": n_status,
            "ais_aoi_positions": int(grp["ais_aoi_positions"].sum()), "ais_aoi_mmsi": int(grp["ais_aoi_mmsi"].max()),
            "ais_footprint_positions": int(grp["ais_footprint_positions"].sum()), "ais_footprint_mmsi": int(grp["ais_footprint_mmsi"].max()),
            "ais_near_footprint_mmsi": int(grp["ais_near_footprint_mmsi"].max()),
            "n_ais_only": int(grp["n_ais_only"].fillna(0).sum()) if "n_ais_only" in grp else len(ao_rows),
            "ais_only": ao_rows if run_id == PR_RUN else [],
            "identity_label": AISSTREAM_LABEL,
            "scene_counts": [{"product_id": r["product_id"], "start_utc": to_py(pd.Timestamp(r["start_utc"])), "n_contacts": int(r["n_contacts"]),
                              "n_high": int(r["n_high"]), "n_medium": int(r["n_medium"]), "n_fixed": int(r["n_fixed"]),
                              "n_matched": int(r["n_matched"]), "n_unmatched": int(r["n_unmatched"]), "n_no_coverage": int(r["n_no_coverage"]),
                              "n_dark_leads": int(r["n_dark_leads"]) if "n_dark_leads" in r and pd.notna(r["n_dark_leads"]) else None,
                              "n_ambiguous": int(r["n_ambiguous"]) if "n_ambiguous" in r and pd.notna(r["n_ambiguous"]) else None,
                              "ais_aoi_positions": int(r["ais_aoi_positions"]), "ais_aoi_mmsi": int(r["ais_aoi_mmsi"]),
                              "ais_footprint_positions": int(r["ais_footprint_positions"]), "ais_footprint_mmsi": int(r["ais_footprint_mmsi"]),
                              "ais_near_footprint_mmsi": int(r["ais_near_footprint_mmsi"]), "ais_recorded_hours": int(r["ais_recorded_hours"])}
                             for _, r in grp.iterrows()],
            "fixture_note": (f"{n_in_fixture} of this pass's {sum(n_status.values()):,} contacts are in the fixture"
                             + (f"; {len(ao_rows)} of its {int(grp['n_ais_only'].fillna(0).sum())} AIS-only vessels" if run_id == PR_RUN else "")),
            "contacts_in_bundle": True,
            "caveat": PRODUCT_CAVEAT, "src": "det_live", "prov": {"n_contacts": "app", "n_ais_only": "aisstream", "ais_only": "aisstream", **{k: "aisstream" for k in AIS_PASS_FIELDS}}}})
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
        "git_hash": git_hash(), "priority_model_id": str(ld["priority_model_id"].iloc[0]) if len(ld) else PRIORITY_MODEL_ID, "app_version": "fixture",
        "sources": SOURCES,
        "files": [
            {"path": "data/live/live_contacts.gpkg", "layer": "contacts_4326", "status": "existing", "rows": int(len(live)), "in_bundle": int(len(live_sel))},
            {"path": "data/detections_regional.gpkg", "layer": "detections_regional_4326", "status": "existing", "rows": int(len(reg)), "in_bundle": int(len(reg_sel))},
            {"path": "data/structures_regional.gpkg", "layer": "structures_regional_4326", "status": "existing", "rows": int(len(st)), "in_bundle": int(len(st_sel))},
            {"path": "data/viirs_lights.gpkg", "layer": "viirs_lights_4326", "status": "existing", "rows": int(len(li)), "in_bundle": int(len(li_sel))},
            {"path": "data/ais_live.gpkg", "layer": "vessels_latest_4326", "status": "existing", "rows": int(len(chosen)), "in_bundle": int(len(ves))},
            {"path": "data/s1_next_passes.json", "layer": "pass_groups", "status": "existing", "rows": len(groups), "in_bundle": len(groups)},
            {"path": "data/leads_open.gpkg", "layer": "leads_4326", "status": "existing", "rows": int(len(leads_open)), "in_bundle": int(len(ld))},
            {"path": f"data/live/{PR_RUN}.gpkg", "layer": "ais_only_4326", "status": "existing", "rows": int(len(ais_only)), "in_bundle": int(len(ais_only_sel))},
            {"path": "data/events_open.gpkg", "layer": "events_4326", "status": "missing", "rows": 0, "in_bundle": 0},
            {"path": "data/ocean_static_cells.parquet", "layer": None, "status": "existing", "rows": 5116, "in_bundle": int(parts["cells"]["n"])},
            {"path": "data/ocean_context_objects.parquet", "layer": None, "status": "existing", "rows": None, "in_bundle": int(n_ctx_c + n_ctx_l)},
            {"path": "data/expected_activity.parquet", "layer": None, "status": "existing", "rows": None, "in_bundle": len(parts["cells"]["records"])},
        ],
        "counts": {"contacts": int(len(c)), "vessels": int(len(ves)), "lights": int(len(li_sel)), "leads": int(len(ld)), "passes": len(feats),
                   "events": 0, "cells": int(parts["cells"]["n"]), "rasters": int(parts["rasters"]["n"]),
                   "object_context": int(n_ctx_c + n_ctx_l)},
        "ais_recording": {"period_start_utc": summ.get("period_start_utc"), "period_end_utc": summ.get("period_end_utc"),
                          "hours_recorded": summ.get("hours_recorded"), "gaps": gaps, "positions": summ.get("positions"),
                          "mmsi_count": summ.get("mmsi_count"), "generated_utc": summ.get("generated_utc"),
                          "note": "aisstream relays shore receivers: dense near Hong Kong, the Singapore and Bangka straits and the Philippines, thin off Vietnam"},
        "live_rules": {k: live_about.get(k) for k in ["no_coverage_rule", "ais_status_rule", "match_quality_rule", "dark_lead_rule", "distance_gate", "ais_window", "cnn"]},
        "data_credit": "Contains modified Copernicus Sentinel data 2026",
        "dropped": [{"part": "chips", "reason": "no chip cache in the fixture"},
                    {"part": "cells", "reason": f"{parts['cells']['n']} of 5,116 cells (those of the fixture's objects and leads); full expected-activity rows for {len(parts['cells']['records'])} cells, counts for the rest"},
                    {"part": "rasters", "reason": f"{parts['rasters']['n']} overlays at reduced resolution"},
                    {"part": "camau", "reason": "not in the fixture"}, {"part": "events", "reason": "data/events_open.gpkg pending"}],
        "fixture": {"synthetic": False, "note": FIXTURE_NOTE},
        "parts": {},
    }
    parts["meta"] = meta
    sizes = {k: len(json.dumps(v, separators=(",", ":"))) for k, v in parts.items() if k != "meta"}
    meta["parts"] = sizes
    return parts


# ----------------------------------------------------------------------------------------------- round 3: D5 shapes
OCEAN_CAVEAT_TEXT = None  # filled from darkvessel.ocean.grid.OCEAN_CAVEAT in build()
# README reading 13: per field the unit, the registry key (static fields) or the column holding the source, and the
# column holding the valid time. Shipping is presence (bool8), never a magnitude (board D4.3).
CONTEXT_FIELD_SPEC = {
    "depth_m": {"unit": "m", "src": "gebco_2026", "s": 1},
    "dist_coast_km": {"unit": "km", "src": "natural_earth", "s": 10},
    "dist_port_km": {"unit": "km", "src": "wpi", "s": 10},
    **{f"ship_presence_{k}": {"unit": "presence", "src": "worldbank_density", "bool": True}
       for k in ("all", "commercial", "fishing", "oilgas", "passenger", "leisure")},
    "sst_c": {"unit": "degC", "src_col": "sst_src", "time_col": "sst_time", "s": 100},
    "sst_grad": {"unit": "degC/km", "src_col": "sst_src", "time_col": "sst_time", "s": 10000},
    "dist_front_km": {"unit": "km", "src_col": "sst_src", "time_col": "sst_time", "s": 10},
    "chl_log10": {"unit": "log10 mg m-3", "src_col": "chl_src", "time_col": "chl_time", "s": 100},
    "current_speed_ms": {"unit": "m/s", "src": "rtofs", "time_col": "current_time", "s": 100},
    "mld_m": {"unit": "m", "src": "rtofs", "time_col": "current_time", "s": 10},
    "wave_hs_m": {"unit": "m", "src": "gfs_wave", "time_col": "wave_time", "s": 100},
}


def context_block(ids: list[str], object_types: tuple[str, ...], caveat: str) -> tuple[dict | None, int]:
    """Columnar `object_context` block parallel to a part's rows (README reading 13), from
    data/ocean_context_objects.parquet keyed by (object_type, object_id); rows without context have time_utc null."""
    t = pq.read_table(DATA_DIR / "ocean_context_objects.parquet").to_pandas()
    t = t[t["object_type"].astype(str).isin(object_types) & t["object_id"].isin(set(ids))].drop_duplicates("object_id")
    t = t.set_index("object_id").reindex(ids)
    n_with = int(t["time_utc"].notna().sum())
    if n_with == 0:
        return None, 0
    cols = {"time_utc": col_time(t["time_utc"].tolist()), "region": col_dict(t["region"].astype(object).where(t["region"].notna(), None).tolist()),
            "cell_id": col_dict([None if pd.isna(v) else str(v) for v in t["cell_id"]]),
            "sst_time": col_dict([None if pd.isna(v) else str(v) for v in t["sst_time"]]),
            "sst_src": col_dict([None if pd.isna(v) else str(v) for v in t["sst_source"]]),
            "chl_time": col_dict([None if pd.isna(v) else str(v) for v in t["chl_time"]]),
            "chl_src": col_dict([None if pd.isna(v) else str(v) for v in t["chl_dataset"]]),
            "current_time": col_dict([None if pd.isna(v) else str(v) for v in t["current_time"]]),
            "wave_time": col_dict([None if pd.isna(v) else str(v) for v in t["wave_time"]])}
    fields = {}
    for name, spec in CONTEXT_FIELD_SPEC.items():
        vals = t[name].astype(object).where(t[name].notna(), None).tolist()
        cols[name] = col_bool(vals) if spec.get("bool") else col_num(vals, spec.get("s", 1))
        fields[name] = {k: v for k, v in spec.items() if k in ("unit", "src", "src_col", "time_col")}
    return {"n": len(ids), "columns": cols, "fields": fields, "caveat": caveat,
            "note": "data/ocean_context_objects.parquet at the object (board D5.3); a row whose time_utc is null has no context"}, n_with


CELL_STATIC_COLS = ["sea_share", "sea_area_km2", "depth_mean_m", "depth_min_m", "depth_max_m", "share_shallower_50m",
                    "share_shallower_200m", "slope_mean_m_per_km", "dist_coast_km", "dist_coast_min_km", "dist_port_km",
                    "dist_port_min_km", "ship_presence_share_all", "ship_presence_share_fishing", "ship_presence_share_commercial",
                    "ship_presence_share_oilgas", "ship_presence_share_passenger", "ship_presence_share_leisure"]
CELL_SCALE = {"sea_share": 100, "share_shallower_50m": 100, "share_shallower_200m": 100, "slope_mean_m_per_km": 10,
              **{c: 100 for c in CELL_STATIC_COLS if c.startswith("ship_presence_share")}}
NIGHT_COLS = {"sst_mean_c": 100, "sst_sd_c": 100, "sst_grad_mean": 10000, "front_share": 100, "dist_front_km": 10,
              "chl_log10_mean": 100, "chl_valid_share": 100, "current_speed_ms": 100, "mld_m": 10, "wave_hs_m": 10, "wind_ms": 10}
EA_ROW_CELLS = 14  # cells whose full D5.4 rows go into records (the rest carry counts only)


def raster_at(name: str, lon: np.ndarray, lat: np.ndarray, box_mean: float | None = None) -> np.ndarray:
    """Raster value at points, or the mean over a box of half-width `box_mean` degrees (look probability is 0.05 deg)."""
    import rasterio
    with rasterio.open(DATA_DIR / "outputs/small" / f"{name}_4326.tif") as ds:
        a = ds.read(1, masked=True).astype("f8").filled(np.nan)
        tr = ds.transform
    out = np.full(len(lon), np.nan)
    for i, (x, y) in enumerate(zip(lon, lat)):
        if box_mean:
            c0, r0 = ~tr * (x - box_mean, y + box_mean)
            c1, r1 = ~tr * (x + box_mean, y - box_mean)
            r0, r1, c0, c1 = max(0, int(r0)), min(a.shape[0], int(np.ceil(r1))), max(0, int(c0)), min(a.shape[1], int(np.ceil(c1)))
            v = a[r0:r1, c0:c1]
            v = v[np.isfinite(v)]
            out[i] = v.mean() if v.size else np.nan
        else:
            c, r = ~tr * (x, y)
            r, c = int(r), int(c)
            if 0 <= r < a.shape[0] and 0 <= c < a.shape[1]:
                out[i] = a[r, c]
    return out


def cells_part(cell_ids: list[str], ea_first: list[str], caveat_ocean: str) -> dict:
    """`cells` part (README reading 11): columnar static fields, the newest night, Marine Regions attributes as published
    (`eez_attrs`, shown only while the EEZ layer is on), expected-activity counts per target, and the full D5.4 rows in
    `records[cell_id].expected_activity` for the first EA_ROW_CELLS cells of `ea_first`."""
    st = pd.read_parquet(DATA_DIR / "ocean_static_cells.parquet")
    st["cell_id"] = [f"r{int(r)}c{int(c)}" for r, c in zip(st["row"], st["col"])]
    st = st[st["cell_id"].isin(set(cell_ids))].reset_index(drop=True)
    n = len(st)
    cols = {"cell_id": col_str(st["cell_id"]), "row": col_num(st["row"], 1), "col": col_num(st["col"], 1),
            "lon": col_num(st["lon"], 1000), "lat": col_num(st["lat"], 1000), "region_box": col_dict(st["region"].astype(str))}
    for c in CELL_STATIC_COLS:
        cols[c] = col_num(st[c].astype(object).where(st[c].notna(), None).tolist(), CELL_SCALE.get(c, 1))
    lon, lat = st["lon"].to_numpy(float), st["lat"].to_numpy(float)
    cols["ais_reach_share"] = col_num(raster_at("ais_reach_share", lon, lat), 100)
    cols["ais_reach_mmsi"] = col_num(raster_at("ais_reach_mmsi", lon, lat), 1)
    for k in ("1d", "7d", "30d"):
        cols[f"look_prob_{k}"] = col_num(raster_at(f"s1_look_prob_{k}", lon, lat, box_mean=0.125), 1)  # percent
    cols["passes_90d"] = col_num(raster_at("s1_passes", lon, lat, box_mean=0.125), 1)
    cols["shipping_note"] = col_const("values as published, not counts; presence only")
    cols["src"] = col_const("app")
    cols["research_only"] = col_const(False)
    cols["caveat"] = col_const(PRODUCT_CAVEAT + " " + caveat_ocean)
    # newest night
    d = pd.read_parquet(DATA_DIR / "ocean_daily_cells.parquet")
    night = d["night"].max()
    d = d[d["night"] == night].copy()
    d["cell_id"] = [f"r{int(r)}c{int(c)}" for r, c in zip(d["row"], d["col"])]
    d = d.set_index("cell_id").reindex(st["cell_id"])
    nightly_cols = {c: col_num(d[c].astype(object).where(d[c].notna(), None).tolist(), s) for c, s in NIGHT_COLS.items()}
    valid = {k: (None if pd.isna(v) else str(v)) for k, v in d[["sst_date", "chl_date", "rtofs_valid_utc", "wave_valid_utc", "wind_valid_utc", "sst_source", "chl_dataset"]].dropna(how="all").iloc[0].items()}
    valid["moon_illum_pct"] = float(d["moon_illum_pct"].dropna().iloc[0]) if d["moon_illum_pct"].notna().any() else None
    # Marine Regions attributes, as published
    eez_cols = {"marineregions_mrgid": col_dict(st["marineregions_mrgid"].astype("Int64").astype(object).where(st["marineregions_mrgid"].notna(), None).tolist()),
                "marineregions_geoname": col_dict(st["marineregions_geoname"].astype(object).where(st["marineregions_geoname"].notna(), None).tolist()),
                "marineregions_pol_type": col_dict(st["marineregions_pol_type"].astype(object).where(st["marineregions_pol_type"].notna(), None).tolist()),
                "marineregions_share": col_num(st["marineregions_share"].astype(object).where(st["marineregions_share"].notna(), None).tolist(), 100),
                "marineregions_n": col_num(st["marineregions_n"].astype(object).where(st["marineregions_n"].notna(), None).tolist(), 1)}
    # expected activity: counts per target for every cell, D5.4 rows for a few
    ea = pd.read_parquet(DATA_DIR / "expected_activity.parquet",
                         columns=["target", "unit_id", "night", "time_start_utc", "time_end_utc", "tested", "observed", "expected", "z",
                                  "q_bh", "flag", "flag_robust", "calm", "exposure_km2", "cell_id"])
    ea = ea[ea["tested"] & ea["cell_id"].isin(set(st["cell_id"]))]
    ea_meta = json.load(open(DATA_DIR / "expected_activity.json"))
    targets = {}
    for tgt in ("viirs", "radar"):
        g = ea[ea["target"] == tgt].groupby("cell_id")
        tested = g.size().reindex(st["cell_id"])
        flagged = g["flag"].apply(lambda x: int((x.astype(str) != "none").sum())).reindex(st["cell_id"])
        robust = g["flag_robust"].apply(lambda x: int((x.astype(str) != "none").sum())).reindex(st["cell_id"])
        targets[tgt] = {"n_tested": col_num(tested.astype(object).where(tested.notna(), None).tolist(), 1),
                        "n_flag": col_num(flagged.astype(object).where(flagged.notna(), None).tolist(), 1),
                        "n_flag_robust": col_num(robust.astype(object).where(robust.notna(), None).tolist(), 1)}
    records = {}
    with_rows = [c for c in ea_first if c in set(ea["cell_id"])][:EA_ROW_CELLS]
    for cid in with_rows:
        sub = ea[ea["cell_id"] == cid].sort_values("time_start_utc", ascending=False)
        rows_by_t = {}
        for tgt, gg in sub.groupby("target"):
            rows_by_t[tgt] = [{"unit_id": str(r.unit_id), "night": str(r.night), "time_start_utc": str(r.time_start_utc),
                               "time_end_utc": str(r.time_end_utc), "tested": True,
                               "observed": None if pd.isna(r.observed) else float(r.observed),
                               "expected": None if pd.isna(r.expected) else round(float(r.expected), 3),
                               "z": None if pd.isna(r.z) else round(float(r.z), 2),
                               "q_bh": None if pd.isna(r.q_bh) else float(f"{float(r.q_bh):.3g}"),
                               "flag": str(r.flag), "flag_robust": str(r.flag_robust), "calm": bool(r.calm),
                               "exposure_km2": None if pd.isna(r.exposure_km2) else round(float(r.exposure_km2), 1)} for r in gg.itertuples()]
        records[cid] = {"expected_activity": {"model_id": ea_meta.get("model_id"), "caveat": ea_meta.get("caveat"), "targets": rows_by_t}}
    return {"type": "cells", "n": n, "columns": cols, "records": records, "_order": st["cell_id"].tolist(),
            "prov": {**{c: "gebco_2026" for c in CELL_STATIC_COLS if c.startswith(("depth", "share_shallower", "slope"))},
                     "dist_coast_km": "natural_earth", "dist_coast_min_km": "natural_earth", "dist_port_km": "wpi", "dist_port_min_km": "wpi",
                     **{c: "worldbank_density" for c in CELL_STATIC_COLS if c.startswith("ship_presence")},
                     "sea_share": "natural_earth", "sea_area_km2": "natural_earth", "nightly": "mur_sst", "ais_reach_share": "aisstream",
                     "ais_reach_mmsi": "aisstream", "look_prob_1d": "s1_grd", "look_prob_7d": "s1_grd", "look_prob_30d": "s1_grd",
                     "passes_90d": "s1_grd", "eez": "marineregions_v12", "expected_activity": "expected_activity"},
            "nightly": {"night": str(night), "n": n, "columns": nightly_cols, "valid": valid,
                        "note": "fields of the newest night; valid times and sources that hold for every cell are in `valid`"},
            "eez_attrs": {"heading": "As published by Marine Regions",
                          "statement": ("Lines and polygons as published by Marine Regions (Flanders Marine Institute, VLIZ), World EEZ v12, "
                                        "CC BY 4.0, doi:10.14284/632. In this sea many zones overlap or are disputed; the source marks them. "
                                        "This product takes no position on any boundary or claim."),
                          "n": n, "columns": eez_cols, "src": "marineregions_v12",
                          "note": "As published by Marine Regions; shown only while the EEZ layer is on; never a filter, factor or feature"},
            "expected_activity": {"n": n, "model_id": ea_meta.get("model_id"), "caveat": ea_meta.get("caveat"), "form": "counts per target; rows in records",
                                  "targets": targets, "note": "tested, flagged and robust-flagged nights per cell and target; D5.4 rows in records[cell_id] for a few cells"},
            "note": "a subset of the 5,116 cells: those of the fixture's objects and leads; look probability in percent; AIS reach and look probability sampled at the cell centre"}


RASTER_FIXTURE = [  # name, colormap, decimation of the source grid
    ("depth_m", "Blues", 10), ("sst_mean_c", "inferno", 10), ("chl_mean_mg_m3", "viridis", 2), ("front_freq", "magma", 10),
    ("wave_hs_mean_m", "plasma", 1), ("ship_density_all", "Greys", 10), ("dist_port_km", "cividis", 10),
]
RASTER_SRC = {"depth": "gebco_2026", "sst": "mur_sst", "front": "mur_sst", "chl": "chl_dineof", "wave": "gfs_wave",
              "ship_density": "worldbank_density", "dist_port": "wpi"}


def rasters_part() -> dict:
    """`rasters` part (README reading 14): `{layers: [{name, unit, valid_period, colormap, vmin, vmax, bounds, src, licence,
    default_on false, note, research_only, resolution_deg, width, height, image}]}`; WebP at about 0.1 degree, 2nd to 98th
    percentile stretch; shipping density as a presence mask (value above 0) with no magnitude (board D4.3)."""
    import io
    import matplotlib
    import rasterio
    from PIL import Image

    layers = []
    for name, cmap, dec in RASTER_FIXTURE:
        with rasterio.open(DATA_DIR / "outputs/small" / f"{name}_4326.tif") as ds:
            a = ds.read(1, masked=True).astype("f4").filled(np.nan)
            tags = ds.tags()
            b = ds.bounds
            res = ds.res[0]
        if dec > 1:
            h, w = (a.shape[0] // dec) * dec, (a.shape[1] // dec) * dec
            with np.errstate(all="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                a = np.nanmean(a[:h, :w].reshape(h // dec, dec, w // dec, dec), axis=(1, 3))
            top, left = b.top, b.left
            bottom, right = top - h * res, left + w * res
        else:
            top, left, bottom, right = b.top, b.left, b.bottom, b.right
        ok = np.isfinite(a)
        presence = name.startswith("ship_density")
        if presence:
            z = np.where(ok & (a > 0), 1.0, 0.0)
            ok = ok & (a > 0)
            vmin, vmax, unit = 0.0, 1.0, "presence (1 = published value above 0)"
        else:
            v = a[ok]
            vmin, vmax = float(np.percentile(v, 2)), float(np.percentile(v, 98))
            z = np.clip((np.nan_to_num(a, nan=vmin) - vmin) / ((vmax - vmin) or 1.0), 0, 1)
            unit = tags.get("units")
        rgba = (matplotlib.colormaps[cmap](z) * 255).astype(np.uint8)
        rgba[..., 3] = np.where(ok, 210, 0).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buf, "WEBP", quality=70, method=6)
        key = next(v for k, v in RASTER_SRC.items() if name.startswith(k))
        layers.append({"name": name, "unit": unit, "valid_period": tags.get("window"), "colormap": cmap, "vmin": vmin, "vmax": vmax,
                       "bounds": [round(left, 4), round(bottom, 4), round(right, 4), round(top, 4)], "src": key,
                       "licence": (tags.get("licence") or "")[:200] or None, "default_on": False,
                       "note": "presence only: published value above 0; never a count, rank or lane" if presence else None,
                       "research_only": False, "resolution_deg": round(res * dec, 4), "width": int(a.shape[1]), "height": int(a.shape[0]),
                       "image": "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")})
    return {"type": "rasters", "n": len(layers), "layers": layers,
            "note": "context overlays for the fixture at reduced resolution; off by default"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path(__file__).with_name("bundle_small.json"))
    ap.add_argument("--live-other", dest="live_other", type=int, default=60, help="contacts of each live pass other than the Pearl River pass")
    ap.add_argument("--ambiguous", type=int, default=12)
    ap.add_argument("--no-coverage", dest="no_coverage", type=int, default=30, help="no_coverage contacts of the Pearl River pass")
    ap.add_argument("--ais-only", dest="ais_only", type=int, default=100)
    ap.add_argument("--l7", type=int, default=8)
    ap.add_argument("--regional", type=int, default=300)
    ap.add_argument("--structures", type=int, default=100)
    ap.add_argument("--lights", type=int, default=300)
    ap.add_argument("--vessels", type=int, default=400)
    ap.add_argument("--tracks", type=int, default=150)
    ap.add_argument("--leads", type=int, default=40, help="real L1 leads of the Pearl River pass, highest priority first")
    args = ap.parse_args()
    parts = build(args)
    out = {"format": "scs-bundle-parts", "contract_version": CONTRACT_VERSION, "parts": parts}
    args.out.write_text(json.dumps(out, separators=(",", ":"), default=to_py))
    size = args.out.stat().st_size
    print(f"wrote {args.out} ({size / 1e6:.2f} MB)")
    for k, v in sorted(parts["meta"]["parts"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:10s} {v / 1e6:.3f} MB")
    print("  counts", parts["meta"]["counts"])
    if size > 1_100_000:
        print("FIXTURE OVER 1.1 MB", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
