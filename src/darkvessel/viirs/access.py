"""VIIRS Day/Night Band granules from the NOAA JPSS buckets on AWS (anonymous HTTPS, ranged HDF5 reads).

Buckets: noaa-nesdis-snpp-pds (S-NPP), noaa-nesdis-n20-pds (NOAA-20), noaa-nesdis-n21-pds (NOAA-21).
Products used:
  VIIRS-DNB-SDR/YYYY/MM/DD/SVDNB_<sat>_d<date>_t<start>_e<end>_b<orbit>_c<created>_oebc_ops.h5   radiance
  VIIRS-DNB-GEO/YYYY/MM/DD/GDNBO_<sat>_...h5                                                    geolocation
  VIIRS-JRR-CloudMask/YYYY/MM/DD/JRR-CloudMask_v3r2_<sat2>_s<start>_e<end>_c<created>.nc       cloud mask
A granule is about 85 s of data: 768 rows x 4064 columns of about 742 m pixels, about 570 km along track
and 3,000 km across. The night pass over the South China Sea is near 01:30 local time (about 17:00 to
20:00 UTC). Only the datasets needed are read, through HTTP range requests (RangeFile).
"""

from __future__ import annotations

import datetime as dt
import io
import re
from dataclasses import dataclass
from urllib.parse import quote

import h5py
import numpy as np

from darkvessel.s1 import aws

# satellite: (bucket, SDR/GEO file tag, cloud-mask file tag)
SATS = {
    "S-NPP": ("noaa-nesdis-snpp-pds", "npp", "npp"),
    "NOAA-20": ("noaa-nesdis-n20-pds", "j01", "j01"),
    "NOAA-21": ("noaa-nesdis-n21-pds", "j02", "n21"),
}
GRAN_RE = re.compile(r"_d(\d{8})_t(\d{7})_e(\d{7})_b(\d+)_")


def bucket_url(sat: str) -> str:
    return f"https://{SATS[sat][0]}.s3.amazonaws.com"


def list_keys(sat: str, prefix: str) -> list[str]:
    """All object keys under prefix (paginated ListObjectsV2), .h5 and .nc only."""
    keys, token = [], None
    while True:
        url = f"{bucket_url(sat)}/?list-type=2&prefix={quote(prefix)}&max-keys=1000"
        if token:
            url += f"&continuation-token={quote(token)}"
        text = aws._get(url).text
        keys += [k for k in re.findall(r"<Key>([^<]*)</Key>", text) if k.endswith((".h5", ".nc"))]
        m = re.search(r"<NextContinuationToken>([^<]*)</NextContinuationToken>", text)
        if not m:
            return keys
        token = m.group(1)


