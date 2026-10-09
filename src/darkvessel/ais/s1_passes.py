"""Next Sentinel-1 passes over the AOI: the windows when live AIS can be matched to radar contacts.

Two sources, best first:
  1. ESA's published acquisition plan (KML on https://sentinels.copernicus.eu/web/sentinel/copernicus/sentinel-1/
     acquisition-plans): planned segments with begin/end times, mode, polarisation and orbit numbers. Used when the
     file can be downloaded; `parse_acquisition_kml` reads it.
  2. The 12-day repeat cycle applied to the project's own scene archive (data/s1_scenes.csv, data/s1_footprints.gpkg):
     Sentinel-1 flies a 12-day repeat orbit with 175 orbits per cycle, held in a 120 m (RMS) orbital tube
     (ESA SentiWiki, https://sentiwiki.copernicus.eu/web/s1-mission). A pass on relative orbit R at time T is
     therefore expected again at T + 12 days, on the same ground track and at the same time of day. The archive is
     checked first (`repeat_check`): the share of consecutive passes on the same relative orbit that are 12.000 days
     apart and the time-of-day drift between them.

Repeat-cycle predictions assume ESA keeps the same acquisition segments; they are NOT the acquisition plan and a
segment can be dropped, shifted or added. Every output says which source it came from.

Plan files are named s1X_mp_user_<start>_<end> (UTC, e.g. s1d_mp_user_20261008t182319_20261015t200700). ESA
publishes a new file every day or two with an overlapping window; `select_plan_segments` lets the newest file win
inside its own window and uses older files only outside it. `fetch_plan` downloads the files a window needs from
the acquisition-plan page into data/cache/s1_plan/. `group_passes` gives rows that are the same physical pass (plan
segments and the repeat prediction of one pass: same mission, time spans within 10 min) one `pass_group` id.
`write_json_atomic` writes the pass file through a temp file and a rename, with NaN turned into null, because other
pipelines poll it.
"""

from __future__ import annotations

import json
import math
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from darkvessel.config import CRS_GEO

CYCLE_DAYS = 12
ORBITS_PER_CYCLE = 175
ACQ_PLAN_PAGE = "https://sentinels.copernicus.eu/web/sentinel/copernicus/sentinel-1/acquisition-plans"
SENTIWIKI_S1 = "https://sentiwiki.copernicus.eu/web/s1-mission"

PASS_NOTE_REPEAT = ("Predicted from the 12-day repeat cycle applied to the project's Sentinel-1 scene archive (same relative "
                    "orbit, same time of day, 12 days after the latest archived pass). This is not ESA's acquisition plan: "
                    "a segment can be dropped, shifted or added. Source for the 12-day, 175-orbit repeat and the 120 m "
                    f"orbital tube: {SENTIWIKI_S1}.")
PASS_NOTE_PLAN = f"From ESA's published Sentinel-1 acquisition plan KML ({ACQ_PLAN_PAGE}); plans can still change."


def passes_from_scenes(scenes: pd.DataFrame, gap_minutes: float = 30.0) -> pd.DataFrame:
    """Group scene rows into passes: consecutive scenes of one mission on one relative orbit within `gap_minutes`.

    Needs columns mission, orbit_rel, pass_dir, start_utc, stop_utc, product_id (data/s1_scenes.csv).
    Returns one row per pass: mission, orbit_rel, pass_dir, start_utc, stop_utc, n_scenes, product_ids (list).
    """
    s = scenes.copy()
    s["start_utc"] = pd.to_datetime(s.start_utc, utc=True)
    s["stop_utc"] = pd.to_datetime(s.stop_utc, utc=True)
    s = s.sort_values(["mission", "orbit_rel", "start_utc"])
    rows = []
    for (mission, rel, pdir), g in s.groupby(["mission", "orbit_rel", "pass_dir"], sort=False):
        gap = g.start_utc.diff() > pd.Timedelta(minutes=gap_minutes)
        for _, p in g.groupby(gap.cumsum()):
            rows.append({"mission": mission, "orbit_rel": int(rel), "pass_dir": pdir, "start_utc": p.start_utc.min(),
                         "stop_utc": p.stop_utc.max(), "n_scenes": int(len(p)), "product_ids": p.product_id.tolist()})
    out = pd.DataFrame(rows, columns=["mission", "orbit_rel", "pass_dir", "start_utc", "stop_utc", "n_scenes", "product_ids"])
    return out.sort_values("start_utc").reset_index(drop=True)


