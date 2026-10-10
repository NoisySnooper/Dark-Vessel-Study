"""Vector context layers for the map (contract 3.7, Vector context; section 5 /geo/{name}.geojson)."""

from __future__ import annotations

import threading

import darkvessel  # noqa: F401
from shapely.geometry import box

from ..config import EEZ_STATEMENT
from .common import geojson

AOI_FRAME = (98.16, -4.22, 123.27, 24.76)  # AOI bounds plus 1 degree
LAYERS = {
    "land": ("land", "natural_earth", 0.01, "Natural Earth 10 m land, clipped to the AOI frame plus 1 degree; simplified for display; the source file holds the published geometry."),
    "aoi": ("aoi", "natural_earth", 0.005, "AOI: Natural Earth 10 m South China Sea, Gulf of Tonkin and Gulf of Thailand; simplified for display."),
    "eez": ("eez", "marineregions_v12", 0.0, EEZ_STATEMENT + " Off by default; polygons as published (local app only)."),
    "eez_boundaries": ("eez_boundaries", "marineregions_v12", 0.0, EEZ_STATEMENT + " Off by default; boundary lines as published, with line_type as published."),
    "depth_contours": ("depth_contours", "gebco_2026", 0.0, "GEBCO_2026 depth contours at 50, 200 and 1,000 m; not for navigation."),
    "ports": ("ports", "wpi", 0.0, "NGA World Port Index and Natural Earth ports."),
    "fronts": ("fronts", "mur_sst", 0.0, "SST front lines of the showcase night (MUR v4.1). Ocean and weather layers describe the sea, not what any vessel does."),
    "footprints": ("footprints", "s1_grd", 0.002, "Sentinel-1 IW footprints of the 90-day archive; simplified for display."),
}
NAMES = list(LAYERS) + ["reporting_boxes"]


class Geo:
    def __init__(self, cat, settings):
        self.cat = cat
        self.settings = settings
        self._cache = {}
        self._lock = threading.Lock()

    def available(self, name: str) -> bool:
        if name == "reporting_boxes":
            return True
        spec = LAYERS.get(name)
        return spec is not None and self.cat.exists(spec[0])

    def layer(self, name: str, t0=None, t1=None) -> dict | None:
        """GeoJSON FeatureCollection (dict) with `note` and `src`, or None when the layer's file is missing."""
        if name == "reporting_boxes":
            from darkvessel.ocean.grid import REPORTING_BOXES

            feats = [{"type": "Feature", "geometry": geojson(box(*b)), "properties": {"name": n, "bounds": list(b)}}
                     for n, b in REPORTING_BOXES.items()]
            return {"type": "FeatureCollection", "features": feats, "src": "app",
                    "note": "reporting box: for statistics only, not a boundary"}
        if not self.available(name):
            return None
        key, src, tol, note = LAYERS[name]
        mt = self.cat.mtime_ns(key)
        ck = (name, mt, str(t0), str(t1))
        with self._lock:
            if ck in self._cache:
                return self._cache[ck]
        if name == "land":
            gdf = self.cat.read_vector_file(key, bbox=AOI_FRAME)
            gdf = gdf.clip(box(*AOI_FRAME))
        else:
            gdf = self.cat.read_gpkg(key, geometry=True)
        if name == "footprints" and gdf is not None and len(gdf):
            import pandas as pd

            t = pd.to_datetime(gdf["start_utc"], utc=True, errors="coerce", format="ISO8601")
            m = pd.Series(True, index=gdf.index)
            if t0 is not None:
                m &= t >= t0
            if t1 is not None:
                m &= t <= t1
            gdf = gdf[m]
        feats = []
        for rec, g in zip(gdf.drop(columns="geometry").to_dict("records"), gdf.geometry):
            if g is None or g.is_empty:
                continue
            if tol:
                g = g.simplify(tol, preserve_topology=True)
            feats.append({"type": "Feature", "geometry": geojson(g, 5), "properties": rec})
        out = {"type": "FeatureCollection", "features": feats, "src": src, "note": note}
        with self._lock:
            self._cache[ck] = out
        return out
