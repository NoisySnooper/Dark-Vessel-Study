"""Sentinel-1 IW GRD reading: windowed COG reads, sigma0 calibration, thermal noise removal,
and geocoding from the annotation geolocation grid.

Calibration follows the ESA Level-1 definition:
    sigma0 = (DN^2 - N) / A_sigma^2
where A_sigma is the sigmaNought LUT and N = noiseRangeLut * noiseAzimuthLut, both
bilinearly interpolated from the annotation grids to each pixel.

Pixels stay in radar (slant-to-ground range) geometry. Detections are geocoded point by
point; imagery is warped only for display.
"""

from __future__ import annotations

import os
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cached_property

import numpy as np
import rasterio
from rasterio.windows import Window
from scipy.interpolate import LinearNDInterpolator, RegularGridInterpolator

from darkvessel.s1 import aws

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tiff,.tif",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "VSI_CACHE": "TRUE",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "2",
}
for _k, _v in GDAL_ENV.items():
    os.environ.setdefault(_k, _v)


def _floats(text: str) -> np.ndarray:
    return np.array(text.split(), dtype=np.float64)


def _row_interp(lines: np.ndarray, rows: np.ndarray):
    """Indices and weights for linear interpolation of `rows` within sorted `lines` (clamped)."""
    i1 = np.clip(np.searchsorted(lines, rows), 1, len(lines) - 1)
    i0 = i1 - 1
    w = np.clip((rows - lines[i0]) / (lines[i1] - lines[i0]), 0.0, 1.0)
    return i0, i1, w.astype(np.float32)


