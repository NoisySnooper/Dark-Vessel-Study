"""Per-scene processing for live passes: detection, persistence, clutter rules, CNN verifier scores.

Detection uses the regional path unchanged (darkvessel.regional.process_scene: block streaming over AOI sea, CA-CFAR
PFA 1e-6, guard 81 px, background 161 px, 1 km shore buffer, VV/VH fusion, heuristic confidence classes). Then, as in
scripts/09_run_regional.py --merge: persistence against up to two earlier same-orbit passes (fixed structures),
the clutter-zone rule and the near-fixed rule. Every object that was a vessel candidate (high or medium) before the
rules is scored by the CNN verifier (data/models/verifier_v0.pt, chips read remotely from the COG, no scene download).

Remote reads. From this environment, roughly one in five new connections to the mirror lands on an S3 front end that
answers 404 NoSuchBucket for objects that exist (measured 2026-10-09: 10 of 60 and 17 of 60 single-connection GETs,
against 0 of 60 on one kept-alive connection). A connection that answers well keeps answering well, a bad one stays
bad, and GDAL remembers a 404 as "file does not exist" for the whole process. Neither GDAL's HTTP retry nor
darkvessel.s1.aws._get retries a 404. `LiveGRDScene` therefore retries on a *new connection*: GDAL keeps its curl
connections per thread, so failed tiles are re-read in a fresh thread pool, single reads are retried in a throwaway
thread, the file's negative cache is dropped first, and the requests session of the thread is replaced before a small
file is fetched again (`http_retry`). The fetch pool is capped at IO_THREADS. `detect` makes darkvessel.regional build
this class instead of the plain GRDScene (the regional module is not edited; the name is swapped for the call).

CPU: the host shares four cores with other jobs. Scenes are processed one at a time; CNN chips and persistence use at
most `workers` CPU threads (default 2), torch is limited to the same number, tile fetches use IO_THREADS I/O threads
and the caller nices the process.
"""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio.windows import Window

from darkvessel import regional as regional_mod
from darkvessel.config import DATA_DIR
from darkvessel.detect.postprocess import clutter_zone, near_fixed
from darkvessel.ml.chips import CHIP_HALF, chips_db
from darkvessel.s1 import aws
from darkvessel.s1.grd import GRDScene

MODEL_PATH = DATA_DIR / "models" / "verifier_v0.pt"
FOOTPRINTS_PATH = DATA_DIR / "s1_footprints.gpkg"
CANDIDATE_CLASSES = ("high", "medium")
CONTACT_CLASSES = ("high", "medium", "fixed")
IO_THREADS = 4               # parallel COG tile fetches per scene (the plain GRDScene uses 16)
IO_RETRIES = 6               # attempts per tile batch or single read, each on a new connection
IO_BACKOFF_S = (1.0, 2.0, 3.0, 5.0, 10.0)
HTTP_ATTEMPTS = 6            # small-file fetches through requests (listing, productInfo, manifest, XML)
HTTP_BACKOFF_S = (1.0, 1.0, 2.0, 3.0, 5.0)
_patch_lock = threading.Lock()

RULES_TEXT = {
    "detector": "ca_cfar_v0 regional, block streaming over AOI sea; PFA 1e-6, guard 81 px, background 161 px, land buffer "
                "1000 m, VV/VH fusion within 3 px; same settings as data/detections_regional.gpkg",
    "confidence_classes": "high = VV and VH; medium = VH only or strong VV only; low = weak VV only, longer than 450 m, in a "
                          "clutter zone or near a fixed structure; fixed = bright return at the same spot on every earlier "
                          "same-orbit pass checked. Fixed contacts are structures (platforms, wrecks, aquaculture), never leads.",
    "persistence": "20 m overview window, contrast >= 7 dB within about 60 m, up to 2 earlier passes of the same relative "
                   "orbit and direction 1 to 30 days before (data/s1_footprints.gpkg archive); persist_dates_checked = 0 "
                   "when no earlier pass covers the position",
    "clutter_zone": "high or medium object with 5 or more low objects within 1 km in the same scene (rain cells, wind fronts, "
                    "aquaculture rafts; a dense small-boat fleet can be flagged too); downgraded to low, low_reason clutter_zone",
    "near_fixed": "high or medium object within 250 m of a fixed structure of the same scene; downgraded to low, low_reason near_fixed",
    "cnn": "verifier_v0, 64 x 64 px VV/VH chips in dB (thermal noise not removed), 8-view test-time augmentation; "
           "cnn_vessel = cnn_score >= threshold (best validation F1). Trained on AI2 Skylight Sentinel-1A/1B point labels "
           "2020-2022 (Apache-2.0); no Sentinel-1C/1D ground truth, so scores are a transfer, not a validated verdict. "
           "Scored: every object that was high or medium before the rules (so also those the rules downgraded).",
    "reads": f"COG tiles read over HTTPS from the AWS mirror with {IO_THREADS} I/O threads. About one new connection in five "
             "from this environment reaches an S3 front end that answers 404 for existing objects and keeps doing so, which "
             f"GDAL and requests treat as final; every failed read is therefore retried up to {IO_RETRIES - 1} times on a new "
             "connection (new thread, negative cache dropped), small files likewise with a new session, and a scene whose "
             "detection still fails is retried on later cycles (detect_attempts, io_retries in the scenes layer).",
}


