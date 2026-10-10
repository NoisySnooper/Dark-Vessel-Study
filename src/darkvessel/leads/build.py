"""Assembly and outputs of the leads queue: inputs per build, L1 and L7 leads with evidence and priority, GeoPackages,
research parquet twin, summary JSON and figure.

File layout (board D6.3: every committed leads file stays under 20 MB). Open build: data/leads_open.gpkg holds
leads_4326 (every contract 3.5 column; the app backend reads it) and about; data/leads_open_detail.gpkg holds
leads_utm49n (EPSG:32649), lead_evidence and the same about row. Research build (local, never committed): one file,
data/research/leads_research.gpkg, with all four layers; the committed research copy is the parquet twin.

Reruns with unchanged inputs write byte-identical tables: rows are sorted, JSON keys are fixed, and the GeoPackage
timestamp (gpkg_contents.last_change, GDAL option OGR_CURRENT_DATE) is the about layer's generated_utc, which changes
only when an input's modification time or size, a rule or weight constant, the model id or a run option changes
(inputs_signature; the previous about layer is read first).

Every file is written to a temporary name in the same directory and moved into place with os.replace, so a reader
(the app backend) never sees a missing or half-written file. In the GeoPackages the constant columns `caveat` (every
leads and lead_evidence row) and `research_only` (lead_evidence) are stored as SQLite column defaults: the columns are
added with ALTER TABLE ... ADD COLUMN ... DEFAULT after the rows are written, so every row reads the full value in any
SQLite reader while the file holds the text once per table (https://www.sqlite.org/fileformat2.html: "Missing values
at the end of the record are filled in using the default value for the corresponding columns defined in the table
schema."). The research parquet stores the caveat on every row (dictionary-encoded).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

from darkvessel.config import CRS_GEO, CRS_UTM_REGIONAL, DATA_DIR, DOCS_DIR
from darkvessel.leads import BUILDS, PRIORITY_MODEL_ID, PRODUCT_CAVEAT, RESEARCH_LINE, caveat_for
from darkvessel.leads import evidence as E
from darkvessel.leads import priority as P
from darkvessel.leads import rules as R
from darkvessel.leads.guard import RESEARCH_DIR, checked_path, layers_of, read_json, read_layer, read_parquet
from darkvessel.ocean.grid import SMALL_DIR, sample

LIVE_DIR = DATA_DIR / "live"
FIG_PATH = DOCS_DIR / "figures" / "leads_priority.png"

OUTPUTS = {
    "open": {"gpkg": DATA_DIR / "leads_open.gpkg", "detail": DATA_DIR / "leads_open_detail.gpkg",
             "summary": DATA_DIR / "leads_open_summary.json", "parquet": None},
    "research": {"gpkg": RESEARCH_DIR / "leads_research.gpkg", "detail": None, "summary": RESEARCH_DIR / "leads_research_summary.json",
                 "parquet": RESEARCH_DIR / "leads_research.parquet"},
}
COMMIT_LIMIT_MB = 20.0   # project rule for committed files (board D6.3 for the leads files)
SIZE_RULE = ("Every committed leads file stays under 20 MB (board D6.3). The open build always writes two files: "
             "data/leads_open.gpkg with leads_4326 (every contract 3.5 column, read by the app backend) and about, and "
             "data/leads_open_detail.gpkg with leads_utm49n (EPSG:32649), lead_evidence and about, so both CRS are kept "
             "(project rule 4). The research GeoPackage is local only and never committed (board D6.5); its committed copy is the parquet.")

JSON_COLUMNS = ["factors", "evidence", "lawful_explanations", "change_indicators", "history", "prov", "nights"]
PTS = [P.POINTS_COLUMNS[f] for f in P.FACTORS]
LEAD_COLUMNS = (
    ["lead_id", "lead_type", "title", "state", "reason", "priority", "priority_band"] + PTS
    + ["factors", "priority_model_id", "calibrated", "primary_type", "primary_id", "evidence", "n_evidence", "lon", "lat",
       "time_utc", "region_box", "next_look_utc", "next_look_pass", "next_look_source", "lawful_explanations",
       "change_indicators", "history", "research_only", "caveat", "src", "prov",
       # L1 detail
       "det_id", "run_id", "pass_id", "mission", "acq_utc", "confidence", "cnn_score", "length_est_m", "ais_status",
       "ais_source", "channels", "weather_known", "weather_missing", "wind_ms", "deep_convection", "nearest_ais_key", "nearest_ais_dist_m",
       "nearest_ais_dt_s", "n_ais_10km", "ais_reach", "n_lights_2km_3h", "n_ais_events_2km_3h", "n_persist_72h",
       # location context (both types)
       "cell_id", "depth_mean_m", "dist_coast_km", "dist_port_km",
       # L7 detail
       "n_lights", "n_nights", "nights", "first_light_utc", "last_light_utc", "n_lights_at_sites", "radiance_med_nw",
       "share_never_imaged", "ais_reach_share"]
)
RESEARCH_COLUMNS = ["identity_kind", "gfw_neural_type", "n_gfw_gaps_50km_24h", "nearest_gfw_gap_km",
                    "n_gfw_encounters_10km_24h", "n_gfw_loitering_10km_24h"]
INT_COLUMNS = ["priority", *PTS, "n_evidence", "n_ais_10km", "n_lights_2km_3h", "n_ais_events_2km_3h", "n_persist_72h",
               "n_lights", "n_nights", "n_lights_at_sites", "n_gfw_gaps_50km_24h", "n_gfw_encounters_10km_24h",
               "n_gfw_loitering_10km_24h"]
BOOL_COLUMNS = ["calibrated", "research_only", "weather_known"]
FLOAT_COLUMNS = ["lon", "lat", "cnn_score", "length_est_m", "wind_ms", "nearest_ais_dist_m", "nearest_ais_dt_s", "ais_reach",
                 "depth_mean_m", "dist_coast_km", "dist_port_km", "radiance_med_nw", "share_never_imaged", "ais_reach_share",
                 "nearest_gfw_gap_km"]

COLUMN_DOC = {
    "lead_id": "<type>-<primary object id>, stable across reruns", "lead_type": "L1 or L7 (spec 4.1)",
    "title": "plain words, no verdict", "state": "new (decisions come from the app's decision log)",
    "reason": "picklist value of the latest closing decision; null while new",
    "priority": "review priority 0 to 100, not a risk score; the clipped sum of the five factor point columns",
    "priority_band": "low 0 to 33, medium 34 to 66, high 67 to 100",
    "pts_evidence_quality": "0 to 30", "pts_corroboration": "0 to 25", "pts_ais_reach": "0 to 20", "pts_persistence": "0 to 15",
    "pts_area_weight": "0 to 10, analyst-set per reporting box, default 0",
    "factors": "JSON list of {factor, value, points, max_points, source}; L1 adds {factor: 'weather unknown', points 0} where weather is unknown",
    "priority_model_id": "weights version", "calibrated": "false until calibrated against owner labels",
    "primary_type": "contact (L1) or cell (L7)", "primary_id": "det_id (L1) or cell_id (L7)",
    "evidence": "JSON list of {type, id, role}; the same rows are in the lead_evidence layer", "n_evidence": "length of evidence",
    "lon, lat": "L1: the contact; L7: the mean position of the cell's qualifying lights (EPSG:4326)",
    "time_utc": "L1: scene start; L7: time of the last qualifying light", "region_box": "reporting box or other; not a boundary",
    "next_look_utc": "first planned Sentinel-1 pass after time_utc whose footprint contains the point (data/s1_next_passes.json window); null when no planned pass covers it",
    "next_look_pass": "pass_group of that pass", "next_look_source": "esa_plan or repeat_cycle (a repeat prediction is not ESA's plan)",
    "lawful_explanations": "JSON list of explanation codes, pre-listed per type; the sentence of each code is in the about layer (lawful_explanations) and docs/leads.md",
    "change_indicators": "JSON list of indicator codes, pre-listed per type; sentences in the about layer (change_indicators)",
    "history": "JSON list of decisions, empty here", "research_only": "true in the research build", "caveat": "PRODUCT_CAVEAT (plus the research line)",
    "src": "default source key of the record (app)", "prov": "JSON map field -> source key for fields whose source differs from src",
    "channels": "L1: pol_class or the class reading of it", "weather_known": "L1: wind and deep convection both known for this contact (the 5 weather points need both)",
    "weather_missing": "L1: which weather part is missing (wind, deep convection, both); null when both are known",
    "deep_convection": "L1: 1 yes, 0 no, null unknown (Himawari-9 cloud-top temperature below 220 K)",
    "n_lights_2km_3h": "L1: VIIRS lit-vessel candidates within 2 km and 3 h",
    "n_persist_72h": "L1: unmatched high or medium contacts, not match_ambiguous, within 2 km on another pass within 72 h",
    "cell_id": "0.25 degree model-grid cell r<row>c<col>",
    "depth_mean_m, dist_coast_km, dist_port_km": ("location context of the cell (GEBCO, Natural Earth coast, WPI ports): 0.25 degree "
                                                  "cell means, not values at the contact, so a near-shore contact can lie much closer "
                                                  "to the coast than its cell mean"),
    "n_lights": "L7: clear-sky lit-vessel candidates never imaged in 90 d, more than 1 km from Satlas infrastructure (all of them; the evidence lists the 20 brightest)", "n_nights": "L7: nights with such lights",
    "nights": "L7: JSON list of those nights (local evening dates)", "n_lights_at_sites": "L7: of those lights, how many lie within 500 m of a recurring light site",
    "radiance_med_nw": "L7: median radiance of the lights, nW cm-2 sr-1", "share_never_imaged": "L7: share of the cell's AOI sea with 0 Sentinel-1 passes in the 90-day window (context, not a factor)",
    "ais_reach_share": "L7: share of recorded hours with AIS heard in the cell (aisstream; context, not a factor)",
}
COLUMN_DOC_BY_BUILD = {
    "open": {
        "nearest_ais_key": "L1: mmsi:<mmsi> of the nearest AIS vessel; evidence, not a match",
        "n_ais_events_2km_3h": "L1: AIS behaviour events within 2 km and 3 h (no event source in the open build yet)",
    },
    "research": {
        "nearest_ais_key": "L1: gfw:<vessel_id> of the nearest AIS vessel (GFW identity); evidence, not a match",
        "n_ais_events_2km_3h": "L1: GFW loitering and encounter events within 2 km and 3 h",
        "identity_kind, gfw_neural_type": "L1: GFW identity kind and neural vessel type of the nearest AIS vessel, as data/research/regional_identity.parquet holds them",
        "n_gfw_gaps_50km_24h, nearest_gfw_gap_km, n_gfw_encounters_10km_24h, n_gfw_loitering_10km_24h": "L1: GFW event counts and distance near the contact, from data/research/regional_identity.parquet",
    },
}
CORROBORATION_RULE = {
    "open": "a VIIRS lit-vessel candidate within 2 km and 3 h of the contact (15 points); AIS behaviour events (10 points) have no source in the open build yet",
    "research": "a VIIRS lit-vessel candidate within 2 km and 3 h of the contact (15 points), or a GFW loitering or encounter event within 2 km whose span, padded by 3 h, contains the contact time (10 points)",
}
CONST_GPKG_COLUMNS = {"leads": ("caveat",), "lead_evidence": ("caveat", "research_only")}


def column_doc(build: str) -> dict:
    """COLUMN_DOC plus the build's own entries; the open build's text names no research-only source."""
    return {**COLUMN_DOC, **COLUMN_DOC_BY_BUILD[build]}


# ----------------------------------------------------------------------------------------------------------------------
# inputs

def input_paths(build: str) -> dict[str, list[Path]]:
    """Every file a build may read, by role (lists because some products come in parts). Missing files are kept in the
    dict so the about layer can say they were absent."""
    paths = {
        "viirs_lights_all": [DATA_DIR / "viirs_lights_all.gpkg"],
        "viirs_lights": [DATA_DIR / "viirs_lights.gpkg"],
        "viirs_summary": [DATA_DIR / "viirs_summary.json"],
        "s1_passes_tif": [SMALL_DIR / "s1_passes_4326.tif"],
        "s1_coverage": [DATA_DIR / "s1_coverage.json"],
        "s1_next_passes": [DATA_DIR / "s1_next_passes.json"],
        "s1_footprints_plan": [DATA_DIR / "ais_live.gpkg"],
        "ais_reach_tif": [SMALL_DIR / "ais_reach_share_4326.tif"],
        "weather": [DATA_DIR / "weather_context.parquet"],
        "ocean_static": [DATA_DIR / "ocean_static_cells.parquet"],
    }
    if build == "open":  # live contacts, and GFS wind and Himawari cloud tops per live contact (darkvessel.live.weather sidecars)
        paths["live_contacts"] = [LIVE_DIR / "live_contacts.gpkg"]   # only the open build reads it, so only its signature
        paths["live_weather"] = sorted(LIVE_DIR.glob("live_*_weather.parquet"))   # moves when a live pass is processed
    if build == "research":
        paths["regional_identity"] = [RESEARCH_DIR / "regional_identity.parquet"]
        paths["gfw_events_loitering"] = sorted(RESEARCH_DIR.glob("gfw_events_loitering*.parquet"))
        paths["gfw_events_encounters"] = sorted(RESEARCH_DIR.glob("gfw_events_encounters*.parquet"))
    for k, lst in paths.items():
        for p in lst:
            checked_path(p, build)
    return paths


def model_fingerprint() -> dict:
    """The rule and weight constants that shape every row (every upper-case constant of rules.py and priority.py, the
    model id, the caveat texts). Part of inputs_signature, so a code change that changes the rows also moves
    generated_utc; a rerun with unchanged code and inputs keeps it."""
    consts = {m.__name__.rsplit(".", 1)[-1]: {k: v for k, v in vars(m).items() if k.isupper()} for m in (R, P)}
    return {"priority_model_id": PRIORITY_MODEL_ID, "caveats": {b: caveat_for(b) for b in BUILDS}, "constants": consts}


def inputs_manifest(paths: dict[str, list[Path]], run: dict | None = None) -> tuple[list[dict], str]:
    """Input files (role, path, size, mtime) and a 16-hex signature over them, the model fingerprint and the run
    options (`run`: since, until, area weights)."""
    rows = []
    for role, lst in sorted(paths.items()):
        for p in lst:
            if p.exists():
                st = p.stat()
                rows.append({"role": role, "path": p.relative_to(DATA_DIR.parent).as_posix(), "size": st.st_size,
                             "mtime_utc": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            else:
                rows.append({"role": role, "path": p.relative_to(DATA_DIR.parent).as_posix() if p.is_absolute() else str(p),
                             "size": None, "mtime_utc": None, "missing": True})
    blob = {"inputs": rows, "model": model_fingerprint(), "run": run or {}}
    sig = hashlib.sha1(json.dumps(blob, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return rows, sig


def _exists(paths, role) -> bool:
    return bool(paths.get(role)) and all(p.exists() for p in paths[role])


def load_contacts(build: str, paths: dict) -> tuple[pd.DataFrame, str]:
    """Contacts of the build with a pass_id column. Open: the combined live file (per-pass files as fallback).
    Research: the September identity table. Returns (frame, note)."""
    if build == "open":
        files = [LIVE_DIR / "live_contacts.gpkg"]
        if not files[0].exists():
            files = sorted(LIVE_DIR.glob("live_S1*.gpkg"))
        frames = []
        for f in files:
            if "contacts_4326" in layers_of(f, build):
                frames.append(read_layer(f, "contacts_4326", build))
        if not frames:
            return pd.DataFrame(columns=["det_id", "run_id", "lon", "lat", "acq_utc", "ais_status", "confidence", "cnn_score"]), "no live contacts file yet"
        df = pd.concat(frames, ignore_index=True).drop_duplicates("det_id")
        df["pass_id"] = df["run_id"].astype(str)
        return df, f"{len(df)} live contacts from {', '.join(f.name for f in files)}"
    if not _exists(paths, "regional_identity"):
        return pd.DataFrame(columns=["det_id", "run_id", "lon", "lat", "acq_utc", "ais_status", "confidence", "cnn_score"]), "regional_identity.parquet missing"
    df = read_parquet(paths["regional_identity"][0], build)
    if "pass_id" not in df.columns:
        df["pass_id"] = df["run_id"].astype(str)
    return df, f"{len(df)} September contacts from regional_identity.parquet"


WEATHER_JOIN = ["det_id", "wind_ms", "ctt_k", "deep_convection"]
WEATHER_LIVE_TEXT = ("Live contacts: GFS 10 m wind of the hour nearest the scene and Himawari-9 cloud-top temperature (deep convection "
                     "below 220 K) from data/live/<run_id>_weather.parquet (darkvessel.live.weather, the method of "
                     "scripts/16_weather_context.py); a part whose source could not be read stays unknown, never calm.")


def load_weather(build: str, paths: dict) -> tuple[pd.DataFrame | None, str]:
    """Weather per contact for the L1 gate: the regional sample (data/weather_context.parquet) and, in the open build,
    the live-pass sidecars (data/live/live_*_weather.parquet). Null wind or deep_convection means unknown."""
    frames, parts = [], []
    if _exists(paths, "weather"):
        frames.append(read_parquet(paths["weather"][0], build, columns=WEATHER_JOIN))
        parts.append(f"regional {len(frames[-1])}")
    for p in paths.get("live_weather", []):
        if p.exists():
            w = read_parquet(p, build, columns=WEATHER_JOIN)
            w["deep_convection"] = w["deep_convection"].astype(object).where(w["deep_convection"].notna(), None)
            frames.append(w)
            parts.append(f"{p.stem} {len(w)} (wind known {int(w.wind_ms.notna().sum())}, convection known {int(w.deep_convection.notna().sum())})")
    if not frames:
        return None, "no weather file"
    w = pd.concat([f.astype({"deep_convection": object}) for f in frames], ignore_index=True).drop_duplicates("det_id", keep="last")
    return w, "; ".join(parts)


def load_events(build: str, paths: dict) -> pd.DataFrame | None:
    """AIS behaviour events for L1 corroboration: research GFW loitering and encounters (event_id, lon, lat, start, end);
    the open build has no event source yet."""
    if build != "research":
        return None
    import pyarrow.parquet as pq

    want = ["event_id", "type", "lon", "lat", "start", "end"]
    frames = []
    for role in ("gfw_events_loitering", "gfw_events_encounters"):
        for p in paths.get(role, []):
            if p.exists():
                have = set(pq.read_schema(checked_path(p, build)).names)
                if {"event_id", "lon", "lat", "start", "end"} <= have:
                    frames.append(read_parquet(p, build, columns=[c for c in want if c in have]))
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True).dropna(subset=["lon", "lat", "start", "end"])


LIGHT_COLUMNS = ["light_id", "time_utc", "night", "lat", "lon", "radiance_nw", "quality", "class", "satlas_infra_m", "s1_passes_90d"]


def load_lights(build: str, paths: dict) -> tuple[pd.DataFrame | None, pd.DataFrame | None, str]:
    """VIIRS lit-vessel candidates (class lit_vessel_candidate) and recurring light sites. Lights come from
    data/viirs_lights_all.gpkg (every night) when it exists, else from the lean data/viirs_lights.gpkg (a subset of
    nights); sites always come from the lean file, which alone holds them. Returns (lights, sites, note)."""
    lights, sites, note = None, None, "no VIIRS lights file"
    for role in ("viirs_lights_all", "viirs_lights"):
        if _exists(paths, role) and "viirs_lights_4326" in layers_of(paths[role][0], build):
            f = paths[role][0]
            import pyogrio

            have = set(pyogrio.read_info(checked_path(f, build), layer="viirs_lights_4326")["fields"])
            lights = read_layer(f, "viirs_lights_4326", build, columns=[c for c in LIGHT_COLUMNS if c in have])
            n_all = len(lights)
            if "class" in lights.columns:
                lights = lights[lights["class"].astype(str) == R.L7_CLASS].reset_index(drop=True)
            nights = lights["night"].astype(str) if "night" in lights.columns else pd.Series([], dtype=str)
            note = (f"{len(lights)} lit-vessel candidates of {n_all} lights from {f.name}, {nights.nunique()} nights "
                    f"{nights.min() if len(nights) else ''} to {nights.max() if len(nights) else ''}")
            break
    if _exists(paths, "viirs_lights") and "viirs_sites_4326" in layers_of(paths["viirs_lights"][0], build):
        sites = read_layer(paths["viirs_lights"][0], "viirs_sites_4326", build)
    return lights, sites, note


def load_passes(build: str, paths: dict):
    plan = read_json(paths["s1_next_passes"][0], build) if _exists(paths, "s1_next_passes") else None
    fps = None
    if _exists(paths, "s1_footprints_plan"):
        f = paths["s1_footprints_plan"][0]
        if "s1_next_passes_4326" in layers_of(f, build):
            fps = read_layer(f, "s1_next_passes_4326", build, read_geometry=True)
    return E.passes_frame(plan, fps), plan


def load_raster(path: Path | None, build: str):
    import rasterio

    if path is None or not path.exists():
        return None, None, None
    with rasterio.open(checked_path(path, build)) as ds:
        arr = ds.read(1).astype(float)
        nodata = ds.nodata
        return arr, ds.transform, nodata


def _filter_window(df: pd.DataFrame, col: str, since, until) -> pd.DataFrame:
    if df is None or len(df) == 0 or (since is None and until is None):
        return df
    t = E.utc(df[col])
    keep = pd.Series(True, index=df.index)
    if since is not None:
        keep &= t >= pd.Timestamp(since, tz="UTC")
    if until is not None:
        keep &= t <= pd.Timestamp(until, tz="UTC")
    return df[keep].copy()


# ----------------------------------------------------------------------------------------------------------------------
# L1

def build_l1(contacts: pd.DataFrame, build: str, weather, static, lights, events, passes, area_weights=None,
             plan_time=None) -> tuple[pd.DataFrame, list[dict], dict]:
    """L1 leads from `contacts` (D1 columns plus extras). `plan_time` is the pass plan's generated_utc (next looks
    start after it). Returns (leads, evidence rows, counts)."""
    counts: dict = {"contacts": int(len(contacts))}
    if len(contacts) == 0:
        return pd.DataFrame(columns=LEAD_COLUMNS), [], {**counts, "gate": {}}
    c = E.join_weather(contacts, weather)
    gate = R.l1_gate(c)
    counts["gate"] = R.l1_gate_counts(gate)
    cand = c[gate.l1].copy()
    cand["weather_known"] = gate.loc[gate.l1, "weather_known"].to_numpy()
    cand["channels"] = gate.loc[gate.l1, "channels"].to_numpy()
    cand["weather_missing"] = gate.loc[gate.l1, "weather_missing"].replace("", None).to_numpy()
    cand["both_channels"] = R.both_channels(cand).to_numpy()
    if len(cand) == 0:
        return pd.DataFrame(columns=LEAD_COLUMNS), [], counts
    cand = cand.sort_values("det_id").reset_index(drop=True)
    cand["cell_id"], cand["row"], cand["col"] = E.cell_ids(cand.lon, cand.lat)
    cand["region_box"] = E.regions(cand.lon, cand.lat)
    cand = E.join_static(cand, static)
    light_hits = E.lights_near(cand, lights)
    event_hits = E.events_near(cand, events)
    # Persistence pool: unmatched high or medium contacts that are not ambiguous (an ambiguous contact on another pass is
    # very likely an AIS vessel, so it must not lend a lead the persistence points).
    pool = c[(gate.unmatched & gate.not_ambiguous & gate.vessel_class).to_numpy(bool)]
    persist = E.persistence_pairs(cand, pool)
    cand["n_lights_2km_3h"] = [len(h) for h in light_hits]
    cand["n_ais_events_2km_3h"] = [len(h) for h in event_hits]
    cand["n_persist_72h"] = [len(h) for h in persist]
    if "ais_source" not in cand.columns:
        cand["ais_source"] = "gfw" if build == "research" else "aisstream"
    cand["ais_source"] = cand["ais_source"].fillna("gfw" if build == "research" else "aisstream")
    pts, factors = P.l1_points(cand, area_weights, build=build)
    cand = pd.concat([cand, pts], axis=1)
    cand["priority"] = P.total(cand)
    cand["priority_band"] = P.band(cand.priority)
    when, grp, src = E.next_look(cand.lon, cand.lat, cand.acq_utc, passes, not_before=plan_time)
    keys = [E.nearest_ais_key(r) for _, r in cand.iterrows()]
    cand["nearest_ais_key"] = keys
    ev_lists = [E.l1_evidence(r, light_hits[i], event_hits[i], persist[i]) for i, (_, r) in enumerate(cand.iterrows())]
    det_src = "det_live" if build == "open" else "det_regional"
    ais_src = "gfw_4wings" if build == "research" else "aisstream"
    prov = {"det_id": det_src, "cnn_score": "cnn_v0", "wind_ms": "gfs_wind", "deep_convection": "himawari_ctt",
            "ais_reach": ais_src, "nearest_ais_key": ais_src, "n_ais_10km": ais_src, "n_lights_2km_3h": "viirs_dnb",
            "next_look_utc": "esa_acq_plan", "depth_mean_m": "gebco_2026", "dist_coast_km": "natural_earth",
            "dist_port_km": "wpi", "priority": "app"}
    if build == "research":
        prov.update({"n_ais_events_2km_3h": "gfw_events", "n_gfw_gaps_50km_24h": "gfw_events",
                     "n_gfw_encounters_10km_24h": "gfw_events", "n_gfw_loitering_10km_24h": "gfw_events",
                     "identity_kind": "gfw_vessels", "gfw_neural_type": "gfw_4wings"})
    prov_s = json.dumps(prov, separators=(",", ":"))
    lawful = json.dumps(list(R.LAWFUL_EXPLANATIONS["L1"]))
    change = json.dumps(list(R.CHANGE_INDICATORS["L1"]))
    out = pd.DataFrame({
        "lead_id": "L1-" + cand.det_id.astype(str), "lead_type": "L1",
        "title": [E.l1_title(L, r) for L, r in zip(cand.length_est_m, cand.region_box)],
        "state": "new", "reason": None, "priority": cand.priority.astype(int), "priority_band": cand.priority_band,
        **{p: cand[p].astype(int) for p in PTS},
        "factors": [json.dumps(f, ensure_ascii=False, separators=(",", ":")) for f in factors], "priority_model_id": PRIORITY_MODEL_ID, "calibrated": False,
        "primary_type": "contact", "primary_id": cand.det_id.astype(str),
        "evidence": [json.dumps(e, separators=(",", ":")) for e in ev_lists], "n_evidence": [len(e) for e in ev_lists],
        "lon": cand.lon.astype(float), "lat": cand.lat.astype(float),
        "time_utc": [E.iso_z(t) for t in E.utc(cand.acq_utc)], "region_box": cand.region_box,
        "next_look_utc": when, "next_look_pass": grp, "next_look_source": src,
        "lawful_explanations": lawful, "change_indicators": change, "history": "[]",
        "research_only": build == "research", "caveat": caveat_for(build), "src": "app", "prov": prov_s,
        "det_id": cand.det_id.astype(str), "run_id": cand.run_id.astype(str), "pass_id": cand.pass_id.astype(str),
        "mission": cand.mission.astype(str), "acq_utc": [E.iso_z(t) for t in E.utc(cand.acq_utc)],
        "confidence": cand.confidence.astype(str), "cnn_score": pd.to_numeric(cand.cnn_score, errors="coerce").astype(float),
        "length_est_m": pd.to_numeric(cand.length_est_m, errors="coerce").astype(float), "ais_status": cand.ais_status.astype(str),
        "ais_source": cand.ais_source.astype(str), "channels": cand.channels.astype(str), "weather_known": cand.weather_known.astype(bool),
        "weather_missing": cand.weather_missing,
        "wind_ms": pd.to_numeric(cand.wind_ms, errors="coerce").astype(float),
        "deep_convection": [None if v is None or v != v else int(bool(v)) for v in cand.deep_convection],
        "nearest_ais_key": keys, "nearest_ais_dist_m": pd.to_numeric(cand.get("nearest_ais_dist_m"), errors="coerce"),
        "nearest_ais_dt_s": pd.to_numeric(cand.get("nearest_ais_dt_s"), errors="coerce"),
        "n_ais_10km": pd.to_numeric(cand.get("n_ais_10km"), errors="coerce"), "ais_reach": pd.to_numeric(cand.get("ais_reach"), errors="coerce"),
        "n_lights_2km_3h": cand.n_lights_2km_3h, "n_ais_events_2km_3h": cand.n_ais_events_2km_3h, "n_persist_72h": cand.n_persist_72h,
        "cell_id": cand.cell_id, "depth_mean_m": cand.depth_mean_m, "dist_coast_km": cand.dist_coast_km, "dist_port_km": cand.dist_port_km,
    })
    if build == "research":
        for col in RESEARCH_COLUMNS:
            out[col] = cand[col].to_numpy() if col in cand.columns else None
    # The gate's promise, checked again on the output rows: no matched, no_coverage, fixed, low or ambiguous contact is a lead.
    amb = (cand["match_ambiguous"].astype(object).map(lambda v: R._bool_or_none(v) is True).to_numpy(bool)
           if "match_ambiguous" in cand.columns else np.zeros(len(cand), bool))
    bad = (out.ais_status.to_numpy() != "unmatched") | ~out.confidence.isin(R.L1_CLASSES).to_numpy() | amb
    if bad.any():
        raise AssertionError(f"{int(bad.sum())} L1 rows break the gate (status, class or ambiguity): {out.lead_id[bad].head(5).tolist()}")
    ev_rows = [{"lead_id": lid, **e} for lid, lst in zip(out.lead_id, ev_lists) for e in lst]
    counts.update({"leads": int(len(out)), "weather_unknown": int((~out.weather_known).sum()),
                   "leads_by_pass": {k: int(v) for k, v in out.pass_id.value_counts().sort_index().items()},
                   "leads_by_ais_status": {k: int(v) for k, v in out.ais_status.value_counts().sort_index().items()},
                   "leads_by_confidence": {k: int(v) for k, v in out.confidence.value_counts().sort_index().items()},
                   "leads_by_channels": {k: int(v) for k, v in out.channels.value_counts().sort_index().items()},
                   "leads_ambiguous": int(amb.sum()),
                   "with_light_2km_3h": int((out.n_lights_2km_3h > 0).sum()), "with_ais_event_2km_3h": int((out.n_ais_events_2km_3h > 0).sum()),
                   "with_persistence_72h": int((out.n_persist_72h > 0).sum()), "with_next_look": int(out.next_look_utc.notna().sum()),
                   "under_25_m": int((out.length_est_m < 25).sum())})
    return out, ev_rows, counts


# ----------------------------------------------------------------------------------------------------------------------
# L7

def never_imaged_share(rows, cols, passes_arr, passes_tr, nodata) -> np.ndarray:
    """Share of each 0.25 degree cell's AOI sea (0.05 degree coverage cells with data) that has 0 passes."""
    from rasterio.windows import from_bounds

    out = np.full(len(rows), np.nan)
    if passes_arr is None:
        return out
    mtr, _ = E.model_grid()
    for i, (r, c) in enumerate(zip(rows, cols)):
        w, n = mtr.c + c * mtr.a, mtr.f + r * mtr.e
        e, s = w + mtr.a, n + mtr.e
        win = from_bounds(w, s, e, n, passes_tr)
        r0, r1 = int(round(win.row_off)), int(round(win.row_off + win.height))
        c0, c1 = int(round(win.col_off)), int(round(win.col_off + win.width))
        sub = passes_arr[max(r0, 0):max(r1, 0), max(c0, 0):max(c1, 0)]
        valid = sub[(sub != nodata) & np.isfinite(sub)] if nodata is not None else sub[np.isfinite(sub)]
        if valid.size:
            out[i] = float((valid == 0).mean())
    return out


