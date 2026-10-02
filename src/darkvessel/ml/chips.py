"""CFAR candidates and 64 px chips for labelled AI2 windows, using the baseline detector settings.

Geolocation. AI2 warped each GRD to Web Mercator with plain `gdalwarp -t_srs epsg:3857`
(src/data/warp.py in their repo), which uses GDAL's default GCP polynomial transform built
from the 210 ground control points in the TIFF. Their label positions therefore live in
that geometry, not in the ESA annotation grid. This module uses the same GDAL transformer
(gdal.Transformer on the VV COG, no METHOD option) for every pixel <-> lon/lat conversion,
so candidates and labels share one frame. Checked on one scene with 119 matched vessels:
median candidate-to-label distance 2.8 px with GDAL's default versus 3.7 px with the
annotation grid, with a 2 px column bias removed.

Window geometry. A Web Mercator window is a rotated quadrilateral in radar geometry. The
read window is its pixel bounding box plus the CFAR margin; candidates are then kept only
if their lon/lat falls inside the window's lon/lat box, because the corner triangles of
the bounding box are outside the labelled area (unlabelled ships there would otherwise
become 'clutter'). Candidates are matched against every label of the scene, labels against
every CFAR object in the read window.

Sea mask. `landmask.sea_mask_on_grid` calls water 'sea' only when connected to a WorldCover
code-0 pixel inside the read box. The 150 km baseline window always has such pixels; a
10 km training window often does not (near Singapore a 0.7 degree box is coded 80
throughout). Here water is sea if its connected component contains code 0 or touches the
edge of a box padded by `pad_deg` around the scene's windows. The 1 km shore buffer, the
8 x decimated mask grid and the CFAR settings are the baseline's.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from osgeo import gdal
from rasterio.windows import Window
from scipy import ndimage
from scipy.spatial import cKDTree

from darkvessel.detect.cfar import ca_cfar_tiled, estimate_enl, extract_detections
from darkvessel.detect.postprocess import assign_confidence
from darkvessel.landmask import WATER_CODES, WC_RES_DEG, read_worldcover, upsample_nearest
from darkvessel.pipeline import fuse_polarisations
from darkvessel.s1.grd import GRDScene

gdal.UseExceptions()

CHIP_HALF = 32          # 64 x 64 px chips, 10 m pixels -> 640 m
MATCH_RADIUS_M = 50.0   # candidate within this distance of an AI2 point = vessel
AMBIGUOUS_RADIUS_M = 150.0  # 50-150 m (or within 0.75 x known length): same ship's fragment or click
PIXEL_M = 10.0          # offset; excluded from training, reported separately


def candidate_class(dist_m, label_length_m=None, radius_m: float = MATCH_RADIUS_M,
                    ambiguous_m: float = AMBIGUOUS_RADIUS_M) -> np.ndarray:
    """'vessel' (<= radius), 'ambiguous' (<= max(ambiguous_m, 0.75 x length)), else 'clutter'."""
    d = np.asarray(dist_m, dtype=float)
    L = np.zeros_like(d) if label_length_m is None else np.nan_to_num(np.asarray(label_length_m, dtype=float))
    amb = np.maximum(ambiguous_m, 0.75 * L)
    return np.select([d <= radius_m, d <= amb], ["vessel", "ambiguous"], default="clutter")


@dataclass(frozen=True)
class CfarSettings:
    """Exactly the baseline (pipeline.run_baseline defaults)."""

    pfa: float = 1e-6
    guard: int = 81
    background: int = 161
    min_pixels: int = 2
    fuse_radius_px: float = 3.0
    buffer_m: float = 1000.0
    mask_factor: int = 8
    enl_default: float = 4.4
    enl_clip: tuple = (1.0, 10.0)

    @property
    def margin(self) -> int:
        return self.background // 2 + 1


SETTINGS = CfarSettings()


# ----------------------------------------------------------------------------- geocoding
class GcpTransformer:
    """Pixel <-> lon/lat through GDAL's default GCP transform of the COG (AI2's geometry).

    Same interface as s1.grd.Geocoder: rowcol(lon, lat) and lonlat(rows, cols), NaN on failure.
    """

    def __init__(self, href: str):
        self.ds = gdal.Open(href)
        if self.ds.GetGCPCount() == 0:
            raise ValueError(f"no GCPs in {href}")
        self.shape = (self.ds.RasterYSize, self.ds.RasterXSize)
        self._tr = gdal.Transformer(self.ds, None, ["DST_SRS=WGS84"])

    def _apply(self, to_pixel: int, x, y):
        x = np.asarray(x, dtype=float).ravel()
        y = np.asarray(y, dtype=float).ravel()
        if len(x) == 0:
            return np.zeros(0), np.zeros(0)
        pts, ok = self._tr.TransformPoints(to_pixel, [(float(a), float(b), 0.0) for a, b in zip(x, y)])
        out = np.array([(p[0], p[1]) for p in pts], dtype=float).reshape(-1, 2)
        bad = ~np.array(ok, dtype=bool) | ~np.isfinite(x) | ~np.isfinite(y)
        out[bad] = np.nan
        return out[:, 0], out[:, 1]

    def rowcol(self, lon, lat):
        col, row = self._apply(1, lon, lat)
        return row, col

    def lonlat(self, rows, cols):
        lon, lat = self._apply(0, cols, rows)
        return lon, lat


def bbox_window(geocoder, shape: tuple[int, int], west, south, east, north, n: int = 21) -> Window | None:
    """Pixel window covering a lon/lat box, clipped to the image; None if disjoint."""
    LON, LAT = np.meshgrid(np.linspace(west, east, n), np.linspace(south, north, n))
    r, c = geocoder.rowcol(LON.ravel(), LAT.ravel())
    ok = np.isfinite(r) & np.isfinite(c)
    if not ok.any():
        return None
    H, W = shape
    r0, r1 = max(0, int(np.floor(r[ok].min()))), min(H, int(np.ceil(r[ok].max())) + 1)
    c0, c1 = max(0, int(np.floor(c[ok].min()))), min(W, int(np.ceil(c[ok].max())) + 1)
    if r1 - r0 < 2 or c1 - c0 < 2:
        return None
    return Window(c0, r0, c1 - c0, r1 - r0)


def pad_window(win: Window, margin: int, shape: tuple[int, int]) -> Window:
    H, W = shape
    r0, c0 = max(0, int(win.row_off) - margin), max(0, int(win.col_off) - margin)
    r1 = min(H, int(win.row_off + win.height) + margin)
    c1 = min(W, int(win.col_off + win.width) + margin)
    return Window(c0, r0, c1 - c0, r1 - r0)


def in_lonlat_box(lon, lat, west, south, east, north) -> np.ndarray:
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    return (lon >= west) & (lon < east) & (lat >= south) & (lat < north)


# ----------------------------------------------------------------------------- sea mask
class SceneSeaMask:
    """WorldCover land/sea over a lon/lat box, with open-sea connectivity and a shore buffer.

    Built once per scene; `sea_ok(lon, lat)` samples it for any grid of coordinates.
    """

    def __init__(self, west, south, east, north, buffer_m: float = 1000.0, factor: int = 8, pad_deg: float = 0.35):
        self.bounds = (west - pad_deg, south - pad_deg, east + pad_deg, north + pad_deg)
        try:
            wc, self.transform = read_worldcover(self.bounds, factor)
            self.empty = False
        except FileNotFoundError:  # no WorldCover tile at all: open ocean
            wc, self.transform, self.empty = None, None, True
        if self.empty:
            self._sea_ok = None
            self.land_fraction = 0.0
            return
        sea = connected_sea(wc)
        lat_c = 0.5 * (self.bounds[1] + self.bounds[3])
        cell_deg = WC_RES_DEG * factor
        dy = cell_deg * 111320.0
        dx = dy * max(np.cos(np.radians(lat_c)), 0.05)
        dist = ndimage.distance_transform_edt(sea, sampling=(dy, dx))
        self._sea_ok = sea & (dist > buffer_m)
        self.land_fraction = float((~sea).mean())

    def sea_ok(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        ok = np.isfinite(lon) & np.isfinite(lat)
        if self.empty:
            return ok
        inv = ~self.transform
        cols, rows = inv * (np.nan_to_num(lon), np.nan_to_num(lat))
        rows = np.clip(rows.astype(int), 0, self._sea_ok.shape[0] - 1)
        cols = np.clip(cols.astype(int), 0, self._sea_ok.shape[1] - 1)
        return self._sea_ok[rows, cols] & ok


def connected_sea(wc: np.ndarray, water_codes=WATER_CODES) -> np.ndarray:
    """Water (codes 0/80) whose connected component holds code 0 or touches the array edge."""
    water = np.isin(wc, water_codes)
    lab, _ = ndimage.label(water, structure=np.ones((3, 3)))
    edge = np.concatenate([lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]])
    ids = np.unique(np.concatenate([lab[(wc == 0) & water], edge]))
    ids = ids[ids > 0]
    return np.isin(lab, ids)


def sea_mask_for_window(mask: SceneSeaMask, geocoder, win: Window, factor: int = 8) -> tuple[np.ndarray, float]:
    """Full-resolution sea_ok for `win` from a decimated lon/lat grid. Returns (mask, sea fraction)."""
    r0, c0, h, w = int(win.row_off), int(win.col_off), int(win.height), int(win.width)
    rd = np.arange(r0, r0 + h, factor) + factor / 2
    cd = np.arange(c0, c0 + w, factor) + factor / 2
    RR, CC = np.meshgrid(rd, cd, indexing="ij")
    lon, lat = geocoder.lonlat(RR.ravel(), CC.ravel())
    ok_d = mask.sea_ok(lon, lat).reshape(RR.shape)
    return upsample_nearest(ok_d, factor, (h, w)), float(ok_d.mean())


# ----------------------------------------------------------------------------- candidates
def cfar_window(scene: GRDScene, read_win: Window, sea_ok: np.ndarray, settings: CfarSettings = SETTINGS,
                pols=("VV", "VH")) -> tuple[pd.DataFrame, dict, dict]:
    """Baseline CFAR on one read window. Returns (fused objects in scene px, sigma0 by pol, meta)."""
    sigma, dets, meta = {}, {}, {}
    r0, c0 = int(read_win.row_off), int(read_win.col_off)
    for pol in pols:
        s0 = scene.read_sigma0(pol, read_win, denoise=False)
        valid = np.isfinite(s0) & sea_ok
        enl = estimate_enl(s0, valid) if valid.sum() > 10_000 else float("nan")
        enl = float(np.clip(enl, *settings.enl_clip)) if np.isfinite(enl) else settings.enl_default
        res = ca_cfar_tiled(s0, valid, pfa=settings.pfa, guard=settings.guard, background=settings.background, enl=enl)
        df = extract_detections(res["detect"], s0, res["bg_mean"], min_pixels=settings.min_pixels, row_off=r0, col_off=c0)
        meta[pol] = {"enl": enl, "alpha": float(res["alpha"]), "n_valid_px": int(valid.sum()),
                     "n_tested_px": int(res["testable"].sum()), "n_det_px": int(res["detect"].sum()), "n_objects": len(df)}
        sigma[pol], dets[pol] = s0, df
    fused = fuse_polarisations(dets.get("VV", pd.DataFrame()), dets.get("VH", pd.DataFrame()),
                               radius_px=settings.fuse_radius_px)
    return fused, sigma, meta


def local_metres(lon, lat, lon0, lat0):
    """Equirectangular offsets (east_m, north_m) from (lon0, lat0); fine for < 20 km."""
    k = np.cos(np.radians(lat0))
    return (np.asarray(lon) - lon0) * 111320.0 * k, (np.asarray(lat) - lat0) * 110540.0


def match_points(cand_lon, cand_lat, lab_lon, lab_lat):
    """Nearest-label distance for each candidate and nearest-candidate distance for each label (metres).

    Returns (cand_dist, cand_idx, lab_dist, lab_idx); idx is -1 and dist inf where the other set is empty.
    """
    cand_lon, cand_lat = np.asarray(cand_lon, float), np.asarray(cand_lat, float)
    lab_lon, lab_lat = np.asarray(lab_lon, float), np.asarray(lab_lat, float)
    nc, nl = len(cand_lon), len(lab_lon)
    cand_dist, cand_idx = np.full(nc, np.inf), np.full(nc, -1)
    lab_dist, lab_idx = np.full(nl, np.inf), np.full(nl, -1)
    if nc == 0 or nl == 0:
        return cand_dist, cand_idx, lab_dist, lab_idx
    lon0 = float(np.concatenate([cand_lon, lab_lon]).mean())
    lat0 = float(np.concatenate([cand_lat, lab_lat]).mean())
    cx, cy = local_metres(cand_lon, cand_lat, lon0, lat0)
    lx, ly = local_metres(lab_lon, lab_lat, lon0, lat0)
    tl = cKDTree(np.column_stack([lx, ly]))
    cand_dist, cand_idx = tl.query(np.column_stack([cx, cy]))
    tc = cKDTree(np.column_stack([cx, cy]))
    lab_dist, lab_idx = tc.query(np.column_stack([lx, ly]))
    return cand_dist, cand_idx, lab_dist, lab_idx


def extract_chip(arr: np.ndarray, row: float, col: float, row_off: int, col_off: int, half: int = CHIP_HALF) -> np.ndarray:
    """(2*half) x (2*half) crop of `arr` centred on scene pixel (row, col); outside = NaN."""
    r = int(round(row)) - row_off
    c = int(round(col)) - col_off
    out = np.full((2 * half, 2 * half), np.nan, dtype=arr.dtype)
    r0, r1 = r - half, r + half
    c0, c1 = c - half, c + half
    sr0, sr1 = max(r0, 0), min(r1, arr.shape[0])
    sc0, sc1 = max(c0, 0), min(c1, arr.shape[1])
    if sr1 > sr0 and sc1 > sc0:
        out[sr0 - r0 : sr1 - r0, sc0 - c0 : sc1 - c0] = arr[sr0:sr1, sc0:sc1]
    return out


def chips_db(sigma: dict, rows, cols, row_off: int, col_off: int, half: int = CHIP_HALF) -> np.ndarray:
    """Stack of (n, 2, 2*half, 2*half) chips in dB (VV, VH), float16, NaN outside the data."""
    n = len(rows)
    out = np.full((n, 2, 2 * half, 2 * half), np.nan, dtype=np.float16)
    with np.errstate(divide="ignore", invalid="ignore"):
        db = {p: 10 * np.log10(sigma[p]) for p in ("VV", "VH")}
    for i, (r, c) in enumerate(zip(rows, cols)):
        for k, p in enumerate(("VV", "VH")):
            out[i, k] = extract_chip(db[p], r, c, row_off, col_off, half).astype(np.float16)
    return out


# ----------------------------------------------------------------------------- per window / scene
def process_window(scene, geocoder, shape, mask, win_rec, win_labels: pd.DataFrame, scene_labels: pd.DataFrame,
                   settings: CfarSettings = SETTINGS):
    """One labelled window -> (candidates, label frame, candidate chips, miss chips, meta). None if off-scene."""
    inner = bbox_window(geocoder, shape, win_rec.west, win_rec.south, win_rec.east, win_rec.north)
    if inner is None:
        return None
    read_win = pad_window(inner, settings.margin, shape)
    sea_ok, sea_frac = sea_mask_for_window(mask, geocoder, read_win, settings.mask_factor)
    r0, c0 = int(read_win.row_off), int(read_win.col_off)
    meta = {"window_id": int(win_rec.window_id), "rows": int(read_win.height), "cols": int(read_win.width),
            "sea_fraction": sea_frac}
    if sea_frac == 0.0:
        meta.update({"n_candidates": 0, "skipped": "no testable sea"})
        return pd.DataFrame(), _label_frame(win_labels, geocoder, shape, sea_ok, r0, c0, None), None, None, meta
    fused, sigma, cfar_meta = cfar_window(scene, read_win, sea_ok, settings)
    meta["cfar"] = cfar_meta
    if len(fused):
        lon, lat = geocoder.lonlat(fused.row.values, fused.col.values)
        fused["lon"], fused["lat"] = lon, lat
    # labels of this window against every CFAR object in the read area (ships straddling the edge)
    _, _, lab_dist, _ = match_points(fused.lon.values if len(fused) else [], fused.lat.values if len(fused) else [],
                                     win_labels.lon.values, win_labels.lat.values)
    labels_out = _label_frame(win_labels, geocoder, shape, sea_ok, r0, c0, lab_dist)
    # candidates: only inside the labelled window, matched against all labels of the scene
    if len(fused):
        keep = in_lonlat_box(fused.lon, fused.lat, win_rec.west, win_rec.south, win_rec.east, win_rec.north)
        fused = fused[keep].reset_index(drop=True)
    meta["n_candidates"] = int(len(fused))
    if len(fused):
        fused = assign_confidence(fused)
        cand_dist, cand_idx, _, _ = match_points(fused.lon.values, fused.lat.values,
                                                 scene_labels.lon.values, scene_labels.lat.values)
        has_lab = len(scene_labels) > 0
        near_len = scene_labels.length_m.values[cand_idx] if has_lab else np.full(len(fused), np.nan)
        fused["match_dist_m"] = cand_dist
        fused["cand_class"] = candidate_class(cand_dist, near_len)
        fused["is_vessel"] = fused.cand_class == "vessel"
        matched = (fused.cand_class != "clutter").values
        fused["match_label_id"] = np.where(matched, scene_labels.label_id.values[cand_idx] if has_lab else -1, -1)
        fused["label_length_m"] = np.where(matched, near_len, np.nan)
        fused["enl_vv"] = cfar_meta.get("VV", {}).get("enl")
        fused["enl_vh"] = cfar_meta.get("VH", {}).get("enl")
    cand_chips = chips_db(sigma, fused.row.values, fused.col.values, r0, c0) if len(fused) else None
    miss_chips = None
    if len(labels_out):
        miss_rows = labels_out[~labels_out.cfar_detected]
        if len(miss_rows):
            miss_chips = chips_db(sigma, miss_rows.row.values, miss_rows.col.values, r0, c0)
    return fused, labels_out, cand_chips, miss_chips, meta


def _label_frame(labels, geocoder, shape, sea_ok, r0, c0, lab_dist):
    """Every AI2 label of the window with its scene pixel, sea flag, and nearest-object distance."""
    if len(labels) == 0:
        return pd.DataFrame()
    rr, cc = geocoder.rowcol(labels.lon.values, labels.lat.values)
    H, W = shape
    in_scene = np.isfinite(rr) & np.isfinite(cc) & (rr >= 0) & (rr < H) & (cc >= 0) & (cc < W)
    ri = np.clip(np.nan_to_num(rr).astype(int) - r0, 0, sea_ok.shape[0] - 1)
    ci = np.clip(np.nan_to_num(cc).astype(int) - c0, 0, sea_ok.shape[1] - 1)
    in_read = in_scene & (np.nan_to_num(rr) >= r0) & (np.nan_to_num(rr) < r0 + sea_ok.shape[0]) \
        & (np.nan_to_num(cc) >= c0) & (np.nan_to_num(cc) < c0 + sea_ok.shape[1])
    on_sea = np.where(in_read, sea_ok[ri, ci], False)
    out = labels[["label_id", "window_id", "lon", "lat", "length_m", "has_attrs", "split", "split_group"]].copy()
    out["row"], out["col"] = rr, cc
    out["in_scene"], out["on_testable_sea"] = in_scene, on_sea
    d = np.full(len(labels), np.inf) if lab_dist is None else np.asarray(lab_dist, float)
    out["nearest_cand_m"] = d
    out["cfar_detected"] = d <= MATCH_RADIUS_M                       # primary rule, 50 m
    out["cfar_detected_loose"] = candidate_class(d, out.length_m.values) != "clutter"  # sensitivity
    return out


def scene_outputs(out_dir: Path, product_id: str) -> tuple[Path, Path, Path]:
    return (out_dir / f"{product_id}.npz", out_dir / "index" / f"{product_id}.parquet",
            out_dir / "index" / f"{product_id}.misses.parquet")


def scene_done(out_dir: Path, product_id: str) -> bool:
    return all(p.exists() for p in scene_outputs(out_dir, product_id)[:2])


def process_scene(product_id: str, aws_path: str, windows: pd.DataFrame, labels: pd.DataFrame, out_dir: Path,
                  settings: CfarSettings = SETTINGS, log=print) -> dict:
    """All selected windows of one scene -> chips npz + candidate/label parquet. Resumable at scene level.

    `labels` must hold every dataset-1 label of the scene (not only those of the selected windows).
    """
    t0 = time.time()
    out_dir = Path(out_dir)
    (out_dir / "index").mkdir(parents=True, exist_ok=True)
    npz_path, idx_path, miss_path = scene_outputs(out_dir, product_id)
    scene = GRDScene(aws_path)
    geocoder = GcpTransformer(scene.href("VV"))
    shape = geocoder.shape
    mask = SceneSeaMask(windows.west.min(), windows.south.min(), windows.east.max(), windows.north.max(),
                        buffer_m=settings.buffer_m, factor=settings.mask_factor)
    cands, misses, chips, mchips, wmeta = [], [], [], [], []
    for rec in windows.itertuples(index=False):
        wl = labels[labels.window_id == rec.window_id]
        try:
            res = process_window(scene, geocoder, shape, mask, rec, wl, labels, settings)
        except Exception as e:  # keep the scene going; the window is reported as failed
            wmeta.append({"window_id": int(rec.window_id), "error": f"{type(e).__name__}: {e}"})
            log(f"  window {rec.window_id} failed: {type(e).__name__}: {e}")
            continue
        if res is None:
            wmeta.append({"window_id": int(rec.window_id), "skipped": "outside scene"})
            continue
        fused, miss, cch, mch, meta = res
        wmeta.append(meta)
        if len(fused):
            fused = fused.copy()
            fused["window_id"] = int(rec.window_id)
            fused["split"], fused["split_group"], fused["region"] = rec.split, rec.split_group, rec.region
            fused["window_n_labels"] = int(rec.n_labels)
            cands.append(fused)
            chips.append(cch)
        if len(miss):
            miss = miss.copy()
            miss["region"] = rec.region
            misses.append(miss)
            if mch is not None:
                mchips.append(mch)
    cand_df = pd.concat(cands, ignore_index=True) if cands else pd.DataFrame()
    miss_df = pd.concat(misses, ignore_index=True) if misses else pd.DataFrame()
    for df in (cand_df, miss_df):
        if len(df):
            df["product_id"] = product_id
            df["mission"] = product_id[:3]
    if len(cand_df):
        cand_df.insert(0, "cand_id", [f"{product_id}_{i:05d}" for i in range(len(cand_df))])
        cand_df["chip_index"] = np.arange(len(cand_df))
    chips_arr = np.concatenate(chips) if chips else np.zeros((0, 2, 2 * CHIP_HALF, 2 * CHIP_HALF), np.float16)
    mchips_arr = np.concatenate(mchips) if mchips else np.zeros((0, 2, 2 * CHIP_HALF, 2 * CHIP_HALF), np.float16)
    if len(miss_df):
        miss_df["chip_index"] = -1
        sel = (~miss_df.cfar_detected).values
        miss_df.loc[sel, "chip_index"] = np.arange(int(sel.sum()))
    # atomic writes so a killed run never leaves a half-written scene
    tmp = npz_path.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, chips=chips_arr, miss_chips=mchips_arr,
                        cand_id=cand_df.cand_id.values.astype(str) if len(cand_df) else np.array([], str))
    os.replace(tmp, npz_path)
    miss_df.to_parquet(miss_path.with_suffix(".tmp.parquet"), index=False)
    os.replace(miss_path.with_suffix(".tmp.parquet"), miss_path)
    cand_df.to_parquet(idx_path.with_suffix(".tmp.parquet"), index=False)
    os.replace(idx_path.with_suffix(".tmp.parquet"), idx_path)
    summary = {
        "product_id": product_id, "n_windows": int(len(windows)), "n_candidates": int(len(cand_df)),
        "n_positive": int(cand_df.is_vessel.sum()) if len(cand_df) else 0,
        "n_ambiguous": int((cand_df.cand_class == "ambiguous").sum()) if len(cand_df) else 0,
        "n_labels": int(len(miss_df)),
        "n_labels_on_sea": int(miss_df.on_testable_sea.sum()) if len(miss_df) else 0,
        "n_cfar_detected": int(miss_df.cfar_detected.sum()) if len(miss_df) else 0,
        "n_window_errors": sum("error" in m for m in wmeta), "runtime_s": round(time.time() - t0, 1),
        "settings": asdict(settings), "windows": wmeta,
    }
    (out_dir / "index" / f"{product_id}.json").write_text(json.dumps(summary, default=str))
    return summary
