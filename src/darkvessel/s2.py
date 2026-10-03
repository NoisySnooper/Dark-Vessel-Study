"""Sentinel-2 L2A on the AWS Open Data mirror (bucket sentinel-cogs), read in place: no account, no download.

Layout: sentinel-s2-l2a-cogs/<utm zone>/<latitude band>/<100 km square>/<year>/<month>/<item id>/
with one Cloud-Optimized GeoTIFF per band (B08 = near infrared, 10 m; SCL = scene classification, 20 m)
and a STAC item JSON carrying eo:cloud_cover. The Sentinel-2 tile id is the MGRS 100 km square of the point.

Used for optical confirmation of radar objects: a structure fixed in place shows in a cloud-free image taken
days apart from the radar pass; a moving vessel usually does not.
"""

from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache

import mgrs
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.windows import Window

from darkvessel.s1 import aws

BUCKET = "https://sentinel-cogs.s3.us-west-2.amazonaws.com"
PREFIX = "sentinel-s2-l2a-cogs"
GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
            "GDAL_HTTP_MAX_RETRY": "4", "GDAL_HTTP_RETRY_DELAY": "2"}
# Scene classification values that make a view unusable: no data, saturated, cloud shadow, cloud, cirrus
SCL_BAD = (0, 1, 3, 8, 9, 10)
_MGRS = mgrs.MGRS()


def tile_of(lat: float, lon: float) -> str:
    """Sentinel-2 tile id (MGRS grid zone and 100 km square), e.g. '48PUQ'."""
    return _MGRS.toMGRS(lat, lon, MGRSPrecision=0)


def tile_prefix(tile: str) -> str:
    m = re.match(r"(\d{1,2})([C-X])([A-Z]{2})$", tile)
    if not m:
        raise ValueError(f"not a tile id: {tile}")
    return f"{PREFIX}/{int(m.group(1))}/{m.group(2)}/{m.group(3)}"


def list_items(tile: str, start: dt.date, end: dt.date) -> list[dict]:
    """Items of one tile with an acquisition date in [start, end]: id, date, cloud cover, base URL."""
    months = sorted({(d.year, d.month) for d in (start + dt.timedelta(days=i) for i in range((end - start).days + 1))})
    out = []
    for y, mth in months:
        prefix = f"{tile_prefix(tile)}/{y}/{mth}/"
        text = aws._get(f"{BUCKET}/?list-type=2&prefix={prefix}&delimiter=/").text
        for p in re.findall(r"<Prefix>([^<]*_L2A/)</Prefix>", text):
            item = p.rstrip("/").rsplit("/", 1)[-1]
            day = dt.datetime.strptime(item.split("_")[2], "%Y%m%d").date()
            if start <= day <= end:
                meta = aws._get(f"{BUCKET}/{p}{item}.json").json()
                out.append({"item": item, "date": day, "datetime": meta["properties"]["datetime"],
                            "cloud": float(meta["properties"].get("eo:cloud_cover", 100.0)), "base": f"{BUCKET}/{p}"})
    return sorted(out, key=lambda r: r["cloud"])


@lru_cache(maxsize=64)
def _transformer(crs_wkt: str) -> Transformer:
    return Transformer.from_crs("EPSG:4326", crs_wkt, always_xy=True)


def read_around(ds, lon: float, lat: float, half: int) -> np.ndarray | None:
    """(2 half + 1) square window centred on lon/lat from an open dataset; None if it leaves the image."""
    x, y = _transformer(ds.crs.to_wkt()).transform(lon, lat)
    row, col = ds.index(x, y)
    if row - half < 0 or col - half < 0 or row + half >= ds.height or col + half >= ds.width:
        return None
    return ds.read(1, window=Window(col - half, row - half, 2 * half + 1, 2 * half + 1))


def nir_contrast(nir: np.ndarray, core: int = 2) -> tuple[float, float, float]:
    """(peak of the central (2 core + 1) square, median of the window, peak minus median), in DN."""
    c = nir.shape[0] // 2
    peak = float(nir[c - core:c + core + 1, c - core:c + core + 1].max())
    med = float(np.median(nir))
    return peak, med, peak - med


def open_band(item: dict, band: str):
    return rasterio.open(f"/vsicurl/{item['base']}{band}.tif")