def build_l7(lights: pd.DataFrame | None, sites: pd.DataFrame | None, build: str, static, passes, passes_raster, reach_raster,
             area_weights=None, plan_time=None) -> tuple[pd.DataFrame, list[dict], dict]:
    """L7 leads: one per 0.25 degree cell with clear-sky lit-vessel candidates never imaged in 90 days. The evidence
    lists the brightest R.L7_EVIDENCE_LIGHTS_MAX lights of a cell; n_lights holds the full count."""
    if lights is None or len(lights) == 0:
        return pd.DataFrame(columns=LEAD_COLUMNS), [], {"lights": 0, "leads": 0}
    sel, counts = R.l7_select(lights)
    if len(sel) == 0:
        return pd.DataFrame(columns=LEAD_COLUMNS), [], {**counts, "leads": 0}
    sel = sel.copy()
    sel["cell_id"], sel["row"], sel["col"] = E.cell_ids(sel.lon, sel.lat)
    sel = sel[sel.cell_id.notna()].copy()
    sel["t"] = E.utc(sel.time_utc)
    sel["at_site"] = False
    site_ids = [[] for _ in range(len(sel))]
    if sites is not None and len(sites):
        from scipy.spatial import cKDTree

        tree = cKDTree(E.to_utm(sites.lon, sites.lat))
        d, j = tree.query(E.to_utm(sel.lon, sel.lat), k=1, distance_upper_bound=500.0)
        ok = np.isfinite(d)
        sel["at_site"] = ok
        sid = sites.site_id.astype(str).to_numpy()
        sel["site_id"] = [sid[k] if o else None for o, k in zip(ok, j)]
    else:
        sel["site_id"] = None
    if "radiance_nw" not in sel.columns:
        sel["radiance_nw"] = np.nan
    sel = sel.sort_values(["cell_id", "t", "light_id"])
    g = sel.groupby("cell_id", sort=True)
    bright = (sel.sort_values(["cell_id", "radiance_nw", "light_id"], ascending=[True, False, True], na_position="last")
              .groupby("cell_id", sort=True).head(R.L7_EVIDENCE_LIGHTS_MAX))
    bright_ids = bright.groupby("cell_id", sort=True)["light_id"].apply(lambda v: [str(x) for x in v])
    cells = pd.DataFrame({
        "cell_id": list(g.groups.keys()),
        "row": g["row"].first().to_numpy(), "col": g["col"].first().to_numpy(),
        "lon": g["lon"].mean().to_numpy(), "lat": g["lat"].mean().to_numpy(),
        "n_lights": g.size().to_numpy(), "n_nights": g["night"].nunique().to_numpy(),
        "nights": [json.dumps(sorted(set(map(str, v)))) for v in g["night"].apply(list)],
        "first_light_utc": [E.iso_z(t) for t in g["t"].min()], "last_light_utc": [E.iso_z(t) for t in g["t"].max()],
        "n_lights_at_sites": g["at_site"].sum().astype(int).to_numpy(),
        "radiance_med_nw": g["radiance_nw"].median().to_numpy(),
        "light_ids": [bright_ids[c] for c in g.groups.keys()],
        "site_ids": [sorted({str(s) for s in v if isinstance(s, str) and s}) for v in g["site_id"].apply(list)],
    })
    cells["region_box"] = E.regions(cells.lon, cells.lat)
    cells = E.join_static(cells, static)
    arr, tr, nd = passes_raster
    cells["share_never_imaged"] = never_imaged_share(cells.row, cells.col, arr, tr, nd)
    rarr, rtr, rnd = reach_raster
    if rarr is not None:
        v = sample(rarr, rtr, cells.lon.to_numpy(), cells.lat.to_numpy())
        v = np.where((v == rnd) | ~np.isfinite(v), np.nan, v) if rnd is not None else v
        cells["ais_reach_share"] = v
    else:
        cells["ais_reach_share"] = np.nan
    pts, factors = P.l7_points(cells, area_weights)
    cells = pd.concat([cells, pts], axis=1)
    cells["priority"] = P.total(cells)
    cells["priority_band"] = P.band(cells.priority)
    when, grp, src = E.next_look(cells.lon, cells.lat, cells.last_light_utc, passes, not_before=plan_time)
    ev_lists = [E.l7_evidence(cid, lids, sids) for cid, lids, sids in zip(cells.cell_id, cells.light_ids, cells.site_ids)]
    prov = {"n_lights": "viirs_dnb", "n_nights": "viirs_dnb", "radiance_med_nw": "viirs_dnb", "share_never_imaged": "s1_grd",
            "ais_reach_share": "aisstream", "next_look_utc": "esa_acq_plan", "depth_mean_m": "gebco_2026",
            "dist_coast_km": "natural_earth", "dist_port_km": "wpi", "priority": "app"}
    out = pd.DataFrame({
        "lead_id": "L7-" + cells.cell_id.astype(str), "lead_type": "L7",
        "title": [E.l7_title(n, k, r) for n, k, r in zip(cells.n_lights, cells.n_nights, cells.region_box)],
        "state": "new", "reason": None, "priority": cells.priority.astype(int), "priority_band": cells.priority_band,
        **{p: cells[p].astype(int) for p in PTS},
        "factors": [json.dumps(f, ensure_ascii=False, separators=(",", ":")) for f in factors], "priority_model_id": PRIORITY_MODEL_ID, "calibrated": False,
        "primary_type": "cell", "primary_id": cells.cell_id.astype(str),
        "evidence": [json.dumps(e, separators=(",", ":")) for e in ev_lists], "n_evidence": [len(e) for e in ev_lists],
        "lon": cells.lon.astype(float), "lat": cells.lat.astype(float), "time_utc": cells.last_light_utc, "region_box": cells.region_box,
        "next_look_utc": when, "next_look_pass": grp, "next_look_source": src,
        "lawful_explanations": json.dumps(list(R.LAWFUL_EXPLANATIONS["L7"])),
        "change_indicators": json.dumps(list(R.CHANGE_INDICATORS["L7"])), "history": "[]",
        "research_only": build == "research", "caveat": caveat_for(build), "src": "app", "prov": json.dumps(prov, separators=(",", ":")),
        "cell_id": cells.cell_id, "depth_mean_m": cells.depth_mean_m, "dist_coast_km": cells.dist_coast_km, "dist_port_km": cells.dist_port_km,
        "n_lights": cells.n_lights.astype(int), "n_nights": cells.n_nights.astype(int), "nights": cells.nights,
        "first_light_utc": cells.first_light_utc, "last_light_utc": cells.last_light_utc, "n_lights_at_sites": cells.n_lights_at_sites.astype(int),
        "radiance_med_nw": cells.radiance_med_nw.astype(float), "share_never_imaged": cells.share_never_imaged.astype(float),
        "ais_reach_share": cells.ais_reach_share.astype(float),
    })
    ev_rows = [{"lead_id": lid, **e} for lid, lst in zip(out.lead_id, ev_lists) for e in lst]
    counts.update({"leads": int(len(out)), "lights_in_leads": int(out.n_lights.sum()), "with_next_look": int(out.next_look_utc.notna().sum()),
                   "cells_with_one_night": int((out.n_nights == 1).sum()),
                   "cells_by_nights": {k: int(v) for k, v in pd.cut(out.n_nights, [0, 1, 2, 3, 6, 10**6], labels=["1", "2", "3", "4-6", "7+"]).value_counts().sort_index().items()},
                   "light_evidence_rows": int(sum(1 for e in ev_rows if e["role"] == "light")),
                   "lights_at_sites": int(out.n_lights_at_sites.sum()),
                   "cells_with_any_ais_reach": int((out.ais_reach_share.fillna(0) > 0).sum()),
                   "max_priority": int(out.priority.max()) if len(out) else None})
    return out, ev_rows, counts


