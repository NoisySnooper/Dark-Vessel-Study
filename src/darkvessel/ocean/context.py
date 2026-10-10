"""Ocean context at every object: the sea at the position and time of each radar object and VIIRS light.

Objects (one row each, keyed like data/weather_context.parquet by the object's own id):
  radar          regional Sentinel-1C/1D objects: vessel candidates (high, medium), fixed structures and the two
                 clutter-flagged classes (clutter_zone, near_fixed), from data/detections_regional_all.gpkg when it
                 exists, else the committed candidates in data/detections_regional.gpkg
  radar_detail   the Ca Mau detail scene, every class including low (data/detections_baseline.gpkg)
  viirs          every VIIRS light at sea (data/viirs_lights_all.gpkg, else the lean data/viirs_lights.gpkg); the
                 group splits lit vessel candidates by cloud quality and keeps recurring lights apart

Keys: cell_id (r<row>c<col> on the 0.25 degree model grid, the Cell record of app/CONTRACT.md 3.7) and region (the
reporting box of the object, not a boundary). EEZ attributes are not object context: the product reads them from the
Cell record, under its separate eez object, only while the EEZ layer is on.
Static fields are the containing 0.01 degree cell of the COGs in data/outputs/small/ (scripts/22_static_layers.py):
depth_m, dist_coast_km, dist_port_km, and ship_presence_<type> by PRESENCE_RULE. The World Bank/IMF shipping rasters
are used for presence only (value above 0): many published values cannot be counts (docs/ocean_context.md 3.4), so
no magnitude, rank, percentile or lane threshold is read from them (board decision D4.3).
Daily fields come from the per-day caches of scripts/23_daily_ocean.py through darkvessel.ocean.daily and fronts:
SST and its gradient on the object's UTC date, the distance to the nearest front pixel of that day, chlorophyll, the
RTOFS current speed and mixed-layer depth of the nearest hourly nowcast within MAX_HOURS_RTOFS (nearest RTOFS sea cell
within RTOFS_MAX_KM), and the GFS-Wave significant wave height of the nearest valid hour within MAX_HOURS_WAVE
(bilinear, NaN neighbours dropped, as in the daily cell table). Every daily field carries the valid time of the source
it was read from, so the user can see how far the sea state is from the observation.

Research only (Global Fishing Watch, CC BY-NC 4.0): gfw_* functions compare GFW's Sentinel-1 vessel detections, which
carry a neural-network fishing score and an AIS match flag, with this project's radar classes. They run on a table the
owner supplies (Data Download Portal export, or a 4Wings report pulled with a token from the environment) and write
under data/research/, never into product files.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

from darkvessel.config import DATA_DIR
from darkvessel.ocean.grid import NODATA, OCEAN_CACHE, OCEAN_CAVEAT, SMALL_DIR, bilinear, cell_index, region_of, sample

CONTEXT_CAVEAT = (OCEAN_CAVEAT + " The context fields describe the sea at an object's position and time, not what the "
                  "object is or does; a radar candidate can be clutter and a light can be a platform.")

STATIC_LAYERS = ("depth_m", "dist_coast_km", "dist_port_km")
# Presence field -> COG name of scripts/22_static_layers.py (the files keep the published values; only > 0 is read)
SHIP_TYPES = ("all", "commercial", "fishing", "oilgas", "passenger", "leisure")
PRESENCE_FIELDS = {f"ship_presence_{t}": f"ship_density_{t}" for t in SHIP_TYPES}
PRESENCE_RULE = ("ship_presence_<type> is True when the World Bank/IMF Global Shipping Traffic Density layer of that "
                 "vessel type holds a value above 0 in the object's 0.01 degree cell (the sum of the four 0.005 degree "
                 "source cells), January 2015 to February 2021; False when it holds 0; null on land, outside the rasters "
                 "or when the layer is missing. Only presence is read. Many published values cannot be counts of AIS "
                 "positions (docs/ocean_context.md 3.4), so no magnitude, rank, percentile, lane or busy-water threshold "
                 "is derived from them. Presence assumes that a 0 means no AIS record of that type in that period; it "
                 "says that AIS traffic of that type touched the water, nothing about the object.")
DAILY_FIELDS = ("sst_c", "sst_grad", "dist_front_km", "chl_log10", "current_speed_ms", "mld_m", "wave_hs_m")
FRONT_NEAR_KM = 10.0
MAX_HOURS_RTOFS = 2.0   # RTOFS diagnostic nowcasts are hourly (n000 to n023 per folder); --fetch pulls the missing hours
MAX_HOURS_WAVE = 3.0    # GFS-Wave: 6-hourly cycles, hourly steps f000 to f005 as used by darkvessel.weather._nearest_gfs
RTOFS_MAX_KM = 10.0     # nearest RTOFS sea cell (1/12 degree, about 9 km) within this distance, else no value
R_EARTH_KM = 6371.0088
CONTEXT_CACHE = OCEAN_CACHE / "context"

# What is compared in the summary: object class labels, by object type.
RADAR_CANDIDATE_GROUPS = ("high", "medium")
RADAR_CLUTTER_GROUPS = ("fixed", "clutter_zone", "near_fixed", "weak_vv_only", "oversized")


# ----------------------------------------------------------------------------------------------------------------------
# Objects

def _utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, format="ISO8601")


def _group_from_classes(confidence: pd.Series, low_reason: pd.Series | None) -> pd.Series:
    """Class label: the confidence, or the low reason for low objects (clutter_zone, near_fixed, weak_vv_only, oversized)."""
    conf = confidence.astype(str)
    if low_reason is None:
        return conf
    reason = low_reason.fillna("").astype(str)
    return pd.Series(np.where((conf == "low") & (reason != ""), reason, conf), index=confidence.index)


def load_radar_objects(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """Regional radar objects: candidates plus fixed and clutter-flagged classes when the full file exists."""
    import pyogrio

    full = data_dir / "detections_regional_all.gpkg"
    cols = ["det_id", "acq_utc", "confidence", "lat", "lon", "length_est_m", "mission"]
    if full.exists():
        d = pyogrio.read_dataframe(full, layer="detections_regional_4326", read_geometry=False, columns=cols + ["low_reason"],
                                   where="confidence IN ('high', 'medium', 'fixed') OR low_reason IN ('clutter_zone', 'near_fixed')")
        group = _group_from_classes(d.confidence, d.low_reason)
        source = full.name
    else:
        d = pyogrio.read_dataframe(data_dir / "detections_regional.gpkg", layer="detections_regional_4326", read_geometry=False, columns=cols)
        group = _group_from_classes(d.confidence, None)
        source = "detections_regional.gpkg"
    out = pd.DataFrame({"object_type": "radar", "object_id": d.det_id.astype(str), "group": group.values,
                        "time_utc": _utc(d.acq_utc), "lat": d.lat.astype(float), "lon": d.lon.astype(float),
                        "length_est_m": d.length_est_m.astype(float), "mission": d.mission.astype(str)})
    out.attrs["source"] = source
    return out


def load_radar_detail_objects(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """Ca Mau detail scene: every class, including low (weak_vv_only, oversized)."""
    import pyogrio

    path = data_dir / "detections_baseline.gpkg"
    d = pyogrio.read_dataframe(path, layer="detections_baseline_4326", read_geometry=False,
                               columns=["det_id", "acq_utc", "confidence", "low_reason", "lat", "lon", "length_est_m", "scene_id"])
    mission = d.scene_id.astype(str).str[:3]
    out = pd.DataFrame({"object_type": "radar_detail", "object_id": d.det_id.astype(str),
                        "group": _group_from_classes(d.confidence, d.low_reason).values, "time_utc": _utc(d.acq_utc),
                        "lat": d.lat.astype(float), "lon": d.lon.astype(float), "length_est_m": d.length_est_m.astype(float),
                        "mission": mission.values})
    out.attrs["source"] = path.name
    return out


def load_viirs_objects(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """VIIRS lights at sea. Group: lit_vessel_candidate_clear, lit_vessel_candidate_under_cloud, persistent_light."""
    import pyogrio

    path = data_dir / "viirs_lights_all.gpkg"
    if not path.exists():
        path = data_dir / "viirs_lights.gpkg"
    d = pyogrio.read_dataframe(path, layer="viirs_lights_4326", read_geometry=False,
                               columns=["light_id", "time_utc", "night", "lat", "lon", "class", "quality", "satellite"])
    cls = d["class"].astype(str)
    group = np.where(cls == "lit_vessel_candidate", "lit_vessel_candidate_" + d.quality.astype(str), cls)
    out = pd.DataFrame({"object_type": "viirs", "object_id": d.light_id.astype(str), "group": group,
                        "time_utc": _utc(d.time_utc), "lat": d.lat.astype(float), "lon": d.lon.astype(float),
                        "length_est_m": np.nan, "mission": d.satellite.astype(str)})
    out.attrs["source"] = path.name
    return out


def load_objects(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """All objects in one table with object_type, object_id, group, time_utc, lat, lon, length_est_m, mission, day."""
    parts = [load_radar_objects(data_dir), load_radar_detail_objects(data_dir), load_viirs_objects(data_dir)]
    sources = {p.object_type.iloc[0]: p.attrs.get("source") for p in parts if len(p)}
    obj = pd.concat(parts, ignore_index=True)
    obj["day"] = obj.time_utc.dt.strftime("%Y-%m-%d")
    obj.attrs["sources"] = sources
    return obj


def cell_keys(lon, lat, transform=None, shape=None) -> pd.DataFrame:
    """cell_id (r<row>c<col> on the 0.25 degree model grid, None outside it) and region (reporting box) of each point."""
    if transform is None:
        from darkvessel.ocean.grid import model_grid

        transform, shape = model_grid()
    row, col, inside = cell_index(transform, shape, lon, lat)
    cid = [f"r{r}c{c}" if ok else None for r, c, ok in zip(row.tolist(), col.tolist(), inside.tolist())]
    return pd.DataFrame({"cell_id": pd.array(cid, dtype="string"), "region": region_of(lon, lat)})


# ----------------------------------------------------------------------------------------------------------------------
# Static layers

def read_cog(path: Path):
    """(array float32 with NaN for nodata, transform, tags) of a single-band COG."""
    import rasterio

    with rasterio.open(path) as ds:
        a = ds.read(1).astype(np.float32)
        nd = ds.nodata if ds.nodata is not None else NODATA
        a[a == nd] = np.nan
        return a, ds.transform, dict(ds.tags())


def presence(values) -> pd.arrays.BooleanArray:
    """PRESENCE_RULE on sampled raster values: True above 0, False at 0, NA where the value is unknown (NaN)."""
    v = np.asarray(values, float)
    return pd.array(np.where(np.isfinite(v), v > 0, None), dtype="boolean")


_TAG_KEYS = ("source", "version", "licence", "licence_url", "access_date", "units", "method", "gebco_file_date_created",
             "vessel_types")


def static_context(obj: pd.DataFrame, small_dir: Path = SMALL_DIR, layers=STATIC_LAYERS,
                   presence_fields: dict[str, str] | None = None):
    """(DataFrame of static fields per object row, metadata dict) from the EPSG:4326 COGs in `small_dir`.

    Fields: the numeric layers, ship_presence_<type> (PRESENCE_RULE; NA where the value is unknown) and in_aoi_grid
    (False for an object outside the rasters). The shipping values are turned into presence as soon as they are
    sampled; no magnitude leaves this function. The metadata holds each layer's version, licence and access date from
    the COG tags (the shipping tags keep their own text, which describes the published values, not the presence).
    """
    presence_fields = PRESENCE_FIELDS if presence_fields is None else presence_fields
    out = pd.DataFrame(index=obj.index)
    meta = {"layers": {}, "presence_rule": PRESENCE_RULE}
    lon, lat = obj.lon.to_numpy(float), obj.lat.to_numpy(float)
    inside_any = np.zeros(len(obj), bool)
    seen_grid = False

    def _read(cog):
        path = small_dir / f"{cog}_4326.tif"
        if not path.exists():
            return None, {"file": path.name, "present": False}
        a, tr, tags = read_cog(path)
        return (a, tr), {"file": path.name, "present": True, "shape": list(a.shape), **{k: tags[k] for k in _TAG_KEYS if tags.get(k)}}

    for name in layers:
        got, info = _read(name)
        meta["layers"][name] = info
        if got is None:
            out[name] = np.float32(np.nan)
            continue
        a, tr = got
        out[name] = sample(a, tr, lon, lat).astype(np.float32)
        inside_any |= cell_index(tr, a.shape, lon, lat)[2]
        seen_grid = True
    for field, cog in presence_fields.items():
        got, info = _read(cog)
        meta["layers"][field] = {**info, "read_as": "presence (value > 0) only"}
        if got is None:
            out[field] = pd.array([None] * len(obj), dtype="boolean")
            continue
        a, tr = got
        out[field] = presence(sample(a, tr, lon, lat))
        inside_any |= cell_index(tr, a.shape, lon, lat)[2]
        seen_grid = True
        del a
    out["in_aoi_grid"] = inside_any if seen_grid else False
    return out, meta


# ----------------------------------------------------------------------------------------------------------------------
# Daily layers

def nearest_time(t: pd.Timestamp, available: list[pd.Timestamp], max_hours: float):
    """The available time closest to t within max_hours, else None."""
    if not available:
        return None
    arr = pd.DatetimeIndex(available)
    i = int(np.argmin(np.abs((arr - t).total_seconds())))
    return arr[i] if abs((arr[i] - t).total_seconds()) <= max_hours * 3600 else None


def cached_times(folder: Path, pattern: str, fmt: str) -> list[pd.Timestamp]:
    """Valid times encoded in cache file names, e.g. rtofs_20260929T18.npz with pattern r'rtofs_(\\d{8}T\\d{2})\\.npz'."""
    out = []
    for p in sorted(Path(folder).glob("*")):
        m = re.fullmatch(pattern, p.name)
        if m:
            out.append(pd.Timestamp(dt.datetime.strptime(m.group(1), fmt), tz="UTC"))
    return out


def rtofs_cached_times() -> list[pd.Timestamp]:
    return cached_times(OCEAN_CACHE / "rtofs", r"rtofs_(\d{8}T\d{2})\.npz", "%Y%m%dT%H")


def wave_cached_valid_times() -> dict[pd.Timestamp, Path]:
    """{valid time: path} of the cached GFS-Wave HTSGW records (cycle plus forecast hour from the file name)."""
    out = {}
    for p in sorted((OCEAN_CACHE / "gfswave").glob("gfswave_*_f*_htsgw.grib2")):
        m = re.fullmatch(r"gfswave_(\d{10})_f(\d{3})_htsgw\.grib2", p.name)
        if m:
            cyc = pd.Timestamp(dt.datetime.strptime(m.group(1), "%Y%m%d%H"), tz="UTC")
            out[cyc + pd.Timedelta(hours=int(m.group(2)))] = p
    return out


def _unit(lon, lat) -> np.ndarray:
    lo, la = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    return np.c_[np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]


def nearest_valid_index(glon, glat, valid, lon, lat, max_km: float = RTOFS_MAX_KM) -> np.ndarray:
    """Index into the flattened grid of the nearest valid cell centre to each point, -1 beyond max_km (great circle)."""
    from scipy.spatial import cKDTree

    glon, glat, valid = np.ravel(glon), np.ravel(glat), np.ravel(valid)
    ok = valid & np.isfinite(glon) & np.isfinite(glat)
    out = np.full(np.size(lon), -1, np.int64)
    if not ok.any() or np.size(lon) == 0:
        return out
    idx_ok = np.nonzero(ok)[0]
    chord, j = cKDTree(_unit(glon[ok], glat[ok])).query(_unit(np.ravel(lon), np.ravel(lat)))
    km = 2.0 * R_EARTH_KM * np.arcsin(np.clip(chord / 2.0, 0.0, 1.0))
    near = km <= max_km
    out[near] = idx_ok[j[near]]
    return out


def _rtofs_index(lon, lat, sea):
    """Nearest RTOFS sea cell of the cached AOI window (flattened index, -1 beyond RTOFS_MAX_KM)."""
    from darkvessel.ocean import daily

    win = daily.rtofs_window(daily.rtofs_url(dt.datetime(2026, 9, 29, 18, tzinfo=dt.timezone.utc)))
    return nearest_valid_index(win["lon"], win["lat"], sea, lon, lat)


def _iso(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def daily_context(obj: pd.DataFrame, fetch: bool = False, log=print, loaders: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """(DataFrame of daily fields per object row, metadata) from the per-day ocean caches.

    `loaders` can replace the darkvessel.ocean.daily and fronts functions (tests); keys: sst_meta(day) -> dict with
    sst, valid_time, source, dataset; grad(day) -> (arr, tr), mask(day) -> (mask, tr, thresholds), sst_transform() ->
    tr, chl(day) -> (arr, tr, dataset) or None, rtofs_times() -> list, rtofs(t) -> dict with the native arrays,
    rtofs_index(lon, lat, sea) -> indices (-1 = no sea cell near), wave_times() -> {valid: path}, wave(path) -> (arr,
    tr, valid), fetch_rtofs(t), fetch_wave(t) -> path.
    With fetch=True missing RTOFS hours and wave files are fetched (anonymous NOAA buckets); else they stay NaN.
    """
    from darkvessel.ocean import daily, fronts

    L = {"sst_meta": daily.load_sst_meta, "sst_transform": lambda: daily.fine_grid()[0], "grad": fronts.load_grad,
         "mask": fronts.load_mask, "chl": daily.load_chl, "rtofs_times": rtofs_cached_times, "rtofs": daily.load_rtofs,
         "rtofs_index": _rtofs_index, "wave_times": wave_cached_valid_times, "wave": lambda p: daily.read_grib_field(p),
         "fetch_rtofs": daily.fetch_rtofs, "fetch_wave": daily.fetch_wave_hs}
    L.update(loaders or {})
    n = len(obj)
    out = pd.DataFrame(index=obj.index)
    for f in DAILY_FIELDS:
        out[f] = np.float32(np.nan)
    for f in ("sst_time", "sst_source", "chl_time", "chl_dataset", "current_time", "wave_time"):
        out[f] = pd.Series([None] * n, index=obj.index, dtype="object")
    meta = {"days": {}, "front_thresholds": {}, "rtofs_hours_used": [], "rtofs_hours_fetched": [], "wave_valid_times_used": [],
            "wave_files_fetched": [], "missing": []}
    lon_all, lat_all = obj.lon.to_numpy(float), obj.lat.to_numpy(float)

    # Per UTC date: SST, gradient, front distance, chlorophyll
    for day, g in obj.groupby("day", sort=True):
        idx = g.index
        lon, lat = g.lon.to_numpy(float), g.lat.to_numpy(float)
        rec = {}
        sst = tr = None
        try:
            m = L["sst_meta"](day)
            sst, tr = m["sst"], L["sst_transform"]()
            out.loc[idx, "sst_c"] = sample(sst, tr, lon, lat).astype(np.float32)
            out.loc[idx, "sst_time"] = str(m.get("valid_time"))
            out.loc[idx, "sst_source"] = str(m.get("source"))
            rec.update(sst_source=str(m.get("source")), sst_valid_time=str(m.get("valid_time")), sst_dataset=str(m.get("dataset")))
        except (FileNotFoundError, OSError, KeyError) as e:
            meta["missing"].append(f"sst {day}: {e!r}"[:160])
        try:
            grad, gtr = L["grad"](day)
            out.loc[idx, "sst_grad"] = sample(grad, gtr, lon, lat).astype(np.float32)
        except (FileNotFoundError, OSError) as e:
            if sst is not None:
                grad = fronts.gradient_magnitude(sst, tr)
                out.loc[idx, "sst_grad"] = sample(grad, tr, lon, lat).astype(np.float32)
                rec["sst_grad"] = "computed from the cached SST (no gradient cache)"
            else:
                meta["missing"].append(f"grad {day}: {e!r}"[:160])
        try:
            mask, mtr, thr = L["mask"](day)
            out.loc[idx, "dist_front_km"] = fronts.distance_to_front_km(mask, mtr, lon, lat)
            meta["front_thresholds"][day] = thr
            rec["front_pixels"] = int(np.count_nonzero(mask))
        except (FileNotFoundError, OSError) as e:
            meta["missing"].append(f"front mask {day}: {e!r}"[:160])
        try:
            c = L["chl"](day)
            if c is not None:
                chl, ctr, dataset = c
                out.loc[idx, "chl_log10"] = sample(chl, ctr, lon, lat).astype(np.float32)
                out.loc[idx, "chl_time"] = day
                out.loc[idx, "chl_dataset"] = dataset
                rec["chl_dataset"] = dataset
            else:
                meta["missing"].append(f"chl {day}: no cached dataset")
        except (FileNotFoundError, OSError) as e:
            meta["missing"].append(f"chl {day}: {e!r}"[:160])
        meta["days"][day] = {"objects": int(len(g)), **rec}
        log(f"{day}: {len(g)} objects, {rec}")

    # Per object hour: RTOFS currents and mixed layer, GFS-Wave significant wave height
    hours = obj.time_utc.dt.round("h")
    rt_times = list(L["rtofs_times"]())
    rt_idx = None
    rt_memo: dict = {}
    wave_files = dict(L["wave_times"]())
    wave_memo: dict = {}
    pos = pd.Series(np.arange(n), index=obj.index)
    for h, g in obj.groupby(hours, sort=True):
        idx = g.index
        lon, lat = g.lon.to_numpy(float), g.lat.to_numpy(float)
        t = nearest_time(h, rt_times, MAX_HOURS_RTOFS)
        if t is None and fetch:
            try:
                L["fetch_rtofs"](h.to_pydatetime())
                rt_times.append(h)
                meta["rtofs_hours_fetched"].append(h.isoformat())
                t = h
            except Exception as e:  # noqa: BLE001  the hour may not exist in the bucket
                meta["missing"].append(f"rtofs {h}: {e!r}"[:160])
        if t is not None:
            try:
                if t not in rt_memo:
                    rt_memo.clear()
                    rt_memo[t] = L["rtofs"](t.to_pydatetime())
                d = rt_memo[t]
                if rt_idx is None:  # the RTOFS land mask is fixed: sea = finite current speed
                    rt_idx = L["rtofs_index"](lon_all, lat_all, np.isfinite(np.asarray(d["speed"])).ravel())
                j = rt_idx[pos[idx].to_numpy()]
                ok = j >= 0
                for field, var in (("current_speed_ms", "speed"), ("mld_m", "mixed_layer_thickness")):
                    v = np.full(len(j), np.nan, np.float32)
                    v[ok] = np.asarray(d[var]).ravel()[j[ok]]
                    out.loc[idx, field] = v
                out.loc[idx, "current_time"] = np.where(ok, str(d.get("valid_time")), None)
                if str(d.get("valid_time")) not in meta["rtofs_hours_used"]:
                    meta["rtofs_hours_used"].append(str(d.get("valid_time")))
            except (FileNotFoundError, OSError, KeyError) as e:
                meta["missing"].append(f"rtofs {t}: {e!r}"[:160])
        wt = nearest_time(h, list(wave_files), MAX_HOURS_WAVE)
        if wt is None and fetch:
            try:
                p = L["fetch_wave"](h.to_pydatetime())
                wave_files = dict(L["wave_times"]())
                wt = nearest_time(h, list(wave_files), MAX_HOURS_WAVE)
                if wt is None and p is not None:
                    wt, wave_files[h] = h, p
                meta["wave_files_fetched"].append(str(Path(str(p)).name) if p is not None else None)
            except Exception as e:  # noqa: BLE001
                meta["missing"].append(f"wave {h}: {e!r}"[:160])
        if wt is not None:
            try:
                if wt not in wave_memo:
                    wave_memo.clear()
                    wave_memo[wt] = L["wave"](wave_files[wt])
                hs, wtr, valid = wave_memo[wt]
                v = bilinear(hs, wtr, lon, lat, wrap_lon=True).astype(np.float32)
                out.loc[idx, "wave_hs_m"] = v
                out.loc[idx, "wave_time"] = np.where(np.isfinite(v), _iso(valid), None)
                if _iso(valid) not in meta["wave_valid_times_used"]:
                    meta["wave_valid_times_used"].append(_iso(valid))
            except (FileNotFoundError, OSError) as e:
                meta["missing"].append(f"wave {wt}: {e!r}"[:160])
    meta["rtofs_hours_used"].sort()
    meta["wave_valid_times_used"].sort()
    return out, meta


# ----------------------------------------------------------------------------------------------------------------------
# Table and summary

def shrink(df: pd.DataFrame, max_categories: int = 4096) -> pd.DataFrame:
    """float64 to float32, repetitive strings to category, so the parquet stays small."""
    out = df.copy()
    for c in out.columns:
        s = out[c]
        if pd.api.types.is_float_dtype(s) and s.dtype != np.float32:
            out[c] = s.astype(np.float32)
        elif (s.dtype == object or pd.api.types.is_string_dtype(s)) and c not in ("object_id", "cell_id"):
            if s.nunique(dropna=True) <= max_categories:
                out[c] = s.astype("category")
    return out


def _median(v: pd.Series):
    v = v.dropna()
    return (round(float(v.median()), 3), int(len(v))) if len(v) else (None, 0)


def _share(mask: pd.Series):
    """(share of True among known values, number known) of a boolean or nullable boolean series."""
    known = mask.notna()
    if not known.any():
        return None, 0
    return round(float(mask[known].astype(bool).mean()), 4), int(known.sum())


def summarise(ctx: pd.DataFrame) -> list[dict]:
    """Medians and shares per (object_type, group), each with its denominator (`<field>_n` = rows with a value)."""
    rows = []
    fields = [f for f in list(STATIC_LAYERS) + list(DAILY_FIELDS) + ["length_est_m"] if f in ctx.columns]
    for (otype, grp), g in ctx.groupby(["object_type", "group"], observed=True, sort=True):
        r = {"object_type": str(otype), "group": str(grp), "n": int(len(g))}
        for f in fields:
            med, n = _median(g[f])
            r[f"{f}_median"] = med
            r[f"{f}_n"] = n
        for f in PRESENCE_FIELDS:
            if f in g:
                r[f"{f}_share"], r[f"{f}_n"] = _share(g[f])
        if "dist_front_km" in g:
            d = g.dist_front_km.dropna()
            r[f"within_{int(FRONT_NEAR_KM)}km_of_front_share"] = round(float((d <= FRONT_NEAR_KM).mean()), 4) if len(d) else None
        if "depth_m" in g:
            d = g.depth_m.dropna()
            r["shallower_than_50m_share"] = round(float((d < 50).mean()), 4) if len(d) else None
            r["shallower_than_200m_share"] = round(float((d < 200).mean()), 4) if len(d) else None
        if "dist_coast_km" in g:
            d = g.dist_coast_km.dropna()
            r["within_20km_of_coast_share"] = round(float((d <= 20).mean()), 4) if len(d) else None
        rows.append(r)
    return rows


def fill_rates(ctx: pd.DataFrame, fields) -> dict:
    """{field: {all: {n, filled, share}, <object_type>: {...}}}: how many rows carry a value."""
    out = {}
    for f in fields:
        if f not in ctx:
            continue
        has = ctx[f].notna()
        rec = {"all": {"n": int(len(ctx)), "filled": int(has.sum()), "share": round(float(has.mean()), 4) if len(ctx) else None}}
        for t, part in has.groupby(ctx.object_type, observed=True):
            rec[str(t)] = {"n": int(len(part)), "filled": int(part.sum()), "share": round(float(part.mean()), 4)}
        out[f] = rec
    return out



# ----------------------------------------------------------------------------------------------------------------------
# Global Fishing Watch comparison (research only, CC BY-NC 4.0)

GFW_GATEWAY = "https://gateway.api.globalfishingwatch.org/v3"
GFW_SAR_DATASET = "public-global-sar-presence:latest"
GFW_LICENCE_NOTE = ("Global Fishing Watch data and APIs: CC BY-NC 4.0, noncommercial use only (GFW repository LICENCE; the API "
                    "documentation front page states 'Global Fishing Watch APIs are only available for non-commercial "
                    "purposes'). Research output: kept under data/research/, out of every product.")
GFW_FISHING_BINS = {"likely_non_fishing": (None, 0.1), "unknown": (0.1, 0.9), "likely_fishing": (0.9, None)}
GFW_FISHING_RULE = ("GFW fishing score bins as published: <= 0.1 likely non fishing, >= 0.9 likely fishing, in between "
                    "unknown (Data Download Portal release note, 31 May 2024; gfwr gfw_sar_vessel_detections() help for "
                    "the neural_vessel_type filter).")
# Resolved in this session on 2026-10-08 (HTTP 200): what GFW publishes about its SAR vessel detections.
GFW_SOURCES = [
    {"name": "gfwr: gfw_sar_vessel_detections() source, filter fields matched, neural_vessel_type (<= 0.1 likely "
             "non-fishing, >= 0.9 likely fishing), shiptype, geartype, flag, vessel_id",
     "url": "https://raw.githubusercontent.com/GlobalFishingWatch/gfwr/main/R/gfw_sar_vessel_detections.R"},
    {"name": "gfwr: 4Wings report request (POST /v3/4wings/report, datasets[0], spatial-resolution LOW 0.1 degree or HIGH "
             "0.01 degree, temporal-resolution, date-range, filters[0], format, GeoJSON region body, Bearer token)",
     "url": "https://raw.githubusercontent.com/GlobalFishingWatch/gfwr/main/R/gfw_4wings.R"},
    {"name": "gfwr: dataset id public-global-sar-presence:latest",
     "url": "https://raw.githubusercontent.com/GlobalFishingWatch/gfwr/main/R/gfw_endpoint.R"},
    {"name": "gfwr vignette: SAR detections matched to AIS; reasons for unmatched detections",
     "url": "https://raw.githubusercontent.com/GlobalFishingWatch/gfwr/main/vignettes/articles/sar_vessel_detections.Rmd"},
    {"name": "GFW Data Download Portal release note: per-detection length_m, presence_score, matching_score, fishing_score; "
             "map bins Likely non fishing <= 0.1, Likely fishing >= 0.9; detections 2017 to 5 days ago, Paolo et al. 2024 models",
     "url": "https://globalfishingwatch.org/platform-update/2024-may-data-download-portal-new-dataset-released-featuring-vessel-detections-from-sentinel-1-sar/"},
    {"name": "GFW API Python client README: 4Wings API serves SAR vessel detections 2017 to about 5 days ago",
     "url": "https://raw.githubusercontent.com/GlobalFishingWatch/gfw-api-python-client/main/README.md"},
    {"name": "GFW API documentation front page: APIs only for non-commercial purposes",
     "url": "https://globalfishingwatch.org/our-apis/documentation"},
    {"name": "Paolo et al. 2024, Satellite mapping reveals extensive industrial activity at sea, Nature 625, 85-91",
     "url": "https://doi.org/10.1038/s41586-023-06825-8"},
]
GFW_NEURAL_LABELS = {"likely fishing": "likely_fishing", "likely non fishing": "likely_non_fishing", "unknown": "unknown",
                     "other/unknown": "unknown", "likely_fishing": "likely_fishing", "likely_non_fishing": "likely_non_fishing"}
GFW_COLUMN_ALIASES = {
    "lat": ("lat", "latitude", "Lat", "detect_lat"), "lon": ("lon", "longitude", "Lon", "detect_lon"),
    "time_utc": ("timestamp", "detect_timestamp", "time", "date", "Time Range", "time_range", "scene_timestamp"),
    "fishing_score": ("fishing_score", "neural_vessel_type", "fishing_prob", "score_fishing"),
    "matched": ("matched", "ais_matched", "matched_category", "match"),
    "length_m": ("length_m", "length", "gfw_length_m"),
    "detections": ("detections", "Detections", "n_detections", "count"),
    "presence_score": ("presence_score",), "matching_score": ("matching_score",),
}


def normalise_gfw_table(df: pd.DataFrame) -> pd.DataFrame:
    """Standard column names for a GFW SAR detections table (portal export or 4Wings report CSV/JSON rows).

    Output columns: lat, lon, time_utc (UTC), and when present fishing_score, fishing_class, matched (boolean), length_m,
    detections (1 per row for point exports). `matched_category` strings become booleans: 'unmatched' -> False.
    """
    cols = {c.lower().strip(): c for c in df.columns}
    out = pd.DataFrame(index=df.index)
    for std, names in GFW_COLUMN_ALIASES.items():
        for nm in names:
            if nm.lower() in cols:
                out[std] = df[cols[nm.lower()]].values
                break
    if "lat" not in out or "lon" not in out:
        raise ValueError(f"GFW table needs lat and lon columns; found {list(df.columns)}")
    out["lat"], out["lon"] = out.lat.astype(float), out.lon.astype(float)
    if "time_utc" in out:
        t = out.time_utc.astype(str).str.split(",").str[0].str.strip()
        out["time_utc"] = pd.to_datetime(t, utc=True, errors="coerce", format="mixed")
        if "hour" in cols:  # 4Wings HOURLY reports: a date column plus the hour of the day
            hours = pd.to_numeric(df[cols["hour"]], errors="coerce").fillna(0).to_numpy()
            out["time_utc"] = out.time_utc + pd.to_timedelta(hours, unit="h")
    if "fishing_score" in out:
        score = pd.to_numeric(out.fishing_score, errors="coerce")
        if score.isna().all() and out.fishing_score.notna().any():  # label strings (neural_vessel_type filter values)
            labels = out.fishing_score.astype(str).str.lower().str.replace("-", " ").str.strip()
            out["fishing_class"] = labels.map(GFW_NEURAL_LABELS).where(labels.isin(GFW_NEURAL_LABELS), None)
            out["fishing_score"] = np.nan
        else:
            out["fishing_score"] = score
            out["fishing_class"] = gfw_fishing_class(score)
    if "matched" in out:
        m = out.matched
        if m.dtype == object or pd.api.types.is_string_dtype(m):
            s = m.astype(str).str.lower().str.strip()
            out["matched"] = pd.array(np.where(s.isin(["true", "1", "matched", "yes"]) | s.str.startswith("matched_"), True,
                                               np.where(s.isin(["false", "0", "unmatched", "no", "none"]), False, None)), dtype="boolean")
        else:
            out["matched"] = m.astype("boolean")
    if "detections" not in out:
        out["detections"] = 1
    out["detections"] = pd.to_numeric(out.detections, errors="coerce").fillna(1).astype(int)
    return out


def gfw_fishing_class(score: pd.Series) -> pd.Series:
    """GFW_FISHING_RULE applied to a fishing score (NaN stays None)."""
    s = pd.to_numeric(score, errors="coerce")
    cls = np.where(s <= 0.1, "likely_non_fishing", np.where(s >= 0.9, "likely_fishing", "unknown"))
    return pd.Series(np.where(s.isna(), None, cls), index=score.index, dtype="object")


def _local_km(lon, lat, lat0_rad):
    return np.c_[np.radians(np.asarray(lon, float)) * 6371.0088 * np.cos(lat0_rad), np.radians(np.asarray(lat, float)) * 6371.0088]


def gfw_compare_points(ours: pd.DataFrame, gfw: pd.DataFrame, radius_m: float = 500.0, max_hours: float = 1.0) -> tuple[pd.DataFrame, dict]:
    """Nearest GFW detection to each of our radar objects on the same pass (within max_hours), and the reverse.

    `ours` needs object_id, group, time_utc, lat, lon (and length_est_m if present); `gfw` is a normalised point table
    with time_utc. Returns (per-object table: gfw_within_radius, gfw_dist_m, gfw_fishing_score, gfw_fishing_class,
    gfw_matched, gfw_length_m; summary dict with shares per group, the GFW side's coverage and length agreement).
    """
    from scipy.spatial import cKDTree

    g = gfw.dropna(subset=["lat", "lon"]).copy()
    per = pd.DataFrame({"object_id": ours.object_id.values, "group": ours.group.values}, index=ours.index)
    per["gfw_within_radius"] = False
    per["gfw_dist_m"] = np.float32(np.nan)
    for c in ("gfw_fishing_score", "gfw_length_m"):
        per[c] = np.float32(np.nan)
    per["gfw_fishing_class"] = pd.Series([None] * len(ours), index=ours.index, dtype="object")
    per["gfw_matched"] = pd.array([None] * len(ours), dtype="boolean")
    gfw_hit = np.zeros(len(g), bool)
    has_time = "time_utc" in g and g.time_utc.notna().any()
    if len(g):
        gt = pd.to_datetime(g.time_utc, utc=True).dt.tz_localize(None).to_numpy() if has_time else None
        for h, part in ours.groupby(ours.time_utc.dt.round("h")):
            if has_time:
                h0 = np.datetime64(pd.Timestamp(h).tz_convert("UTC").tz_localize(None))
                near = np.abs((gt - h0) / np.timedelta64(1, "h")) <= max_hours
                gi = np.nonzero(near)[0]
            else:
                gi = np.arange(len(g))
            if not len(gi):
                continue
            lat0 = np.radians(float(part.lat.mean()))
            tree = cKDTree(_local_km(g.lon.values[gi], g.lat.values[gi], lat0))
            d, j = tree.query(_local_km(part.lon.values, part.lat.values, lat0))
            d_m = d * 1000.0
            ok = d_m <= radius_m
            per.loc[part.index, "gfw_dist_m"] = d_m.astype(np.float32)
            per.loc[part.index, "gfw_within_radius"] = ok
            hit_rows = gi[j[ok]]
            gfw_hit[hit_rows] = True
            idx_ok = part.index[ok]
            if "fishing_score" in g:
                per.loc[idx_ok, "gfw_fishing_score"] = g.fishing_score.values[hit_rows].astype(np.float32)
                per.loc[idx_ok, "gfw_fishing_class"] = g.fishing_class.values[hit_rows]
            if "matched" in g:
                per.loc[idx_ok, "gfw_matched"] = pd.array(g.matched.values[hit_rows], dtype="boolean")
            if "length_m" in g:
                per.loc[idx_ok, "gfw_length_m"] = pd.to_numeric(g.length_m, errors="coerce").values[hit_rows].astype(np.float32)
    summary = {"radius_m": radius_m, "max_hours": max_hours, "gfw_detections": int(len(g)),
               "gfw_detections_with_one_of_ours_within_radius": int(gfw_hit.sum()),
               "gfw_detections_with_one_of_ours_share": round(float(gfw_hit.mean()), 4) if len(g) else None, "by_group": []}
    for grp, p in per.groupby("group", observed=True, sort=True):
        r = {"group": str(grp), "n": int(len(p)), "with_gfw_detection_share": round(float(p.gfw_within_radius.mean()), 4)}
        hit = p[p.gfw_within_radius]
        if "fishing_score" in g and len(hit):
            r["gfw_fishing_class_counts"] = {str(k): int(v) for k, v in hit.gfw_fishing_class.value_counts(dropna=False).items()}
            r["gfw_fishing_score_median"] = round(float(hit.gfw_fishing_score.median()), 3)
        if "matched" in g and len(hit):
            m = hit.gfw_matched.dropna()
            r["gfw_ais_matched_share"] = round(float(m.astype(bool).mean()), 4) if len(m) else None
            r["gfw_ais_matched_n"] = int(len(m))
        if "length_est_m" in ours and "length_m" in g and len(hit):
            both = hit.gfw_length_m.notna() & ours.loc[hit.index, "length_est_m"].notna()
            if both.any():
                ratio = ours.loc[hit.index[both], "length_est_m"].to_numpy() / hit.gfw_length_m[both].to_numpy()
                r["length_ratio_ours_over_gfw_median"] = round(float(np.median(ratio)), 3)
                r["length_pairs"] = int(both.sum())
        summary["by_group"].append(r)
    if "fishing_class" in g:
        summary["gfw_fishing_class_counts"] = {str(k): int(v) for k, v in g.fishing_class.value_counts(dropna=False).items()}
        hit_cls = g.fishing_class[gfw_hit]
        summary["gfw_with_one_of_ours_by_fishing_class"] = {
            str(k): {"n": int((g.fishing_class == k).sum()), "share_with_ours": round(float((hit_cls == k).sum() / max((g.fishing_class == k).sum(), 1)), 4)}
            for k in g.fishing_class.dropna().unique()}
    if "matched" in g:
        m = g.matched.dropna()
        summary["gfw_ais_matched_share"] = round(float(m.astype(bool).mean()), 4) if len(m) else None
    return per, summary


def gfw_compare_cells(ours: pd.DataFrame, gfw: pd.DataFrame, res_deg: float = 0.1, by_day: bool = True) -> dict:
    """Counts per lon/lat cell (and UTC day): ours against GFW detections; rank correlation over cells either side saw.

    Works for a 4Wings report (gridded counts with a date) as well as a point export. `ours` and `gfw` need lat, lon,
    time_utc; GFW rows carry `detections` (1 for points).
    """
    from scipy.stats import spearmanr

    def key(df, w):
        k = pd.DataFrame({"r": np.floor(df.lat.to_numpy(float) / res_deg).astype(int),
                          "c": np.floor(df.lon.to_numpy(float) / res_deg).astype(int)})
        if by_day and "time_utc" in df:
            k["day"] = pd.to_datetime(df.time_utc, utc=True).dt.strftime("%Y-%m-%d").to_numpy()
        k["w"] = np.asarray(w, float)
        return k.groupby([c for c in k.columns if c != "w"]).w.sum()

    a = key(ours, np.ones(len(ours)))
    b = key(gfw, gfw.detections.to_numpy() if "detections" in gfw else np.ones(len(gfw)))
    both = pd.concat([a.rename("ours"), b.rename("gfw")], axis=1).fillna(0.0)
    out = {"res_deg": res_deg, "by_day": by_day, "cells": int(len(both)), "cells_both": int(((both.ours > 0) & (both.gfw > 0)).sum()),
           "cells_ours_only": int(((both.ours > 0) & (both.gfw == 0)).sum()), "cells_gfw_only": int(((both.ours == 0) & (both.gfw > 0)).sum()),
           "ours_total": float(both.ours.sum()), "gfw_total": float(both.gfw.sum())}
    if len(both) >= 3 and both.ours.std() > 0 and both.gfw.std() > 0:
        out["spearman_all_cells"] = round(float(spearmanr(both.ours, both.gfw)[0]), 3)
    bb = both[(both.ours > 0) & (both.gfw > 0)]
    if len(bb) >= 3 and bb.ours.std() > 0 and bb.gfw.std() > 0:
        out["spearman_cells_both"] = round(float(spearmanr(bb.ours, bb.gfw)[0]), 3)
        out["ratio_ours_over_gfw_median_cells_both"] = round(float((bb.ours / bb.gfw).median()), 3)
    return out


def gfw_cells_at_objects(ours: pd.DataFrame, hourly: pd.DataFrame, neural: pd.DataFrame | None = None, res_deg: float = 0.01,
                         max_hours: float = 1.0) -> tuple[pd.DataFrame, dict]:
    """GFW's gridded SAR detections (4Wings report cells) at each of our radar objects: same cell, same UTC date and hour.

    `hourly` is a normalised table of cell rows with time_utc (date plus hour), detections and matched; `neural` the
    daily cell rows with fishing_class (from the neural_vessel_type filter). Per object: gfw_cell_detections (sum within
    max_hours of the object hour), gfw_cell_matched (any AIS-matched detection in that cell-hour), gfw_cell_unmatched,
    gfw_cell_fishing_class (the one class present that day, else 'mixed', else None). The summary gives shares per group
    and the reverse view: GFW cell-hours with one of our objects, by matched flag and class.
    """
    def keys(lon, lat):
        return np.floor(np.asarray(lon, float) / res_deg).astype(np.int64), np.floor(np.asarray(lat, float) / res_deg).astype(np.int64)

    o = pd.DataFrame({"object_id": ours.object_id.values, "group": ours.group.values}, index=ours.index)
    oc, orow = keys(ours.lon, ours.lat)
    ot = pd.to_datetime(ours.time_utc, utc=True)
    o["cell"] = orow.astype(np.int64) * 1_000_000 + oc
    o["day"] = ot.dt.strftime("%Y-%m-%d").to_numpy()
    o["hour_f"] = (ot.dt.hour + ot.dt.minute / 60.0).to_numpy()
    o["gfw_cell_detections"] = 0.0
    o["gfw_cell_unmatched"] = 0.0
    o["gfw_cell_matched"] = pd.array([None] * len(o), dtype="boolean")
    o["gfw_cell_fishing_class"] = pd.Series([None] * len(o), index=o.index, dtype="object")
    h = hourly.dropna(subset=["lat", "lon", "time_utc"]).copy()
    hc, hr = keys(h.lon, h.lat)
    h["cell"] = hr * 1_000_000 + hc
    ht = pd.to_datetime(h.time_utc, utc=True)
    h["day"] = ht.dt.strftime("%Y-%m-%d").to_numpy()
    h["hour_f"] = ht.dt.hour.to_numpy().astype(float)
    h["det"] = h.detections.astype(float)
    h["matched_b"] = h.matched.astype("boolean") if "matched" in h else pd.array([None] * len(h), dtype="boolean")
    gfw_hit = np.zeros(len(h), bool)
    m = o.reset_index().merge(h.reset_index().rename(columns={"index": "h_index"}), on=["cell", "day"], how="inner", suffixes=("", "_g"))
    if len(m):
        m = m[np.abs(m.hour_f - m.hour_f_g) <= max_hours]
    if len(m):
        g = m.groupby("index")
        det = g.det.sum()
        o.loc[det.index, "gfw_cell_detections"] = det.values
        mt = m.matched_b.astype("boolean")
        matched_any = m.assign(mb=mt.fillna(False).astype(bool)).groupby("index").mb.any()
        o.loc[matched_any.index, "gfw_cell_matched"] = pd.array(matched_any.values, dtype="boolean")
        unm = m.assign(u=np.where(mt.fillna(True).astype(bool), 0.0, m.det)).groupby("index").u.sum()
        o.loc[unm.index, "gfw_cell_unmatched"] = unm.values
        gfw_hit[m.h_index.to_numpy()] = True
    if neural is not None and len(neural) and "fishing_class" in neural:
        nn = neural.dropna(subset=["lat", "lon", "time_utc", "fishing_class"]).copy()
        nc, nr = keys(nn.lon, nn.lat)
        nn["cell"] = nr * 1_000_000 + nc
        nn["day"] = pd.to_datetime(nn.time_utc, utc=True).dt.strftime("%Y-%m-%d").to_numpy()
        cls = nn.groupby(["cell", "day"]).fishing_class.agg(lambda s: s.iloc[0] if s.nunique() == 1 else "mixed")
        j = o.reset_index().merge(cls.rename("fc").reset_index(), on=["cell", "day"], how="inner")
        o.loc[j["index"].values, "gfw_cell_fishing_class"] = j.fc.values
    o["gfw_in_cell_hour"] = o.gfw_cell_detections > 0
    summary = {"res_deg": res_deg, "max_hours": max_hours, "gfw_cell_hours": int(len(h)), "gfw_detections": float(h.det.sum()),
               "gfw_cell_hours_with_one_of_ours": int(gfw_hit.sum()),
               "gfw_cell_hours_with_one_of_ours_share": round(float(gfw_hit.mean()), 4) if len(h) else None, "by_group": []}
    if "matched" in h and len(h):
        for flag, part in h.groupby(h.matched_b.fillna(False).astype(bool)):
            summary[f"gfw_cell_hours_{'matched' if flag else 'unmatched'}_with_one_of_ours_share"] = round(float(gfw_hit[part.index.to_numpy()].mean()), 4)
    for grp, p in o.groupby("group", observed=True, sort=True):
        hit = p[p.gfw_in_cell_hour]
        r = {"group": str(grp), "n": int(len(p)), "with_gfw_detection_same_cell_hour_share": round(float(p.gfw_in_cell_hour.mean()), 4)}
        if len(hit):
            mm = hit.gfw_cell_matched.dropna()
            r["gfw_cell_has_ais_matched_share"] = round(float(mm.astype(bool).mean()), 4) if len(mm) else None
            r["gfw_cell_fishing_class_counts"] = {str(k): int(v) for k, v in hit.gfw_cell_fishing_class.value_counts(dropna=False).items()}
        summary["by_group"].append(r)
    return o.drop(columns=["cell", "day", "hour_f"]), summary


def gfw_report_request(geometry: dict, start: str, end: str, spatial: str = "HIGH", temporal: str = "DAILY",
                       filters: str | None = None, fmt: str = "JSON", dataset: str = GFW_SAR_DATASET) -> dict:
    """URL, query and body of a 4Wings report request for GFW SAR detections (as gfwr builds it). No token inside.

    `end` is exclusive in the GFW date range (gfwr: 'excluding this date'). `filters` like "matched='false'" or
    "neural_vessel_type >= 0.9". The token goes in the Authorization header at call time (gfw_fetch_report).
    """
    params = {"spatial-resolution": spatial, "temporal-resolution": temporal, "date-range": f"{start},{end}",
              "datasets[0]": dataset, "format": fmt}
    if filters:
        params["filters[0]"] = filters
    return {"url": f"{GFW_GATEWAY}/4wings/report", "params": params, "json": {"geojson": geometry}}


def gfw_fetch_report(request: dict, token_env: str = "GFW_API_TOKEN", timeout: int = 300) -> pd.DataFrame | None:
    """POST a 4Wings report with the token from the environment. Returns rows as a DataFrame, or None without a token.

    The token is read from the environment only and never written anywhere. Research only (GFW_LICENCE_NOTE).
    """
    import requests

    token = os.environ.get(token_env, "").strip()
    if not token:
        return None
    r = requests.post(request["url"], params=request["params"], json=request["json"], timeout=timeout,
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    r.raise_for_status()
    return gfw_rows_from_report(r.json() if request["params"].get("format", "JSON") == "JSON" else r.text)


def gfw_rows_from_report(payload) -> pd.DataFrame:
    """Flatten a 4Wings JSON report ({'entries': [{dataset: [rows]}]}) or a CSV text into a DataFrame."""
    if isinstance(payload, str):
        from io import StringIO

        return pd.read_csv(StringIO(payload))
    rows = []
    for entry in payload.get("entries", []):
        for dataset, items in entry.items():
            for it in items or []:
                rows.append({**it, "dataset": dataset})
    return pd.DataFrame(rows)


def gfw_load_table(path: Path) -> pd.DataFrame:
    """Read a GFW SAR detections export (csv, csv.gz, parquet, json report) and normalise it."""
    path = Path(path)
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix == ".json":
        df = gfw_rows_from_report(json.loads(path.read_text()))
    else:
        df = pd.read_csv(path)
    return normalise_gfw_table(df)
