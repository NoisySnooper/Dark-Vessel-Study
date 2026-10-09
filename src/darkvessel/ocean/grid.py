"""Shared grids, cell lookups and raster writers for the ocean context layers and the expected-activity model.

Two lon/lat grids over the bounds of the South China Sea AOI:
  model grid, 0.25 degree: the cell-night grid of scripts/15_viirs_lights.py (same transform and shape), the unit
      of the expected-activity model;
  fine grid, 0.01 degree: static layers (depth, distance to coast) and 1 km SST.
Both grids come from darkvessel.coverage.grid_for over the AOI bounds: the model grid has its origin at 99.0E 24.0N
(109 x 94 cells), the fine grid at 99.16E 23.76N (2698 x 2311 cells). Model-cell edges are whole multiples of 0.01
degree, so every fine cell lies inside exactly one model cell (25 x 25 fine cells per full model cell).
Every raster product is written twice, as COGs: EPSG:4326 on its native grid, and UTM 49N (EPSG:32649), the zone at
the centre of the sea.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from rasterio import features
from rasterio.warp import Resampling, calculate_default_transform, reproject

from darkvessel.config import CRS_GEO, CRS_UTM_REGIONAL, DARK_CAVEAT_SHORT, DATA_DIR, DEFAULT_AOI

MODEL_RES = 0.25
FINE_RES = 0.01
SMALL_DIR = DATA_DIR / "outputs" / "small"
OCEAN_CACHE = DATA_DIR / "cache" / "ocean"
NODATA = -9999.0

OCEAN_CAVEAT = ("Ocean and weather layers describe the sea, not what any vessel does. Expected activity says where lit "
                "boats or radar candidates usually are, given the sea and the weather; a cell above or below it is a "
                "lead for review, not evidence. " + DARK_CAVEAT_SHORT)

# Reporting boxes (west, south, east, north) of scripts/21_viirs_regions.py, mirrored in darkvessel.ocean.model:
# plain boxes for reporting, not boundaries or claims. Cells outside every box are 'other'.
REPORTING_BOXES = {"Gulf of Tonkin": (105.5, 17.0, 110.0, 22.5), "North shelf": (110.0, 18.0, 118.0, 23.5),
                   "Gulf of Thailand": (99.0, 6.0, 105.0, 14.0), "South Vietnam shelf": (105.0, 6.0, 110.0, 12.0),
                   "Central sea": (110.0, 6.0, 118.0, 17.0), "Southern sea": (102.0, -3.5, 110.0, 6.0)}


def region_of(lon, lat) -> np.ndarray:
    """Reporting box of each point ('other' outside all boxes; the first box in REPORTING_BOXES order wins)."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    out = np.full(lon.shape, "other", dtype=object)
    for name, (w, s, e, n) in reversed(list(REPORTING_BOXES.items())):
        out[(lon >= w) & (lon < e) & (lat >= s) & (lat < n)] = name
    return out.astype(str)


def aoi_geom(name: str = DEFAULT_AOI):
    from darkvessel.aoi import aoi_gdf

    return aoi_gdf(name).geometry.iloc[0]


def grid(res: float, name: str = DEFAULT_AOI):
    """(transform, (height, width)) of the lon/lat grid at `res` degrees that covers the AOI bounds."""
    from darkvessel.coverage import grid_for

    return grid_for(aoi_geom(name).bounds, res)


def model_grid():
    return grid(MODEL_RES)


def fine_grid():
    return grid(FINE_RES)


def centres(transform, shape):
    """(lon, lat) 2-D arrays of cell centres."""
    h, w = shape
    lon = transform.c + (np.arange(w) + 0.5) * transform.a
    lat = transform.f + (np.arange(h) + 0.5) * transform.e
    return np.meshgrid(lon, lat)


def aoi_mask(transform, shape, name: str = DEFAULT_AOI) -> np.ndarray:
    """True for cells whose centre lies inside the AOI polygon."""
    return features.rasterize([(aoi_geom(name), 1)], out_shape=shape, transform=transform, dtype="uint8").astype(bool)


