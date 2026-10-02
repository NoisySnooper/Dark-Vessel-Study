"""Where and how often Sentinel-1 images the AOI: pass counts on a regular lon/lat grid.

A "pass" is one datatake (one continuous acquisition); its slices are merged before
counting so slice overlaps are not double counted. Areas use exact spherical cell areas.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from rasterio import features
from rasterio.transform import from_origin

R_EARTH_KM = 6371.0088  # mean Earth radius (IUGG)


def merge_passes(fp: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """One geometry per (mission, datatake): slices of one acquisition merged."""
    key = fp["product_id"].str.split("_").str[-2]  # datatake id field of the product name
    g = fp.assign(datatake=key)
    agg = g.dissolve(by=["mission", "datatake"], aggfunc={"start_utc": "min", "pass_dir": "first", "orbit_rel": "first"})
    return agg.reset_index()


def grid_for(bounds, res_deg: float):
    west, south, east, north = bounds
    west, south = np.floor(west / res_deg) * res_deg, np.floor(south / res_deg) * res_deg
    east, north = np.ceil(east / res_deg) * res_deg, np.ceil(north / res_deg) * res_deg
    w, h = int(round((east - west) / res_deg)), int(round((north - south) / res_deg))
    return from_origin(west, north, res_deg, res_deg), (h, w)


def cell_area_km2(transform, shape) -> np.ndarray:
    """Exact area of each lon/lat cell on a sphere (km2), shape (h, w)."""
    h, w = shape
    lat_top = transform.f + np.arange(h) * transform.e
    lat_bot = lat_top + transform.e
    band = (R_EARTH_KM ** 2) * np.radians(transform.a) * np.abs(np.sin(np.radians(lat_top)) - np.sin(np.radians(lat_bot)))
    return np.repeat(band[:, None], w, axis=1)


def pass_counts(passes: gpd.GeoDataFrame, aoi_geom, res_deg: float = 0.05):
    """Count passes per cell inside the AOI. Returns (counts uint16, aoi_mask bool, transform)."""
    transform, shape = grid_for(aoi_geom.bounds, res_deg)
    counts = np.zeros(shape, np.uint16)
    for geom in passes.geometry:
        counts += features.rasterize([(geom, 1)], out_shape=shape, transform=transform, fill=0, dtype="uint16")
    aoi_mask = features.rasterize([(aoi_geom, 1)], out_shape=shape, transform=transform, fill=0, dtype="uint8").astype(bool)
    return counts, aoi_mask, transform


def coverage_stats(counts: np.ndarray, aoi_mask: np.ndarray, transform, days: int) -> dict:
    area = cell_area_km2(transform, counts.shape)
    a_total = float(area[aoi_mask].sum())
    out = {"aoi_area_km2_grid": round(a_total)}
    for k in (1, 3, 6, 10):
        out[f"share_imaged_ge_{k}"] = round(float(area[aoi_mask & (counts >= k)].sum()) / a_total, 4)
    out["area_never_imaged_km2"] = round(float(area[aoi_mask & (counts == 0)].sum()))
    imaged = aoi_mask & (counts > 0)
    if imaged.any():
        c = counts[imaged].astype(float)
        w = area[imaged]
        out["mean_passes_where_imaged"] = round(float((c * w).sum() / w.sum()), 2)
        out["median_passes_where_imaged"] = float(np.median(c))
        out["typical_revisit_days_where_imaged"] = round(days / out["mean_passes_where_imaged"], 1)
    out["max_passes"] = int(counts[aoi_mask].max()) if aoi_mask.any() else 0
    return out
