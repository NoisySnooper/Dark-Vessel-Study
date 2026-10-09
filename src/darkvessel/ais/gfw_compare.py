"""Global Fishing Watch model predictions against this project's products. Research build only (CC BY-NC 4.0).

Scope
-----
The other GFW module (`darkvessel.ais.gfw`, script 27) pulls GFW's SAR detections, AIS grids and events and compares
our radar candidates with GFW's SAR detections cell by cell. This module covers the *other* GFW model outputs and the
comparisons that need them:

* fixed offshore infrastructure (GFW's SAR plus Sentinel-2 structure detections, Bulk Download API) against our
  persistence-based fixed structures and the Satlas marine points;
* the neural vessel type on GFW SAR detections (Likely Fishing, Likely non-fishing, Unknown) against our length
  estimate, CNN score, VIIRS lit co-location and coast distance;
* AIS apparent fishing hours per 0.25 degree cell against our VIIRS clear-sky lit-vessel density (rank correlation,
  rank-difference raster, high-low cells);
* AIS gap events against our radar candidates in space and time.

Only pure functions live here, so they can be tested offline on synthetic inputs. Network calls go through
`darkvessel.ais.gfw.GFWClient` (cache, rate limit, 429 retry, redaction); the only new endpoint is the Bulk Download
API (`fetch_fixed_infrastructure`).

Facts from the GFW documentation, read on 2026-10-08 (URLs in SOURCES):
* Fixed infrastructure: dataset `public-fixed-infrastructure-data:latest` (resolved to v1.1), POST /v3/bulk-reports
  with a GeoJSON region and filters, GET /v3/bulk-reports/{id} until status is done, GET
  /v3/bulk-reports/{id}/download-file-url?file=DATA for a signed URL (valid 60 s). One row per detection with
  structure_id, lon, lat, structure_start_date, structure_end_date, label (oil, wind, unknown; the bulk file also
  holds noise), label_confidence (high, medium, low). The Map and the context-layer MVT exclude noise, keep only
  structures seen for at least 3 months with a noise probability under 0.3; the bulk file is unfiltered.
* SAR detections: filter `neural_vessel_type` with values 'Likely Fishing', 'Likely non-fishing', 'Unknown' (dataset
  metadata), defined as model score >= 0.9, <= 0.1 and in between (4Wings docs). The report endpoint returns cell
  counts, not detections, so a type can only be attached to a cell and date.
* Length: "Same data as in the Map but there are missing some fields compared to the Data Downloads: length_m or any
  of the scores like presence_score, matching_score, or fishing_score" (GFW products-difference PDF, 4 Aug 2026).
* Gap events: a gap is at least 12 h, starts at least 50 nautical miles from shore, in an area with satellite
  reception over 10 positions per day, with at least 14 positions in the 12 h before (data caveats). GFW calls the
  dataset a prototype.
"""

from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree
from scipy.stats import spearmanr

from darkvessel.ais import gfw as G
from darkvessel.config import DATA_DIR

RESEARCH_DIR = DATA_DIR / "research"
BULK_CACHE = G.GFW_CACHE / "bulk"
DATASET_FIXED_INFRA = "public-fixed-infrastructure-data:latest"
DATASET_FIXED_INFRA_FILTERED = "public-fixed-infrastructure-filtered:latest"
NEURAL_TYPES = ("Likely Fishing", "Likely non-fishing", "Unknown")
R_M = 6371008.8
LOCAL_UTC_OFFSET_H = 7  # the project's local night (UTC+7), as in scripts/15_viirs_lights.py

RESEARCH_LABEL = "research build only, Global Fishing Watch data CC BY-NC 4.0, noncommercial"
GAP_CAVEAT = ("Dark does not mean illegal. Many boats need not carry AIS, AIS can be off for lawful reasons, and AIS "
              "has blind spots. A gap event is not proof of intent.")
MODEL_CAVEAT = ("Global Fishing Watch models are another system's predictions, not truth. GFW SAR detections cover "
                "mostly industrial vessels: GFW's data caveats say they miss most vessels under 15 m, and Paolo et al. "
                "2024 (doi:10.1038/s41586-023-06825-8) report a detection rate over 70 % at 25 m and over 90 % at 50 m, "
                "with thresholds calibrated to 60 % detection of 15 to 20 m vessels. " + GAP_CAVEAT)