def granule_start(key: str) -> dt.datetime:
    d, t = GRAN_RE.search(key).group(1, 2)
    return dt.datetime.strptime(d + t[:6], "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)


def geo_keys(sat: str, day: dt.date, start_utc: str = "16:00", end_utc: str = "21:00") -> list[str]:
    """DNB geolocation granules of one UTC day inside a time window (the local night pass)."""
    tag = SATS[sat][1]
    keys = list_keys(sat, f"VIIRS-DNB-GEO/{day:%Y/%m/%d}/GDNBO_{tag}_d{day:%Y%m%d}_t")
    t0, t1 = (dt.datetime.combine(day, dt.time.fromisoformat(x), dt.timezone.utc) for x in (start_utc, end_utc))
    return sorted(k for k in keys if t0 <= granule_start(k) < t1)


class RangeFile(io.RawIOBase):
    """Read-only file over HTTP range requests, in cached blocks: what h5py needs to read a remote file.

    Plain synchronous requests (one session per thread, retries in darkvessel.s1.aws._get), so many
    threads can read many files at once without sharing an async event loop.
    """

    def __init__(self, url: str, block: int = 256 * 1024):
        super().__init__()
        self.url, self.block, self.pos, self.cache = url, block, 0, {}
        r = aws._get(url, headers={"Range": "bytes=0-0"})
        self.size = int(r.headers["Content-Range"].split("/")[-1])

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        self.pos = {io.SEEK_SET: offset, io.SEEK_CUR: self.pos + offset, io.SEEK_END: self.size + offset}[whence]
        return self.pos

    def _blocks(self, start: int, end: int) -> bytes:
        b0, b1 = start // self.block, (end - 1) // self.block
        missing = [b for b in range(b0, b1 + 1) if b not in self.cache]
        if missing:  # one request for the whole missing span
            lo, hi = missing[0] * self.block, min(self.size, (missing[-1] + 1) * self.block) - 1
            data = aws._get(self.url, headers={"Range": f"bytes={lo}-{hi}"}).content
            for b in range(missing[0], missing[-1] + 1):
                off = (b - missing[0]) * self.block
                self.cache[b] = data[off:off + self.block]
        buf = b"".join(self.cache[b] for b in range(b0, b1 + 1))
        return buf[start - b0 * self.block:end - b0 * self.block]

    def readinto(self, b):
        n = min(len(b), max(0, self.size - self.pos))
        if n == 0:
            return 0
        b[:n] = self._blocks(self.pos, self.pos + n)
        self.pos += n
        return n


def _open(sat: str, key: str, block: int):
    """Remote HDF5/NetCDF file handle for h5py."""
    return RangeFile(f"{bucket_url(sat)}/{key}", block=min(block, 1024 * 1024))


def gring(sat: str, geo_key: str) -> tuple[np.ndarray, np.ndarray, int]:
    """Granule outline (G-Ring lat, lon) and ascending (0) / descending (1) flag from the GEO metadata."""
    with _open(sat, geo_key, 128 * 1024) as f, h5py.File(f, "r") as h:
        a = h["Data_Products/VIIRS-DNB-GEO/VIIRS-DNB-GEO_Gran_0"].attrs
        return (np.ravel(a["G-Ring_Latitude"]).astype(float), np.ravel(a["G-Ring_Longitude"]).astype(float),
                int(np.ravel(a["Ascending/Descending_Indicator"])[0]))


def matching_key(sat: str, geo_key: str, kind: str) -> str | None:
    """SDR or cloud-mask key for the granule of geo_key (same start time; creation stamps differ)."""
    d, t = GRAN_RE.search(geo_key).group(1, 2)
    day = dt.datetime.strptime(d, "%Y%m%d")
    if kind == "sdr":
        prefix = f"VIIRS-DNB-SDR/{day:%Y/%m/%d}/SVDNB_{SATS[sat][1]}_d{d}_t{t}"
    elif kind == "cloud":
        prefix = f"VIIRS-JRR-CloudMask/{day:%Y/%m/%d}/JRR-CloudMask_v3r2_{SATS[sat][2]}_s{d}{t}"
    else:
        raise ValueError(kind)
    keys = list_keys(sat, prefix)
    return sorted(keys)[-1] if keys else None


@dataclass
class Granule:
    sat: str
    geo_key: str
    start: dt.datetime
    rad: np.ndarray            # W cm-2 sr-1, fill < -1
    lat: np.ndarray
    lon: np.ndarray
    moon_illum: float          # percent
    lunar_zenith_median: float # degrees
    solar_zenith_median: float # degrees
    cloud_lat: np.ndarray | None = None
    cloud_lon: np.ndarray | None = None
    cloud: np.ndarray | None = None   # 0 clear, 1 probably clear, 2 probably cloudy, 3 cloudy


def read_granule(sat: str, geo_key: str, with_cloud: bool = True) -> Granule:
    sdr_key = matching_key(sat, geo_key, "sdr")
    if sdr_key is None:
        raise FileNotFoundError(f"no SDR for {geo_key}")
    with _open(sat, sdr_key, 4 * 1024 * 1024) as f, h5py.File(f, "r") as h:
        rad = h["All_Data/VIIRS-DNB-SDR_All/Radiance"][:]
    with _open(sat, geo_key, 4 * 1024 * 1024) as f, h5py.File(f, "r") as h:
        g = h["All_Data/VIIRS-DNB-GEO_All"]
        lat, lon = g["Latitude"][:], g["Longitude"][:]
        lz, sz = g["LunarZenithAngle"][:], g["SolarZenithAngle"][:]
        moon = float(np.ravel(g["MoonIllumFraction"][:])[0])
    ok = lat > -90
    gran = Granule(sat, geo_key, granule_start(geo_key), rad, lat, lon, moon,
                   float(np.median(lz[ok])) if ok.any() else np.nan, float(np.median(sz[ok])) if ok.any() else np.nan)
    if with_cloud:
        ck = matching_key(sat, geo_key, "cloud")
        if ck:
            with _open(sat, ck, 4 * 1024 * 1024) as f, h5py.File(f, "r") as h:
                gran.cloud_lat, gran.cloud_lon, gran.cloud = h["Latitude"][:], h["Longitude"][:], h["CloudMask"][:]
    return gran
