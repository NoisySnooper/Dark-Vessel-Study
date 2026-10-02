"""Weather context at the radar time: 10 m wind from GFS and cloud-top temperature from Himawari-9.

Both come from the NOAA Open Data buckets on AWS, read anonymously over HTTPS:
  GFS 0.25 degree, bucket noaa-gfs-bdp-pds: gfs.YYYYMMDD/HH/atmos/gfs.tHHz.pgrb2.0p25.fFFF (+ .idx).
      Only the UGRD and VGRD records at 10 m above ground are fetched, by byte range from the .idx
      (about 1 MB instead of 480 MB), and read with GDAL's GRIB driver. Cycles every 6 h, hourly steps.
  Himawari-9 AHI Level 2 cloud height, bucket noaa-himawari9:
      AHI-L2-FLDK-Clouds/YYYY/MM/DD/HHMM/AHI-CHGT_v1r1_h09_s<start>_e<end>_c<created>.nc, every 10 min,
      2 km full disk, 200 x 200 chunks: only the chunks over the area are read. Cloud-top temperature
      is taken at parallax-corrected positions. Tops colder than 220 K mark deep convection, the
      source of rain-cell clutter in C-band radar.
"""

from __future__ import annotations

import datetime as dt
import re
import tempfile
from pathlib import Path

import h5py
import numpy as np
import rasterio
from scipy.spatial import cKDTree

from darkvessel.s1 import aws
from darkvessel.viirs.access import RangeFile

GFS = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
HIMA = "https://noaa-himawari9.s3.amazonaws.com"
DEEP_CONVECTION_K = 220.0


def _nearest_gfs(t: dt.datetime) -> tuple[dt.datetime, int]:
    """GFS cycle and forecast hour closest to t (cycles 00/06/12/18 UTC, hourly steps 0 to 5)."""
    t = t.astimezone(dt.timezone.utc)
    hour = (t + dt.timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
    cycle = hour.replace(hour=hour.hour - hour.hour % 6)
    return cycle, int((hour - cycle).total_seconds() // 3600)


def gfs_wind(t: dt.datetime, cache_dir: Path | None = None):
    """(speed m/s, rasterio transform) of the global 0.25 degree 10 m wind nearest to t."""
    cycle, fh = _nearest_gfs(t)
    base = f"{GFS}/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos/gfs.t{cycle:%H}z.pgrb2.0p25.f{fh:03d}"
    cache = (cache_dir / f"gfs_{cycle:%Y%m%d%H}_f{fh:03d}_wind10.grib2") if cache_dir else None
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
        data = b"".join(parts)
        if cache is None:
            tmp = tempfile.NamedTemporaryFile(suffix=".grib2", delete=False)
            tmp.write(data)
            tmp.close()
            cache = Path(tmp.name)
        else:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
    with rasterio.open(cache) as ds:
        u, v = ds.read(1).astype(np.float32), ds.read(2).astype(np.float32)
        return np.hypot(u, v), ds.transform


def sample_grid(arr: np.ndarray, transform, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Value of the global lon/lat grid cell that contains each point.

    The transform's origin is the outer edge of the first cell; GDAL may present GFS longitudes
    as -180 to 180 or 0 to 360, so longitudes are wrapped to the grid's own range.
    """
    lon = np.asarray(lon, float)
    lon = np.mod(lon - transform.c, 360.0) + transform.c
    col = np.floor((lon - transform.c) / transform.a).astype(int) % arr.shape[1]
    row = np.clip(np.floor((np.asarray(lat, float) - transform.f) / transform.e).astype(int), 0, arr.shape[0] - 1)
    return arr[row, col]


def himawari_key(t: dt.datetime) -> str | None:
    """Cloud-height file of the 10-minute Himawari-9 full disk that starts nearest to t."""
    t = t.astimezone(dt.timezone.utc)
    slot = (t - dt.timedelta(minutes=5)).replace(second=0, microsecond=0)
    slot = slot.replace(minute=slot.minute - slot.minute % 10)
    for s in (slot, slot + dt.timedelta(minutes=10), slot - dt.timedelta(minutes=10)):
        prefix = f"AHI-L2-FLDK-Clouds/{s:%Y/%m/%d/%H%M}/AHI-CHGT_"
        text = aws._get(f"{HIMA}/?list-type=2&prefix={prefix}&max-keys=5").text
        keys = re.findall(r"<Key>([^<]*\.nc)</Key>", text)
        if keys:
            return keys[0]
    return None


class HimawariWindow:
    """Rows and columns of the Himawari full-disk grid that cover a lon/lat box (found once, reused)."""

    def __init__(self, key: str, bbox, pad: int = 10):
        with RangeFile(f"{HIMA}/{key}", block=1024 * 1024) as f, h5py.File(f, "r") as h:
            # coarse search on every 50th line, then the exact window
            lat = h["Latitude"][::50, ::50]
            lon = h["Longitude"][::50, ::50]
        w, s, e, n = bbox
        ok = (lat >= s) & (lat <= n) & (lon >= w) & (lon <= e)
        r, c = np.nonzero(ok)
        if len(r) == 0:
            raise ValueError("box outside the Himawari disk")
        self.r0, self.r1 = int(max(0, r.min() * 50 - 50 - pad)), int(min(5500, r.max() * 50 + 50 + pad))
        self.c0, self.c1 = int(max(0, c.min() * 50 - 50 - pad)), int(min(5500, c.max() * 50 + 50 + pad))


def himawari_ctt(key: str, win: HimawariWindow):
    """(lon_pc, lat_pc, cloud-top temperature K) inside the window; clear pixels are NaN."""
    sl = (slice(win.r0, win.r1), slice(win.c0, win.c1))
    with RangeFile(f"{HIMA}/{key}", block=1024 * 1024) as f, h5py.File(f, "r") as h:
        lon, lat = h["Longitude_Pc"][sl], h["Latitude_Pc"][sl]
        ctt = h["CldTopTemp"][sl]
    ctt = np.where(ctt > 0, ctt, np.nan).astype(np.float32)
    return lon, lat, ctt


def ctt_at(lon_pc, lat_pc, ctt, lon, lat, max_km: float = 4.0) -> np.ndarray:
    """Cloud-top temperature over each point (NaN where clear or no cloudy pixel within max_km)."""
    ok = np.isfinite(ctt) & (lat_pc > -90)
    out = np.full(len(lon), np.nan, np.float32)
    if not ok.any() or len(lon) == 0:
        return out
    lat0 = np.radians(float(np.median(lat)))
    xy = lambda lo, la: np.c_[np.radians(lo) * 6371.0 * np.cos(lat0), np.radians(la) * 6371.0]  # noqa: E731
    d, i = cKDTree(xy(lon_pc[ok], lat_pc[ok])).query(xy(np.asarray(lon), np.asarray(lat)))
    hit = d <= max_km
    out[hit] = ctt[ok][i[hit]]
    return out