SOURCES = {
    **G.SOURCES,
    "docs_bulk_download": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/bulk-download",
    "docs_bulk_create": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/bulk-download/create-bulk-report",
    "docs_bulk_query": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/bulk-download/query-bulk-report",
    "docs_fixed_infra_context_layer": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/datasets/fixed-infra-context-layer",
    "docs_insights": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/insights",
    "docs_reference_data": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/reference-data",
    "docs_release_notes": "https://globalfishingwatch.org/our-apis/documentation/docs/release-notes",
    "products_difference_pdf": "https://globalfishingwatch.org/our-apis/documentation/assets/APIs_gfwr_and_Data_Downloads_Products_Differences.pdf",
    "data_download_portal_fixed_infra": "https://globalfishingwatch.org/data-download/datasets/public-fixed-infrastructure:v1.1",
    "docs_llms_full": "https://globalfishingwatch.org/our-apis/documentation/llms-full.txt",
}

# Reporting boxes of scripts/21_viirs_regions.py (plain boxes, not boundaries or claims). Copied, because importing
# that script runs the VIIRS cache scan at import time.
REGIONS = {"Gulf of Tonkin": (105.5, 17.0, 110.0, 22.5), "North shelf": (110.0, 18.0, 118.0, 23.5),
           "Gulf of Thailand": (99.0, 6.0, 105.0, 14.0), "South Vietnam shelf": (105.0, 6.0, 110.0, 12.0),
           "Central sea": (110.0, 6.0, 118.0, 17.0), "Southern sea": (102.0, -3.5, 110.0, 6.0)}

LENGTH_BINS = ((0, 25), (25, 50), (50, 100), (100, 10_000))


# -- small statistics ---------------------------------------------------------------------------------------------
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None, float | None]:
    """(share, low, high): Wilson score interval of k successes in n trials; Nones when n = 0."""
    if n <= 0:
        return None, None, None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(float(p), 4), round(float(max(0.0, centre - half)), 4), round(float(min(1.0, centre + half)), 4)


