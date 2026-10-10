"""Weather at the radar time for live-pass contacts: GFS 10 m wind and Himawari-9 cloud-top temperature.

Same sources and method as scripts/16_weather_context.py (darkvessel.weather): the GFS 0.25 degree 10 m wind of the
hour nearest the scene (UGRD and VGRD records fetched by byte range from the NOAA Open Data bucket on AWS) and the
Himawari-9 AHI Level 2 cloud-top temperature of the 10-minute full disk that starts nearest the scene time, taken at
parallax-corrected positions within 4 km; tops colder than 220 K mark deep convection. Live passes differ in one
way: the newest GFS cycle may not be posted yet when a pass is processed, so when the nearest cycle's file is missing
the previous cycles are tried with the forecast hour advanced by 6 or 12 h (the cycle and forecast hour used are
recorded on every row).

Unknown is kept apart from calm: a fetch that fails leaves wind_ms null (wind unknown) or deep_convection null
(convection unknown), with the failing URL and status in wind_source or cloud_source; a Himawari file that was read
and shows no cloud within 4 km gives ctt_k null and deep_convection false (clear sky). The lead builder gates each
part only where it is known (board D4.5).

Output per pass: data/live/<run_id>_weather.parquet with WEATHER_COLUMNS, one row per contact (det_id), joined by the
lead builder on det_id. A sidecar with an unknown part is retried on later watcher cycles for RETRY_HOURS after the
scene time, at most once per RETRY_MINUTES.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

from darkvessel import weather as wx
from darkvessel.config import DATA_DIR
from darkvessel.s1 import aws

CACHE = DATA_DIR / "cache" / "weather"
WEATHER_COLUMNS = ["det_id", "run_id", "scene_id", "scene_time_utc", "wind_ms", "ctt_k", "deep_convection", "gfs_cycle_utc",
                   "gfs_forecast_h", "himawari_key", "wind_source", "cloud_source", "fetched_utc"]
RETRY_HOURS = 48.0
RETRY_MINUTES = 30.0
GFS_FALLBACK_CYCLES = (0, 6, 12)   # hours back from the nearest cycle, forecast hour advanced by the same
WEATHER_TEXT = (
    "wind_ms: GFS 0.25 degree 10 m wind speed of the hour nearest the scene (NOAA GFS on AWS, noaa-gfs-bdp-pds; UGRD and "
    "VGRD at 10 m above ground), from the newest cycle posted (gfs_cycle_utc, gfs_forecast_h). ctt_k: Himawari-9 AHI L2 "
    "cloud-top temperature (noaa-himawari9, AHI-L2-FLDK-Clouds, parallax corrected) within 4 km; null where clear. "
    "deep_convection: ctt_k < 220 K; false where clear; null when the Himawari file could not be read. Null wind or null "
    "deep_convection means unknown, never calm."
)


def gfs_candidates(t: dt.datetime) -> list[tuple[dt.datetime, int]]:
    """(cycle, forecast hour) to try for time t: the nearest (darkvessel.weather._nearest_gfs), then older cycles."""
    cycle, fh = wx._nearest_gfs(t)
    return [(cycle - dt.timedelta(hours=h), fh + h) for h in GFS_FALLBACK_CYCLES]


def gfs_wind_cycle(cycle: dt.datetime, fh: int, cache_dir: Path | None = CACHE):
    """(speed m/s, transform) of the 10 m wind of one GFS cycle and forecast hour (darkvessel.weather.gfs_wind's fetch)."""
    base = f"{wx.GFS}/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos/gfs.t{cycle:%H}z.pgrb2.0p25.f{fh:03d}"
    cache = (Path(cache_dir) / f"gfs_{cycle:%Y%m%d%H}_f{fh:03d}_wind10.grib2") if cache_dir else None
    if cache is None or not cache.exists():
        idx = aws._get(base + ".idx").text.splitlines()
        starts = [int(line.split(":")[1]) for line in idx]
        parts = []
        for i, line in enumerate(idx):
            if re.search(r":(UGRD|VGRD):10 m above ground:", line):
                end = starts[i + 1] - 1 if i + 1 < len(starts) else ""
                parts.append(aws._get(base, headers={"Range": f"bytes={starts[i]}-{end}"}).content)
        if len(parts) != 2:
            raise RuntimeError(f"10 m wind records not found in {base}.idx")
        cache = cache or Path(os.environ.get("TMPDIR", "/tmp")) / f"gfs_{cycle:%Y%m%d%H}_f{fh:03d}_{os.getpid()}.grib2"
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(f"{cache.name}.{os.getpid()}.tmp")
        tmp.write_bytes(b"".join(parts))
        os.replace(tmp, cache)
    import rasterio

    with rasterio.open(cache) as ds:
        u, v = ds.read(1).astype(np.float32), ds.read(2).astype(np.float32)
        return np.hypot(u, v), ds.transform, base


def _status(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    url = getattr(resp, "url", None) or getattr(getattr(exc, "request", None), "url", None)
    return f"{type(exc).__name__}{' HTTP ' + str(code) if code else ''}{' ' + url if url else ''}: {str(exc)[:160]}"


def wind_at(t: dt.datetime, lon, lat, cache_dir=CACHE, fetch=gfs_wind_cycle):
    """(wind m/s per point or NaN, cycle, forecast hour, source text). Tries the cycles of `gfs_candidates`."""
    tried = []
    for cycle, fh in gfs_candidates(t):
        try:
            spd, tr, base = fetch(cycle, fh, cache_dir)
            return np.round(wx.sample_grid(spd, tr, lon, lat), 2), cycle, fh, f"GFS {cycle:%Y-%m-%d %H}Z f{fh:03d} ({base})"
        except Exception as exc:  # noqa: BLE001 try the previous cycle; report all failures if none works
            tried.append(f"{cycle:%Y-%m-%d %H}Z f{fh:03d}: {_status(exc)}")
    return np.full(len(lon), np.nan), None, None, "unknown: " + " | ".join(tried)


def cloud_at(t: dt.datetime, lon, lat, cache_dir=CACHE, key_fn=wx.himawari_key, window_fn=None, ctt_fn=wx.himawari_ctt):
    """(ctt K per point or NaN, deep convection per point or None, key, source text)."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    try:
        key = key_fn(t)
        if key is None:
            raise FileNotFoundError(f"no Himawari-9 cloud-height file within 10 min of {t:%Y-%m-%d %H:%M} UTC in {wx.HIMA}")
        pad = 0.5
        bbox = (float(lon.min()) - pad, float(lat.min()) - pad, float(lon.max()) + pad, float(lat.max()) + pad)
        win = (window_fn or wx.HimawariWindow)(key, bbox)
        plon, plat, ctt = ctt_fn(key, win)
        c = np.round(wx.ctt_at(plon, plat, ctt, lon, lat), 1).astype(float)
        deep = np.where(np.isfinite(c), c < wx.DEEP_CONVECTION_K, False)
        return c, [bool(x) for x in deep], key, f"Himawari-9 {key}"
    except Exception as exc:  # noqa: BLE001 unknown, not clear
        return np.full(len(lon), np.nan), [None] * len(lon), None, "unknown: " + _status(exc)


def scene_weather(contacts: pd.DataFrame, scene_time: pd.Timestamp, run_id: str, scene_id: str, cache_dir=CACHE,
                  wind_fn=wind_at, cloud_fn=cloud_at) -> pd.DataFrame:
    """WEATHER_COLUMNS for the contacts of one scene (one GFS hour and one Himawari slot for the whole scene)."""
    t = pd.Timestamp(scene_time).to_pydatetime()
    lon, lat = contacts.lon.to_numpy(float), contacts.lat.to_numpy(float)
    out = pd.DataFrame({"det_id": contacts.det_id.astype(str).to_numpy(), "run_id": run_id, "scene_id": scene_id,
                        "scene_time_utc": pd.Timestamp(scene_time).isoformat()})
    if len(out) == 0:
        return pd.DataFrame(columns=WEATHER_COLUMNS)
    wind, cycle, fh, wsrc = wind_fn(t, lon, lat, cache_dir)
    ctt, deep, key, csrc = cloud_fn(t, lon, lat, cache_dir)
    out["wind_ms"] = np.asarray(wind, float)
    out["ctt_k"] = np.asarray(ctt, float)
    out["deep_convection"] = pd.array(deep, dtype="boolean")
    out["gfs_cycle_utc"] = None if cycle is None else f"{cycle:%Y-%m-%dT%H:%M}Z"
    out["gfs_forecast_h"] = pd.array([fh] * len(out), dtype="Int64")
    out["himawari_key"] = key
    out["wind_source"], out["cloud_source"] = wsrc, csrc
    out["fetched_utc"] = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    return out[WEATHER_COLUMNS]


def sidecar_path(run_id: str, live_dir: Path) -> Path:
    return Path(live_dir) / f"{run_id}_weather.parquet"


def complete(df: pd.DataFrame | None) -> bool:
    """True when every contact has both parts known."""
    return df is not None and len(df) > 0 and bool(df.wind_ms.notna().all()) and bool(df.deep_convection.notna().all())


def update_sidecars(scene_records: list[dict], contacts: pd.DataFrame, live_dir: Path, now: pd.Timestamp | None = None,
                    force: bool = False, log=print, scene_fn=scene_weather) -> dict:
    """Write or refresh data/live/<run_id>_weather.parquet for every pass in `scene_records` (status done).

    A sidecar is (re)built when it is missing, when its contacts differ from the pass's (a rematch or a new scene), or
    when a part is unknown, the last attempt is RETRY_MINUTES old and the pass is less than RETRY_HOURS old; `force`
    rebuilds every one. Returns {run_id: state}.
    """
    now = now or pd.Timestamp.now(tz="UTC")
    out = {}
    if contacts is None or len(contacts) == 0:
        return out
    recs = pd.DataFrame(scene_records)
    for run_id, sc in recs.groupby("run_id"):
        c = contacts[contacts.run_id == run_id]
        if len(c) == 0:
            continue
        path = sidecar_path(run_id, live_dir)
        old = pd.read_parquet(path) if path.exists() else None
        same = old is not None and set(old.det_id.astype(str)) == set(c.det_id.astype(str))
        t_scene = pd.to_datetime(sc.scene_time_utc, utc=True).max()
        if not force and same:
            if complete(old):
                out[run_id] = "complete"
                continue
            last = pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC")
            if now - last < pd.Timedelta(minutes=RETRY_MINUTES) or now - t_scene > pd.Timedelta(hours=RETRY_HOURS):
                out[run_id] = "unknown parts kept"
                continue
        parts = []
        for _, r in sc.iterrows():
            cs = c[c.scene_id == r.product_id] if "scene_id" in c else c
            if len(cs):
                parts.append(scene_fn(cs, pd.Timestamp(r.scene_time_utc), run_id, r.product_id))
        if not parts:
            continue
        df = pd.concat(parts, ignore_index=True)
        tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp.parquet")
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)
        out[run_id] = "complete" if complete(df) else "written with unknown parts"
        log(f"  weather {run_id}: {len(df)} contacts, wind known {int(df.wind_ms.notna().sum())}, convection known "
            f"{int(df.deep_convection.notna().sum())}, deep convection {int(df.deep_convection.fillna(False).sum())}; "
            f"{df.wind_source.iloc[0][:90]}; {df.cloud_source.iloc[0][:90]}")
    return out
