"""Baseline pipeline: one Sentinel-1 GRD scene -> land-masked CA-CFAR detections.

Steps: windowed read (VV, VH) -> calibrated sigma0 -> WorldCover sea mask with shore
buffer -> CA-CFAR per polarisation -> object extraction -> VV/VH fusion -> geocoding.
"""

from __future__ import annotations

import datetime as dt
import gc
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely.geometry import Polygon

from darkvessel.config import CRS_GEO, DARK_CAVEAT
from darkvessel.detect.cfar import ca_cfar_tiled, estimate_enl, extract_detections
from darkvessel.landmask import sea_mask_on_grid, upsample_nearest
from darkvessel.s1.grd import GRDScene

ICT = dt.timezone(dt.timedelta(hours=7), "ICT")
DETECTOR = "ca_cfar_v0"


def _log(msg, t0):
    print(f"[{time.time() - t0:7.1f}s] {msg}", flush=True)


def window_polygon(scene: GRDScene, window, n: int = 25) -> Polygon:
    r0, c0 = int(window.row_off), int(window.col_off)
    r1, c1 = r0 + int(window.height) - 1, c0 + int(window.width) - 1
    rows = np.concatenate([np.full(n, r0), np.linspace(r0, r1, n), np.full(n, r1), np.linspace(r1, r0, n)])
    cols = np.concatenate([np.linspace(c0, c1, n), np.full(n, c1), np.linspace(c1, c0, n), np.full(n, c0)])
    lon, lat = scene.geocoder.lonlat(rows, cols)
    return Polygon(zip(lon, lat))


def fuse_polarisations(dv: pd.DataFrame, dh: pd.DataFrame, radius_px: float = 3.0) -> pd.DataFrame:
    """Merge VV and VH object lists. Objects within `radius_px` are one detection."""
    out = []
    used_h = set()
    if len(dv) and len(dh):
        tree = cKDTree(dh[["row", "col"]].values)
        dist, idx = tree.query(dv[["row", "col"]].values, distance_upper_bound=radius_px)
    else:
        dist, idx = np.full(len(dv), np.inf), np.full(len(dv), len(dh))
    for i, v in enumerate(dv.itertuples(index=False)):
        rec = {"row": v.row, "col": v.col, "detected_vv": True, "detected_vh": False,
               "n_pixels": v.n_pixels, "length_est_m": v.length_est_m, "width_est_m": v.width_est_m,
               "peak_vv_db": v.peak_sigma0_db, "bg_vv_db": v.bg_sigma0_db, "scr_vv_db": v.peak_to_bg_db,
               "peak_vh_db": np.nan, "bg_vh_db": np.nan, "scr_vh_db": np.nan}
        if np.isfinite(dist[i]) and idx[i] not in used_h:
            h = dh.iloc[int(idx[i])]
            used_h.add(int(idx[i]))
            rec.update({"detected_vh": True, "peak_vh_db": h.peak_sigma0_db, "bg_vh_db": h.bg_sigma0_db,
                        "scr_vh_db": h.peak_to_bg_db,
                        "length_est_m": max(v.length_est_m, h.length_est_m)})
        out.append(rec)
    for j, h in enumerate(dh.itertuples(index=False)):
        if j in used_h:
            continue
        out.append({"row": h.row, "col": h.col, "detected_vv": False, "detected_vh": True,
                    "n_pixels": h.n_pixels, "length_est_m": h.length_est_m, "width_est_m": h.width_est_m,
                    "peak_vv_db": np.nan, "bg_vv_db": np.nan, "scr_vv_db": np.nan,
                    "peak_vh_db": h.peak_sigma0_db, "bg_vh_db": h.bg_sigma0_db, "scr_vh_db": h.peak_to_bg_db})
    df = pd.DataFrame(out)
    if len(df):
        df["pol_class"] = np.select(
            [df.detected_vv & df.detected_vh, df.detected_vv], ["VV+VH", "VV only"], default="VH only"
        )
    return df