# ----------------------------------------------------------------------------------------------------------------------
# assembly

def finalize(frames: list[pd.DataFrame], build: str) -> pd.DataFrame:
    """Concatenate lead frames into the fixed column order with stable dtypes and a deterministic row order."""
    cols = LEAD_COLUMNS + (RESEARCH_COLUMNS if build == "research" else [])
    frames = [f for f in frames if f is not None and len(f)]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols]
    for c in INT_COLUMNS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    for c in BOOL_COLUMNS:
        df[c] = df[c].map(lambda v: bool(v) if v is not None and v == v else False).astype(bool)
    for c in FLOAT_COLUMNS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    if "deep_convection" in df.columns:
        df["deep_convection"] = pd.to_numeric(df["deep_convection"], errors="coerce").astype("Int16")
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].where(df[c].notna(), None)
    order = {"L1": 0, "L7": 1}
    df = df.assign(_o=df.lead_type.map(order).fillna(9), _p=-df.priority.astype(int)).sort_values(["_o", "_p", "lead_id"]).drop(columns=["_o", "_p"])
    return df.reset_index(drop=True)


def evidence_frame(rows: list[dict], build: str) -> pd.DataFrame:
    """The lead_evidence table: (lead_id, type, id, role) plus the caveat and the build flag on every row, sorted."""
    ev = pd.DataFrame(rows, columns=["lead_id", "type", "id", "role"])
    ev["caveat"] = caveat_for(build)
    ev["research_only"] = build == "research"
    return ev.sort_values(["lead_id", "role", "type", "id"]).reset_index(drop=True)


