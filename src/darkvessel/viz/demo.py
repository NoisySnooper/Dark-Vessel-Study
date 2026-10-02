"""Build the self-contained HTML demo page from pipeline outputs.

Two views share one inspector:
- Regional: South China Sea AOI, 90-day Sentinel-1 coverage, the most recent processed scenes and
  their detections (data/detections_regional.gpkg, data/outputs/small/s1_passes_4326.tif).
- Ca Mau detail: one scene in radar view (data/detections_baseline.gpkg and the 40 m VV COG).
Imagery chips are re-read from the AWS mirror. Optional CNN scores from data/detections_ml.gpkg.
"""

from __future__ import annotations

import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.windows import Window
from shapely.geometry import box, mapping

from darkvessel.aoi import aoi_gdf, natural_earth_land
from darkvessel.config import CRS_UTM, DARK_CAVEAT, DATA_DIR, DEFAULT_AOI
from darkvessel.coverage import merge_passes
from darkvessel.landmask import sea_mask_on_grid
from darkvessel.s1.grd import GRDScene

TEMPLATE = Path(__file__).with_name("demo_template.html")
# Coverage classes: same bins and colours as docs/figures/coverage.png
COV_BINS = [0, 1, 3, 6, 11, 21, 10_000]
COV_COLORS = ["#e4e2dc", "#b7d3f6", "#86b6ef", "#5598e7", "#256abf", "#104281"]
COV_LABELS = ["not imaged", "1-2", "3-5", "6-10", "11-20", "21+"]


