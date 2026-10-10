"""Evidence joins for leads: cells and reporting boxes, weather and sea context, lights and AIS events within 2 km and
3 h, persistence pairs across passes, the next planned Sentinel-1 look, titles and evidence lists.

Distances are computed in UTM 49N (EPSG:32649) with KD-trees; times in UTC. Every function is pure and works on
synthetic frames (tests/test_leads.py).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pyproj
from scipy.spatial import cKDTree

from darkvessel.config import CRS_GEO, CRS_UTM_REGIONAL
from darkvessel.leads import rules as R
from darkvessel.ocean.grid import cell_index, model_grid, region_of

_TO_UTM = pyproj.Transformer.from_crs(CRS_GEO, CRS_UTM_REGIONAL, always_xy=True)


def to_utm(lon, lat) -> np.ndarray:
    x, y = _TO_UTM.transform(np.asarray(lon, float), np.asarray(lat, float))
    return np.c_[x, y]


def utc(series) -> pd.Series:
    """Parse a time column to tz-aware UTC timestamps (NaT where missing)."""
    return pd.to_datetime(series, utc=True, errors="coerce")


def utc64(series) -> np.ndarray:
    """UTC times as a numpy datetime64[ns] array (NaT where missing); tz-aware pandas series give object arrays otherwise."""
    return utc(pd.Series(series)).dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")


def iso_z(ts) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def cell_ids(lon, lat) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(cell_id, row, col) on the 0.25 degree model grid; cell_id is 'r<row>c<col>' or None outside the grid."""
    transform, shape = model_grid()
    row, col, inside = cell_index(transform, shape, lon, lat)
    ids = np.array([f"r{r}c{c}" if ok else None for r, c, ok in zip(row, col, inside)], dtype=object)
    return ids, row, col


def cell_centres(row, col) -> tuple[np.ndarray, np.ndarray]:
    transform, _ = model_grid()
    lon = transform.c + (np.asarray(col) + 0.5) * transform.a
    lat = transform.f + (np.asarray(row) + 0.5) * transform.e
    return lon, lat


def join_weather(df: pd.DataFrame, weather: pd.DataFrame | None) -> pd.DataFrame:
    """Left join of wind_ms, ctt_k and deep_convection on det_id (regional objects only; live has none yet), plus
    weather_row: True when the contact has a row in the weather sample (its values may still be missing)."""
    out = df.copy()
    for c in ("wind_ms", "ctt_k", "deep_convection", "weather_row"):
        if c in out.columns:
            out = out.drop(columns=c)
    if weather is None or len(weather) == 0:
        out["wind_ms"] = np.nan
        out["ctt_k"] = np.nan
        out["deep_convection"] = pd.Series([None] * len(out), index=out.index, dtype=object)
        out["weather_row"] = False
        return out
    w = weather[["det_id", "wind_ms", "ctt_k", "deep_convection"]].drop_duplicates("det_id").assign(weather_row=True)
    out = out.merge(w, on="det_id", how="left")
    out["deep_convection"] = out["deep_convection"].astype(object).where(out["deep_convection"].notna(), None)
    out["weather_row"] = out["weather_row"].astype("boolean").fillna(False).astype(bool)
    return out


def join_static(df: pd.DataFrame, static: pd.DataFrame | None) -> pd.DataFrame:
    """Sea context of the contact's cell (location context only): depth_mean_m, dist_coast_km, dist_port_km."""
    out = df.copy()
    cols = ["depth_mean_m", "dist_coast_km", "dist_port_km"]
    if static is None or len(static) == 0 or "row" not in out.columns:
        for c in cols:
            out[c] = np.nan
        return out
    s = static[["row", "col"] + [c for c in cols if c in static.columns]].drop_duplicates(["row", "col"])
    out = out.merge(s, on=["row", "col"], how="left")
    for c in cols:
        if c not in out.columns:
            out[c] = np.nan
    return out