def repeat_check(passes: pd.DataFrame, cycle_days: int = CYCLE_DAYS, tol_s: float = 120.0) -> dict:
    """Does the archive repeat every `cycle_days`? Consecutive passes on one (mission, relative orbit) are compared.

    Returns pairs, share within `tol_s` of an exact multiple of the cycle, median and max absolute drift (seconds),
    and the list of relative orbits whose drift exceeds the tolerance.
    """
    drifts, bad = [], []
    for (mission, rel), g in passes.groupby(["mission", "orbit_rel"]):
        t = g.start_utc.sort_values().values
        for a, b in zip(t[:-1], t[1:]):
            d = (b - a) / np.timedelta64(1, "s")
            cycles = round(d / (cycle_days * 86400))
            if cycles < 1:
                continue
            drift = d - cycles * cycle_days * 86400
            drifts.append(drift)
            if abs(drift) > tol_s:
                bad.append(f"{mission} rel {rel}: {drift:+.0f} s over {cycles} cycle(s)")
    n = len(drifts)
    arr = np.abs(np.array(drifts)) if n else np.array([])
    return {"pairs": n, "cycle_days": cycle_days, "tolerance_s": tol_s,
            "share_within_tolerance": round(float((arr <= tol_s).mean()), 4) if n else None,
            "median_abs_drift_s": round(float(np.median(arr)), 1) if n else None,
            "max_abs_drift_s": round(float(arr.max()), 1) if n else None, "outliers": bad[:20],
            "verdict": ("repeat holds" if n and (arr <= tol_s).mean() >= 0.9 else "repeat NOT confirmed" if n else "no pairs")}


def predict_passes(passes: pd.DataFrame, now: pd.Timestamp, horizon_h: float = 72.0, cycle_days: int = CYCLE_DAYS,
                   max_cycles_back: int = 3) -> pd.DataFrame:
    """Passes expected in [now, now + horizon_h]: each (mission, relative orbit, direction) repeats `cycle_days` later.

    The basis is the latest archived pass of that orbit; it must be at most `max_cycles_back` cycles old, or the
    orbit is skipped as stale (the segment may have been dropped). Returns one row per predicted pass with
    start_utc, stop_utc, mission, orbit_rel, pass_dir, basis_start_utc, cycles_ahead, n_scenes, product_ids, source.
    """
    now = pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    end = now + pd.Timedelta(hours=horizon_h)
    cycle = pd.Timedelta(days=cycle_days)
    rows = []
    for (mission, rel, pdir), g in passes.groupby(["mission", "orbit_rel", "pass_dir"]):
        last = g.sort_values("start_utc").iloc[-1]
        k = max(1, math.ceil((now - last.start_utc) / cycle))
        if k > max_cycles_back:
            continue
        while True:
            t0 = last.start_utc + k * cycle
            if t0 > end:
                break
            if t0 >= now:
                rows.append({"start_utc": t0, "stop_utc": last.stop_utc + k * cycle, "mission": mission, "orbit_rel": int(rel),
                             "pass_dir": pdir, "basis_start_utc": last.start_utc, "cycles_ahead": int(k),
                             "n_scenes": int(last.n_scenes), "product_ids": list(last.product_ids), "source": "repeat_cycle"})
            k += 1
    cols = ["start_utc", "stop_utc", "mission", "orbit_rel", "pass_dir", "basis_start_utc", "cycles_ahead", "n_scenes",
            "product_ids", "source"]
    return pd.DataFrame(rows, columns=cols).sort_values("start_utc").reset_index(drop=True)


