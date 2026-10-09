"""SST fronts on the 1 km grid: gradient magnitude, a hysteresis front mask, front lines and distance to the nearest front.

Method (gradient-based, single image):
  1. 3 x 3 median filter of the daily SST. NaN (land, no data) is filled from the nearest valid pixel before filtering
     and every pixel whose 3 x 3 neighbourhood touched a NaN is dropped again, so the coast makes no false gradient.
  2. Gradient magnitude in degrees C per km with metric spacing: dy = 0.01 degree x 111.32 km, dx = dy x cos(latitude).
  3. Front mask by hysteresis: a pixel is a front if its gradient is above the high threshold, or above the low
     threshold and connected (8-neighbours) to a pixel above the high one. The thresholds are quantiles of the pooled
     gradient distribution of all days of the window over AOI sea (scripts/23_daily_ocean.py records the values), so
     the rule adapts to this sea and this season instead of a number carried over from another region.
  4. Pixels within 2 km of the Natural Earth coast, or flagged land or ice by the SST mask, are never fronts.
References for gradient-based SST front detection (titles and DOIs resolved through Crossref on 2026-10-08; the
papers' full texts were not accessible from this session): Belkin and O'Reilly (2009), An algorithm for oceanic front
detection in chlorophyll and SST satellite imagery, J. Marine Systems 78, 319-326, doi:10.1016/j.jmarsys.2008.11.018;
Cayula and Cornillon (1992), Edge detection algorithm for SST images, J. Atmos. Oceanic Technol. 9, 67-80,
doi:10.1175/1520-0426(1992)009<0067:EDAFSI>2.0.CO;2.
"""

from __future__ import annotations

import numpy as np
from rasterio import features
from scipy import ndimage
from scipy.spatial import cKDTree
from shapely.geometry import LineString
from shapely.ops import linemerge

from darkvessel.ocean.grid import OCEAN_CACHE, centres, fine_grid

KM_PER_DEG = 111.32
R_EARTH_KM = 6371.0088
FRONT_CACHE = OCEAN_CACHE / "fronts"


def _day(day) -> str:
    return str(day)[:10].replace("-", "")


def grad_path(day):
    return FRONT_CACHE / f"grad_{_day(day)}.npz"


def mask_path(day):
    return FRONT_CACHE / f"front_{_day(day)}.npz"


def save_grad(day, grad: np.ndarray) -> None:
    FRONT_CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(grad_path(day), grad=grad.astype(np.float16))


def load_grad(day):
    """(SST gradient magnitude, degrees C per km, fine grid, transform) cached by scripts/23_daily_ocean.py."""
    return np.load(grad_path(day))["grad"].astype(np.float32), fine_grid()[0]


def save_mask(day, mask: np.ndarray, low: float, high: float) -> None:
    FRONT_CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(mask_path(day), bits=np.packbits(mask), shape=np.array(mask.shape), low=low, high=high)


def load_mask(day):
    """(front mask bool on the fine grid, transform, {'low', 'high'} thresholds) cached by scripts/23_daily_ocean.py."""
    z = np.load(mask_path(day))
    shape = tuple(int(x) for x in z["shape"])
    m = np.unpackbits(z["bits"])[: shape[0] * shape[1]].reshape(shape).astype(bool)
    return m, fine_grid()[0], {"low": float(z["low"]), "high": float(z["high"])}


def fill_nearest(arr: np.ndarray) -> np.ndarray:
    """Copy of `arr` with every NaN replaced by the nearest finite value (unchanged if nothing is finite)."""
    bad = ~np.isfinite(arr)
    if not bad.any() or bad.all():
        return arr.copy()
    idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
    return arr[tuple(idx)]


def metric_spacing_km(transform, shape):
    """(dy km, dx km per row) of a lon/lat grid: dx shrinks with the cosine of latitude."""
    _, lat = centres(transform, shape)
    dy = abs(transform.e) * KM_PER_DEG
    dx = abs(transform.a) * KM_PER_DEG * np.cos(np.radians(lat[:, 0]))
    return dy, dx


def gradient_magnitude(sst: np.ndarray, transform, median_size: int = 3) -> np.ndarray:
    """Gradient magnitude of the median-filtered SST in degrees C per km (NaN where the filter window touched no data)."""
    valid = np.isfinite(sst)
    sm = ndimage.median_filter(fill_nearest(sst.astype(np.float32)), size=median_size, mode="nearest")
    touched = ndimage.minimum_filter(valid.astype(np.uint8), size=median_size, mode="nearest") == 0
    dy, dx = metric_spacing_km(transform, sst.shape)
    gy = np.gradient(sm, axis=0) / dy
    gx = np.gradient(sm, axis=1) / dx[:, None]
    g = np.hypot(gx, gy).astype(np.float32)
    g[touched] = np.nan
    return g


def thresholds(grad_samples: np.ndarray, q_low: float = 0.90, q_high: float = 0.97) -> tuple[float, float]:
    """(low, high) hysteresis thresholds as quantiles of pooled finite gradient values."""
    v = np.asarray(grad_samples, float)
    v = v[np.isfinite(v)]
    lo, hi = np.quantile(v, [q_low, q_high])
    return float(lo), float(hi)


