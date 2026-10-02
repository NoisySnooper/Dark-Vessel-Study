"""Regional detection: stream whole Sentinel-1 scenes block by block, over sea inside the AOI only.

Per scene:
1. Sea mask on a 160 m grid over the full scene (WorldCover, open-sea connectivity, 1 km
   shore buffer) intersected with the AOI polygon.
2. One ENL estimate per polarisation from a few sea blocks (stable thresholds across blocks).
3. For every 2048 px block that contains testable sea: read the block plus an 81 px margin,
   CA-CFAR on VV and VH, keep objects whose centroid falls in the block core, fuse VV/VH.
4. Confidence classes as in the Ca Mau detail run (detect.postprocess.assign_confidence).
Memory stays near one padded block per worker whatever the scene size.

Persistence (fixed structures) is checked per candidate afterwards: a small window around the
position in up to two earlier passes of the same relative orbit, read from the 20 m overview.
"""

from __future__ import annotations

import datetime as dt
import gc

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from rasterio.windows import Window

from darkvessel.config import CRS_GEO, DARK_CAVEAT_SHORT
from darkvessel.detect.cfar import ca_cfar, estimate_enl, extract_detections, gamma_alpha
from darkvessel.detect.postprocess import assign_confidence
from darkvessel.landmask import sea_mask_on_grid
from darkvessel.pipeline import fuse_polarisations
from darkvessel.s1.grd import GRDScene

BLOCK = 2048
FACTOR = 16  # mask grid = 160 m


SEED_INSET_DEG = 0.05  # cells this far inside the marine-area AOI are certainly sea (about 5.5 km)


def scene_mask(scene: GRDScene, aoi_geom, buffer_m: float = 1000.0, factor: int = FACTOR, aoi_inner=None):
    """Testable-sea mask on the decimated grid (True = sea inside the AOI, beyond the shore buffer).

    `aoi_inner` (the AOI shrunk by SEED_INSET_DEG) seeds the sea test so nearshore water that
    WorldCover codes as permanent water is kept when it connects to the open sea of the AOI.
    """
    H, W = scene.shape
    rd = np.arange(0, H, factor) + factor / 2
    cd = np.arange(0, W, factor) + factor / 2
    RR, CC = np.meshgrid(rd, cd, indexing="ij")
    lon, lat = scene.geocoder.lonlat(RR.ravel(), CC.ravel())
    lon, lat = lon.reshape(RR.shape), lat.reshape(RR.shape)
    seed = shapely.contains_xy(aoi_inner, lon, lat) if aoi_inner is not None else None
    sea_ok, _ = sea_mask_on_grid(lon, lat, cell_m=10.0 * factor, buffer_m=buffer_m, factor=factor, seed=seed)
    in_aoi = shapely.contains_xy(aoi_geom, lon, lat)
    return sea_ok & in_aoi