def corroboration_feasibility(contacts: pd.DataFrame | None, lights: pd.DataFrame | None) -> dict:
    """How often the 3 h corroboration window can hit: radar pass times against VIIRS overpass times, by time of day
    (same clock) and in absolute time (the data windows)."""
    out = {"window_h": R.CORROBORATION_H}
    if contacts is None or lights is None or len(contacts) == 0 or len(lights) == 0:
        return {**out, "note": "no contacts or no lights"}
    ct = E.utc(contacts.acq_utc).dropna()
    lt = E.utc(lights.time_utc).dropna()
    passes = ct.dt.floor("10min").drop_duplicates()
    psec = (passes.dt.hour * 3600 + passes.dt.minute * 60).to_numpy()
    lsec = np.sort((lt.dt.hour * 3600 + lt.dt.minute * 60).drop_duplicates().to_numpy())
    def gap(s):
        d = np.abs(lsec - s)
        return float(np.minimum(d, 86400 - d).min()) / 3600.0
    tod = np.array([gap(s) for s in psec])
    labs = np.sort(E.utc64(lt))
    pabs = E.utc64(passes)
    j = np.searchsorted(labs, pabs)
    absgap = np.full(len(pabs), np.inf)
    for i, k in enumerate(j):
        for kk in (k - 1, k):
            if 0 <= kk < len(labs):
                absgap[i] = min(absgap[i], abs((labs[kk] - pabs[i]) / np.timedelta64(1, "h")))
    out.update({
        "radar_passes": int(len(passes)), "radar_hours_utc": sorted(set(int(h) for h in passes.dt.hour)),
        "viirs_hours_utc": sorted(set(int(h) for h in lt.dt.hour)),
        "passes_within_3h_by_time_of_day": int((tod <= R.CORROBORATION_H).sum()),
        "min_gap_by_time_of_day_h": round(float(tod.min()), 2) if len(tod) else None,
        "passes_within_3h_absolute": int((absgap <= R.CORROBORATION_H).sum()),
        "min_gap_absolute_h": round(float(absgap.min()), 1) if len(absgap) and np.isfinite(absgap.min()) else None,
        "radar_dates": [E.iso_z(ct.min())[:10], E.iso_z(ct.max())[:10]], "viirs_dates": [E.iso_z(lt.min())[:10], E.iso_z(lt.max())[:10]],
    })
    return out