def front_mask(grad: np.ndarray, low: float, high: float, exclude: np.ndarray | None = None, min_pixels: int = 3) -> np.ndarray:
    """Hysteresis front mask: pixels above `high`, plus pixels above `low` 8-connected to them; small specks dropped."""
    from skimage.filters import apply_hysteresis_threshold

    g = np.where(np.isfinite(grad), grad, 0.0)
    if exclude is not None:
        g[exclude] = 0.0
    m = apply_hysteresis_threshold(g, low, high)
    if min_pixels > 1:
        lab, n = ndimage.label(m, structure=np.ones((3, 3)))
        if n:
            sizes = np.bincount(lab.ravel())
            m &= (sizes >= min_pixels)[lab]
    return m


def coast_buffer_mask(transform, shape, land_geoms, buffer_m: float = 2000.0, crs: str = "EPSG:4326",
                      metric_crs: str = "EPSG:32649") -> np.ndarray:
    """True for cells whose centre lies within `buffer_m` of the land polygons (buffered in `metric_crs`).

    Land is clipped to the grid's box (padded by one degree) first: continent-sized polygons do not survive a trip
    through a UTM zone.
    """
    import geopandas as gpd
    from shapely.geometry import box

    h, w = shape
    pad = 1.0
    clip = box(transform.c - pad, transform.f + h * transform.e - pad, transform.c + w * transform.a + pad, transform.f + pad)
    land = gpd.GeoSeries(list(land_geoms), crs=crs).intersection(clip)
    land = land[~land.is_empty]
    if land.empty:
        return np.zeros(shape, bool)
    buffered = land.to_crs(metric_crs).buffer(buffer_m).to_crs(crs)
    return features.rasterize([(g, 1) for g in buffered if not g.is_empty], out_shape=shape, transform=transform,
                              dtype="uint8").astype(bool)


def _local_km(lon, lat, lat0_rad: float):
    return np.c_[np.radians(lon) * R_EARTH_KM * np.cos(lat0_rad), np.radians(lat) * R_EARTH_KM]


def _unit(lon, lat) -> np.ndarray:
    lo, la = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    return np.c_[np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]


def distance_to_front_km(mask: np.ndarray, transform, lon, lat) -> np.ndarray:
    """Great-circle distance from each point to the centre of the nearest front pixel, km (NaN when the mask is empty).

    Nearest neighbour on 3-D unit vectors (chord length), converted to arc length: exact over the whole AOI, where a
    single local plane would mis-scale east-west distances by up to 7 % between 3 S and 24 N.
    """
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    out = np.full(lon.shape, np.nan, np.float32)
    r, c = np.nonzero(mask)
    if len(r) == 0 or lon.size == 0:
        return out
    flon = transform.c + (c + 0.5) * transform.a
    flat = transform.f + (r + 0.5) * transform.e
    chord, _ = cKDTree(_unit(flon, flat)).query(_unit(lon.ravel(), lat.ravel()), workers=-1)
    out.flat[:] = 2.0 * R_EARTH_KM * np.arcsin(np.clip(chord / 2.0, 0.0, 1.0))
    return out


def front_lines(mask: np.ndarray, transform, grad: np.ndarray | None = None, min_length_km: float = 10.0):
    """Front polylines in lon/lat from a front mask: skeleton pixels joined to their 8-neighbours, merged, short ones dropped.

    Returns a list of (LineString, length_km, mean gradient along the line or NaN).
    """
    from skimage.morphology import skeletonize

    sk = skeletonize(mask.astype(bool))
    r, c = np.nonzero(sk)
    if len(r) == 0:
        return []
    h, w = sk.shape
    idx = -np.ones(sk.shape, np.int64)
    idx[r, c] = np.arange(len(r))
    lon = transform.c + (c + 0.5) * transform.a
    lat = transform.f + (r + 0.5) * transform.e
    segs = []
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):  # each neighbour pair once
        rr, cc = r + dr, c + dc
        ok = (rr >= 0) & (rr < h) & (cc >= 0) & (cc < w)
        j = np.where(ok, idx[np.clip(rr, 0, h - 1), np.clip(cc, 0, w - 1)], -1)
        i = np.nonzero(j >= 0)[0]
        segs += [LineString([(lon[a], lat[a]), (lon[b], lat[b])]) for a, b in zip(i, j[i], strict=True)]
    merged = linemerge(segs) if segs else None
    if merged is None:
        return []
    lines = list(merged.geoms) if merged.geom_type == "MultiLineString" else [merged]
    out = []
    for ln in lines:
        xy = np.asarray(ln.coords)
        lat0 = np.radians(xy[:, 1].mean())
        km = _local_km(xy[:, 0], xy[:, 1], lat0)
        length = float(np.hypot(*np.diff(km, axis=0).T).sum())
        if length < min_length_km:
            continue
        g = np.nan
        if grad is not None:
            rr = np.floor((xy[:, 1] - transform.f) / transform.e).astype(int).clip(0, h - 1)
            cc = np.floor((xy[:, 0] - transform.c) / transform.a).astype(int).clip(0, w - 1)
            g = float(np.nanmean(grad[rr, cc]))
        out.append((ln, length, g))
    return out