def _block_valid(mask_d, r0, r1, c0, c1, factor=FACTOR):
    ri = np.clip(np.arange(r0, r1) // factor, 0, mask_d.shape[0] - 1)
    ci = np.clip(np.arange(c0, c1) // factor, 0, mask_d.shape[1] - 1)
    return mask_d[np.ix_(ri, ci)]


def process_scene(path: str, aoi_geom, pfa: float = 1e-6, guard: int = 81, background: int = 161,
                  buffer_m: float = 1000.0, min_pixels: int = 2, log=print, aoi_inner=None) -> tuple[gpd.GeoDataFrame, dict]:
    t0 = dt.datetime.now()
    scene = GRDScene(path)
    H, W = scene.shape
    mask_d = scene_mask(scene, aoi_geom, buffer_m, aoi_inner=aoi_inner)
    margin = background // 2 + 1
    blocks = []
    for r0 in range(0, H, BLOCK):
        for c0 in range(0, W, BLOCK):
            sub = mask_d[r0 // FACTOR:(r0 + BLOCK) // FACTOR + 1, c0 // FACTOR:(c0 + BLOCK) // FACTOR + 1]
            if sub.any():
                blocks.append((r0, c0, float(sub.mean())))
    stats = {"scene_id": scene.product_id, "blocks_total": int(np.ceil(H / BLOCK) * np.ceil(W / BLOCK)),
             "blocks_processed": len(blocks), "tested_km2": 0.0}
    if not blocks:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=CRS_GEO), stats

    # Scene-level ENL from up to 3 of the most sea-filled blocks
    enl = {}
    sample = sorted(blocks, key=lambda b: -b[2])[:3]
    for pol in ("VV", "VH"):
        vals = []
        for r0, c0, _ in sample:
            win = Window(c0, r0, min(BLOCK, W - c0), min(BLOCK, H - r0))
            s0 = scene.read_sigma0(pol, win, denoise=False)
            valid = np.isfinite(s0) & _block_valid(mask_d, r0, r0 + win.height, c0, c0 + win.width)
            if valid.mean() > 0.3:
                vals.append(estimate_enl(s0, valid))
        e = float(np.nanmedian(vals)) if vals else 4.4
        enl[pol] = float(np.clip(e, 1.0, 10.0)) if np.isfinite(e) else 4.4

    rows = []
    for r0, c0, _ in blocks:
        rr0, cc0 = max(0, r0 - margin), max(0, c0 - margin)
        rr1, cc1 = min(H, r0 + BLOCK + margin), min(W, c0 + BLOCK + margin)
        win = Window(cc0, rr0, cc1 - cc0, rr1 - rr0)
        sea = _block_valid(mask_d, rr0, rr1, cc0, cc1)
        per = {}
        for pol in ("VV", "VH"):
            s0 = scene.read_sigma0(pol, win, denoise=False)
            valid = np.isfinite(s0) & sea
            res = ca_cfar(s0, valid, guard=guard, background=background, pfa=pfa, enl=enl[pol])
            df = extract_detections(res["detect"], s0, res["bg_mean"], min_pixels=min_pixels, row_off=rr0, col_off=cc0)
            if len(df):
                core = (df.row >= r0) & (df.row < r0 + BLOCK) & (df.col >= c0) & (df.col < c0 + BLOCK)
                df = df[core]
            per[pol] = df
            if pol == "VV":
                core_valid = valid[r0 - rr0:r0 - rr0 + BLOCK, c0 - cc0:c0 - cc0 + BLOCK]
                stats["tested_km2"] += float((core_valid & res["testable"][r0 - rr0:r0 - rr0 + BLOCK,
                                                                            c0 - cc0:c0 - cc0 + BLOCK]).sum()) * 1e-4
            del s0, valid, res
        fused = fuse_polarisations(per["VV"], per["VH"])
        if len(fused):
            rows.append(fused)
        gc.collect()

    meta = {"scene_id": scene.product_id, "mission": scene.meta["mission"],
            "acq_utc": scene.meta["start"].replace(tzinfo=dt.timezone.utc).isoformat(),
            "pass_dir": scene.manifest["pass_dir"], "orbit_rel": scene.manifest["orbit_rel"],
            "enl_vv": enl["VV"], "enl_vh": enl["VH"], "alpha_vv": gamma_alpha(pfa, enl["VV"]),
            "alpha_vh": gamma_alpha(pfa, enl["VH"])}
    stats.update(meta)
    stats["runtime_s"] = (dt.datetime.now() - t0).total_seconds()
    if not rows:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=CRS_GEO), stats
    det = assign_confidence(pd.concat(rows, ignore_index=True))
    lon, lat = scene.geocoder.lonlat(det.row.values, det.col.values)
    det["lon"], det["lat"] = lon, lat
    det["inc_angle_deg"] = scene.geocoder.incidence(det.row.values, det.col.values)
    for k in ("scene_id", "mission", "acq_utc", "pass_dir", "orbit_rel"):
        det[k] = meta[k]
    det["det_id"] = [f"{meta['mission']}_{scene.meta['start']:%Y%m%dT%H%M%S}_{i:05d}" for i in range(len(det))]
    det["caveat"] = DARK_CAVEAT_SHORT
    log(f"{scene.product_id}: {stats['blocks_processed']}/{stats['blocks_total']} blocks, "
        f"{stats['tested_km2']:,.0f} km2 tested, {len(det)} objects, {stats['runtime_s']:.0f} s")
    return gpd.GeoDataFrame(det, geometry=gpd.points_from_xy(lon, lat), crs=CRS_GEO), stats


def recurs(scene: GRDScene, lon: float, lat: float, half: int = 16, ratio_db: float = 7.0) -> bool | None:
    """Is there a bright point within ~60 m of (lon, lat) in `scene`? None if not covered.

    Reads a 64 x 64 window of the 20 m overview (2x2 averaged pixels) of VV around the position
    and compares the brightest pixel near the centre with the median of the window. A fixed
    structure gives a contrast well above `ratio_db`; open sea does not.
    """
    r, c = scene.geocoder.rowcol(np.array([lon]), np.array([lat]))
    r, c = float(r[0]), float(c[0])
    H, W = scene.shape
    if not (np.isfinite(r) and np.isfinite(c)) or r < 2 * half or c < 2 * half or r > H - 2 * half or c > W - 2 * half:
        return None
    win = Window(int(c) - 2 * half, int(r) - 2 * half, 4 * half, 4 * half)
    dn = scene._ds("VV").read(1, window=win, out_shape=(2 * half, 2 * half)).astype(np.float64)
    if (dn == 0).mean() > 0.5:
        return None
    p = dn ** 2
    centre = p[half - 3:half + 3, half - 3:half + 3].max()
    bg = np.median(p[p > 0])
    return bool(bg > 0 and 10 * np.log10(centre / bg) >= ratio_db)
