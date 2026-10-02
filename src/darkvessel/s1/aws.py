"""Sentinel-1 GRD access through the AWS Open Data mirror (bucket sentinel-s1-l1c).

Why this path: on 2026-10-02 the project's network policy blocked the Planetary
Computer and Copernicus Data Space STAC APIs, while anonymous HTTPS reads of this
bucket worked. Search therefore runs on the bucket listing (one folder per UTC day)
plus each product's productInfo.json footprint and manifest.safe orbit metadata.

Bucket layout: GRD/{yyyy}/{m}/{d}/{mode}/{pol}/{product_id}/
    productInfo.json, manifest.safe, measurement/iw-vv.tiff (COG, 1024 px deflate tiles),
    annotation/iw-vv.xml, annotation/calibration/{calibration,noise}-iw-vv.xml, ...
"""

from __future__ import annotations

import datetime as dt
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

import requests
from shapely.geometry import shape

from darkvessel.config import AWS_S1_BUCKET_URL

PRODUCT_RE = re.compile(
    r"^(?P<mission>S1[A-D])_(?P<mode>IW|EW|S[1-6]|WV)_(?P<ptype>GRD|SLC|RAW|OCN)(?P<res>[FHM_])_"
    r"(?P<level>\d)(?P<pclass>[SA])(?P<pol>SH|SV|DH|DV|HH|VV)_(?P<start>\d{8}T\d{6})_"
    r"(?P<stop>\d{8}T\d{6})_(?P<orbit>\d{6})_(?P<datatake>[0-9A-F]{6})_(?P<uid>[0-9A-F]{4})$"
)
_S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
_local = threading.local()

# Sentinel-1 is sun-synchronous (ascending node ~18:00 local solar time). Over the
# Vietnamese coast (~105E) passes fall near 11:00 UTC (ascending) and 23:00 UTC
# (descending). These windows pre-filter product names before any footprint fetch.
VIETNAM_UTC_WINDOWS = ((dt.time(10, 30), dt.time(11, 35)), (dt.time(22, 30), dt.time(23, 30)))