def spearman(a, b) -> dict:
    """Spearman rank correlation over the finite pairs: {rho, p, n}; rho None under 3 pairs or a constant series."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b)
    n = int(ok.sum())
    if n < 3 or np.ptp(a[ok]) == 0 or np.ptp(b[ok]) == 0:
        return {"rho": None, "p": None, "n": n}
    r = spearmanr(a[ok], b[ok])
    return {"rho": round(float(r.statistic), 3), "p": float(r.pvalue), "n": n}


def in_box(lon, lat, box: tuple[float, float, float, float]) -> np.ndarray:
    w, s, e, n = box
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    return (lon >= w) & (lon < e) & (lat >= s) & (lat < n)


# -- geometry -----------------------------------------------------------------------------------------------------
def unit_vectors(lon, lat) -> np.ndarray:
    """Points on the unit sphere, so a KD-tree on them gives great-circle neighbours anywhere on Earth."""
    lon, lat = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    return np.c_[np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)]


def chord_to_m(chord: np.ndarray) -> np.ndarray:
    return 2 * R_M * np.arcsin(np.clip(np.asarray(chord, float) / 2, 0, 1))


def m_to_chord(d_m: float) -> float:
    return float(2 * np.sin(min(d_m / R_M, np.pi) / 2))


def nearest_m(lon_a, lat_a, lon_b, lat_b) -> tuple[np.ndarray, np.ndarray]:
    """(distance in metres, index into b) of the nearest b point for each a point; inf and -1 when b is empty."""
    lon_a = np.asarray(lon_a, float)
    if len(lon_a) == 0:
        return np.zeros(0), np.zeros(0, int)
    if len(np.asarray(lon_b)) == 0:
        return np.full(len(lon_a), np.inf), np.full(len(lon_a), -1)
    d, i = cKDTree(unit_vectors(lon_b, lat_b)).query(unit_vectors(lon_a, lat_a))
    return chord_to_m(d), i


def haversine_m(lon1, lat1, lon2, lat2) -> np.ndarray:
    lon1, lat1, lon2, lat2 = (np.radians(np.asarray(x, float)) for x in (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * R_M * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


# -- fixed infrastructure (Bulk Download API) ----------------------------------------------------------------------
def bulk_report_body(geojson: dict, name: str = "darkvessel-scs-fixed-infrastructure",
                     filters: list[str] | None = None, fmt: str = "CSV") -> dict:
    """Body of POST /v3/bulk-reports. `filters` is required by the API; the default keeps every structure."""
    return {"name": name, "dataset": DATASET_FIXED_INFRA, "format": fmt,
            "filters": filters if filters is not None else ["structure_start_date between '2017-01-01' and '2030-12-31'"],
            "geojson": geojson}


def fetch_fixed_infrastructure(client, geojson: dict, *, poll_s: float = 20.0, max_wait_s: float = 5400.0,
                               cache_dir: Path = BULK_CACHE, log=print) -> tuple[pd.DataFrame, dict]:
    """GFW fixed infrastructure detections inside `geojson` through the Bulk Download API, cached as CSV.

    Returns (one row per detection, metadata). The report request is cached by the client (same body, same report
    id); status polls and the signed download URL are never cached. The download itself goes to Google Cloud
    Storage with no Authorization header.
    """
    body = bulk_report_body(geojson)
    created = client.request("POST", "bulk-reports", body=body, group="bulk")
    rid = created["id"]
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    csv_path = cache_dir / f"fixed_infrastructure_{rid}.csv.gz"
    meta_path = cache_dir / f"fixed_infrastructure_{rid}.meta.json"
    if csv_path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text())
    else:
        t0 = time.time()
        status = created
        while status.get("status") not in ("done", "failed"):
            if time.time() - t0 > max_wait_s:
                raise G.GFWError(f"bulk report {rid} not done after {max_wait_s} s (status {status.get('status')})")
            time.sleep(poll_s)
            status = client.request("GET", f"bulk-reports/{rid}", group="bulk", use_cache=False)
            log("bulk report", rid, status.get("status"), status.get("fileSize"))
        if status.get("status") == "failed":
            raise G.GFWError(f"bulk report {rid} failed: {json.dumps(status)[:300]}")
        url = client.request("GET", f"bulk-reports/{rid}/download-file-url", params={"file": "DATA"}, group="bulk",
                             use_cache=False)["url"]
        r = requests.get(url, timeout=600)  # signed URL, 60 s validity, no token
        r.raise_for_status()
        raw = r.content
        if raw[:2] != b"\x1f\x8b":  # the API's example name is data.csv.gz; keep gzip either way
            raw = gzip.compress(raw)
        csv_path.write_bytes(raw)
        meta = {"report_id": rid, "dataset": status.get("dataset"), "filepath": status.get("filepath"),
                "filters": status.get("filters"), "format": status.get("format"), "file_size": status.get("fileSize"),
                "created_at": status.get("createdAt"), "updated_at": status.get("updatedAt"),
                "fetched_utc": pd.Timestamp.now("UTC").isoformat(), "licence": G.LICENCE, "licence_url": G.LICENCE_URL,
                "use": RESEARCH_LABEL}
        meta_path.write_text(json.dumps(meta, indent=1))
    with gzip.open(csv_path, "rb") as fh:
        df = pd.read_csv(io.BytesIO(fh.read()))
    return df, meta


def structures_from_detections(det: pd.DataFrame) -> pd.DataFrame:
    """One row per structure_id from the detection rows: median position, most common label, last confidence,
    first and last dates, detection count, and `ongoing` (no end date)."""
    if det.empty:
        return pd.DataFrame(columns=["structure_id", "lon", "lat", "label", "label_confidence", "structure_start_date",
                                     "structure_end_date", "n_detections", "last_detection", "ongoing", "months_seen"])
    d = det.copy()
    for c in ("structure_start_date", "structure_end_date", "detection_date"):
        if c in d:
            d[c] = pd.to_datetime(d[c], errors="coerce")
    d = d.sort_values(["structure_id"] + (["detection_date"] if "detection_date" in d else []))
    g = d.groupby("structure_id", sort=False)
    out = pd.DataFrame({
        "lon": g.lon.median(), "lat": g.lat.median(),
        "label": g.label.agg(lambda s: s.mode().iloc[0] if len(s.mode()) else None),
        "label_confidence": g.label_confidence.last() if "label_confidence" in d else None,
        "structure_start_date": g.structure_start_date.min(),
        "structure_end_date": g.structure_end_date.max() if "structure_end_date" in d else pd.NaT,
        "n_detections": g.size(),
        "last_detection": g.detection_date.max() if "detection_date" in d else pd.NaT,
    }).reset_index()
    out["ongoing"] = out.structure_end_date.isna()
    span_end = out.structure_end_date.fillna(out.last_detection)
    out["months_seen"] = ((span_end - out.structure_start_date).dt.days / 30.44).round(1)
    return out


def agreement(lon_a, lat_a, lon_b, lat_b, radii_m=(500.0, 1000.0)) -> dict:
    """Share of a points with a b point within each radius (Wilson interval), plus the median nearest distance."""
    d, _ = nearest_m(lon_a, lat_a, lon_b, lat_b)
    n = int(len(d))
    out = {"n": n, "median_nearest_m": round(float(np.median(d)), 1) if n and np.isfinite(d).all() else None}
    for r in radii_m:
        k = int((d <= r).sum())
        p, lo, hi = wilson(k, n)
        out[f"within_{int(r)}m"] = {"k": k, "n": n, "share": p, "ci95": [lo, hi]}
    return out


def agreement_by_region(a: pd.DataFrame, b: pd.DataFrame, regions: dict = REGIONS, radii_m=(500.0, 1000.0),
                        name_a: str = "a", name_b: str = "b") -> dict:
    """Both directions of `agreement` for the whole set and for each region box (points of a and b inside the box).

    a and b need lon and lat columns. Returns {region: {"<a>_near_<b>": ..., "<b>_near_<a>": ...}}.
    """
    out = {}
    boxes = {"All": None, **regions}
    for reg, box in boxes.items():
        if box is None:
            aa, bb = a, b
        else:
            aa, bb = a[in_box(a.lon, a.lat, box)], b[in_box(b.lon, b.lat, box)]
        out[reg] = {f"{name_a}_near_{name_b}": agreement(aa.lon, aa.lat, bb.lon, bb.lat, radii_m),
                    f"{name_b}_near_{name_a}": agreement(bb.lon, bb.lat, aa.lon, aa.lat, radii_m)}
    return out


# -- neural vessel type on SAR cells ------------------------------------------------------------------------------
def assign_gfw_type(cands: pd.DataFrame, cells: pd.DataFrame, max_dist_m: float = 1000.0) -> pd.DataFrame:
    """Attach GFW's neural vessel type to our candidates from GFW's per-cell, per-date counts.

    cands: det_id, lon, lat, date (YYYY-MM-DD). cells: lon, lat (cell centres), date, detections, neural_vessel_type.
    For each candidate, every GFW cell of the same date whose centre lies within `max_dist_m` is collected.
    gfw_type = the one type present, 'mixed' when several, None when no cell is near. gfw_n_cells and
    gfw_n_detections count what was collected; gfw_single is True when exactly one GFW detection is near, the
    clean case for a one-to-one reading.
    """
    out = cands.copy()
    out["gfw_type"] = None
    out["gfw_n_cells"] = 0
    out["gfw_n_detections"] = 0
    out["gfw_nearest_m"] = np.nan
    if cands.empty or cells.empty:
        out["gfw_single"] = False
        return out
    cells = cells.copy()
    cells["detections"] = pd.to_numeric(cells.detections, errors="coerce").fillna(0).astype(int)
    r_chord = m_to_chord(max_dist_m)
    for date, c in cells.groupby("date"):
        sel = np.flatnonzero((out.date == date).values)
        if len(sel) == 0:
            continue
        tree = cKDTree(unit_vectors(c.lon.values, c.lat.values))
        uv = unit_vectors(out.lon.values[sel], out.lat.values[sel])
        dist, _ = tree.query(uv)
        hits = tree.query_ball_point(uv, r_chord)
        types, ncell, ndet = [], [], []
        for h in hits:
            if not h:
                types.append(None), ncell.append(0), ndet.append(0)
                continue
            sub = c.iloc[h]
            t = sorted(set(sub.neural_vessel_type.dropna()))
            types.append(t[0] if len(t) == 1 else "mixed")
            ncell.append(len(h))
            ndet.append(int(sub.detections.sum()))
        out.iloc[sel, out.columns.get_loc("gfw_type")] = types
        out.iloc[sel, out.columns.get_loc("gfw_n_cells")] = ncell
        out.iloc[sel, out.columns.get_loc("gfw_n_detections")] = ndet
        out.iloc[sel, out.columns.get_loc("gfw_nearest_m")] = chord_to_m(dist)
    out["gfw_single"] = out.gfw_n_detections == 1
    return out


def length_bin(length_m, bins=LENGTH_BINS) -> pd.Series:
    labels = [f"{lo}-{hi} m" if hi < 10_000 else f"{lo} m and longer" for lo, hi in bins]
    edges = [bins[0][0]] + [hi for _, hi in bins]
    return pd.cut(pd.Series(np.asarray(length_m, float)), edges, labels=labels, right=False, include_lowest=True)


def type_shares(typed: pd.DataFrame, by: str | None = None, types=NEURAL_TYPES) -> pd.DataFrame:
    """Rows per group of `by` (or one row): n with a GFW type, and the share of each type (mixed included)."""
    t = typed[typed.gfw_type.notna()]
    groups = [(None, t)] if by is None else list(t.groupby(by, observed=True))
    rows = []
    for key, g in groups:
        n = int(len(g))
        row = {by or "group": str(key) if key is not None else "all", "n": n}
        for ty in list(types) + ["mixed"]:
            k = int((g.gfw_type == ty).sum())
            row[f"n_{ty}"] = k
            row[f"share_{ty}"] = round(k / n, 4) if n else None
        rows.append(row)
    return pd.DataFrame(rows)


# -- local night --------------------------------------------------------------------------------------------------
def night_of(ts_utc, offset_h: int = LOCAL_UTC_OFFSET_H) -> pd.Series:
    """Local evening date of the night that contains (or precedes) each UTC time: local times before noon belong to
    the night that started the evening before. Matches the `night` field of the VIIRS products (UTC+7)."""
    t = pd.to_datetime(pd.Series(ts_utc), utc=True) + pd.Timedelta(hours=offset_h)
    night = t.dt.normalize() - pd.to_timedelta((t.dt.hour < 12).astype(int), unit="D")
    return night.dt.strftime("%Y-%m-%d")


def lit_colocation(cands: pd.DataFrame, lights: pd.DataFrame, radius_m: float = 1000.0) -> pd.Series:
    """True for each candidate with a VIIRS light of the same local night within `radius_m`.

    cands: lon, lat, night. lights: lon, lat, night. Lights and radar are hours apart, so this is co-location in a
    cell-sized radius, never a one-to-one match.
    """
    out = pd.Series(False, index=cands.index)
    if cands.empty or lights.empty:
        return out
    r = m_to_chord(radius_m)
    for night, l in lights.groupby("night"):
        sel = cands.index[(cands.night == night).values]
        if len(sel) == 0:
            continue
        tree = cKDTree(unit_vectors(l.lon.values, l.lat.values))
        d, _ = tree.query(unit_vectors(cands.loc[sel, "lon"].values, cands.loc[sel, "lat"].values))
        out.loc[sel] = d <= r
    return out


# -- fishing effort against lights on the model grid ---------------------------------------------------------------
def rank_pct(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Percentile rank in [0, 1] of each valid cell (ties averaged); NaN elsewhere."""
    from scipy.stats import rankdata

    out = np.full(values.shape, np.nan)
    v = values[valid]
    if v.size:
        out[valid] = (rankdata(v, method="average") - 0.5) / v.size
    return out