def top_of_queue(leads: pd.DataFrame, sizes=(100, 1000)) -> dict:
    """Lead types among the first n leads of the queue's default sort (priority, high first; ties by lead_id), and the
    first 100 by type and band."""
    if len(leads) == 0:
        return {}
    q = leads.assign(_p=-leads.priority.astype(int)).sort_values(["_p", "lead_id"])
    out = {f"top_{n}": {k: int(v) for k, v in q.head(n).lead_type.value_counts().sort_index().items()} for n in sizes}
    h = q.head(100)
    out["top_100_by_type_band"] = {f"{t}_{b}": int(n) for (t, b), n in h.groupby(["lead_type", "priority_band"]).size().items()}
    return out


def queue_order(leads: pd.DataFrame) -> dict:
    """Owner priority P0, vessel leads first: the lowest L1 and the highest L7 priority, how many L1 leads score at or
    below the highest L7 lead (ties sort L1 first by lead_id) and whether the queue's default sort puts every L1 lead
    before every L7 lead."""
    l1 = leads.priority[leads.lead_type == "L1"].astype(int)
    l7 = leads.priority[leads.lead_type == "L7"].astype(int)
    if len(l1) == 0 or len(l7) == 0:
        return {"l1_min_priority": int(l1.min()) if len(l1) else None, "l7_max_priority": int(l7.max()) if len(l7) else None,
                "l1_below_l7_max": 0, "l1_at_l7_max": 0, "every_l1_before_every_l7": True}
    q = leads.assign(_p=-leads.priority.astype(int)).sort_values(["_p", "lead_id"]).lead_type.to_numpy()
    first_l7 = int(np.argmax(q == "L7"))
    return {"l1_min_priority": int(l1.min()), "l7_max_priority": int(l7.max()),
            "l1_below_l7_max": int((l1 < l7.max()).sum()), "l1_at_l7_max": int((l1 == l7.max()).sum()),
            "every_l1_before_every_l7": bool((q[first_l7:] != "L1").all())}