def attach_footprints(pred: pd.DataFrame, footprints: gpd.GeoDataFrame, aoi=None) -> gpd.GeoDataFrame:
    """Union of the basis pass's scene footprints per predicted pass, with its bbox and the AOI overlap (km2)."""
    fp = footprints.to_crs(CRS_GEO).set_index("product_id")
    geoms, overlap = [], []
    for ids in pred.product_ids:
        parts = [fp.geometry[i] for i in ids if i in fp.index]
        geom = unary_union(parts) if parts else None
        geoms.append(geom)
        if aoi is not None and geom is not None:
            inter = geom.intersection(aoi)
            overlap.append(round(float(gpd.GeoSeries([inter], crs=CRS_GEO).to_crs("EPSG:6933").area.iloc[0] / 1e6), 1))
        else:
            overlap.append(np.nan)
    g = gpd.GeoDataFrame(pred.copy(), geometry=geoms, crs=CRS_GEO)
    g["aoi_overlap_km2"] = overlap
    b = g.geometry.bounds
    for c in ("minx", "miny", "maxx", "maxy"):
        g[c] = b[c].round(4)
    g["product_ids"] = g.product_ids.map(lambda ids: ";".join(ids))
    return g


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def parse_acquisition_kml(path: str | Path) -> gpd.GeoDataFrame:
    """Placemarks of an ESA Sentinel-1 acquisition plan KML -> GeoDataFrame (begin_utc, end_utc, ExtendedData, polygon).

    Field names in the KML (Mode, OrbitAbsolute, OrbitRelative, Polarisation, Swath, ...) are kept as lower-case
    columns; a column is absent when the file has no such field.
    """
    tree = ET.parse(str(path))
    rows = []
    for pm in tree.iter():
        if _strip_ns(pm.tag) != "Placemark":
            continue
        rec = {"name": None, "begin_utc": None, "end_utc": None}
        coords = None
        for el in pm.iter():
            tag = _strip_ns(el.tag)
            if tag == "name" and rec["name"] is None:
                rec["name"] = (el.text or "").strip()
            elif tag == "begin":
                rec["begin_utc"] = pd.to_datetime((el.text or "").strip(), utc=True, errors="coerce")
            elif tag == "end":
                rec["end_utc"] = pd.to_datetime((el.text or "").strip(), utc=True, errors="coerce")
            elif tag == "Data":
                key = el.attrib.get("name", "").strip().lower()
                val = next((c.text for c in el if _strip_ns(c.tag) == "value"), None)
                if key:
                    rec[key] = (val or "").strip()
            elif tag == "SimpleData":
                key = el.attrib.get("name", "").strip().lower()
                if key:
                    rec[key] = (el.text or "").strip()
            elif tag == "coordinates" and coords is None:
                coords = el.text or ""
        pts = []
        for tok in re.split(r"\s+", coords.strip()) if coords else []:
            parts = tok.split(",")
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    pass
        rec["geometry"] = Polygon(pts) if len(pts) >= 3 else None
        rows.append(rec)
    g = gpd.GeoDataFrame(rows, geometry="geometry", crs=CRS_GEO)
    for c in ("orbitabsolute", "orbitrelative"):
        if c in g:
            g[c] = pd.to_numeric(g[c], errors="coerce").astype("Int64")
    return g


def plan_passes_over(plan: gpd.GeoDataFrame, aoi, start: pd.Timestamp, end: pd.Timestamp, mission: str) -> pd.DataFrame:
    """Planned segments that overlap the AOI polygon within [start, end], as rows like `predict_passes` (source 'esa_plan')."""
    if plan.empty:
        return pd.DataFrame(columns=["start_utc", "stop_utc", "mission", "orbit_rel", "pass_dir", "source", "mode", "geometry"])
    p = plan[plan.geometry.notna() & plan.begin_utc.notna()].copy()
    p = p[(p.end_utc >= start) & (p.begin_utc <= end)]
    p = p[p.intersects(aoi)]
    out = gpd.GeoDataFrame({"start_utc": p.begin_utc, "stop_utc": p.end_utc, "mission": mission,
                            "orbit_rel": p["orbitrelative"] if "orbitrelative" in p else pd.NA,
                            "pass_dir": p["pass"].str.upper() if "pass" in p else None, "source": "esa_plan",
                            "mode": p["mode"] if "mode" in p else None, "polarisation": p["polarisation"] if "polarisation" in p else None,
                            "plan_file": p["plan_file"] if "plan_file" in p else None},
                           geometry=p.geometry.values, crs=CRS_GEO)
    return out.sort_values("start_utc").reset_index(drop=True)


def aoi_bbox_polygon(bounds) -> Polygon:
    return box(*bounds)


# ---------------------------------------------------------------------------------------------------------------
# Plan files: windows, newest-wins selection, download
# ---------------------------------------------------------------------------------------------------------------

PLAN_NAME_RE = re.compile(r"(s1[a-d])_mp_user_(\d{8}t\d{6})_(\d{8}t\d{6})", re.IGNORECASE)
PLAN_BASE_URL = "https://sentinels.copernicus.eu"