def rank_compare(a: np.ndarray, b: np.ndarray, valid: np.ndarray, lon2d: np.ndarray, lat2d: np.ndarray,
                 regions: dict = REGIONS, tercile: float = 1 / 3) -> dict:
    """Compare two gridded series over the same cells by rank.

    Returns {"spearman": {region: {rho, p, n}}, "rank_diff": rank(a) - rank(b) in [-1, 1],
    "quadrant": int8 array (1 = a high and b low, 2 = a low and b high, 0 = other, -1 = not valid),
    "counts": per region the number of valid cells and of each quadrant class}.
    High and low are the top and bottom tercile of the valid cells' ranks.
    """
    valid = np.asarray(valid, bool) & np.isfinite(a) & np.isfinite(b)
    ra, rb = rank_pct(a, valid), rank_pct(b, valid)
    diff = ra - rb
    quad = np.full(a.shape, -1, np.int8)
    quad[valid] = 0
    hi_a, lo_b = ra >= 1 - tercile, rb < tercile
    lo_a, hi_b = ra < tercile, rb >= 1 - tercile
    quad[valid & hi_a & lo_b] = 1
    quad[valid & lo_a & hi_b] = 2
    sp, counts = {}, {}
    for reg, box in {"All": None, **regions}.items():
        m = valid if box is None else valid & in_box(lon2d, lat2d, box)
        sp[reg] = spearman(a[m], b[m])
        counts[reg] = {"cells": int(m.sum()), "a_high_b_low": int((quad[m] == 1).sum()),
                       "a_low_b_high": int((quad[m] == 2).sum()), "a_zero": int((a[m] == 0).sum()),
                       "b_zero": int((b[m] == 0).sum())}
    return {"spearman": sp, "rank_diff": diff, "quadrant": quad, "counts": counts}


