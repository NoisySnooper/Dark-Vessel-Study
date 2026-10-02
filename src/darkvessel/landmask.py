"""Land mask from ESA WorldCover 2021 (10 m), sampled onto a SAR window.

WorldCover codes open sea as 0 (no data, outside mapped land) and nearshore or inland
water as 80. 'Sea' here is water (0 or 80) connected to open sea (0). Inland water
such as Ca Mau shrimp ponds is masked with the land. A shore buffer (default 1 km)
removes coastal clutter, river mouths, and mapping error.
"""

from __future__ import annotations

import math

import numpy as np
import rasterio
from rasterio.merge import merge
from scipy import ndimage

from darkvessel.config import WORLDCOVER_URL

WC_RES_DEG = 1 / 12000  # native 10 m grid
WATER_CODES = (0, 80)


def worldcover_tile_names(west: float, south: float, east: float, north: float) -> list[str]:
    """WorldCover 3x3 degree tiles intersecting the box (named by lower-left corner)."""
    names = []
    for lat0 in range(int(math.floor(south / 3) * 3), int(math.floor(north / 3) * 3) + 1, 3):
        for lon0 in range(int(math.floor(west / 3) * 3), int(math.floor(east / 3) * 3) + 1, 3):
            ns = f"{'N' if lat0 >= 0 else 'S'}{abs(lat0):02d}"
            ew = f"{'E' if lon0 >= 0 else 'W'}{abs(lon0):03d}"
            names.append(f"ESA_WorldCover_10m_2021_v200_{ns}{ew}_Map.tif")
    return names


def read_worldcover(bounds: tuple[float, float, float, float], factor: int = 8):
    """Mosaic WorldCover over `bounds` at `factor` x native resolution (uses COG overviews).

    Tiles that do not exist (all-ocean areas) are skipped; missing area reads as 0 (sea).
    Returns (array uint8, affine transform).
    """
    srcs = []
    for name in worldcover_tile_names(*bounds):
        try:
            srcs.append(rasterio.open(f"/vsicurl/{WORLDCOVER_URL}/{name}"))
        except rasterio.errors.RasterioIOError:
            continue
    res = WC_RES_DEG * factor
    if not srcs:
        # WorldCover has no tiles over open ocean: everything here is sea (code 0).
        west, south, east, north = bounds
        w, h = max(1, int(np.ceil((east - west) / res))), max(1, int(np.ceil((north - south) / res)))
        return np.zeros((h, w), np.uint8), rasterio.transform.from_origin(west, north, res, res)
    arr, transform = merge(srcs, bounds=bounds, res=(res, res), nodata=0, resampling=rasterio.enums.Resampling.mode)
    for s in srcs:
        s.close()
    return arr[0], transform


def sea_mask_on_grid(lon: np.ndarray, lat: np.ndarray, cell_m: float, buffer_m: float = 1000.0,
                     factor: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """Classify a (decimated) SAR grid given per-cell lon/lat.

    Returns (sea_ok, land) boolean arrays of lon.shape:
      land   = WorldCover land or inland water
      sea_ok = open-sea-connected water farther than `buffer_m` from land
    """
    pad = 0.02
    bounds = (float(np.nanmin(lon)) - pad, float(np.nanmin(lat)) - pad,
              float(np.nanmax(lon)) + pad, float(np.nanmax(lat)) + pad)
    wc, tr = read_worldcover(bounds, factor)
    inv = ~tr
    cols, rows = inv * (np.nan_to_num(lon), np.nan_to_num(lat))
    rows = np.clip(rows.astype(int), 0, wc.shape[0] - 1)
    cols = np.clip(cols.astype(int), 0, wc.shape[1] - 1)
    code = wc[rows, cols]
    water = np.isin(code, WATER_CODES) & np.isfinite(lon)
    # keep only water bodies that touch open sea (code 0)
    lab, n = ndimage.label(water, structure=np.ones((3, 3)))
    open_ids = np.unique(lab[(code == 0) & water])
    sea = np.isin(lab, open_ids[open_ids > 0])
    land = ~sea & np.isfinite(lon)
    dist_m = ndimage.distance_transform_edt(~land) * cell_m
    return sea & (dist_m > buffer_m), land


def upsample_nearest(a: np.ndarray, factor: int, shape: tuple[int, int]) -> np.ndarray:
    """Repeat a decimated mask back to full resolution and crop to `shape`."""
    return np.repeat(np.repeat(a, factor, axis=0), factor, axis=1)[: shape[0], : shape[1]]