def _within(points_xy: np.ndarray, times: np.ndarray, other_xy: np.ndarray, other_t0: np.ndarray, other_t1: np.ndarray,
            km: float, hours: float) -> list[list[int]]:
    """Indices of `other` rows within `km` of each point whose [t0 - hours, t1 + hours] span contains the point time."""
    if len(other_xy) == 0 or len(points_xy) == 0:
        return [[] for _ in range(len(points_xy))]
    tree = cKDTree(other_xy)
    hits = tree.query_ball_point(points_xy, r=km * 1000.0)
    pad = np.timedelta64(int(hours * 3600), "s")
    out = []
    for i, lst in enumerate(hits):
        if not lst:
            out.append([])
            continue
        idx = np.asarray(lst)
        t = times[i]
        ok = (other_t0[idx] - pad <= t) & (t <= other_t1[idx] + pad)
        out.append(sorted(int(j) for j in idx[ok]))
    return out


def lights_near(contacts: pd.DataFrame, lights: pd.DataFrame | None, km: float = R.CORROBORATION_KM,
                hours: float = R.CORROBORATION_H) -> list[list[str]]:
    """light_ids within `km` and `hours` of each contact (any VIIRS quality)."""
    if lights is None or len(lights) == 0 or len(contacts) == 0:
        return [[] for _ in range(len(contacts))]
    lt = utc64(lights["time_utc"])
    hits = _within(to_utm(contacts.lon, contacts.lat), utc64(contacts.acq_utc), to_utm(lights.lon, lights.lat),
                   lt, lt, km, hours)
    ids = lights["light_id"].astype(str).to_numpy()
    return [[ids[j] for j in h] for h in hits]


def events_near(contacts: pd.DataFrame, events: pd.DataFrame | None, km: float = R.CORROBORATION_KM,
                hours: float = R.CORROBORATION_H) -> list[list[str]]:
    """event_ids of AIS behaviour events (columns event_id, lon, lat, start, end) within `km` whose span, padded by
    `hours`, contains the contact time."""
    if events is None or len(events) == 0 or len(contacts) == 0:
        return [[] for _ in range(len(contacts))]
    t0 = utc64(events["start"])
    t1 = utc64(events["end"])
    hits = _within(to_utm(contacts.lon, contacts.lat), utc64(contacts.acq_utc), to_utm(events.lon, events.lat),
                   t0, t1, km, hours)
    ids = events["event_id"].astype(str).to_numpy()
    return [[ids[j] for j in h] for h in hits]


def persistence_pairs(leads: pd.DataFrame, unmatched: pd.DataFrame, km: float = R.PERSISTENCE_KM,
                      hours: float = R.PERSISTENCE_H, min_gap_s: float = R.PERSISTENCE_MIN_GAP_S) -> list[list[str]]:
    """det_ids of unmatched contacts within `km` of each lead contact, on another pass (time gap over `min_gap_s`
    and a different pass_id where known) within `hours`. `unmatched` needs det_id, lon, lat, acq_utc (pass_id optional)."""
    if len(leads) == 0 or unmatched is None or len(unmatched) == 0:
        return [[] for _ in range(len(leads))]
    tree = cKDTree(to_utm(unmatched.lon, unmatched.lat))
    hits = tree.query_ball_point(to_utm(leads.lon, leads.lat), r=km * 1000.0)
    ut = utc64(unmatched["acq_utc"])
    lt = utc64(leads["acq_utc"])
    uid = unmatched["det_id"].astype(str).to_numpy()
    lid = leads["det_id"].astype(str).to_numpy()
    upass = unmatched["pass_id"].astype(object).to_numpy() if "pass_id" in unmatched.columns else np.array([None] * len(unmatched), dtype=object)
    lpass = leads["pass_id"].astype(object).to_numpy() if "pass_id" in leads.columns else np.array([None] * len(leads), dtype=object)
    out = []
    for i, lst in enumerate(hits):
        if not lst:
            out.append([])
            continue
        idx = np.asarray(lst)
        dt = np.abs((ut[idx] - lt[i]) / np.timedelta64(1, "s")).astype(float)
        ok = (dt <= hours * 3600.0) & (dt > min_gap_s) & (uid[idx] != lid[i])
        if lpass[i] is not None:
            ok &= np.array([p != lpass[i] for p in upass[idx]])
        out.append(sorted(uid[idx[ok]].tolist()))
    return out


