"""Helpers shared by the loaders: model-grid cell ids, filters, geometry to GeoJSON, a metric KD tree."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely.geometry import mapping

from ..envelope import ApiError
from ..records import to_utc_series

# 0.25 degree model grid (darkvessel.ocean.grid.model_grid): origin 99.0E 24.0N, row 0 north, 109 rows x 94 columns.
GRID_W, GRID_N, GRID_RES, GRID_ROWS, GRID_COLS = 99.0, 24.0, 0.25, 109, 94
R_EARTH_M = 6_371_000.0


def obj_cache(owner, name: str) -> dict:
    """A cache dict kept on a loader's data object, so it lives exactly as long as the data it derives from: a reload of
    that loader starts empty caches, and a reload of any other loader keeps them."""
    caches = owner.__dict__.setdefault("_scs_caches", {})
    return caches.setdefault(name, {})


def cell_rc(lon, lat):
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    row = np.floor((GRID_N - lat) / GRID_RES)
    col = np.floor((lon - GRID_W) / GRID_RES)
    ok = np.isfinite(row) & np.isfinite(col) & (row >= 0) & (row < GRID_ROWS) & (col >= 0) & (col < GRID_COLS)
    return np.where(ok, row, -1).astype(int), np.where(ok, col, -1).astype(int), ok


def cell_ids(lon, lat) -> np.ndarray:
    row, col, ok = cell_rc(lon, lat)
    return np.array([f"r{r}c{c}" if k else None for r, c, k in zip(row, col, ok)], dtype=object)


def cell_centre(row: int, col: int) -> tuple[float, float]:
    return GRID_W + (col + 0.5) * GRID_RES, GRID_N - (row + 0.5) * GRID_RES


def parse_cell_id(cell_id: str) -> tuple[int, int] | None:
    import re

    m = re.fullmatch(r"r(\d+)c(\d+)", cell_id or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_bbox(bbox: str | None):
    if not bbox:
        return None
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError:
        raise ApiError(422, "bad_bbox", "bbox must be w,s,e,n in decimal degrees") from None
    if not (w <= e and s <= n):
        raise ApiError(422, "bad_bbox", "bbox must be w,s,e,n with w <= e and s <= n")
    return w, s, e, n


def parse_time(t: str | None, name: str):
    if not t:
        return None
    ts = pd.to_datetime(t, utc=True, errors="coerce", format="ISO8601")
    if pd.isna(ts):
        raise ApiError(422, "bad_time", f"{name} must be an ISO 8601 time")
    return ts


def csv_list(v: str | None) -> list[str] | None:
    if v is None or v == "":
        return None
    return [x.strip() for x in v.split(",") if x.strip()]


def mask_window(df: pd.DataFrame, t0=None, t1=None, bbox=None, tcol="_t", lon="lon", lat="lat") -> np.ndarray:
    m = np.ones(len(df), bool)
    if t0 is not None:
        m &= (df[tcol] >= t0).to_numpy(na_value=False) if len(df) else m
    if t1 is not None:
        m &= (df[tcol] <= t1).to_numpy(na_value=False) if len(df) else m
    if bbox is not None:
        w, s, e, n = bbox
        m &= ((df[lon] >= w) & (df[lon] <= e) & (df[lat] >= s) & (df[lat] <= n)).to_numpy(na_value=False)
    return m


def add_time(df: pd.DataFrame, col: str, out: str = "_t") -> pd.DataFrame:
    df[out] = to_utc_series(df[col]) if col in df else pd.NaT
    return df


def geojson(geom, ndigits: int = 5):
    """Shapely geometry to a GeoJSON dict with rounded coordinates (None for empty)."""
    if geom is None or getattr(geom, "is_empty", True):
        return None
    g = mapping(geom)

    def rnd(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            return [round(float(x), ndigits) for x in c]
        return [rnd(x) for x in c]

    return {"type": g["type"], "coordinates": rnd(g["coordinates"])}


class MetricTree:
    """KD tree on lon/lat projected to local metres (equirectangular at the data's mean latitude)."""

    def __init__(self, lon, lat):
        lon, lat = np.asarray(lon, float), np.asarray(lat, float)
        ok = np.isfinite(lon) & np.isfinite(lat)
        self.index = np.flatnonzero(ok)
        self.lat0 = float(np.nanmean(lat[ok])) if ok.any() else 0.0
        self.k = math.cos(math.radians(self.lat0))
        self.tree = cKDTree(self._xy(lon[ok], lat[ok])) if ok.any() else None

    def _xy(self, lon, lat):
        lon, lat = np.asarray(lon, float), np.asarray(lat, float)
        return np.column_stack([np.radians(lon) * R_EARTH_M * self.k, np.radians(lat) * R_EARTH_M])

    def within(self, lon, lat, radius_m: float) -> np.ndarray:
        """Row positions (into the original arrays) within radius of one point."""
        if self.tree is None:
            return np.array([], int)
        hits = self.tree.query_ball_point(self._xy([lon], [lat])[0], radius_m)
        return self.index[np.asarray(hits, int)] if hits else np.array([], int)

    def nearest(self, lon, lat, max_m: float):
        """(position, distance_m) of the nearest point to each query, position -1 beyond max_m."""
        if self.tree is None:
            n = len(np.atleast_1d(lon))
            return np.full(n, -1), np.full(n, np.inf)
        d, i = self.tree.query(self._xy(lon, lat), distance_upper_bound=max_m)
        ok = np.isfinite(d)
        pos = np.where(ok, self.index[np.minimum(i, len(self.index) - 1)], -1)
        return pos, d
