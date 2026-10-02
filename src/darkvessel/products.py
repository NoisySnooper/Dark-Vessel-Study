"""Write detection products in a lean, ArcGIS-friendly layout.

data/detections_baseline.gpkg (committed)
  detections_baseline_4326 / _utm48n   one row per fused detection, short caveat per row
  processing_window_4326 / _utm48n     the processed image window
  about                                non-spatial: full caveat, scene and detector settings
data/detections_baseline_perpol.gpkg (gitignored, regenerable)
  detections_vv_* / detections_vh_*    raw objects per polarisation
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

from darkvessel.config import CRS_GEO, CRS_UTM, DARK_CAVEAT, DARK_CAVEAT_SHORT
from darkvessel.io import utm_suffix, write_dual_crs

SCENE_FIELDS = ["platform", "acq_local_ict", "pass_dir", "orbit_rel", "detector", "pfa", "guard_px",
                "background_px", "land_buffer_m", "enl_vv", "enl_vh", "persist_scenes", "caveat"]


def write_detection_products(dets: gpd.GeoDataFrame, per_pol: dict, window_gdf: gpd.GeoDataFrame,
                             meta: dict, path: Path, perpol_path: Path, geocoder=None) -> None:
    for p in (path, perpol_path):
        if Path(p).exists():
            Path(p).unlink()
    slim = dets.drop(columns=[c for c in SCENE_FIELDS if c in dets.columns]).copy()
    slim["caveat"] = DARK_CAVEAT_SHORT
    write_dual_crs(slim, path, "detections_baseline")
    win = window_gdf[["scene_id", "acq_utc", "rows", "cols", "geometry"]].copy()
    win["caveat"] = DARK_CAVEAT_SHORT
    write_dual_crs(win, path, "processing_window")
    about = {k: str(v) for k, v in meta.items() if k != "caveat"}
    about.update({
        "caveat_full": DARK_CAVEAT,
        "layers": f"detections_baseline_4326 / _{utm_suffix(CRS_UTM)}, processing_window_4326 / _{utm_suffix(CRS_UTM)}",
        "crs": f"{CRS_GEO} and {CRS_UTM}",
        "confidence_classes": "high = VV and VH; medium = VH only, or VV only with contrast >= 12 dB and >= 3 px; "
                              "low = other VV only (likely sea clutter); fixed = recurs on every earlier same-orbit date checked",
        "data_credit": "Contains modified Copernicus Sentinel data 2026; land mask ESA WorldCover 2021 v200 (CC BY 4.0)",
    })
    pyogrio.write_dataframe(pd.DataFrame([about]), path, layer="about", driver="GPKG")
    if geocoder is not None:
        for pol, df in per_pol.items():
            if not len(df):
                continue
            lon, lat = geocoder.lonlat(df.row.values, df.col.values)
            g = gpd.GeoDataFrame(df.assign(lon=lon, lat=lat, pol=pol, scene_id=meta["scene_id"], caveat=DARK_CAVEAT_SHORT),
                                 geometry=gpd.points_from_xy(lon, lat), crs=CRS_GEO)
            write_dual_crs(g, perpol_path, f"detections_{pol.lower()}")