@dataclass
class CalibrationLUT:
    lines: np.ndarray        # (n,) image line of each vector
    pixels: list             # per-vector pixel positions
    sigma: list              # per-vector sigmaNought values

    @classmethod
    def from_xml(cls, xml: bytes | str) -> "CalibrationLUT":
        root = ET.fromstring(xml)
        vecs = root.findall(".//calibrationVector")
        return cls(
            lines=np.array([float(v.find("line").text) for v in vecs]),
            pixels=[_floats(v.find("pixel").text) for v in vecs],
            sigma=[_floats(v.find("sigmaNought").text) for v in vecs],
        )

    def grid(self, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
        """A_sigma on the (rows x cols) pixel grid."""
        per_vec = np.stack([np.interp(cols, p, s) for p, s in zip(self.pixels, self.sigma)]).astype(np.float32)
        i0, i1, w = _row_interp(self.lines, rows.astype(np.float64))
        return per_vec[i0] * (1 - w)[:, None] + per_vec[i1] * w[:, None]


@dataclass
class NoiseLUT:
    range_lines: np.ndarray
    range_pixels: list
    range_lut: list
    az_blocks: list          # dicts: first_line, last_line, first_sample, last_sample, lines, lut

    @classmethod
    def from_xml(cls, xml: bytes | str) -> "NoiseLUT":
        root = ET.fromstring(xml)
        rv = root.findall(".//noiseRangeVector")
        blocks = []
        for b in root.findall(".//noiseAzimuthVector"):
            blocks.append(
                {
                    "first_line": int(b.find("firstAzimuthLine").text),
                    "last_line": int(b.find("lastAzimuthLine").text),
                    "first_sample": int(b.find("firstRangeSample").text),
                    "last_sample": int(b.find("lastRangeSample").text),
                    "lines": _floats(b.find("line").text),
                    "lut": _floats(b.find("noiseAzimuthLut").text),
                }
            )
        return cls(
            range_lines=np.array([float(v.find("line").text) for v in rv]),
            range_pixels=[_floats(v.find("pixel").text) for v in rv],
            range_lut=[_floats(v.find("noiseRangeLut").text) for v in rv],
            az_blocks=blocks,
        )

    def grid(self, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
        """Thermal noise power N on the (rows x cols) pixel grid, in DN^2 units."""
        per_vec = np.stack([np.interp(cols, p, s) for p, s in zip(self.range_pixels, self.range_lut)]).astype(np.float32)
        i0, i1, w = _row_interp(self.range_lines, rows.astype(np.float64))
        noise = per_vec[i0] * (1 - w)[:, None] + per_vec[i1] * w[:, None]
        if self.az_blocks:
            az = np.ones_like(noise)
            for b in self.az_blocks:
                rsel = (rows >= b["first_line"]) & (rows <= b["last_line"])
                csel = (cols >= b["first_sample"]) & (cols <= b["last_sample"])
                if not rsel.any() or not csel.any():
                    continue
                vals = np.interp(rows[rsel], b["lines"], b["lut"]).astype(np.float32)
                az[np.ix_(rsel, csel)] = vals[:, None]
            noise *= az
        return noise


class Geocoder:
    """Pixel <-> lon/lat from the annotation geolocation grid (regular in line/pixel)."""

    def __init__(self, lines, pixels, lat, lon, inc):
        self.lines, self.pixels = lines, pixels
        self._lat = RegularGridInterpolator((lines, pixels), lat, bounds_error=False, fill_value=None)
        self._lon = RegularGridInterpolator((lines, pixels), lon, bounds_error=False, fill_value=None)
        self._inc = RegularGridInterpolator((lines, pixels), inc, bounds_error=False, fill_value=None)
        pts = np.column_stack([lon.ravel(), lat.ravel()])
        rr, cc = np.meshgrid(lines, pixels, indexing="ij")
        self._row = LinearNDInterpolator(pts, rr.ravel())
        self._col = LinearNDInterpolator(pts, cc.ravel())

    @classmethod
    def from_annotation(cls, xml: bytes | str) -> "Geocoder":
        root = ET.fromstring(xml)
        pts = root.findall(".//geolocationGridPoint")
        arr = np.array(
            [[float(p.find(t).text) for t in ("line", "pixel", "latitude", "longitude", "incidenceAngle")] for p in pts]
        )
        lines, pixels = np.unique(arr[:, 0]), np.unique(arr[:, 1])
        if len(lines) * len(pixels) != len(arr):
            raise ValueError("Geolocation grid is not regular; cannot build Geocoder.")
        order = np.lexsort((arr[:, 1], arr[:, 0]))
        a = arr[order]
        shape = (len(lines), len(pixels))
        return cls(lines, pixels, a[:, 2].reshape(shape), a[:, 3].reshape(shape), a[:, 4].reshape(shape))

    def lonlat(self, rows, cols):
        pts = np.column_stack([np.asarray(rows, float).ravel(), np.asarray(cols, float).ravel()])
        return self._lon(pts), self._lat(pts)

    def incidence(self, rows, cols):
        pts = np.column_stack([np.asarray(rows, float).ravel(), np.asarray(cols, float).ravel()])
        return self._inc(pts)

    def rowcol(self, lon, lat):
        """Inverse mapping (NaN outside the scene)."""
        return self._row(lon, lat), self._col(lon, lat)


class GRDScene:
    """One Sentinel-1 GRD product in the AWS bucket, read lazily over HTTPS."""

    def __init__(self, path: str):
        self.path = path.rstrip("/")
        self.product_id = self.path.rsplit("/", 1)[-1]
        self.meta = aws.parse_product_id(self.product_id)
        self._local = threading.local()
        self._lut_cache: dict = {}
        self._lut_lock = threading.Lock()

    def _text(self, rel: str) -> bytes:
        return aws._get(aws.product_url(self.path, rel)).content

    @cached_property
    def manifest(self) -> dict:
        return aws.fetch_manifest_meta(self.path)

    @cached_property
    def annotation_xml(self) -> bytes:
        pol = "vv" if "VV" in self.pols else self.pols[0].lower()
        return self._text(f"annotation/iw-{pol}.xml")

    @cached_property
    def pols(self) -> list[str]:
        return self.manifest["pols"] or ["VV", "VH"]

    @cached_property
    def geocoder(self) -> Geocoder:
        return Geocoder.from_annotation(self.annotation_xml)

    @cached_property
    def shape(self) -> tuple[int, int]:
        with rasterio.open(self.href("VV" if "VV" in self.pols else self.pols[0])) as ds:
            return ds.height, ds.width

    def href(self, pol: str) -> str:
        return aws.vsicurl(self.path, f"measurement/iw-{pol.lower()}.tiff")

    def _cached(self, key: str, build):
        with self._lut_lock:
            if key not in self._lut_cache:
                self._lut_cache[key] = build()
            return self._lut_cache[key]

    def calibration(self, pol: str) -> CalibrationLUT:
        """sigmaNought LUT, downloaded once per scene and polarisation."""
        rel = f"annotation/calibration/calibration-iw-{pol.lower()}.xml"
        return self._cached("cal_" + pol, lambda: CalibrationLUT.from_xml(self._text(rel)))

    def noise(self, pol: str) -> NoiseLUT:
        """Thermal noise LUTs, downloaded once per scene and polarisation."""
        rel = f"annotation/calibration/noise-iw-{pol.lower()}.xml"
        return self._cached("noise_" + pol, lambda: NoiseLUT.from_xml(self._text(rel)))

    def _ds(self, pol: str):
        key = f"ds_{pol}"
        if not hasattr(self._local, key):
            setattr(self._local, key, rasterio.open(self.href(pol)))
        return getattr(self._local, key)

    def read_dn(self, pol: str, window: Window, block: int = 1024, max_workers: int = 16) -> np.ndarray:
        """Windowed read of raw DN, fetching 1024 px COG tiles in parallel."""
        r0, c0 = int(window.row_off), int(window.col_off)
        h, w = int(window.height), int(window.width)
        out = np.zeros((h, w), dtype=np.uint16)
        tiles = [(r, c) for r in range(r0, r0 + h, block) for c in range(c0, c0 + w, block)]

        def fetch(rc):
            r, c = rc
            win = Window(c, r, min(block, c0 + w - c), min(block, r0 + h - r))
            out[r - r0 : r - r0 + win.height, c - c0 : c - c0 + win.width] = self._ds(pol).read(1, window=win)

        with ThreadPoolExecutor(max_workers) as ex:
            list(ex.map(fetch, tiles))
        return out

    def read_sigma0(self, pol: str, window: Window, denoise: bool = True, block_rows: int = 1024) -> np.ndarray:
        """Calibrated (and optionally thermal-noise-corrected) linear sigma0 for the window, float32.

        Pixels with DN == 0 (outside the swath) are NaN. Calibration runs in row blocks so
        peak memory stays near one output array even for 15k x 15k windows.
        """
        dn = self.read_dn(pol, window)
        rows = np.arange(int(window.row_off), int(window.row_off + window.height))
        cols = np.arange(int(window.col_off), int(window.col_off + window.width))
        cal = self.calibration(pol)
        noise = self.noise(pol) if denoise else None
        out = np.empty(dn.shape, dtype=np.float32)
        for r in range(0, dn.shape[0], block_rows):
            sl = slice(r, min(r + block_rows, dn.shape[0]))
            power = dn[sl].astype(np.float32) ** 2
            if noise is not None:
                power -= noise.grid(rows[sl], cols)
            a = cal.grid(rows[sl], cols)
            blk = power / (a * a)
            np.maximum(blk, 1e-6, out=blk)  # floor after noise subtraction
            blk[dn[sl] == 0] = np.nan
            out[sl] = blk
        return out

    def read_overview(self, pol: str, factor: int = 16) -> np.ndarray:
        """Whole-scene DN at reduced resolution (uses COG overviews)."""
        with rasterio.open(self.href(pol)) as ds:
            return ds.read(1, out_shape=(ds.height // factor, ds.width // factor))

    def window_for_bbox(self, west, south, east, north, pad: int = 0) -> Window | None:
        """Pixel window covering a lon/lat box (clipped to the image), or None if disjoint."""
        lon = np.linspace(west, east, 41)
        lat = np.linspace(south, north, 41)
        LON, LAT = np.meshgrid(lon, lat)
        r, c = self.geocoder.rowcol(LON.ravel(), LAT.ravel())
        ok = np.isfinite(r) & np.isfinite(c)
        if not ok.any():
            return None
        H, W = self.shape
        r0, r1 = max(0, int(np.nanmin(r[ok])) - pad), min(H, int(np.nanmax(r[ok])) + pad)
        c0, c1 = max(0, int(np.nanmin(c[ok])) - pad), min(W, int(np.nanmax(c[ok])) + pad)
        return Window(c0, r0, c1 - c0, r1 - r0)