# -- AIS gap events against radar candidates ----------------------------------------------------------------------
def gap_proximity(cands: pd.DataFrame, gaps: pd.DataFrame, max_km: float, max_h: float) -> pd.DataFrame:
    """Radar candidates near the start (AIS off) and end (AIS on) of each gap event.

    cands: lon, lat, ts (UTC). gaps: event_id, off_lon, off_lat, start (UTC), on_lon, on_lat, end (UTC).
    For each event: n_off = candidates within max_km of the off position and within max_h hours of the start;
    n_on likewise for the on position and the end; n_between = candidates inside the window from start to end
    within max_km of the straight line's bounding circle (centre of off and on, radius half the distance plus
    max_km), a loose bound on what the radar saw while the vessel was dark.
    """
    rows = []
    ts = pd.to_datetime(cands.ts, utc=True).values.astype("datetime64[s]").astype("int64") if len(cands) else np.zeros(0)
    for _, g in gaps.iterrows():
        t0 = pd.Timestamp(g.start).tz_convert("UTC") if pd.Timestamp(g.start).tzinfo else pd.Timestamp(g.start, tz="UTC")
        t1 = pd.Timestamp(g.end).tz_convert("UTC") if pd.Timestamp(g.end).tzinfo else pd.Timestamp(g.end, tz="UTC")
        t0s, t1s = int(t0.timestamp()), int(t1.timestamp())
        row = {"event_id": g.event_id, "start": t0.isoformat(), "end": t1.isoformat(), "duration_h": round((t1s - t0s) / 3600, 1),
               "max_km": max_km, "max_h": max_h, "n_off": 0, "n_on": 0, "n_between": 0, "nearest_off_km": None, "nearest_on_km": None}
        if len(cands):
            if pd.notna(g.off_lon) and pd.notna(g.off_lat):
                d = haversine_m(cands.lon.values, cands.lat.values, g.off_lon, g.off_lat) / 1000
                near_t = np.abs(ts - t0s) <= max_h * 3600
                row["n_off"] = int(((d <= max_km) & near_t).sum())
                row["nearest_off_km"] = round(float(d[near_t].min()), 1) if near_t.any() else None
            if pd.notna(g.on_lon) and pd.notna(g.on_lat):
                d = haversine_m(cands.lon.values, cands.lat.values, g.on_lon, g.on_lat) / 1000
                near_t = np.abs(ts - t1s) <= max_h * 3600
                row["n_on"] = int(((d <= max_km) & near_t).sum())
                row["nearest_on_km"] = round(float(d[near_t].min()), 1) if near_t.any() else None
            if all(pd.notna(g[c]) for c in ("off_lon", "off_lat", "on_lon", "on_lat")):
                cx, cy = (g.off_lon + g.on_lon) / 2, (g.off_lat + g.on_lat) / 2
                half = haversine_m(g.off_lon, g.off_lat, g.on_lon, g.on_lat) / 2000
                d = haversine_m(cands.lon.values, cands.lat.values, cx, cy) / 1000
                in_t = (ts >= t0s) & (ts <= t1s)
                row["n_between"] = int(((d <= half + max_km) & in_t).sum())
        rows.append(row)
    return pd.DataFrame(rows)


# -- misc ---------------------------------------------------------------------------------------------------------
def coast_distance_km(lon, lat, land, crs_m: str = "EPSG:32649") -> np.ndarray:
    """Distance from each point to the nearest Natural Earth land polygon, in km, measured in `crs_m` (UTM 49N for
    the South China Sea: scale error under about 3 % at the AOI's edges)."""
    import geopandas as gpd

    pts = gpd.GeoSeries(gpd.points_from_xy(lon, lat), crs="EPSG:4326").to_crs(crs_m)
    land_m = land.to_crs(crs_m)
    tree = land_m.sindex
    out = np.full(len(pts), np.nan)
    if len(land_m) == 0 or len(pts) == 0:
        return out
    idx = tree.nearest(pts.geometry.values, return_all=False)
    for i_pt, i_land in zip(idx[0], idx[1]):
        out[i_pt] = pts.iloc[i_pt].distance(land_m.geometry.iloc[i_land]) / 1000
    return out


def today() -> str:
    return dt.date.today().isoformat()
