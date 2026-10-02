"""Post-processing of fused detections: confidence classes and multi-date persistence.

Confidence (heuristic baseline, to be replaced by the learned verifier):
  high   = detected in VV and VH
  medium = VH only, or VV only with peak-to-background >= `scr_strong_db` and >= `min_px_strong` pixels
  low    = all other VV-only objects (mostly sea clutter in textured areas), and any object
           longer than `max_vessel_m` (no vessel is that long; rows of structures, artefacts)
  fixed  = recurs within `radius_m` on every other date checked (same orbit geometry):
           wind turbines, platforms, fixed fishing gear. Overrides high/medium.
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
