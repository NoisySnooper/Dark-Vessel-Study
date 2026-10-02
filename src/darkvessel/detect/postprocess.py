"""Post-processing of fused detections: confidence classes and multi-date persistence.

Confidence (heuristic baseline, to be replaced by the learned verifier):
  high   = detected in VV and VH
  medium = VH only, or VV only with peak-to-background >= `scr_strong_db` and >= `min_px_strong` pixels
  low    = all other VV-only objects (mostly sea clutter in textured areas)
  fixed  = recurs within `radius_m` on every other date checked (same orbit geometry):
           wind turbines, platforms, fixed fishing gear. Overrides the classes above.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from scipy.spatial import cKDTree

from darkvessel.config import CRS_UTM


def assign_confidence(df, scr_strong_db: float = 12.0, min_px_strong: int = 3):
    out = df.copy()
    both = out.detected_vv & out.detected_vh
    vh_only = out.detected_vh & ~out.detected_vv
    vv_strong = out.detected_vv & ~out.detected_vh & (out.scr_vv_db >= scr_strong_db) & (out.n_pixels >= min_px_strong)
    out["confidence"] = np.select([both, vh_only | vv_strong], ["high", "medium"], default="low")
    return out


def persistence(main: gpd.GeoDataFrame, others: list[gpd.GeoDataFrame], radius_m: float = 50.0,
                utm_crs: str = CRS_UTM) -> np.ndarray:
    """Number of other dates with a detection within `radius_m` of each main detection."""
    if main.empty:
        return np.zeros(0, int)
    xy = np.column_stack([main.to_crs(utm_crs).geometry.x, main.to_crs(utm_crs).geometry.y])
    n = np.zeros(len(main), int)
    for o in others:
        if o.empty:
            continue
        oxy = np.column_stack([o.to_crs(utm_crs).geometry.x, o.to_crs(utm_crs).geometry.y])
        d, _ = cKDTree(oxy).query(xy, distance_upper_bound=radius_m)
        n += np.isfinite(d).astype(int)
    return n


def apply_persistence(main: gpd.GeoDataFrame, n_matched: np.ndarray, n_dates: int) -> gpd.GeoDataFrame:
    out = main.copy()
    out["persist_dates"] = n_matched
    out["persist_dates_checked"] = n_dates
    if n_dates > 0:
        out.loc[n_matched >= n_dates, "confidence"] = "fixed"
    return out