def factor_distributions(leads: pd.DataFrame) -> dict:
    out = {}
    for t, sub in leads.groupby("lead_type"):
        d = {"n": int(len(sub)), "priority": _quantiles(sub.priority)}
        for f in P.FACTORS:
            d[f] = _quantiles(sub[P.POINTS_COLUMNS[f]])
        out[str(t)] = d
    return out


def _quantiles(s: pd.Series) -> dict:
    v = pd.to_numeric(s, errors="coerce").dropna().astype(float)
    if len(v) == 0:
        return {}
    q = v.quantile([0, 0.25, 0.5, 0.75, 1.0])
    return {"min": float(q.iloc[0]), "q25": float(q.iloc[1]), "median": float(q.iloc[2]), "q75": float(q.iloc[3]),
            "max": float(q.iloc[4]), "mean": round(float(v.mean()), 2)}


def assemble(build: str, since=None, until=None, area_weights: dict | None = None, log=print) -> dict:
    """Build every lead of `build`. Returns a dict with leads, evidence, counts, inputs, notes."""
    if build not in BUILDS:
        raise ValueError(f"unknown build {build!r}")
    t0 = time.time()
    paths = input_paths(build)
    manifest, signature = inputs_manifest(paths, {"since": since, "until": until, "area_weights": area_weights})
    notes = {}
    contacts, notes["contacts"] = load_contacts(build, paths)
    contacts = _filter_window(contacts, "acq_utc", since, until)
    weather, weather_note = load_weather(build, paths)
    if build == "open":
        notes["weather"] = weather_note
    static = read_parquet(paths["ocean_static"][0], build) if _exists(paths, "ocean_static") else None
    lights, sites, notes["viirs"] = load_lights(build, paths)
    lights = _filter_window(lights, "time_utc", since, until) if lights is not None else None
    events = load_events(build, paths)
    passes, plan = load_passes(build, paths)
    plan_time = plan.get("generated_utc") if plan else None
    passes_raster = load_raster(paths["s1_passes_tif"][0], build) if _exists(paths, "s1_passes_tif") else (None, None, None)
    reach_raster = load_raster(paths["ais_reach_tif"][0], build) if _exists(paths, "ais_reach_tif") else (None, None, None)
    log(f"[{build}] inputs read in {time.time() - t0:.1f} s: {notes['contacts']}; lights {0 if lights is None else len(lights)}; "
        f"planned passes {len(passes)}; events {0 if events is None else len(events)}")
    l1, ev1, c1 = build_l1(contacts, build, weather, static, lights, events, passes, area_weights, plan_time=plan_time)
    log(f"[{build}] L1: {c1.get('leads', 0)} leads ({time.time() - t0:.1f} s)")
    l7, ev7, c7 = build_l7(lights, sites, build, static, passes, passes_raster, reach_raster, area_weights, plan_time=plan_time)
    log(f"[{build}] L7: {c7.get('leads', 0)} leads ({time.time() - t0:.1f} s)")
    leads = finalize([l1, l7], build)
    evidence = evidence_frame(ev1 + ev7, build)
    feas = corroboration_feasibility(contacts[contacts.ais_status.astype(str) == "unmatched"] if len(contacts) else None, lights)
    counts = {
        "leads": int(len(leads)), "by_type": {k: int(v) for k, v in leads.lead_type.value_counts().sort_index().items()},
        "by_band": {k: int(v) for k, v in leads.priority_band.value_counts().sort_index().items()},
        "by_type_band": {f"{t}_{b}": int(n) for (t, b), n in leads.groupby(["lead_type", "priority_band"]).size().items()},
        "by_region_box": {k: int(v) for k, v in leads.region_box.value_counts().sort_index().items()},
        "by_type_region_box": {f"{t}|{r}": int(n) for (t, r), n in leads.groupby(["lead_type", "region_box"]).size().items()},
        "evidence_rows": int(len(evidence)), "L1": c1, "L7": c7,
        "with_next_look": int(leads.next_look_utc.notna().sum()),
        "factor_distributions": factor_distributions(leads), "corroboration_window": feas,
        "top_of_queue_by_type": top_of_queue(leads), "queue_order": queue_order(leads),
    }
    plan_meta = {k: plan.get(k) for k in ("generated_utc", "window_start_utc", "window_end_utc", "primary_source")} if plan else {}
    return {"build": build, "leads": leads, "evidence": evidence, "counts": counts, "inputs": manifest, "inputs_signature": signature,
            "notes": notes, "plan": plan_meta, "since": str(since) if since else None, "until": str(until) if until else None,
            "seconds": round(time.time() - t0, 1)}


# ----------------------------------------------------------------------------------------------------------------------
# outputs

def _rel(p: Path) -> str:
    """Path relative to the repo root when it lies inside it, else the absolute path (tests write to tmp dirs)."""
    try:
        return p.relative_to(DATA_DIR.parent).as_posix()
    except ValueError:
        return str(p)


def previous_generated(gpkg: Path) -> tuple[str | None, str | None]:
    """(generated_utc, inputs_signature) of the about layer already on disk, or (None, None)."""
    if not gpkg.exists():
        return None, None
    try:
        import pyogrio

        if "about" not in [l[0] for l in pyogrio.list_layers(gpkg)]:
            return None, None
        ab = pyogrio.read_dataframe(gpkg, layer="about")
        return str(ab.generated_utc.iloc[0]), str(ab.inputs_signature.iloc[0])
    except Exception:
        return None, None


def gfw_tags(build: str) -> dict:
    """Licence, attribution and access-date stamps copied from regional_identity.parquet's metadata (the research
    identity file this build is derived from), as scripts/31_gfw_identity.py wrote them."""
    if build != "research":
        return {}
    import pyarrow.parquet as pq

    p = RESEARCH_DIR / "regional_identity.parquet"
    tags = {}
    if p.exists():
        meta = pq.read_metadata(p).metadata or {}
        for k in ("use", "licence", "licence_url", "terms_url", "attribution", "accessed_by_dataset", "datasets", "caveat"):
            if k.encode() in meta:
                tags["gfw_caveat" if k == "caveat" else k] = meta[k.encode()].decode()
    if not tags:
        from darkvessel.ais import gfw as G

        tags = {"use": G.RESEARCH_TAG, "licence": G.LICENCE, "licence_url": G.LICENCE_URL, "terms_url": G.TERMS_URL,
                "attribution": "see data/research/regional_identity_summary.json", "gfw_caveat": G.GFW_CAVEAT}
    tags["research_line"] = RESEARCH_LINE
    return tags


def file_layout(build: str) -> dict:
    """Which layers each GeoPackage of a build holds (paths relative to the repo)."""
    out = OUTPUTS[build]
    if out.get("detail") is not None:
        return {_rel(out["gpkg"]): ["leads_4326", "about"], _rel(out["detail"]): ["leads_utm49n", "lead_evidence", "about"]}
    return {_rel(out["gpkg"]): ["leads_4326", "leads_utm49n", "lead_evidence", "about"]}


