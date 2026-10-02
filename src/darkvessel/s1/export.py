"""Warp radar-geometry windows to a map grid and write Cloud-Optimized GeoTIFFs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.control import GroundControlPoint
from rasterio.warp import Resampling, calculate_default_transform, reproject
from rasterio.windows import Window

from darkvessel.config import CRS_UTM


def window_gcps(geocoder, window: Window, margin_lines: int = 2) -> list[GroundControlPoint]:
    """Geolocation-grid points near the window, shifted to window pixel coordinates."""
    r0, c0 = int(window.row_off), int(window.col_off)
    r1, c1 = r0 + int(window.height), c0 + int(window.width)
    L, P = geocoder.lines, geocoder.pixels
    li = np.searchsorted(L, [r0, r1])
    pi = np.searchsorted(P, [c0, c1])
    lsel = L[max(0, li[0] - margin_lines) : min(len(L), li[1] + margin_lines)]
    psel = P[max(0, pi[0] - margin_lines) : min(len(P), pi[1] + margin_lines)]
    rr, cc = np.meshgrid(lsel, psel, indexing="ij")
    lon, lat = geocoder.lonlat(rr.ravel(), cc.ravel())
    return [
        GroundControlPoint(row=float(r - r0), col=float(c - c0), x=float(x), y=float(y), z=0.0)
        for r, c, x, y in zip(rr.ravel(), cc.ravel(), lon, lat)
    ]


def scale_gcps(gcps, factor: float) -> list[GroundControlPoint]:
    """GCPs for an array decimated by `factor` (pixel coordinates divided by it)."""
    return [GroundControlPoint(row=g.row / factor, col=g.col / factor, x=g.x, y=g.y, z=0.0) for g in gcps]


def warp_to_map(arr: np.ndarray, gcps, res_m: float = 20.0, dst_crs: str = CRS_UTM,
                resampling: Resampling = Resampling.average, nodata=np.nan, dst_transform=None, dst_shape=None):
    """Warp a radar-geometry array to `dst_crs` using GCP thin-plate splines.

    Give `dst_transform` and `dst_shape` to land on an existing grid; otherwise a grid at
    `res_m` covering the source is computed.
    """
    h, w = arr.shape
    if dst_transform is None:
        transform, dw, dh = calculate_default_transform(
            "EPSG:4326", dst_crs, w, h, gcps=gcps, resolution=res_m, SRC_METHOD="GCP_TPS"
        )
    else:
        transform, (dh, dw) = dst_transform, dst_shape
    dst = np.full((dh, dw), nodata, dtype=arr.dtype)
    reproject(
        arr, dst, gcps=gcps, src_crs="EPSG:4326", dst_transform=transform, dst_crs=dst_crs,
        resampling=resampling, src_nodata=nodata, dst_nodata=nodata, SRC_METHOD="GCP_TPS",
    )
    return dst, transform


def write_cog(arr: np.ndarray, transform, crs: str, path: str | Path, nodata=None, tags: dict | None = None,
              zlevel: int = 6):
    """Write a single-band COG (deflate, internal overviews). `zlevel` 1-9 trades speed for size."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "COG", "width": arr.shape[1], "height": arr.shape[0], "count": 1,
        "dtype": arr.dtype, "crs": crs, "transform": transform, "nodata": nodata,
        "compress": "DEFLATE", "predictor": 3 if arr.dtype.kind == "f" else 2, "blocksize": 512, "level": zlevel,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr, 1)
        if tags:
            dst.update_tags(**{k: str(v) for k, v in tags.items()})
    return path