def _b64_png(arr: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def _b64_jpeg(rgb: np.ndarray, quality: int = 84) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
    return base64.b64encode(buf.getvalue()).decode()


def _dms(v: float, pos: str, neg: str) -> str:
    h = pos if v >= 0 else neg
    v = abs(v)
    d = int(v)
    m = (v - d) * 60
    return f"{d}°{m:06.3f}'{h}"


def _num(v, nd=1):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def render_map_image(cog_path: Path, buffer_m: float = 1000.0):
    """RGB map image: sea in gray, shore buffer dimmed, land dark and muted. Returns (rgb, transform, meta)."""
    with rasterio.open(cog_path) as ds:
        u8 = ds.read(1)
        tr = ds.transform
    valid = u8 > 0
    db = u8.astype(np.float32) * 35 / 255 - 35
    h, w = u8.shape
    xs = tr.c + (np.arange(w) + 0.5) * tr.a
    ys = tr.f + (np.arange(h) + 0.5) * tr.e
    X, Y = np.meshgrid(xs, ys)
    lon, lat = Transformer.from_crs(CRS_UTM, "EPSG:4326", always_xy=True).transform(X, Y)
    lon = np.where(valid, lon, np.nan)
    lat = np.where(valid, lat, np.nan)
    sea_ok, land = sea_mask_on_grid(lon, lat, cell_m=abs(tr.a), buffer_m=buffer_m)
    lo, hi = np.percentile(db[sea_ok & valid], [2, 99.7])
    g = np.clip((db - lo) / (hi - lo), 0, 1)
    rgb = np.zeros((h, w, 3), np.float32)
    rgb[:] = (12, 14, 16)
    sea_rgb = (g[..., None] * 235 + 8) * np.ones(3)
    rgb[sea_ok & valid] = sea_rgb[sea_ok & valid]
    buf = valid & ~sea_ok & ~land
    rgb[buf] = sea_rgb[buf] * 0.45
    tint = np.array([72, 68, 56], np.float32)
    rgb[land & valid] = (tint + g[..., None] * 70)[land & valid]
    meta = {"vmin_db": float(lo), "vmax_db": float(hi), "sea_frac": float((sea_ok & valid).mean())}
    return rgb.astype(np.uint8), tr, meta


def chip_png(scene: GRDScene, row: float, col: float, half: int = 24) -> str:
    win = Window(int(col) - half, int(row) - half, 2 * half, 2 * half)
    pair = []
    for pol in ("VV", "VH"):
        db = 10 * np.log10(scene.read_sigma0(pol, win, denoise=False))
        lo, hi = np.nanpercentile(db, 2), np.nanmax(db)
        pair.append(np.clip((db - lo) / max(hi - lo, 1e-3), 0, 1))
    gap = np.ones((2 * half, 2))
    return _b64_png((np.hstack([pair[0], gap, pair[1]]) * 255).astype(np.uint8))


def _scene_path(product_id: str) -> str:
    from darkvessel.s1.aws import parse_product_id

    p = parse_product_id(product_id)
    t = p["start"]
    return f"GRD/{t.year}/{t.month}/{t.day}/{p['mode']}/{p['pol']}/{product_id}"


def _record(r, lat, lon, m, extra=None):
    d = {
        "id": r.det_id, "c": r.confidence, "lat": round(float(lat), 5), "lon": round(float(lon), 5),
        "dms": f"{_dms(lat, 'N', 'S')} {_dms(lon, 'E', 'W')}",
        "mgrs": m.toMGRS(float(lat), float(lon), MGRSPrecision=4),
        "len": int(round(float(r.length_est_m))), "px_n": int(r.n_pixels),
        "vv": _num(r.peak_vv_db), "vh": _num(r.peak_vh_db), "svv": _num(r.scr_vv_db), "svh": _num(r.scr_vh_db),
        "inc": _num(r.inc_angle_deg), "per": int(r.persist_dates), "perN": int(r.persist_dates_checked),
    }
    if "cnn_score" in r._fields and not pd.isna(r.cnn_score):
        d["cnn"] = round(float(r.cnn_score), 3)
    if extra:
        d.update(extra)
    return d


def _chips_for(dets: pd.DataFrame, scene_of, n: int) -> dict:
    keep = dets[dets.confidence.isin(["high", "medium", "fixed"])].copy()
    keep["score"] = keep[["scr_vv_db", "scr_vh_db"]].max(axis=1)
    keep = keep.sort_values("score", ascending=False).head(n)
    jobs = [(r.det_id, scene_of(r), r.row, r.col) for r in keep.itertuples()]
    with ThreadPoolExecutor(8) as ex:
        out = ex.map(lambda j: (j[0], chip_png(j[1], j[2], j[3])), jobs)
        return dict(out)


def camau_data(m, max_chips: int) -> tuple[dict, str, dict]:
    summary = json.loads((DATA_DIR / "baseline_run_summary.json").read_text())
    dets = gpd.read_file(DATA_DIR / "detections_baseline.gpkg", layer="detections_baseline_utm48n")
    ml_path = DATA_DIR / "detections_ml.gpkg"
    if ml_path.exists():
        ml = gpd.read_file(ml_path, layer="detections_verified_utm48n")[["det_id", "cnn_score"]]
        dets = dets.merge(ml, on="det_id", how="left")
    rgb, tr, img_meta = render_map_image(DATA_DIR / "outputs" / "small" / "sigma0_vv_db_utm48n_40m_u8.tif")
    h, w = rgb.shape[:2]
    to_ll = Transformer.from_crs(CRS_UTM, "EPSG:4326", always_xy=True)
    lon, lat = to_ll.transform(dets.geometry.x.values, dets.geometry.y.values)
    px = (dets.geometry.x - tr.c) / tr.a
    py = (dets.geometry.y - tr.f) / tr.e
    scene = GRDScene(_scene_path(summary["scene_id"]))
    chips = _chips_for(dets, lambda r: scene, max_chips)
    full, low = [], []
    for i, r in enumerate(dets.itertuples()):
        if r.confidence == "low":
            low.append([round(px.iloc[i], 1), round(py.iloc[i], 1)])
        else:
            full.append(_record(r, lat[i], lon[i], m, {"x": round(px.iloc[i], 1), "y": round(py.iloc[i], 1),
                                                       "scene": summary["scene_id"], "t": summary["acq_utc"][:16]}))
    gx, gy = np.meshgrid(np.linspace(0, w, 9), np.linspace(0, h, 9))
    glon, glat = to_ll.transform(tr.c + gx * tr.a, tr.f + gy * tr.e)
    data = {"summary": summary, "img": {"w": w, "h": h, "res_m": abs(tr.a), **img_meta},
            "grid": {"nx": 9, "ny": 9, "w": w, "h": h, "lon": np.round(glon, 6).tolist(), "lat": np.round(glat, 6).tolist()},
            "dets": full, "low": low}
    return data, "data:image/jpeg;base64," + _b64_jpeg(rgb), chips


def coverage_overlay() -> tuple[str, list]:
    with rasterio.open(DATA_DIR / "outputs" / "small" / "s1_passes_4326.tif") as ds:
        c = ds.read(1)
        b = ds.bounds
    rgba = np.zeros(c.shape + (4,), np.uint8)
    inside = c != 65535
    for lo, hi, col in zip(COV_BINS[:-1], COV_BINS[1:], COV_COLORS):
        sel = inside & (c >= lo) & (c < hi)
        rgba[sel, :3] = [int(col[i:i + 2], 16) for i in (1, 3, 5)]
        rgba[sel, 3] = 200
    return "data:image/png;base64," + _b64_png(rgba), [[b.bottom, b.left], [b.top, b.right]]


def _geojson(geoms_props, nd=3):
    feats = []
    for geom, props in geoms_props:
        g = mapping(geom)
        g = json.loads(json.dumps(g), parse_float=lambda s: round(float(s), nd))
        feats.append({"type": "Feature", "geometry": g, "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def regional_data(m, max_chips: int) -> tuple[dict, dict]:
    summary = json.loads((DATA_DIR / "regional_summary.json").read_text())
    cov = json.loads((DATA_DIR / "s1_coverage.json").read_text())
    search = json.loads((DATA_DIR / "s1_search_summary.json").read_text())
    dets = gpd.read_file(DATA_DIR / "detections_regional.gpkg", layer="detections_regional_4326")
    proc = gpd.read_file(DATA_DIR / "detections_regional.gpkg", layer="scenes_processed_4326")
    a = aoi_gdf(DEFAULT_AOI)
    aoi = a.geometry.iloc[0]
    view = (aoi.bounds[0] - 1.0, aoi.bounds[1] - 1.0, aoi.bounds[2] + 1.0, aoi.bounds[3] + 1.0)
    land = natural_earth_land(bbox=view)
    land_geoms = [(g.intersection(box(*view)).simplify(0.015), {}) for g in land.geometry]
    land_geoms = [(g, p) for g, p in land_geoms if not g.is_empty and g.area > 0.0004]
    scenes = {}

    def scene_of(r):
        if r.scene_id not in scenes:
            scenes[r.scene_id] = GRDScene(_scene_path(r.scene_id))
        return scenes[r.scene_id]

    chips = _chips_for(dets, scene_of, max_chips)
    full, low = [], []
    for r in dets.itertuples():
        if r.confidence == "low":
            low.append([round(r.lat, 4), round(r.lon, 4)])
        else:
            full.append(_record(r, r.lat, r.lon, m, {"scene": r.scene_id, "t": str(r.acq_utc)[:16],
                                                     "mis": r.mission}))
    fp_all = gpd.read_file(DATA_DIR / "s1_footprints.gpkg", layer="s1_footprints_4326")
    passes = merge_passes(fp_all)
    aoi_ea = gpd.GeoSeries([aoi], crs="EPSG:4326").to_crs("EPSG:6933").iloc[0]
    inter = passes.to_crs("EPSG:6933").geometry.intersection(aoi_ea).area / 1e6
    pass_list = [{"t": pd.Timestamp(r.start_utc).strftime("%Y-%m-%dT%H:%M"), "mis": r.mission, "pass": r.pass_dir,
                  "km2": int(k)} for r, k in zip(passes.itertuples(), inter)]
    img, bounds = coverage_overlay()
    data = {
        "summary": summary, "coverage": cov, "search_window": [s.strip() for s in search["window"].split(" to ")],
        "aoi_km2": float(a.area_km2.iloc[0]), "cov_img": img, "cov_bounds": bounds,
        "cov_legend": [{"label": l, "color": c} for l, c in zip(COV_LABELS, COV_COLORS)],
        "view": [[view[1], view[0]], [view[3], view[2]]],
        "land": _geojson(land_geoms), "aoi": _geojson([(aoi.simplify(0.02), {})]),
        "fps": _geojson([(g.simplify(0.01), {"id": r.product_id, "t": str(r.start_utc)[:16], "mis": r.mission,
                                              "km2": int(r.tested_km2)}) for g, r in zip(proc.geometry, proc.itertuples())]),
        "dets": full, "low": low, "passes": pass_list,
    }
    return data, chips


def build_demo(out_html: Path, max_chips_regional: int = 500, max_chips_detail: int = 600) -> Path:
    import mgrs

    m = mgrs.MGRS()
    reg, chips_r = regional_data(m, max_chips_regional)
    det, det_img, chips_d = camau_data(m, max_chips_detail)
    data = {"regional": reg, "detail": det, "chips": {**chips_r, **chips_d}, "caveat": DARK_CAVEAT,
            "has_ml": any("cnn" in d for d in det["dets"])}
    html = TEMPLATE.read_text()
    leaflet_css = Path(__file__).with_name("leaflet-1.9.4.css").read_text()
    html = (html.replace("/*__LEAFLET_CSS__*/", leaflet_css)
                .replace("__DETAIL_IMG__", det_img)
                .replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":"))))
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html)
    return out_html