def run_baseline(scene_path: str, bbox: tuple[float, float, float, float], pols=("VV", "VH"),
                 pfa: float = 1e-6, guard: int = 81, background: int = 161, buffer_m: float = 1000.0,
                 enl: float | None = None, mask_factor: int = 8, min_pixels: int = 2,
                 keep_sigma: tuple[str, ...] = ("VV",)) -> dict:
    """Run the baseline on the part of `scene_path` inside `bbox` (lon/lat). Returns products.

    Only the polarisations in `keep_sigma` keep their full sigma0 array in the result; the
    rest are freed as soon as they are processed (a 15k x 15k window is ~0.9 GB per array).
    """
    t0 = time.time()
    scene = GRDScene(scene_path)
    window = scene.window_for_bbox(*bbox)
    if window is None:
        raise ValueError("bbox does not intersect the scene")
    h, w = int(window.height), int(window.width)
    _log(f"scene {scene.product_id} window rows {window.row_off}+{h} cols {window.col_off}+{w}", t0)

    # Sea mask on a decimated grid (mask_factor x 10 m), then back to full resolution.
    rd = np.arange(int(window.row_off), int(window.row_off) + h, mask_factor) + mask_factor / 2
    cd = np.arange(int(window.col_off), int(window.col_off) + w, mask_factor) + mask_factor / 2
    RR, CC = np.meshgrid(rd, cd, indexing="ij")
    lon_d, lat_d = scene.geocoder.lonlat(RR.ravel(), CC.ravel())
    lon_d, lat_d = lon_d.reshape(RR.shape), lat_d.reshape(RR.shape)
    sea_ok_d, land_d = sea_mask_on_grid(lon_d, lat_d, cell_m=10.0 * mask_factor, buffer_m=buffer_m)
    sea_ok = upsample_nearest(sea_ok_d, mask_factor, (h, w))
    _log(f"sea mask: {sea_ok_d.mean():.1%} of window is testable sea", t0)

    sigma, dets, cfar_meta = {}, {}, {}
    for pol in pols:
        s0 = scene.read_sigma0(pol, window, denoise=False)
        valid = np.isfinite(s0) & sea_ok
        _log(f"{pol}: read + calibrated, valid sea px {valid.sum():,}", t0)
        e = enl
        if e is None:
            r, c = h // 2, w // 2
            crop = (slice(max(0, r - 1024), r + 1024), slice(max(0, c - 1024), c + 1024))
            e = estimate_enl(s0[crop], valid[crop])
            e = float(np.clip(e, 1.0, 10.0)) if np.isfinite(e) else 4.4
        res = ca_cfar_tiled(s0, valid, pfa=pfa, guard=guard, background=background, enl=e)
        df = extract_detections(res["detect"], s0, res["bg_mean"], min_pixels=min_pixels,
                                row_off=int(window.row_off), col_off=int(window.col_off))
        cfar_meta[pol] = {"enl": e, "alpha": res["alpha"], "n_det_px": int(res["detect"].sum()),
                          "n_tested_px": int(res["testable"].sum()), "n_objects": len(df)}
        _log(f"{pol}: ENL {e:.2f} alpha {res['alpha']:.2f} -> {len(df)} objects", t0)
        dets[pol] = df
        if pol in keep_sigma:
            sigma[pol] = s0
        del s0, res, valid
        gc.collect()

    fused = fuse_polarisations(dets.get("VV", pd.DataFrame()), dets.get("VH", pd.DataFrame()))
    acq = scene.meta["start"].replace(tzinfo=dt.timezone.utc)
    if len(fused):
        lon, lat = scene.geocoder.lonlat(fused.row.values, fused.col.values)
        fused["lon"], fused["lat"] = lon, lat
        fused["inc_angle_deg"] = scene.geocoder.incidence(fused.row.values, fused.col.values)
        fused = fused.sort_values("lat", ascending=False).reset_index(drop=True)
        fused.insert(0, "det_id", [f"{scene.meta['mission']}_{acq:%Y%m%dT%H%M}_{i:04d}" for i in range(len(fused))])
    meta = {
        "scene_id": scene.product_id, "platform": scene.manifest["platform"], "acq_utc": acq.isoformat(),
        "acq_local_ict": acq.astimezone(ICT).isoformat(), "pass_dir": scene.manifest["pass_dir"],
        "orbit_rel": scene.manifest["orbit_rel"], "detector": DETECTOR, "pfa": pfa, "guard_px": guard,
        "background_px": background, "land_buffer_m": buffer_m,
        "enl_vv": cfar_meta.get("VV", {}).get("enl"), "enl_vh": cfar_meta.get("VH", {}).get("enl"),
        "ais_status": "not_checked: no AIS source connected", "caveat": DARK_CAVEAT,
    }
    gdf = gpd.GeoDataFrame(fused, geometry=gpd.points_from_xy(fused.get("lon", []), fused.get("lat", [])), crs=CRS_GEO)
    for k, v in meta.items():
        gdf[k] = v
    win_gdf = gpd.GeoDataFrame([{**meta, "rows": h, "cols": w}], geometry=[window_polygon(scene, window)], crs=CRS_GEO)
    _log(f"fused: {len(gdf)} detections ({gdf.pol_class.value_counts().to_dict() if len(gdf) else {}})", t0)
    return {"scene": scene, "window": window, "detections": gdf, "per_pol": dets, "sigma0": sigma,
            "sea_ok_d": sea_ok_d, "land_d": land_d, "lon_d": lon_d, "lat_d": lat_d, "mask_factor": mask_factor,
            "window_gdf": win_gdf, "cfar_meta": cfar_meta, "meta": meta, "runtime_s": time.time() - t0}