def plan_file_window(name: str) -> tuple[str, pd.Timestamp, pd.Timestamp] | None:
    """('S1D', start, end) from a plan file name or URL; None when the name does not follow the ESA pattern."""
    m = PLAN_NAME_RE.search(str(name))
    if not m:
        return None
    t = lambda x: pd.Timestamp(pd.to_datetime(x.upper(), format="%Y%m%dT%H%M%S"), tz="UTC")  # noqa: E731
    return m.group(1).upper(), t(m.group(2)), t(m.group(3))


def select_plan_segments(plans: list[tuple[str, gpd.GeoDataFrame]]) -> gpd.GeoDataFrame:
    """Merge parsed plan files: per mission, the newest file (latest window start) wins inside its window.

    `plans` holds (file name, GeoDataFrame from `parse_acquisition_kml`). A segment of an older file is kept only when
    its begin time lies outside the windows of all newer files of the same mission. Adds columns plan_file and mission.
    """
    by_mission: dict[str, list] = {}
    for name, g in plans:
        w = plan_file_window(name)
        if w is None or g is None or g.empty:
            continue
        by_mission.setdefault(w[0], []).append((w[1], w[2], name, g))
    keep = []
    for mission, files in by_mission.items():
        covered: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        for start, end, name, g in sorted(files, key=lambda f: (f[0], f[2]), reverse=True):
            g = g.copy()
            g["plan_file"] = name
            g["mission"] = mission
            ok = g.begin_utc.notna()
            for c0, c1 in covered:
                ok &= ~((g.begin_utc >= c0) & (g.begin_utc <= c1))
            keep.append(g[ok])
            covered.append((start, end))
    if not keep:
        return gpd.GeoDataFrame(columns=["name", "begin_utc", "end_utc", "plan_file", "mission", "geometry"],
                                geometry="geometry", crs=CRS_GEO)
    out = pd.concat(keep, ignore_index=True)
    return gpd.GeoDataFrame(out, geometry="geometry", crs=CRS_GEO).sort_values("begin_utc").reset_index(drop=True)


def plan_links(html: str, base: str = PLAN_BASE_URL) -> list[str]:
    """Absolute URLs of the Sentinel-1 plan documents linked from the acquisition-plan page (any mission)."""
    out = []
    for href in re.findall(r'href="([^"]+)"', html):
        if not PLAN_NAME_RE.search(href):
            continue
        href = href.split("?")[0]
        if href.startswith("https://documents/"):  # a malformed absolute link seen on the page on 2026-10-08
            href = "/" + href[len("https://"):]
        url = href if href.startswith("http") else base + (href if href.startswith("/") else "/" + href)
        if url not in out:
            out.append(url)
    return out


def files_needed(urls: list[str], start: pd.Timestamp, end: pd.Timestamp, missions=("S1C", "S1D")) -> list[str]:
    """The plan files that `select_plan_segments` needs for [start, end]: per mission, newest first, until the newer
    windows cover the whole interval (at most 6 files per mission)."""
    need = []
    for mission in missions:
        files = []
        for u in urls:
            w = plan_file_window(u)
            if w and w[0] == mission and w[2] >= start and w[1] <= end:
                files.append((w[1], w[2], u))
        files.sort(reverse=True)
        windows = []
        for f_start, f_end, u in files[:6]:
            need.append(u)
            windows.append((f_start, f_end))
            if _covers(windows, start, end):
                break
    return need


def _covers(windows, start, end) -> bool:
    """Does the union of (a, b) windows cover [start, end]?"""
    t = start
    for a, b in sorted(windows):
        if a > t:
            return False
        t = max(t, b)
        if t >= end:
            return True
    return t >= end