def utc_windows_for(lon_min: float, lon_max: float, margin_min: float = 30.0):
    """UTC time-of-day windows in which Sentinel-1 passes over longitudes lon_min..lon_max.

    Local solar time of the passes is ~18:00 (ascending) and ~06:00 (descending); UTC is
    local time minus longitude/15 h. The margin covers swath offset, latitude and slice length.
    """
    def window(local_h: float):
        a = (local_h - lon_max / 15.0) * 60 - margin_min
        b = (local_h - lon_min / 15.0) * 60 + margin_min
        to_t = lambda m: dt.time(int(m % 1440) // 60, int(m % 1440) % 60)  # noqa: E731
        return to_t(a), to_t(b)

    return (window(18.0), window(6.0))


def _session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


RETRY_WAITS_S = (2, 4, 8, 16)  # transient proxy and connection errors: back off, then give up


def _get(url: str, **kw) -> requests.Response:
    """GET with retries on connection, proxy and timeout errors and on 5xx; 4xx fails at once."""
    for wait in (*RETRY_WAITS_S, None):
        try:
            r = _session().get(url, timeout=60, **kw)
            if r.status_code < 500 or wait is None:
                r.raise_for_status()
                return r
        except (requests.ConnectionError, requests.Timeout):
            if wait is None:
                raise
        time.sleep(wait)
    raise RuntimeError("unreachable")


def parse_product_id(product_id: str) -> dict:
    """Parse a Sentinel-1 product name into its fields. Raises ValueError if malformed."""
    m = PRODUCT_RE.match(product_id)
    if not m:
        raise ValueError(f"Not a Sentinel-1 product id: {product_id!r}")
    d = m.groupdict()
    d["start"] = dt.datetime.strptime(d["start"], "%Y%m%dT%H%M%S")
    d["stop"] = dt.datetime.strptime(d["stop"], "%Y%m%dT%H%M%S")
    d["orbit"] = int(d["orbit"])
    d["product_id"] = product_id
    return d


def in_utc_windows(t: dt.datetime | dt.time, windows: Iterable[tuple[dt.time, dt.time]]) -> bool:
    """True if the time of day of `t` falls in any (start, end) window. Handles windows over midnight."""
    tod = t.time() if isinstance(t, dt.datetime) else t
    for a, b in windows:
        if a <= b and a <= tod <= b:
            return True
        if a > b and (tod >= a or tod <= b):
            return True
    return False


def list_prefixes(prefix: str, bucket_url: str = AWS_S1_BUCKET_URL) -> list[str]:
    """List the immediate sub-prefixes ('folders') under `prefix` with ListObjectsV2."""
    out, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix, "delimiter": "/", "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        root = ET.fromstring(_get(bucket_url + "/", params=params).content)
        out += [e.text for e in root.findall("s3:CommonPrefixes/s3:Prefix", _S3_NS)]
        nxt = root.find("s3:NextContinuationToken", _S3_NS)
        if nxt is None:
            return out
        token = nxt.text


def list_day_products(day: dt.date, mode: str = "IW", pol: str = "DV") -> list[str]:
    """Product paths (bucket keys without trailing slash) for one UTC day."""
    prefix = f"GRD/{day.year}/{day.month}/{day.day}/{mode}/{pol}/"
    return [p.rstrip("/") for p in list_prefixes(prefix)]


def product_url(path: str, rel: str) -> str:
    return f"{AWS_S1_BUCKET_URL}/{path}/{rel}"


def vsicurl(path: str, rel: str) -> str:
    """GDAL virtual path for a file inside a product, for windowed reads."""
    return "/vsicurl/" + product_url(path, rel)


def fetch_product_info(path: str) -> dict:
    return _get(product_url(path, "productInfo.json")).json()


def fetch_manifest_meta(path: str) -> dict:
    """Orbit pass, relative orbit, platform and polarisations from manifest.safe."""
    txt = _get(product_url(path, "manifest.safe")).text

    def first(tag: str) -> str | None:
        m = re.search(rf"<{tag}[^>]*>([^<]*)<", txt)
        return m.group(1).strip() if m else None

    return {
        "pass_dir": (first("s1:pass") or "").upper() or None,
        "orbit_rel": int(first("safe:relativeOrbitNumber")) if first("safe:relativeOrbitNumber") else None,
        "platform": "SENTINEL-1" + (first("safe:number") or "?"),
        "pols": re.findall(r"<s1sarl1:transmitterReceiverPolarisation>([A-Z]{2})<", txt),
    }


def search_aws(
    aoi_geom,
    start: dt.date,
    end: dt.date,
    missions: tuple[str, ...] = ("S1C", "S1D"),
    mode_pols: tuple[tuple[str, str], ...] = (("IW", "DV"), ("IW", "SV")),
    utc_windows=VIETNAM_UTC_WINDOWS,
    max_workers: int = 16,
) -> list[dict]:
    """Find GRD products whose footprint intersects `aoi_geom` (EPSG:4326) between dates.

    Returns one record per product with footprint geometry and orbit metadata.
    `utc_windows=None` disables the time-of-day pre-filter (slow: ~850 products/day).
    """
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    jobs = [(d, m, p) for d in days for m, p in mode_pols]
    with ThreadPoolExecutor(max_workers) as ex:
        listings = list(ex.map(lambda j: list_day_products(*j), jobs))
    candidates = []
    for paths in listings:
        for path in paths:
            try:
                meta = parse_product_id(path.rsplit("/", 1)[-1])
            except ValueError:
                continue
            if meta["mission"] not in missions:
                continue
            if utc_windows and not in_utc_windows(meta["start"], utc_windows):
                continue
            candidates.append(path)

    def check(path: str) -> dict | None:
        info = fetch_product_info(path)
        geom = shape(info["footprint"])
        if not geom.intersects(aoi_geom):
            return None
        rec = parse_product_id(info["id"])
        rec.update(fetch_manifest_meta(path))
        rec.update({"path": path, "geometry": geom, "source": "aws:sentinel-s1-l1c"})
        return rec

    with ThreadPoolExecutor(max_workers) as ex:
        hits = [r for r in ex.map(check, candidates) if r is not None]
    return sorted(hits, key=lambda r: r["start"])
