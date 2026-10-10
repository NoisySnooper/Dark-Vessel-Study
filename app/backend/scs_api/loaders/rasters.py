"""Raster registry (contract 3.7, Raster layers) from `data/outputs/small/*_4326.tif` (research adds the GFW rasters),
values at a point, cell means on the model grid, and colour-mapped WebP overlays.
"""

from __future__ import annotations

import io
import threading

import darkvessel  # noqa: F401
import numpy as np
import rasterio
from rasterio.enums import Resampling

from ..config import SHIPPING_LABEL

SRC_BY_PREFIX = [("depth", "gebco_2026"), ("dist_coast", "natural_earth"), ("dist_port", "wpi"),
                 ("ship_density", "worldbank_density"), ("sst", "mur_sst"), ("front", "mur_sst"), ("chl", "chl_dineof"),
                 ("current", "rtofs"), ("mld", "rtofs"), ("ssh", "rtofs"), ("wave", "gfs_wave"), ("wind", "gfs_wind"),
                 ("s1_", "s1_grd"), ("ais_reach", "aisstream"), ("vessel_density", "det_regional"), ("viirs", "viirs_dnb"),
                 ("gfw_", "gfw_4wings")]
PRESENCE_UNIT = "presence (1 = published value above 0)"


def is_presence(name: str) -> bool:
    """Layers served as presence only (board D4.3): the World Bank shipping density grids."""
    return name.startswith("ship_density")


CMAP = {"depth": "Blues", "dist": "cividis", "ship_density": "Greys", "sst": "inferno", "front": "magma", "chl": "viridis",
        "current": "plasma", "wave": "plasma", "wind": "plasma", "s1_": "viridis", "ais_reach": "viridis", "gfw_": "viridis"}
MAX_PX = 1200


def src_of(name: str) -> str:
    return next((s for p, s in SRC_BY_PREFIX if name.startswith(p)), "app")


def cmap_of(name: str) -> str:
    return next((c for p, c in CMAP.items() if name.startswith(p)), "viridis")


