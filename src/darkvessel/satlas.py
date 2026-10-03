"""Satlas marine infrastructure points (AI2; offshore platforms and wind turbines; ODC-BY).

Model predictions from Sentinel-2, with a score per point: an independent reference for fixed structures,
not ground truth. Snapshot: latest.geojson (39,582 points globally; docs/data_landscape.md, row B08).
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from scipy.spatial import cKDTree

from darkvessel.config import DATA_DIR
from darkvessel.s1 import aws

URL = "https://storage.googleapis.com/satlas-explorer-public/outputs/marine/latest.geojson"
CACHE = DATA_DIR / "cache" / "satlas_marine_latest.geojson"
R_M = 6371008.8


def points() -> gpd.GeoDataFrame:
    """The snapshot, downloaded once (with retries) and read from the local cache."""
    if not CACHE.exists() or CACHE.stat().st_size < 1000:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_bytes(aws._get(URL).content)
    return gpd.read_file(CACHE)


def distance_m(lon, lat, pts: gpd.GeoDataFrame | None = None) -> np.ndarray:
    """Distance in metres from each lon/lat to the nearest Satlas point (local equirectangular, fine below 50 km)."""
    pts = points() if pts is None else pts
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    lat0 = np.radians(float(np.median(lat))) if len(lat) else 0.0
    xy = lambda lo, la: np.c_[np.radians(lo) * R_M * np.cos(lat0), np.radians(la) * R_M]  # noqa: E731
    return cKDTree(xy(pts.geometry.x.to_numpy(), pts.geometry.y.to_numpy())).query(xy(lon, lat))[0]