def passes_frame(plan: dict | None, footprints=None) -> "pd.DataFrame":
    """Planned passes as a GeoDataFrame (geometry in EPSG:4326): footprint polygons from `footprints` (the
    s1_next_passes_4326 layer) joined on pass_group and start_utc, else the plan's footprint_bbox."""
    import geopandas as gpd
    from shapely.geometry import box

    if plan is None or not plan.get("passes"):
        return gpd.GeoDataFrame({"pass_group": [], "start_utc": [], "source": [], "status": []}, geometry=[], crs=CRS_GEO)
    p = pd.DataFrame(plan["passes"]).copy()
    geoms = [None] * len(p)
    if footprints is not None and len(footprints):
        key = footprints.assign(_k=footprints.pass_group.astype(str) + "|" + footprints.start_utc.astype(str))
        lookup = dict(zip(key._k, key.geometry))
        geoms = [lookup.get(f"{g}|{s}") for g, s in zip(p.pass_group.astype(str), p.start_utc.astype(str))]
    for i, g in enumerate(geoms):
        if g is None:
            bb = p.footprint_bbox.iloc[i] if "footprint_bbox" in p.columns else None
            if isinstance(bb, str):
                bb = json.loads(bb)
            geoms[i] = box(*bb) if bb is not None and len(bb) == 4 else None
    out = gpd.GeoDataFrame(p, geometry=geoms, crs=CRS_GEO)
    out = out[out.geometry.notna()].copy()
    out["_t"] = utc(out["start_utc"])
    return out.sort_values(["_t", "pass_group"]).reset_index(drop=True)


def next_look(lon, lat, times, passes, not_before=None) -> tuple[list, list, list]:
    """For each point and time, the first planned pass starting after max(time, not_before) whose footprint contains
    the point: (next_look_utc ISO, pass_group, source). None where no planned pass covers the point. `not_before` is
    the plan's generated_utc (the plan window starts 72 h earlier, so its first passes are already past); passes with
    status 'past' are skipped as well. Using the plan time, not the wall clock, keeps reruns byte-identical."""
    from shapely import STRtree
    from shapely.geometry import Point

    lon = np.asarray(lon, float)
    lat = np.asarray(lat, float)
    t = utc64(times)
    n = len(lon)
    if passes is None or len(passes) == 0 or n == 0:
        return [None] * n, [None] * n, [None] * n
    tree = STRtree(passes.geometry.to_numpy())
    pts = [Point(x, y) for x, y in zip(lon, lat)]
    q = tree.query(pts, predicate="within")   # (2, k): point index, pass index
    pt_idx, ps_idx = q[0], q[1]
    ptimes = utc64(passes["_t"])
    usable = np.ones(len(passes), bool)
    if "status" in passes.columns:
        usable &= passes["status"].astype(str).to_numpy() != "past"
    if not_before is not None and not pd.isna(pd.Timestamp(not_before)):
        usable &= ptimes > utc64([not_before])[0]
    groups = passes["pass_group"].astype(str).to_numpy()
    sources = passes["source"].astype(str).to_numpy() if "source" in passes.columns else np.array(["unknown"] * len(passes))
    order = np.argsort(pt_idx, kind="stable")
    pt_idx, ps_idx = pt_idx[order], ps_idx[order]
    when, grp, src = [None] * n, [None] * n, [None] * n
    bounds = np.searchsorted(pt_idx, np.arange(n + 1))
    for i in range(n):
        cand = ps_idx[bounds[i]:bounds[i + 1]]
        cand = cand[usable[cand]]
        if len(cand) == 0:
            continue
        later = cand[ptimes[cand] > t[i]] if not pd.isna(t[i]) else cand
        if len(later) == 0:
            continue
        j = later[np.argmin(ptimes[later])]
        when[i], grp[i], src[i] = iso_z(pd.Timestamp(ptimes[j])), groups[j], sources[j]
    return when, grp, src