def _clear_curl_cache(url: str) -> None:
    """Drop GDAL's cached ranges and file status of one /vsicurl/ file (same libgdal as rasterio in this environment).

    GDAL caches a 404 as "the file does not exist": after one spurious 404 every open in the process fails with
    "not recognized as being in a supported file format" until the cache is dropped. The /vsicurl/ prefix stays
    on the path: GDAL picks the curl handler from it.
    """
    try:
        from osgeo import gdal

        gdal.VSICurlPartialClearCache(url)
    except Exception:  # noqa: BLE001 osgeo missing or a different GDAL: the fresh connection alone still helps
        pass


def _reset_requests_session() -> None:
    """Drop this thread's darkvessel.s1.aws session so the next request opens a new connection."""
    s = getattr(aws._local, "s", None)
    if s is not None:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            del aws._local.s
        except AttributeError:
            pass


def http_retry(fn, attempts: int = HTTP_ATTEMPTS, waits=HTTP_BACKOFF_S, what: str = "fetch", log=None, reset=_reset_requests_session):
    """Call `fn` until it returns, retrying any exception (a 404 included) on a new connection, `attempts` times in all."""
    last = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt + 1 >= attempts:
                raise
            if log:
                log(f"  {what} retry {attempt + 1}/{attempts - 1}: {type(exc).__name__}: {str(exc)[:160]}")
            if reset:
                reset()
            time.sleep(waits[min(attempt, len(waits) - 1)])
    raise last