def about_frame(result: dict, generated_utc: str) -> pd.DataFrame:
    build = result["build"]
    c = result["counts"]
    row = {
        "product": "SCS Vessel Watch leads queue", "build": build, "generated_utc": generated_utc,
        "inputs_signature": result["inputs_signature"], "inputs": json.dumps(result["inputs"]),
        "input_notes": json.dumps(result["notes"]), "window_since": result["since"], "window_until": result["until"],
        "n_leads": int(c["leads"]), "counts": json.dumps({k: v for k, v in c.items() if k not in ("factor_distributions",)}, default=str),
        "lead_types": json.dumps(R.LEAD_NAMES), "rules": json.dumps(R.RULE_TEXT),
        **({"rules_l1_ambiguity": R.L1_AMBIGUITY_TEXT, "weather_live": WEATHER_LIVE_TEXT} if build == "open" else {}),
        "corroboration_rule": CORROBORATION_RULE[build],
        "persistence_rule": f"an unmatched high or medium contact, not match_ambiguous where the flag exists, within {R.PERSISTENCE_KM:.0f} km on another pass (over {R.PERSISTENCE_MIN_GAP_S:.0f} s apart) within {R.PERSISTENCE_H:.0f} h",
        "next_look_rule": ("first planned pass that starts after both time_utc and the plan's generated_utc (passes with status past are "
                           "skipped) and whose footprint contains the point (data/s1_next_passes.json; polygons from data/ais_live.gpkg "
                           "s1_next_passes_4326, else the plan bbox); a repeat_cycle row is a prediction, not ESA's plan"),
        "plan_generated_utc": (result.get("plan") or {}).get("generated_utc"),
        "l7_scoring": (f"L7 is scored within the spec meaning of each factor: evidence quality on a sixth of the L1 scale (at most "
                       f"{P.L7_EVIDENCE_PTS}), corroboration 0 (radar did not look; lights not matched to AIS), AIS reach 0 (no AIS "
                       f"claim), persistence for lights on other nights (at most {P.L7_PERSISTENCE_PTS}), area weight as L1. Without "
                       f"an area weight an L7 lead scores at most {P.L7_CEILING}, the low band; an L1 lead with known calm weather "
                       f"scores at least {P.L1_FLOOR_WEATHER_KNOWN} (both channels and weather), so vessel leads come first (owner "
                       f"priority P0; ties sort L1 first by lead_id)."),
        "file_layout": json.dumps(file_layout(build)), "size_rule": SIZE_RULE,
        "l7_evidence_cap": f"the {R.L7_EVIDENCE_LIGHTS_MAX} brightest lights of a cell are evidence rows; n_lights is the full count",
        "gpkg_constant_columns": ("caveat (leads layers and lead_evidence) and research_only (lead_evidence) are SQLite column defaults: "
                                  "every row reads the value; the file stores it once per table"),
        "gpkg_page_size": GPKG_PAGE_SIZE,
        "priority_model_id": PRIORITY_MODEL_ID, "calibrated": False, "weights": json.dumps(P.WEIGHTS),
        "priority_meaning": "review priority 0 to 100, not a risk score; no AIS match adds 0 points; uncalibrated until owner labels exist (data/labels/owner_2026-10.csv)",
        "bands": "low 0 to 33, medium 34 to 66, high 67 to 100", "states": "new, reviewing, closed_explained, closed_unexplained, closed_false_alarm (decisions in the app's log; every lead here is new)",
        "lawful_explanations": json.dumps(R.LAWFUL_EXPLANATIONS, ensure_ascii=False), "change_indicators": json.dumps(R.CHANGE_INDICATORS, ensure_ascii=False),
        "evidence_roles": "primary, pass, nearest_ais, same_night_light, ais_behaviour, persistence, weather, cell, light, recurring_site",
        "list_columns": json.dumps(JSON_COLUMNS), "columns": json.dumps(column_doc(build)),
        "cnn_vessel_note": "cnn_vessel is never a filter for contacts under 25 m (acceptance 0.9 % there); L1 uses cnn_score >= 0.5",
        "l7_meaning": "L7 is a coverage statement for tasking (clear-sky lit activity where Sentinel-1 did not look in 90 days). It names no vessel and is not a vessel lead.",
        "dark_meaning": "Dark means only no AIS match. It never means illegal; an AIS gap is not proof of intent.",
        "caveat": caveat_for(build), "research_only": build == "research",
    }
    row.update(gfw_tags(build))
    return pd.DataFrame([row])


def _tmp(path: Path) -> Path:
    """A temporary name next to `path` with the same extension (GDAL picks the driver from it)."""
    return path.with_name(f".{path.stem}.writing{path.suffix}")