def cell_index(transform, shape, lon, lat):
    """(row, col, inside) of the grid cell that contains each point."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    row = np.floor((lat - transform.f) / transform.e).astype(np.int64)
    col = np.floor((lon - transform.c) / transform.a).astype(np.int64)
    inside = (row >= 0) & (row < shape[0]) & (col >= 0) & (col < shape[1])
    return row, col, inside


def sample(arr: np.ndarray, transform, lon, lat, fill=np.nan) -> np.ndarray:
    """Value of the cell that contains each point (`fill` outside the grid)."""
    row, col, inside = cell_index(transform, arr.shape, lon, lat)
    out = np.full(np.shape(lon), fill, dtype=np.result_type(arr.dtype, np.float32))
    out[inside] = arr[row[inside], col[inside]]
    return out


def bilinear(arr: np.ndarray, transform, lon, lat, wrap_lon: bool = False) -> np.ndarray:
    """Bilinear value of a north-up lon/lat grid at each point, NaN-aware: the weights of NaN neighbours are dropped
    and the rest renormalised (NaN only when all four neighbours are NaN or the point is off the grid).

    A point that sits on the shared corner of four cells (a 0.25 degree model-cell centre on the GFS 0.25 degree grid)
    gets the plain mean of the four. `wrap_lon` wraps longitudes onto a global grid's own 0..360 or -180..180 range.
    """
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    h, w = arr.shape
    if wrap_lon:
        lon = np.mod(lon - transform.c, 360.0) + transform.c
    x = (lon - transform.c) / transform.a - 0.5  # fractional column of the cell centres
    y = (lat - transform.f) / transform.e - 0.5
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = x - x0, y - y0
    num = np.zeros(lon.shape)
    den = np.zeros(lon.shape)
    for dy, dx, wt in ((0, 0, (1 - fy) * (1 - fx)), (0, 1, (1 - fy) * fx), (1, 0, fy * (1 - fx)), (1, 1, fy * fx)):
        r, c = y0 + dy, x0 + dx
        if wrap_lon:
            c = np.mod(c, w)
        ok = (r >= 0) & (r < h) & (c >= 0) & (c < w)
        v = np.full(lon.shape, np.nan)
        v[ok] = arr[r[ok], c[ok]]
        good = np.isfinite(v) & (wt > 0)
        num += np.where(good, v * wt, 0.0)
        den += np.where(good, wt, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def bin_mean(lon, lat, values, transform, shape) -> np.ndarray:
    """Mean of point values per grid cell (NaN where no finite point falls)."""
    v = np.asarray(values, float)
    row, col, inside = cell_index(transform, shape, lon, lat)
    ok = inside & np.isfinite(v)
    flat = row[ok] * shape[1] + col[ok]
    s = np.bincount(flat, weights=v[ok], minlength=shape[0] * shape[1])
    n = np.bincount(flat, minlength=shape[0] * shape[1])
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, s / n, np.nan).reshape(shape)


def write_dual_cog(arr: np.ndarray, transform, name: str, units: str, source: str, *, utm_res_m: float | None = None,
                   resampling: Resampling = Resampling.bilinear, nodata: float = NODATA, tags: dict | None = None,
                   out_dir: Path = SMALL_DIR, zlevel: int = 9) -> list[Path]:
    """Write `arr` (lon/lat grid) as <name>_4326.tif and <name>_utm49n.tif COGs in `out_dir`.

    NaN becomes `nodata`. `utm_res_m` defaults to the grid spacing at the equator (0.01 degree -> 1,113 m).
    Tags carry units, source, nodata meaning and the ocean caveat, so the files explain themselves in ArcGIS Pro.
    """
    from darkvessel.s1.export import write_cog

    a = np.where(np.isfinite(arr), arr, nodata).astype(np.float32)
    meta = {"units": units, "source": source, "nodata": f"{nodata} = no data or outside the AOI", "caveat": OCEAN_CAVEAT}
    meta.update(tags or {})
    paths = [write_cog(a, transform, CRS_GEO, out_dir / f"{name}_4326.tif", nodata=nodata, tags=meta, zlevel=zlevel)]
    h, w = a.shape
    bounds = (transform.c, transform.f + h * transform.e, transform.c + w * transform.a, transform.f)
    res = utm_res_m or round(abs(transform.a) * 111_320)
    t2, w2, h2 = calculate_default_transform(CRS_GEO, CRS_UTM_REGIONAL, w, h, *bounds, resolution=res)
    dst = np.full((h2, w2), nodata, np.float32)
    reproject(a, dst, src_transform=transform, src_crs=CRS_GEO, dst_transform=t2, dst_crs=CRS_UTM_REGIONAL,
              resampling=resampling, src_nodata=nodata, dst_nodata=nodata)
    paths.append(write_cog(dst, t2, CRS_UTM_REGIONAL, out_dir / f"{name}_utm49n.tif", nodata=nodata, tags=meta, zlevel=zlevel))
    return paths