def fetch_plan(dest: Path, start: pd.Timestamp, end: pd.Timestamp, *, page: str = ACQ_PLAN_PAGE, session=None,
               log=print, timeout: float = 120.0) -> list[dict]:
    """Download the plan KMLs that cover [start, end] into `dest` (skipping files already cached).

    Returns one dict per needed file: file, url, status (cached, downloaded, failed), bytes, fetched_utc.
    """
    import requests

    sess = session or requests.Session()
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    r = sess.get(page, timeout=timeout)
    r.raise_for_status()
    urls = plan_links(r.text)
    accessed = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for url in files_needed(urls, start, end):
        name = PLAN_NAME_RE.search(url).group(0).lower() + ".kml"
        path = dest / name
        rec = {"file": name, "url": url}
        if path.exists() and path.stat().st_size > 1000:
            rec.update(status="cached", bytes=path.stat().st_size,
                       fetched_utc=pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"))
            out.append(rec)
            continue
        try:
            resp = sess.get(url, timeout=timeout)
            resp.raise_for_status()
            if b"<kml" not in resp.content[:2000].lower():
                raise ValueError("response is not a KML document")
            tmp = path.with_name(path.name + ".part")
            tmp.write_bytes(resp.content)
            os.replace(tmp, path)
            rec.update(status="downloaded", bytes=len(resp.content), fetched_utc=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"))
            log(f"plan: downloaded {name} ({len(resp.content) / 1e6:.1f} MB)")
        except Exception as exc:
            rec.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:200])
            log(f"plan: could not download {url}: {rec['error']}")
        out.append(rec)
    write_json_atomic(dest / "fetch_log.json", {"page": page, "page_accessed_utc": accessed, "links_on_page": len(urls),
                                                "window_utc": [start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")],
                                                "files": out})
    return out


# ---------------------------------------------------------------------------------------------------------------
# Pass groups, areas, JSON
# ---------------------------------------------------------------------------------------------------------------

def group_passes(df: pd.DataFrame, gap_min: float = 10.0) -> pd.Series:
    """`pass_group` id per row: rows of one mission whose time spans overlap or lie within `gap_min` of each other are
    one physical pass (one satellite flies one pass at a time; consecutive passes over the AOI are about 100 min
    apart). Plan segments of one data take and the repeat prediction of the same pass therefore share an id, even when
    the relative orbit number changes inside the take (it increments at the ascending equator crossing). The id is
    mission, relative orbit and start of the group's first row: 'S1D_R164_20261008T2258'."""
    out = pd.Series(index=df.index, dtype=object)
    starts = pd.to_datetime(df.start_utc, utc=True)
    stops = pd.to_datetime(df.stop_utc, utc=True)
    orbit = pd.to_numeric(df.orbit_rel, errors="coerce") if "orbit_rel" in df else pd.Series(np.nan, index=df.index)
    tol = pd.Timedelta(minutes=gap_min)
    for mission, idx in df.groupby("mission", sort=False).groups.items():
        order = starts[idx].sort_values(kind="stable").index
        gid, g_stop = None, None
        for i in order:
            if gid is None or starts[i] > g_stop + tol:
                rel = f"R{int(orbit[i]):03d}" if pd.notna(orbit[i]) else "R___"
                gid, g_stop = f"{mission}_{rel}_{starts[i]:%Y%m%dT%H%M}", stops[i]
            g_stop = max(g_stop, stops[i])
            out[i] = gid
    return out


def area_names(geom, parts: gpd.GeoDataFrame, name_col: str = "name", min_km2: float = 100.0) -> list[str]:
    """Names of the AOI parts (e.g. Natural Earth marine areas) that `geom` overlaps by at least `min_km2`."""
    if geom is None or geom.is_empty or parts is None or parts.empty:
        return []
    hits = []
    for nm, pg in zip(parts[name_col], parts.geometry):
        inter = geom.intersection(pg)
        if inter.is_empty:
            continue
        km2 = gpd.GeoSeries([inter], crs=CRS_GEO).to_crs("EPSG:6933").area.iloc[0] / 1e6
        if km2 >= min_km2:
            hits.append((km2, str(nm)))
    return [nm for _, nm in sorted(hits, reverse=True)]


def json_clean(obj):
    """Recursively replace NaN, NaT and pd.NA with None and numpy scalars with Python ones (valid JSON for any reader)."""
    if isinstance(obj, dict):
        return {k: json_clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_clean(v) for v in obj]
    if obj is None or obj is pd.NA or obj is pd.NaT:
        return None
    if isinstance(obj, (np.floating, float)):
        return None if not math.isfinite(float(obj)) else float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.strftime("%Y-%m-%dT%H:%M:%SZ") if obj.tzinfo is not None else obj.isoformat()
    return obj


def write_json_atomic(path: Path, obj) -> None:
    """Write `obj` as JSON through a temp file in the same directory and a rename (readers never see half a file)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    try:
        tmp.write_text(json.dumps(json_clean(obj), indent=1, allow_nan=False, default=str))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