def l1_title(length_m, region: str) -> str:
    L = f"{float(length_m):.0f} m" if length_m is not None and np.isfinite(float(length_m)) else "length unknown"
    where = region if region and region != "other" else "outside the reporting boxes"
    return f"{R.LEAD_NAMES['L1']}, {L}, {where}"


def l7_title(n_lights: int, n_nights: int, region: str) -> str:
    where = region if region and region != "other" else "outside the reporting boxes"
    return (f"{R.LEAD_NAMES['L7']}, {int(n_lights)} clear-sky light{'s' if n_lights != 1 else ''} on {int(n_nights)} "
            f"night{'s' if n_nights != 1 else ''}, {where} (coverage lead, not a vessel lead)")


def l1_evidence(row: pd.Series, lights: list[str], events: list[str], persist: list[str], max_persist: int = 5) -> list[dict]:
    """Evidence list of an L1 lead: primary contact, pass, nearest AIS vessel, lights, AIS events, persistence
    contacts (capped), weather sample, cell."""
    ev = [{"type": "contact", "id": str(row["det_id"]), "role": "primary"}]
    if row.get("pass_id") not in (None, "") and row.get("pass_id") == row.get("pass_id"):
        ev.append({"type": "pass", "id": str(row["pass_id"]), "role": "pass"})
    key = row.get("nearest_ais_key")
    if key:
        ev.append({"type": "vessel", "id": str(key), "role": "nearest_ais"})
    ev += [{"type": "light", "id": l, "role": "same_night_light"} for l in lights]
    ev += [{"type": "event", "id": e, "role": "ais_behaviour"} for e in events]
    ev += [{"type": "contact", "id": d, "role": "persistence"} for d in persist[:max_persist]]
    if bool(row.get("weather_row")):
        ev.append({"type": "weather", "id": str(row["det_id"]), "role": "weather"})
    if row.get("cell_id"):
        ev.append({"type": "cell", "id": str(row["cell_id"]), "role": "cell"})
    return ev


def l7_evidence(cell_id: str, light_ids: list[str], site_ids: list[str], max_lights: int = R.L7_EVIDENCE_LIGHTS_MAX) -> list[dict]:
    """Evidence list of an L7 lead: the cell, the first `max_lights` lights (callers pass them brightest first; the
    lead's n_lights holds the full count) and every recurring light site within 500 m of one of its lights."""
    ev = [{"type": "cell", "id": cell_id, "role": "primary"}]
    ev += [{"type": "light", "id": l, "role": "light"} for l in light_ids[:max_lights]]
    ev += [{"type": "light_site", "id": s, "role": "recurring_site"} for s in site_ids]
    return ev


def nearest_ais_key(row: pd.Series) -> str | None:
    """Vessel key of the nearest AIS vessel: the research identity's vessel id with its prefix, or mmsi:<mmsi>."""
    vid = row.get("nearest_ais_vessel_id")
    if vid is not None and vid == vid and str(vid) not in ("", "None", "nan"):
        return f"gfw:{vid}"
    m = row.get("nearest_ais_mmsi")
    if m is None or m != m or str(m) in ("", "None", "nan", "<NA>"):
        return None
    try:
        return f"mmsi:{int(float(m))}"
    except (TypeError, ValueError):
        return f"mmsi:{m}"


def regions(lon, lat) -> np.ndarray:
    return region_of(lon, lat)