def _sql_literal(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return "'" + str(v).replace("'", "''") + "'"


def add_const_columns(gpkg: Path, table: str, values: dict) -> None:
    """Append constant columns to a GeoPackage table as SQLite column defaults (ALTER TABLE ADD COLUMN ... NOT NULL
    DEFAULT): every existing row reads the value, and the file stores it once in the schema. Booleans use the
    BOOLEAN declared type, which GDAL reads as a boolean field."""
    con = sqlite3.connect(gpkg)
    try:
        for col, v in values.items():
            typ = "BOOLEAN" if isinstance(v, bool) else "TEXT"
            con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {typ} NOT NULL DEFAULT {_sql_literal(v)}')
        con.commit()
    finally:
        con.close()


GPKG_PAGE_SIZE = 16384


def compact_gpkg(gpkg: Path, page_size: int = GPKG_PAGE_SIZE) -> None:
    """Rebuild the file with VACUUM at a larger page size. Lead rows are 2 to 3 KB, so on GDAL's default 4 KB pages one
    row fills a page and about a third of the file is empty space; 16 KB pages pack them. Deterministic for the same
    content, so reruns stay byte-identical."""
    con = sqlite3.connect(gpkg, isolation_level=None)
    try:
        con.execute(f"PRAGMA page_size={int(page_size)}")
        con.execute("VACUUM")
    finally:
        con.close()


def write_gpkg(path: Path, leads: pd.DataFrame, evidence: pd.DataFrame, about: pd.DataFrame, build: str, generated: str,
               detail: Path | None = None) -> list[Path]:
    """Leads in EPSG:4326 and UTM 49N, lead_evidence and about to temporary files, the constant columns as defaults,
    VACUUM, then os.replace into place. Without `detail` the four layers go to `path`. With it (open build, board D6.3)
    `path` gets leads_4326 and about, and `detail` gets leads_utm49n, lead_evidence and the same about row. Returns the
    files written."""
    import geopandas as gpd
    import pyogrio

    from darkvessel.io import utm_suffix

    main_tmp = _tmp(path)
    det_tmp = _tmp(detail) if detail is not None else main_tmp
    for t in {main_tmp, det_tmp}:
        if t.exists():
            t.unlink()
    lead_consts = {c: leads[c].iloc[0] if len(leads) else caveat_for(build) for c in CONST_GPKG_COLUMNS["leads"]}
    ev_consts = {"caveat": caveat_for(build), "research_only": build == "research"}
    for c, v in lead_consts.items():
        if len(leads) and not (leads[c] == v).all():
            raise ValueError(f"column {c} is not constant; it cannot be stored as a default")
    lead_body = leads.drop(columns=list(lead_consts))
    gdf = gpd.GeoDataFrame(lead_body, geometry=gpd.points_from_xy(lead_body.lon, lead_body.lat), crs=CRS_GEO)
    utm_name = f"leads_{utm_suffix(CRS_UTM_REGIONAL)}"
    opts = None if build == "open" else {"SPATIAL_INDEX": "NO"}   # the research file is local; ArcGIS Pro can add an index
    pyogrio.set_gdal_config_options({"OGR_CURRENT_DATE": generated.replace("Z", ".000Z")})
    try:
        gdf.to_file(main_tmp, layer="leads_4326", driver="GPKG", engine="pyogrio", layer_options=opts)
        gdf.to_crs(CRS_UTM_REGIONAL).to_file(det_tmp, layer=utm_name, driver="GPKG", engine="pyogrio", layer_options=opts)
        pyogrio.write_dataframe(evidence.drop(columns=list(ev_consts)), det_tmp, layer="lead_evidence", driver="GPKG")
        pyogrio.write_dataframe(about, main_tmp, layer="about", driver="GPKG")
        if det_tmp != main_tmp:
            pyogrio.write_dataframe(about, det_tmp, layer="about", driver="GPKG")
    except BaseException:
        for t in {main_tmp, det_tmp}:
            if t.exists():
                t.unlink()
        raise
    finally:
        pyogrio.set_gdal_config_options({"OGR_CURRENT_DATE": None})
    consts = {c: (bool(v) if isinstance(v, (bool, np.bool_)) else str(v)) for c, v in lead_consts.items()}
    add_const_columns(main_tmp, "leads_4326", consts)
    add_const_columns(det_tmp, utm_name, consts)
    add_const_columns(det_tmp, "lead_evidence", ev_consts)
    for t in sorted({main_tmp, det_tmp}):
        compact_gpkg(t)
    written = []
    if det_tmp != main_tmp:   # the detail file first: the backend reads only `path`
        os.replace(det_tmp, detail)
        written.append(detail)
    os.replace(main_tmp, path)
    return [path, *written]


def _write_text(path: Path, text: str) -> Path:
    tmp = _tmp(path)
    tmp.write_text(text)
    os.replace(tmp, path)
    return path


def write_outputs(result: dict, figure: bool = True, log=print, now: str | None = None) -> dict:
    """GeoPackages (dual CRS leads, lead_evidence, about; the open build in two files, see file_layout), research
    parquet, summary JSON; each written to a temporary name and moved into place. `now` replaces the clock (tests).
    Returns paths, sizes and generated_utc."""
    build = result["build"]
    out = OUTPUTS[build]
    gpkg: Path = out["gpkg"]
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    prev_time, prev_sig = previous_generated(gpkg)
    clock = now or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    generated = prev_time if prev_sig == result["inputs_signature"] and prev_time else clock
    about = about_frame(result, generated)
    leads = result["leads"]
    detail = out.get("detail")
    write_gpkg(gpkg, leads, result["evidence"], about, build, generated, detail=detail)
    written = {"gpkg": gpkg}
    if detail is not None:
        written["detail"] = detail
    if out["parquet"] is not None:
        written["parquet"] = write_parquet(leads, out["parquet"], about.iloc[0].to_dict(), build)
    summary = {
        "product": "SCS Vessel Watch leads queue", "build": build, "generated_utc": generated, "inputs_signature": result["inputs_signature"],
        "priority_model_id": PRIORITY_MODEL_ID, "calibrated": False,
        "counts": result["counts"], "weights": P.WEIGHTS, "rules": R.RULE_TEXT, "lead_names": R.LEAD_NAMES,
        **({"rules_l1_ambiguity": R.L1_AMBIGUITY_TEXT, "weather_live": WEATHER_LIVE_TEXT} if result["build"] == "open" else {}),
        "corroboration_rule": CORROBORATION_RULE[build],
        "plan_file": result["plan"], "inputs": result["inputs"], "input_notes": result["notes"],
        "outputs": {k: _rel(v) for k, v in written.items()},
        "output_bytes": {_rel(v): int(v.stat().st_size) for v in written.values()},
        "file_layout": file_layout(build), "size_rule": SIZE_RULE,
        "caveat": caveat_for(build), "research_only": build == "research",
    }
    summary.update(gfw_tags(build))
    written["summary"] = _write_text(out["summary"], json.dumps(summary, indent=1, default=str))
    sizes = {k: round(v.stat().st_size / 1e6, 2) for k, v in written.items()}
    for k, v in written.items():
        log(f"[{build}] wrote {_rel(v)} ({sizes[k]} MB)")
        if sizes[k] > COMMIT_LIMIT_MB and build == "research" and k == "gpkg":
            log(f"[{build}] note: {_rel(v)} is over the {COMMIT_LIMIT_MB:.0f} MB commit limit: it stays out of git (ignored by "
                f"name, board D6.5); the committed research copy is the parquet, and the file is rebuilt in seconds")
        elif sizes[k] > COMMIT_LIMIT_MB:
            log(f"[{build}] WARNING {_rel(v)} is over the {COMMIT_LIMIT_MB:.0f} MB commit limit (board D6.3): do not commit "
                f"it; narrow the build window (--since) or slim the detail layers before the next commit")
    return {"paths": written, "sizes_mb": sizes, "generated_utc": generated}


def write_parquet(leads: pd.DataFrame, path: Path, about: dict, build: str) -> Path:
    """The research twin: the leads table with the licence, attribution, caveat and model stamps in the file metadata."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    keep = ("use", "licence", "licence_url", "terms_url", "attribution", "accessed_by_dataset", "datasets", "gfw_caveat",
            "research_line", "generated_utc", "inputs_signature", "priority_model_id", "build", "caveat", "rules", "weights",
            "lawful_explanations", "change_indicators", "list_columns", "l7_scoring", "corroboration_rule", "next_look_rule")
    meta = {k: str(about[k]) for k in keep if k in about and about[k] is not None}
    meta["table"] = "SCS Vessel Watch leads (research build): L1 and L7 leads with evidence, factors and review priority"
    meta["research_only"] = "true"
    table = pa.Table.from_pandas(leads, preserve_index=False)
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), **{k.encode(): v.encode() for k, v in meta.items()}})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(path)
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)
    return path


# ----------------------------------------------------------------------------------------------------------------------
# figure

def figure(results: dict[str, dict], path: Path = FIG_PATH, log=print) -> Path | None:
    """Priority distribution by lead type and build (small, repo style, caveat in the caption)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from darkvessel.viz.style import BASELINE, GRID, INK, INK_2, MUTED, SERIES_LIGHT, apply_matplotlib_style

    apply_matplotlib_style()
    builds = [b for b in ("open", "research") if b in results]
    if not builds:
        return None
    fig, axes = plt.subplots(1, len(builds), figsize=(4.6 * len(builds), 3.4), squeeze=False, sharey=False)
    bins = np.arange(-0.5, 101, 1)   # one bin per point; the band edges 33.5 and 66.5 fall on bin edges
    for ax, b in zip(axes[0], builds):
        leads = results[b]["leads"]
        for j, t in enumerate(("L1", "L7")):
            sub = leads[leads.lead_type == t]
            if len(sub) == 0:
                continue
            lab = f"{t} {R.LEAD_NAMES[t]}"
            if t == "L1":
                ax.hist(sub.priority.astype(int), bins=bins, color=SERIES_LIGHT[0], alpha=0.85, label=lab, edgecolor=GRID, linewidth=0.3)
            else:   # outline, so the L7 shape stays readable over the taller L1 bars
                ax.hist(sub.priority.astype(int), bins=bins, histtype="step", color=SERIES_LIGHT[1], linewidth=1.6, label=lab)
        for x in (33.5, 66.5):
            ax.axvline(x, color=BASELINE, linewidth=0.8, linestyle="--")
        # log counts: the open build has a few hundred L1 leads spread over 40 points beside 2,000 L7 cells on 10
        ax.set_yscale("log", nonpositive="clip")
        ax.set_ylim(0.7, ax.get_ylim()[1] * 3)
        for x, lab in ((16, "low"), (50, "medium"), (83, "high")):
            ax.text(x, ax.get_ylim()[1] * 0.85, lab, ha="center", va="top", fontsize=7, color=MUTED)
        ax.set_xlim(0, 100)
        ax.set_xlabel("review priority (0 to 100)", fontsize=8)
        ax.set_ylabel("leads per point (log scale)", fontsize=8)
        ax.set_title(f"{b} build, {len(leads):,} leads" + "".join(f"; {t} {int((leads.lead_type == t).sum()):,}" for t in ("L1", "L7") if (leads.lead_type == t).any()),
                     loc="left", fontsize=9, color=INK)
        ax.tick_params(labelsize=7)
        ax.grid(axis="y")
    handles, labels = {}, []
    for ax in axes[0]:
        for h, l in zip(*ax.get_legend_handles_labels()):
            if l not in handles:
                handles[l] = h
    handles = dict(sorted(handles.items()))   # L1 before L7
    fig.legend(handles.values(), handles.keys(), loc="upper left", bbox_to_anchor=(0.02, 0.9), ncol=2, fontsize=7, frameon=False)
    fig.suptitle(f"Leads queue: review priority by type and build ({PRIORITY_MODEL_ID}, uncalibrated)", x=0.02, ha="left",
                 fontsize=10.5, color=INK, fontweight="bold")
    research_note = (" Research build: noncommercial, CC BY-NC 4.0, contains Global Fishing Watch data. Powered by Global Fishing Watch."
                     if "research" in builds else "")
    fig.text(0.02, 0.01, "Review priority is not a risk score. Dark = no AIS match; not evidence of illegal activity; an AIS gap is not proof "
             f"of intent. L7 (outline) is a coverage lead for tasking, not a vessel lead; it scores at most {P.L7_CEILING} without an analyst area weight, "
             f"so vessel leads (L1) sort first. Open L1: live passes (live AIS relayed by aisstream.io; terms UNVERIFIED)."
             + research_note, fontsize=6.5, color=INK_2, wrap=True)
    fig.subplots_adjust(left=0.08, right=0.98, top=0.74, bottom=0.27, wspace=0.25)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(path)
    fig.savefig(tmp, dpi=150, format="png", metadata={"Software": None})
    plt.close(fig)
    os.replace(tmp, path)
    log(f"wrote {_rel(path)}")
    return path