class Rasters:
    def __init__(self, cat, settings):
        self.cat = cat
        self.settings = settings
        self._lock = threading.Lock()
        self._arrays: dict = {}
        self._stats: dict = {}
        self._webp: dict = {}
        self.paths = {}
        for key in ("rasters", "rasters_research"):
            for p in cat.paths(key):
                name = p.name[: -len("_4326.tif")]
                if not settings.research and name.startswith("gfw_"):
                    continue
                self.paths[name] = p
        self.meta = {}
        for name, p in self.paths.items():
            with rasterio.open(p) as ds:
                tags = ds.tags()
                b = ds.bounds
                self.meta[name] = {"res": float(ds.res[0]), "bounds": [b.left, b.bottom, b.right, b.top], "nodata": ds.nodata,
                                   "tags": tags, "shape": ds.shape, "transform": ds.transform}

    def names(self) -> list[str]:
        return sorted(self.paths)

    def _mtime(self, name):
        return self.paths[name].stat().st_mtime_ns

    def array(self, name: str, decimate: int | None = None) -> np.ndarray:
        """The band as float with NaN for nodata; `decimate` reads at most that many pixels on the long side."""
        key = (name, self._mtime(name), decimate)
        with self._lock:
            if key in self._arrays:
                return self._arrays[key]
        p = self.cat.guard(self.paths[name])
        self.cat._note(p)
        with rasterio.open(p) as ds:
            if decimate and max(ds.shape) > decimate:
                f = max(ds.shape) / decimate
                shape = (max(1, int(ds.shape[0] / f)), max(1, int(ds.shape[1] / f)))
                a = ds.read(1, out_shape=shape, resampling=Resampling.average if ds.dtypes[0].startswith("float") else Resampling.nearest)
            else:
                a = ds.read(1)
        a = a.astype(float)
        nd = self.meta[name]["nodata"]
        if nd is not None:
            a[a == nd] = np.nan
        with self._lock:
            self._arrays[key] = a
        return a

    def stats(self, name: str) -> tuple[float | None, float | None]:
        key = (name, self._mtime(name))
        if key not in self._stats:
            a = self.array(name, decimate=600)
            v = a[np.isfinite(a)]
            if name.startswith("ship_density"):
                v = v[v > 0]
            self._stats[key] = (float(np.percentile(v, 2)), float(np.percentile(v, 98))) if v.size else (None, None)
        return self._stats[key]

    def entry(self, name: str, caveat: str) -> dict:
        m = self.meta[name]
        t = m["tags"]
        vmin, vmax = self.stats(name)
        note = t.get("note") or t.get("use")
        unit = t.get("units")
        if is_presence(name):  # board D4.3: the legend and the value are presence (0 or 1), never the published magnitude
            use = str(t.get("use") or "")
            if use.lower().startswith("presence only"):  # the label already says it
                use = use.split(":", 1)[1].strip() if ":" in use else ""
            note = SHIPPING_LABEL + (": " + use if use else "")
            vmin, vmax, unit = 0.0, 1.0, PRESENCE_UNIT
        return {"name": name, "unit": unit, "resolution_deg": round(m["res"], 6),
                "valid_period": t.get("window") or t.get("period"), "colormap": cmap_of(name), "vmin": vmin, "vmax": vmax,
                "bounds": [round(x, 6) for x in m["bounds"]], "src": src_of(name), "licence": t.get("licence") or t.get("terms"),
                "default_on": False, "note": note, "research_only": name.startswith("gfw_"), "caveat": caveat}

    def presence_value(self, name: str, lon: float, lat: float) -> dict:
        """The point value as served: presence (1.0 where the published value is above 0, else 0.0) for the shipping
        density layers, with the published number kept apart as `value_as_published`; the value itself otherwise."""
        v = self.value(name, lon, lat)
        if not is_presence(name):
            return {"value": v}
        return {"value": None if v is None else float(v > 0), "presence": None if v is None else bool(v > 0),
                "value_as_published": v}

    def value(self, name: str, lon: float, lat: float) -> float | None:
        m = self.meta[name]
        a = self.array(name)
        r, c = rasterio.transform.rowcol(m["transform"], lon, lat)
        if 0 <= r < a.shape[0] and 0 <= c < a.shape[1] and np.isfinite(a[r, c]):
            return float(a[r, c])
        return None

    def cell_mean(self, name: str, w: float, s: float, e: float, n: float) -> float | None:
        """Mean of the valid pixels whose centres fall in the box (cell means of 0.05 degree layers, contract 3.7)."""
        m = self.meta[name]
        a = self.array(name)
        t = m["transform"]
        r0, c0 = rasterio.transform.rowcol(t, w + t.a / 2, n + t.e / 2)
        r1, c1 = rasterio.transform.rowcol(t, e - t.a / 2, s - t.e / 2)
        r0, r1 = max(0, min(r0, r1)), min(a.shape[0], max(r0, r1) + 1)
        c0, c1 = max(0, min(c0, c1)), min(a.shape[1], max(c0, c1) + 1)
        if r0 >= r1 or c0 >= c1:
            return None
        v = a[r0:r1, c0:c1]
        v = v[np.isfinite(v)]
        return float(v.mean()) if v.size else None

    def webp(self, name: str, theme: str = "dark") -> bytes:
        key = (name, self._mtime(name), theme)
        if key in self._webp:
            return self._webp[key]
        import matplotlib
        from PIL import Image

        a = self.array(name, decimate=MAX_PX)
        vmin, vmax = self.stats(name)
        ok = np.isfinite(a)
        if name.startswith("ship_density"):  # presence only (board D4.3): one colour where value > 0
            z = np.where(ok & (a > 0), 1.0, np.nan)
            vmin, vmax = 0.0, 1.0
            ok = np.isfinite(z)
            a = z
        span = (vmax - vmin) if vmin is not None and vmax is not None and vmax > vmin else 1.0
        z = np.clip((np.nan_to_num(a, nan=vmin or 0.0) - (vmin or 0.0)) / span, 0, 1)
        rgba = (matplotlib.colormaps[cmap_of(name)](z) * 255).astype(np.uint8)
        rgba[..., 3] = np.where(ok, 210 if theme == "dark" else 190, 0).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buf, "WEBP", quality=80, method=4)
        out = buf.getvalue()
        self._webp[key] = out
        return out
