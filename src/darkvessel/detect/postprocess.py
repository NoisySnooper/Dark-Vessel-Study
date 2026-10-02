"""Post-processing of fused detections: confidence classes and multi-date persistence.

Confidence (heuristic baseline, to be replaced by the learned verifier):
  high   = detected in VV and VH
  medium = VH only, or VV only with peak-to-background >= `scr_strong_db` and >= `min_px_strong` pixels
  low    = all other VV-only objects (mostly sea clutter in textured areas), and any object
           longer than `max_vessel_m` (no vessel is that long; rows of structures, artefacts)
  fixed  = recurs within `radius_m` on every other date checked (same orbit geometry):
           wind turbines, platforms, fixed fishing gear. Overrides high/medium.
  clutter zone (regional run) = a high or medium object with at least `min_low` low objects
           within `radius_m` in the same scene: rain cells, wind fronts and aquaculture rafts
           light up as fields of weak returns. Downgraded to low (low_reason "clutter_zone").
  near fixed (regional run) = a high or medium object within `radius_m` (250 m) of a fixed
           structure of the same scene: mostly turbines or platforms the persistence test missed,
           and their sidelobes. Downgraded to low (low_reason "near_fixed").
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from scipy.spatial import cKDTree

from darkvessel.config import CRS_UTM


def assign_confidence(df, scr_strong_db: float = 12.0, min_px_strong: int = 3, max_vessel_m: float = 450.0):
    out = df.copy()
    both = out.detected_vv & out.detected_vh
    vh_only = out.detected_vh & ~out.detected_vv
    vv_strong = out.detected_vv & ~out.detected_vh & (out.scr_vv_db >= scr_strong_db) & (out.n_pixels >= min_px_strong)
    oversized = out.length_est_m > max_vessel_m
    out["confidence"] = np.select([oversized, both, vh_only | vv_strong], ["low", "high", "medium"], default="low")
    out["low_reason"] = np.select([oversized, out.confidence == "low"], ["oversized", "weak_vv_only"], default="")
    return out


def persistence(main: gpd.GeoDataFrame, others: list[gpd.GeoDataFrame], radius_m: float = 50.0,
                utm_crs: str = CRS_UTM) -> np.ndarray:
    """Number of other dates with any detection within `radius_m` of each main detection.

    Other dates should include all classes: a fixed structure can come back weak on one date.
    Random clutter is sparse enough (well under 1 object per km2) that a chance match within
    50 m on every date checked is negligible.
    """
    if main.empty:
        return np.zeros(0, int)
    m = main.to_crs(utm_crs)
    xy = np.column_stack([m.geometry.x, m.geometry.y])
    n = np.zeros(len(main), int)
    for o in others:
        if o.empty:
            continue
        o = o.to_crs(utm_crs)
        d, _ = cKDTree(np.column_stack([o.geometry.x, o.geometry.y])).query(xy, distance_upper_bound=radius_m)
        n += np.isfinite(d).astype(int)
    return n


def apply_persistence(main: gpd.GeoDataFrame, n_matched: np.ndarray, n_dates: int) -> gpd.GeoDataFrame:
    out = main.copy()
    out["persist_dates"] = n_matched
    out["persist_dates_checked"] = n_dates
    if n_dates > 0:
        fixed = (n_matched >= n_dates) & out.confidence.isin(["high", "medium"])
        out.loc[fixed, "confidence"] = "fixed"
    return out


def clutter_zone(df, radius_m: float = 1000.0, min_low: int = 5, group: str = "scene_id"):
    """Flag vessel candidates that sit among many weak returns of the same scene.

    `df` needs lon, lat, confidence and `group` columns. Returns (flag, n_low): flag is True for
    high/medium rows with at least `min_low` low-class objects within `radius_m`; n_low is that
    count for every row. Check on AI2-labelled Sentinel-1A/1B candidates (scripts/11_clutter_zone_check.py):
    1 km and 5 removes about a quarter of clutter candidates and about 2 % of labelled vessels. A very
    dense fleet of small boats with weak returns can be flagged too, so flagged rows are kept, not dropped.
    """
    n_low = np.zeros(len(df), int)
    pos = {k: i for i, k in enumerate(df.index)}
    for _, g in df.groupby(group):
        lat0 = np.radians(float(g.lat.mean()))
        xy = np.column_stack([np.radians(g.lon.values) * 6371008.8 * np.cos(lat0), np.radians(g.lat.values) * 6371008.8])
        low = (g.confidence == "low").to_numpy()
        if not low.any():
            continue
        counts = cKDTree(xy[low]).query_ball_point(xy, radius_m, return_length=True)
        n_low[[pos[k] for k in g.index]] = counts
    flag = df.confidence.isin(["high", "medium"]).to_numpy() & (n_low >= min_low)
    return flag, n_low


def near_fixed(df, radius_m: float = 250.0, group: str = "scene_id"):
    """Flag high/medium objects within `radius_m` of a fixed structure of the same scene.

    Returns (flag, dist_m): dist_m is the distance to the nearest other fixed object (inf if none).
    Masking a buffer around known infrastructure is the usual practice; here the infrastructure
    layer is the project's own persistence result. Vessels moored at or passing close to a structure
    are lost too, at an unmeasured rate; flagged rows are kept in the full product.
    """
    dist = np.full(len(df), np.inf)
    pos = {k: i for i, k in enumerate(df.index)}
    for _, g in df.groupby(group):
        fixed = (g.confidence == "fixed").to_numpy()
        if not fixed.any():
            continue
        lat0 = np.radians(float(g.lat.mean()))
        xy = np.column_stack([np.radians(g.lon.values) * 6371008.8 * np.cos(lat0), np.radians(g.lat.values) * 6371008.8])
        k = 2 if fixed.sum() > 1 else 1
        d, _ = cKDTree(xy[fixed]).query(xy, k=k)
        if k == 2:
            d = np.where(fixed, d[:, 1], d[:, 0])  # a fixed object's nearest fixed object is itself
        else:
            d = np.where(fixed, np.inf, d)
        dist[[pos[i] for i in g.index]] = d
    flag = df.confidence.isin(["high", "medium"]).to_numpy() & (dist <= radius_m)
    return flag, dist