def _in_fresh_thread(fn):
    """Run `fn` in a new thread (so GDAL opens a new connection) and return its result or raise its exception."""
    box: dict = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 handed back to the caller
            box["error"] = exc

    t = threading.Thread(target=run, name="live-fresh-read", daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


class LiveGRDScene(GRDScene):
    """GRDScene with retried reads on new connections and a bounded fetch pool (see the module docstring)."""

    def __init__(self, path: str, io_threads: int = IO_THREADS, retries: int = IO_RETRIES, backoff=IO_BACKOFF_S, log=None):
        super().__init__(path)
        self.io_threads, self.retries, self.backoff, self._log = max(1, int(io_threads)), max(1, int(retries)), tuple(backoff), log
        self.io_retries = 0
        self._count_lock = threading.Lock()

    # -- small files through requests: new session on every retry
    def _text(self, rel: str) -> bytes:
        return http_retry(lambda: super(LiveGRDScene, self)._text(rel), what=f"xml {rel.rsplit('/', 1)[-1]}", log=self._log)

    @property
    def manifest(self) -> dict:
        if "manifest" not in self.__dict__:
            self.__dict__["manifest"] = http_retry(lambda: aws.fetch_manifest_meta(self.path), what="manifest", log=self._log)
        return self.__dict__["manifest"]

    # -- datasets
    def _open(self, pol: str):
        return rasterio.open(self.href(pol))

    def _ds(self, pol: str):
        key = f"ds_{pol}"
        if not hasattr(self._local, key):
            setattr(self._local, key, self._open(pol))
        return getattr(self._local, key)

    def _note_failure(self, n: int, what: str, pol: str, exc: BaseException, attempt: int) -> None:
        with self._count_lock:
            self.io_retries += n
        if self._log:
            self._log(f"  read retry {attempt + 1}/{self.retries - 1} ({what} {pol}, {n} read{'s' if n > 1 else ''}): "
                      f"{type(exc).__name__}: {str(exc).replace(chr(10), ' ')[:200]}")
        _clear_curl_cache(self.href(pol))

    def _retry_fresh(self, what: str, pol: str, fn, first_in_place: bool = False):
        """`fn` with up to `retries` attempts; every attempt (or every retry) runs in a new thread, so on a new connection."""
        for attempt in range(self.retries):
            try:
                return fn() if (first_in_place and attempt == 0) else _in_fresh_thread(fn)
            except Exception as exc:  # noqa: BLE001 RasterioIOError, or a curl error surfaced through it
                if attempt + 1 >= self.retries:
                    raise
                self._note_failure(1, what, pol, exc, attempt)
                time.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])

    def read_tile(self, pol: str, win: Window, **kw) -> np.ndarray:
        """One windowed read; the first try reuses this thread's dataset, retries run on fresh connections."""
        return self._retry_fresh("tile", pol, lambda: self._ds(pol).read(1, window=win, **kw), first_in_place=True)

    @property
    def shape(self) -> tuple[int, int]:
        if "shape" not in self.__dict__:
            pol = "VV" if "VV" in self.pols else self.pols[0]

            def once():
                with rasterio.open(self.href(pol)) as ds:
                    return ds.height, ds.width

            self.__dict__["shape"] = self._retry_fresh("shape", pol, once)
        return self.__dict__["shape"]

    def _renew_pool(self) -> None:
        """Replace the fetch pool: new threads, so new connections for every worker."""
        if self._pool is not None:
            self._pool.shutdown(wait=True)
        self._pool = ThreadPoolExecutor(self.io_threads)

    def read_dn(self, pol: str, window: Window, block: int = 1024, max_workers: int | None = None) -> np.ndarray:
        """Windowed DN read; tiles that fail are re-read in a renewed pool, up to `retries` rounds."""
        r0, c0 = int(window.row_off), int(window.col_off)
        h, w = int(window.height), int(window.width)
        out = np.zeros((h, w), dtype=np.uint16)
        pending = [(r, c) for r in range(r0, r0 + h, block) for c in range(c0, c0 + w, block)]

        def fetch(rc):
            r, c = rc
            win = Window(c, r, min(block, c0 + w - c), min(block, r0 + h - r))
            try:
                out[r - r0: r - r0 + win.height, c - c0: c - c0 + win.width] = self._ds(pol).read(1, window=win)
                return None
            except Exception as exc:  # noqa: BLE001 collected, retried on a new connection
                return rc, exc

        if self._pool is None:
            self._pool = ThreadPoolExecutor(self.io_threads)
        for attempt in range(self.retries):
            failed = [f for f in self._pool.map(fetch, pending) if f is not None]
            if not failed:
                return out
            if attempt + 1 >= self.retries:
                raise failed[0][1]
            self._note_failure(len(failed), "tiles", pol, failed[0][1], attempt)
            self._renew_pool()
            time.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
            pending = [rc for rc, _ in failed]
        raise RuntimeError("unreachable")

    def read_overview(self, pol: str, factor: int = 16) -> np.ndarray:
        def once():
            with rasterio.open(self.href(pol)) as ds:
                return ds.read(1, out_shape=(ds.height // factor, ds.width // factor))

        return self._retry_fresh("overview", pol, once)


def load_verifier(path: Path = MODEL_PATH, threads: int = 2):
    """(model, meta, model_id) or (None, None, None) when the weights are not present."""
    if not Path(path).exists():
        return None, None, None
    import torch

    from darkvessel.ml.model import MODEL_ID, load_model

    torch.set_num_threads(max(1, int(threads)))
    model, meta = load_model(path)
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:8]
    return model, meta, f"{MODEL_ID}_{digest}"


def load_footprints(path: Path = FOOTPRINTS_PATH):
    """Scene archive for the persistence test (EPSG:4326), or None when the file is missing."""
    if not Path(path).exists():
        return None
    import geopandas as gpd

    fp = gpd.read_file(path, layer="s1_footprints_4326")
    fp["start_utc"] = pd.to_datetime(fp.start_utc, utc=True)
    return fp


C_LIGHT_MS = 299_792_458.0
GEOMETRY_COLUMNS = ["az_time_utc", "slant_range_m", "az_e", "az_n", "rg_e", "rg_n"]


def _iso_seconds(text: str, ref: pd.Timestamp) -> float:
    return (pd.Timestamp(text) - ref).total_seconds()


def sar_geometry(annotation_xml: bytes | str, rows, cols, geocoder) -> tuple[pd.DataFrame, dict]:
    """Imaging geometry of each object from the product annotation (the matcher's azimuth-shift correction needs it).

    Per object: az_time_utc (zero-Doppler azimuth time of its line, ISO UTC), slant_range_m (c x two-way slant range
    time / 2), az_e/az_n (unit vector on the ground, east and north components, of increasing azimuth time: the
    direction the satellite moves) and rg_e/rg_n (unit vector of increasing slant range: away from the ground track).
    Times and ranges are interpolated linearly in the regular line/pixel geolocation grid; directions are finite
    differences of the same grid's lon/lat (`geocoder`). Returns (frame, info) with info = platform_heading_deg,
    sat_speed_ms (median |velocity| of the annotation's Earth-fixed orbit state vectors), first_line_utc,
    line_interval_s, pass.
    """
    import xml.etree.ElementTree as ET

    from scipy.interpolate import RegularGridInterpolator

    root = ET.fromstring(annotation_xml)
    pts = root.findall(".//geolocationGridPoint")
    ref = pd.Timestamp(pts[0].find("azimuthTime").text)
    arr = np.array([[float(p.find("line").text), float(p.find("pixel").text), _iso_seconds(p.find("azimuthTime").text, ref),
                     float(p.find("slantRangeTime").text)] for p in pts])
    lines, pixels = np.unique(arr[:, 0]), np.unique(arr[:, 1])
    if len(lines) * len(pixels) != len(arr):
        raise ValueError("geolocation grid is not regular")
    a = arr[np.lexsort((arr[:, 1], arr[:, 0]))]
    shape = (len(lines), len(pixels))
    az = RegularGridInterpolator((lines, pixels), a[:, 2].reshape(shape), bounds_error=False, fill_value=None)
    srt = RegularGridInterpolator((lines, pixels), a[:, 3].reshape(shape), bounds_error=False, fill_value=None)
    rows, cols = np.asarray(rows, float), np.asarray(cols, float)
    p = np.column_stack([rows, cols])
    az_s = az(p)
    out = pd.DataFrame(index=range(len(rows)))
    out["az_time_utc"] = [(ref + pd.Timedelta(seconds=float(s))).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z" for s in az_s]
    out["slant_range_m"] = np.round(srt(p) * C_LIGHT_MS / 2.0, 1)
    step = 25.0
    lon0, lat0 = geocoder.lonlat(rows, cols)

    def unit(r2, c2, sign):
        lon2, lat2 = geocoder.lonlat(r2, c2)
        e = np.radians(lon2 - lon0) * np.cos(np.radians(lat0)) * sign
        n = np.radians(lat2 - lat0) * sign
        norm = np.hypot(e, n)
        return e / norm, n / norm

    # increasing azimuth time and increasing slant range, whatever the line and pixel order of the product
    s_az = np.sign(np.nanmedian(np.diff(a[:, 2].reshape(shape), axis=0))) or 1.0
    s_rg = np.sign(np.nanmedian(np.diff(a[:, 3].reshape(shape), axis=1))) or 1.0
    out["az_e"], out["az_n"] = (np.round(v, 5) for v in unit(rows + step, cols, s_az))
    out["rg_e"], out["rg_n"] = (np.round(v, 5) for v in unit(rows, cols + step, s_rg))
    info = {}
    head = root.find(".//productInformation/platformHeading")
    info["platform_heading_deg"] = float(head.text) if head is not None else None
    vel = [[float(o.find(f"velocity/{k}").text) for k in "xyz"] for o in root.findall(".//orbitList/orbit")]
    info["sat_speed_ms"] = round(float(np.median(np.linalg.norm(np.array(vel), axis=1))), 1) if vel else None
    first = root.find(".//imageInformation/productFirstLineUtcTime")
    dt_line = root.find(".//imageInformation/azimuthTimeInterval")
    info["first_line_utc"] = first.text if first is not None else None
    info["line_interval_s"] = float(dt_line.text) if dt_line is not None else None
    pas = root.find(".//productInformation/pass")
    info["pass"] = pas.text if pas is not None else None
    return out, info


def mask_polygon(mask: np.ndarray, factor: int, lonlat, simplify_px: float = 8.0):
    """Polygon (EPSG:4326) of the True cells of a decimated scene mask: cell (i, j) covers rows i*factor to
    (i+1)*factor and columns j*factor to (j+1)*factor of the full-resolution grid; `lonlat(rows, cols)` maps full
    resolution pixel coordinates to lon, lat (the scene's Geocoder.lonlat). Outlines are simplified by `simplify_px`
    full-resolution pixels before they are mapped."""
    from affine import Affine
    from rasterio import features

    m = np.asarray(mask, bool)
    if not m.any():
        return shapely.Polygon()
    shapes = [shapely.geometry.shape(g) for g, v in features.shapes(m.astype(np.uint8), mask=m, transform=Affine(factor, 0, 0, 0, factor, 0)) if v]
    poly = shapely.union_all(shapes).simplify(simplify_px, preserve_topology=True)

    def to_ll(xy):
        lon, lat = lonlat(xy[:, 1], xy[:, 0])
        return np.column_stack([np.asarray(lon, float), np.asarray(lat, float)])

    return shapely.make_valid(shapely.transform(poly, to_ll))


def tested_area(scene: GRDScene, aoi, inner, buffer_m: float = 1000.0):
    """The detector's tested sea for one scene as a polygon (EPSG:4326): darkvessel.regional.scene_mask (WorldCover water
    connected to the open sea, beyond `buffer_m` of land, inside the AOI) on its 160 m grid. Used for on_tested_sea."""
    m = regional_mod.scene_mask(scene, aoi, buffer_m, aoi_inner=inner)
    return mask_polygon(m, regional_mod.FACTOR, scene.geocoder.lonlat)


def tested_area_for(path: str, aoi, inner, io_threads: int = 2, log=print):
    """tested_area for a mirror path (annotation and WorldCover read again; for scenes processed before 2026-10-10)."""
    return tested_area(LiveGRDScene(path, io_threads=io_threads, log=log), aoi, inner)


def detect(path: str, aoi, inner, pfa: float = 1e-6, io_threads: int = IO_THREADS, log=print):
    """Regional detection on one scene through LiveGRDScene. Returns (GeoDataFrame of all objects, stats dict).

    The regional module is read-only for this package, so its module-level `GRDScene` name is swapped for a
    LiveGRDScene factory while process_scene runs (one scene at a time; the swap is serialised by a lock).
    The imaging geometry of every object (`sar_geometry`) is added from the annotation the detector already read;
    when that fails the columns stay null and the matcher falls back to the scene time and no azimuth correction.
    stats["_tested"] is the tested-sea polygon (`tested_area`), absent when it could not be built.
    """
    made: list[LiveGRDScene] = []

    def factory(p: str) -> LiveGRDScene:
        s = LiveGRDScene(p, io_threads=io_threads, log=log)
        made.append(s)
        return s

    with _patch_lock:
        original = regional_mod.GRDScene
        regional_mod.GRDScene = factory
        try:
            det, stats = regional_mod.process_scene(path, aoi, pfa=pfa, aoi_inner=inner, log=log)
        finally:
            regional_mod.GRDScene = original
    stats = dict(stats)
    stats["io_retries"] = int(sum(s.io_retries for s in made))
    if len(det) and made and "row" in det:
        try:
            geo, info = sar_geometry(made[0].annotation_xml, det.row.to_numpy(float), det.col.to_numpy(float), made[0].geocoder)
            for c in GEOMETRY_COLUMNS:
                det[c] = geo[c].to_numpy()
            stats.update(info)
        except Exception as exc:  # noqa: BLE001 the matcher falls back to the scene time and no correction
            log(f"  geometry: annotation parse failed ({type(exc).__name__}: {str(exc)[:120]}); no azimuth correction")
    if made:
        try:  # the tested sea as a polygon, for the AIS-only on_tested_sea test (stats key "_tested", popped by the watcher)
            stats["_tested"] = tested_area(made[0], aoi, inner)
        except Exception as exc:  # noqa: BLE001 the matcher falls back to the distance-to-coast layer
            log(f"  tested area: mask polygon failed ({type(exc).__name__}: {str(exc)[:120]}); on_tested_sea from dist_coast")
    return det, stats


def persistence(det: pd.DataFrame, stats: dict, footprints, workers: int = 2, io_threads: int = IO_THREADS, log=print) -> pd.DataFrame:
    """persist_dates and persist_dates_checked for high/medium objects; confidence 'fixed' where they recur every time."""
    from darkvessel.regional import recurs

    out = det.copy()
    out["persist_dates"], out["persist_dates_checked"] = 0, 0
    cand = out[out.confidence.isin(CANDIDATE_CLASSES)]
    if footprints is None or cand.empty or stats.get("orbit_rel") is None:
        return out
    acq = pd.Timestamp(stats["acq_utc"])
    fp = footprints
    prev = fp[(fp.orbit_rel == stats["orbit_rel"]) & (fp.pass_dir == stats["pass_dir"])
              & (fp.start_utc < acq - pd.Timedelta(days=1)) & (fp.start_utc >= acq - pd.Timedelta(days=30))]
    prev = prev[prev.product_id != stats["scene_id"]].sort_values("start_utc", ascending=False)
    if prev.empty:
        log(f"  persistence: no earlier pass on relative orbit {stats['orbit_rel']} in the archive")
        return out
    hit = shapely.contains_xy(np.asarray(prev.geometry.values)[:, None], cand.lon.values[None, :], cand.lat.values[None, :])
    paths = prev.path.to_numpy()
    jobs = [(idx, list(paths[hit[:, j]][:2]), cand.lon.values[j], cand.lat.values[j]) for j, idx in enumerate(cand.index)]
    jobs = [j for j in jobs if j[1]]
    scenes: dict[str, LiveGRDScene] = {}
    for p in {p for _, ps, _, _ in jobs for p in ps}:
        scenes[p] = LiveGRDScene(p, io_threads=io_threads, log=log)
        scenes[p].geocoder  # annotation XML once, single-threaded

    def check(job):
        idx, ps, lon, lat = job
        res = [scenes[p]._retry_fresh("recurs", "VV", lambda p=p: recurs(scenes[p], lon, lat), first_in_place=True) for p in ps]
        res = [x for x in res if x is not None]
        return idx, sum(res), len(res)

    t0 = time.time()
    with ThreadPoolExecutor(max(1, workers)) as ex:
        for idx, hits, n in ex.map(check, jobs):
            out.loc[idx, ["persist_dates", "persist_dates_checked"]] = [hits, n]
    fixed = out.confidence.isin(CANDIDATE_CLASSES) & (out.persist_dates_checked > 0) & (out.persist_dates >= out.persist_dates_checked)
    out.loc[fixed, "confidence"] = "fixed"
    stats["io_retries"] = int(stats.get("io_retries", 0)) + sum(s.io_retries for s in scenes.values())
    log(f"  persistence: {len(jobs)} candidates checked against {len(scenes)} earlier scenes, {int(fixed.sum())} fixed, {time.time() - t0:.0f} s")
    return out


def apply_rules(det: pd.DataFrame, log=print) -> pd.DataFrame:
    """Clutter-zone and near-fixed downgrades (after persistence). Adds n_low_1km and near_fixed_m."""
    out = det.copy()
    if "low_reason" not in out:
        out["low_reason"] = ""
    if out.empty:
        out["n_low_1km"], out["near_fixed_m"] = pd.Series(dtype=int), pd.Series(dtype=float)
        return out
    flag, out["n_low_1km"] = clutter_zone(out)
    out.loc[flag, ["confidence", "low_reason"]] = ["low", "clutter_zone"]
    n_cz = int(flag.sum())
    flag, dist = near_fixed(out, radius_m=250.0)
    out["near_fixed_m"] = np.where(np.isfinite(dist), np.round(dist, 1), np.nan)
    out.loc[flag, ["confidence", "low_reason"]] = ["low", "near_fixed"]
    log(f"  rules: clutter zone {n_cz}, near fixed {int(flag.sum())} downgraded to low")
    return out


def cnn_scores(det: pd.DataFrame, scene: GRDScene, idx, model, meta, workers: int = 2, block: int = 1024,
               log=print) -> pd.DataFrame:
    """Score the rows `idx` of `det` with the verifier: cnn_score, cnn_vessel, cnn_chip_valid_frac (others null).

    `scene` should be a LiveGRDScene (retried reads, bounded pool); chips are grouped by 1024 px tile so each COG tile
    is fetched once. `workers` CPU threads prepare the chip groups.
    """
    from darkvessel.ml.model import predict_proba

    out = det.copy()
    out["cnn_score"], out["cnn_vessel"], out["cnn_chip_valid_frac"] = np.nan, pd.array([None] * len(out), dtype="boolean"), np.nan
    idx = list(idx)
    if not idx or model is None:
        return out
    t0 = time.time()
    rows = out.loc[idx, "row"].to_numpy(float)
    cols = out.loc[idx, "col"].to_numpy(float)
    H, W = scene.shape
    chips = np.full((len(idx), 2, 2 * CHIP_HALF, 2 * CHIP_HALF), np.nan, np.float16)
    key = (rows // block).astype(int) * 100000 + (cols // block).astype(int)

    def read_group(kk):
        sel = np.nonzero(key == kk)[0]
        r0 = max(0, int(rows[sel].min()) - CHIP_HALF - 1)
        r1 = min(H, int(rows[sel].max()) + CHIP_HALF + 2)
        c0 = max(0, int(cols[sel].min()) - CHIP_HALF - 1)
        c1 = min(W, int(cols[sel].max()) + CHIP_HALF + 2)
        win = Window(c0, r0, c1 - c0, r1 - r0)
        sigma = {p: scene.read_sigma0(p, win, denoise=False) for p in ("VV", "VH")}
        return sel, chips_db(sigma, rows[sel], cols[sel], r0, c0)

    with ThreadPoolExecutor(max(1, workers)) as ex:
        for sel, ch in ex.map(read_group, np.unique(key)):
            chips[sel] = ch
    score = predict_proba(model, chips, meta, tta=True).astype(float)
    thr = float(meta["threshold"])
    centre = chips[:, :, CHIP_HALF - 4:CHIP_HALF + 4, CHIP_HALF - 4:CHIP_HALF + 4].astype(np.float32)
    valid = np.isfinite(centre).mean(axis=(1, 2, 3))
    out.loc[idx, "cnn_score"] = np.round(score, 4)
    out.loc[idx, "cnn_vessel"] = pd.array(score >= thr, dtype="boolean")
    out.loc[idx, "cnn_chip_valid_frac"] = np.round(valid, 3)
    log(f"  cnn: {len(idx)} chips in {len(np.unique(key))} windows, {int((score >= thr).sum())} accepted at {thr:.3f}, {time.time() - t0:.0f} s")
    return out


def process(path: str, aoi, inner, *, footprints=None, model=None, meta=None, model_id: str | None = None,
            pfa: float = 1e-6, workers: int = 2, io_threads: int = IO_THREADS, do_persistence: bool = True, do_cnn: bool = True, log=print):
    """Detection, persistence, rules and CNN for one scene. Returns (objects DataFrame with lon/lat, stats)."""
    det, stats = detect(path, aoi, inner, pfa=pfa, io_threads=io_threads, log=log)
    det = pd.DataFrame(det.drop(columns="geometry")) if "geometry" in det else pd.DataFrame(det)
    if det.empty:
        for c in ("persist_dates", "persist_dates_checked", "n_low_1km", "near_fixed_m", "cnn_score", "cnn_vessel", "cnn_chip_valid_frac"):
            det[c] = pd.Series(dtype=float)
        stats.update({"n_candidates_pre_rules": 0, "n_cnn_scored": 0, "cnn_model_id": model_id})
        return det, stats
    cand_idx = det.index[det.confidence.isin(CANDIDATE_CLASSES)].tolist()
    stats["n_candidates_pre_rules"] = len(cand_idx)
    det = (persistence(det, stats, footprints, workers=workers, io_threads=io_threads, log=log) if do_persistence
           else det.assign(persist_dates=0, persist_dates_checked=0))
    det = apply_rules(det, log=log)
    if do_cnn and model is not None:
        scene = LiveGRDScene(path, io_threads=io_threads, log=log)
        det = cnn_scores(det, scene, cand_idx, model, meta, workers=workers, log=log)
        stats["io_retries"] = int(stats.get("io_retries", 0)) + scene.io_retries
    else:
        det["cnn_score"], det["cnn_vessel"], det["cnn_chip_valid_frac"] = np.nan, pd.array([None] * len(det), dtype="boolean"), np.nan
    stats["n_cnn_scored"] = int(det.cnn_score.notna().sum())
    stats["cnn_model_id"] = model_id
    stats["cnn_threshold"] = float(meta["threshold"]) if meta else None
    return det, stats
