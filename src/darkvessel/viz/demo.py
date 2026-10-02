"""Build the self-contained HTML demo page from pipeline outputs.

Two views share one inspector:
- Regional: South China Sea AOI, 90-day Sentinel-1 coverage, the most recent processed scenes and
  their detections (data/detections_regional_all.gpkg, data/outputs/small/s1_passes_4326.tif).
  Regional records ship as base64 typed columns so tens of thousands of contacts fit in the page.
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
    """VV | VH chip (2*half px square) around a pixel; parts outside the image are left dark."""
    H, W = scene.shape
    r0, c0 = int(row) - half, int(col) - half
    rr0, cc0 = max(r0, 0), max(c0, 0)
    rr1, cc1 = min(r0 + 2 * half, H), min(c0 + 2 * half, W)
    win = Window(cc0, rr0, cc1 - cc0, rr1 - rr0)
    pair = []
    for pol in ("VV", "VH"):
        full = np.full((2 * half, 2 * half), np.nan, np.float32)
        full[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = scene.read_sigma0(pol, win, denoise=False)
        db = 10 * np.log10(full)
        lo, hi = np.nanpercentile(db, 2), np.nanmax(db)
        pair.append(np.nan_to_num(np.clip((db - lo) / max(hi - lo, 1e-3), 0, 1)))
    gap = np.ones((2 * half, 2))
    img = (np.hstack([pair[0], gap, pair[1]]) * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def _scene_path(product_id: str) -> str:
    from darkvessel.s1.aws import parse_product_id

    p = parse_product_id(product_id)
    t = p["start"]
    return f"GRD/{t.year}/{t.month}/{t.day}/{p['mode']}/{p['pol']}/{product_id}"


CLASS_CODE = {"high": 0, "medium": 1, "fixed": 2, "low": 3}
COLS = ["lat", "lon", "c", "len", "px", "svv", "svh", "vv", "vh", "inc", "per", "perN", "s", "n"]


def _scene_table(det_ids: pd.Series, scene_ids: pd.Series, times: pd.Series, missions: pd.Series):
    """Per-scene id prefix so each record only carries its numeric suffix."""
    scenes, index = [], {}
    for did, sid, t, mis in zip(det_ids, scene_ids, times, missions):
        if sid in index:
            continue
        pfx, num = did.rsplit("_", 1)
        index[sid] = len(scenes)
        scenes.append({"id": sid, "pfx": pfx + "_", "w": len(num), "t": str(t)[:16], "mis": mis})
    return scenes, index


def _rows(dets: pd.DataFrame, lat, lon, index, extra_cols=()):
    out = []
    for i, r in enumerate(dets.itertuples()):
        row = [round(float(lat[i]), 5), round(float(lon[i]), 5), CLASS_CODE[r.confidence],
               int(round(float(r.length_est_m))), int(r.n_pixels), _num(r.scr_vv_db), _num(r.scr_vh_db),
               _num(r.peak_vv_db), _num(r.peak_vh_db), _num(r.inc_angle_deg), int(r.persist_dates),
               int(r.persist_dates_checked), index[r.scene_id], int(r.det_id.rsplit("_", 1)[1])]
        row += [getattr(r, c) for c in extra_cols]
        out.append(row)
    return out


def _b64col(a: np.ndarray, t: str, scale: float = 0, na=None) -> dict:
    return {"t": t, "b": base64.b64encode(np.ascontiguousarray(a).tobytes()).decode(), "s": scale, "na": na}


def _cols_b64(dets: pd.DataFrame, lat, lon, index) -> dict:
    """Same fields as _rows, as little-endian typed columns (about 30 bytes a record instead of 70).

    Scaled columns decode as value / s in the page; na marks missing values.
    """
    def q(v, s, dtype, na):
        v = np.asarray(v, float)
        return np.where(np.isfinite(v), np.round(v * s), na).astype(dtype)

    return {
        "lat": _b64col(np.round(np.asarray(lat, float) * 1e5).astype("<i4"), "i32", 1e5),
        "lon": _b64col(np.round(np.asarray(lon, float) * 1e5).astype("<i4"), "i32", 1e5),
        "c": _b64col(dets.confidence.map(CLASS_CODE).to_numpy("<u1"), "u8"),
        "len": _b64col(np.clip(np.round(dets.length_est_m.to_numpy(float)), 0, 65535).astype("<u2"), "u16"),
        "px": _b64col(np.clip(dets.n_pixels.to_numpy(float), 0, 65535).astype("<u2"), "u16"),
        "svv": _b64col(q(dets.scr_vv_db, 10, "<i2", -32768), "i16", 10, -32768),
        "svh": _b64col(q(dets.scr_vh_db, 10, "<i2", -32768), "i16", 10, -32768),
        "vv": _b64col(q(dets.peak_vv_db, 10, "<i2", -32768), "i16", 10, -32768),
        "vh": _b64col(q(dets.peak_vh_db, 10, "<i2", -32768), "i16", 10, -32768),
        "inc": _b64col(q(dets.inc_angle_deg, 10, "<i2", -32768), "i16", 10, -32768),
        "per": _b64col(dets.persist_dates.to_numpy("<u1"), "u8"),
        "perN": _b64col(dets.persist_dates_checked.to_numpy("<u1"), "u8"),
        "s": _b64col(dets.scene_id.map(index).to_numpy("<u2"), "u16"),
        "n": _b64col(dets.det_id.str.rsplit("_", n=1).str[1].astype(int).to_numpy("<u4"), "u32"),
    }


QUEUE_SHARE = {"high": 0.4, "medium": 0.4, "fixed": 0.2}


def label_queue(dets: pd.DataFrame, n: int, seed: int = 20261002) -> list[str]:
    """Labeling sample: a fixed random draw per class (40 % high, 40 % medium, 20 % fixed), shuffled.

    A random draw, not the brightest contacts, so labels give unbiased per-class precision. The seed
    and shares are recorded so the sampling weights can be recovered (class count / sample count).
    """
    parts = []
    for k, share in QUEUE_SHARE.items():
        pool = dets[dets.confidence == k]
        parts.append(pool.sample(min(len(pool), int(round(n * share))), random_state=seed))
    q = pd.concat(parts).sample(frac=1.0, random_state=seed)
    return q.det_id.tolist()


def _chips_for(dets: pd.DataFrame, scene_of, ids: list[str]) -> dict:
    keep = dets[dets.det_id.isin(ids)]
    jobs = [(r.det_id, scene_of(r), r.row, r.col) for r in keep.itertuples()]
    with ThreadPoolExecutor(8) as ex:
        out = ex.map(lambda j: (j[0], chip_png(j[1], j[2], j[3])), jobs)
        return dict(out)


def _top_ids(dets: pd.DataFrame, n: int = 12) -> list[str]:
    keep = dets[dets.confidence.isin(["high", "medium"])]
    score = keep[["scr_vv_db", "scr_vh_db"]].max(axis=1)
    return keep.loc[score.sort_values(ascending=False).index[:n], "det_id"].tolist()


def camau_data(max_chips: int, max_px: int = 2600) -> tuple[dict, str, dict]:
    summary = json.loads((DATA_DIR / "baseline_run_summary.json").read_text())
    dets = gpd.read_file(DATA_DIR / "detections_baseline.gpkg", layer="detections_baseline_utm48n")
    ml_path = DATA_DIR / "detections_ml.gpkg"
    if ml_path.exists():
        ml = gpd.read_file(ml_path, layer="detections_verified_utm48n")[["det_id", "cnn_score"]]
        dets = dets.merge(ml, on="det_id", how="left")
    rgb, tr, img_meta = render_map_image(DATA_DIR / "outputs" / "small" / "sigma0_vv_db_utm48n_40m_u8.tif")
    k = max(1.0, max(rgb.shape[:2]) / max_px)
    if k > 1:
        rgb = np.asarray(Image.fromarray(rgb).resize((int(rgb.shape[1] / k), int(rgb.shape[0] / k)), Image.LANCZOS))
        tr = tr * tr.scale(k, k)
    h, w = rgb.shape[:2]
    to_ll = Transformer.from_crs(CRS_UTM, "EPSG:4326", always_xy=True)
    lon, lat = to_ll.transform(dets.geometry.x.values, dets.geometry.y.values)
    dets["px_x"] = ((dets.geometry.x - tr.c) / tr.a).round(1)
    dets["px_y"] = ((dets.geometry.y - tr.f) / tr.e).round(1)
    dets["scene_id"] = summary["scene_id"]
    scene = GRDScene(_scene_path(summary["scene_id"]))
    queue = label_queue(dets, max_chips)
    chips = _chips_for(dets, lambda r: scene, queue + _top_ids(dets))
    scenes, index = _scene_table(dets.det_id, dets.scene_id, pd.Series([summary["acq_utc"]] * len(dets)),
                                 pd.Series([summary["scene_id"][:3]] * len(dets)))
    vessel = dets[dets.confidence != "low"].reset_index(drop=True)
    vl = np.asarray(lat)[dets.confidence.values != "low"]
    vo = np.asarray(lon)[dets.confidence.values != "low"]
    rows = _rows(vessel, vl, vo, index, extra_cols=("px_x", "px_y"))
    low = dets[dets.confidence == "low"]
    gx, gy = np.meshgrid(np.linspace(0, w, 9), np.linspace(0, h, 9))
    glon, glat = to_ll.transform(tr.c + gx * tr.a, tr.f + gy * tr.e)
    data = {"summary": summary, "img": {"w": w, "h": h, "res_m": abs(tr.a), **img_meta},
            "grid": {"nx": 9, "ny": 9, "w": w, "h": h, "lon": np.round(glon, 6).tolist(), "lat": np.round(glat, 6).tolist()},
            "cols": COLS + ["x", "y"], "rows": rows, "scenes": scenes,
            "low": low[["px_x", "px_y"]].round(1).values.tolist(), "queue": queue}
    if "cnn_score" in vessel:
        data["cnn"] = [None if pd.isna(v) else round(float(v), 3) for v in vessel.cnn_score]
    return data, "data:image/jpeg;base64," + _b64_jpeg(rgb, quality=82), chips


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


def label_check() -> dict | None:
    """How the heuristic classes fared against AI2 expert labels on Sentinel-1A/1B scenes in Southeast Asia.

    Share of candidates within 50 m of a label (vessel), 50 to 150 m (ambiguous), or neither (clutter).
    Source: data/ml/candidates.parquet (scripts/04_build_training_set.py). Labels are incomplete, so the
    vessel share is a lower bound on precision.
    """
    path = DATA_DIR / "ml" / "candidates.parquet"
    if not path.exists():
        return None
    c = pd.read_parquet(path, columns=["confidence", "cand_class", "region", "product_id"])
    c = c[c.region == "sea_asia"]
    t = pd.crosstab(c.confidence, c.cand_class)
    out = {k: {"n": int(t.loc[k].sum()), **{m: round(float(t.loc[k, m]) / float(t.loc[k].sum()), 3) for m in t.columns}}
           for k in t.index}
    out["scenes"] = int(c.product_id.nunique())
    return out


def regional_data(max_chips: int) -> tuple[dict, dict]:
    summary = json.loads((DATA_DIR / "regional_summary.json").read_text())
    cov = json.loads((DATA_DIR / "s1_coverage.json").read_text())
    search = json.loads((DATA_DIR / "s1_search_summary.json").read_text())
    dets = gpd.read_file(DATA_DIR / "detections_regional_all.gpkg", layer="detections_regional_4326",
                         where="confidence <> 'low'")
    proc = gpd.read_file(DATA_DIR / "detections_regional_all.gpkg", layer="scenes_processed_4326")
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

    queue = label_queue(dets, max_chips)
    chips = _chips_for(dets, scene_of, queue + _top_ids(dets))
    vessel = dets[dets.confidence != "low"].reset_index(drop=True)
    scenes_tab, index = _scene_table(dets.det_id, dets.scene_id, dets.acq_utc, dets.mission)
    colz = _cols_b64(vessel, vessel.lat.values, vessel.lon.values, index)
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
        "cols": COLS, "colz": colz, "n": int(len(vessel)), "scenes": scenes_tab,
        "n_low": int(summary["classes"].get("low", 0)),
        "passes": pass_list, "label_check": label_check(), "queue": queue,
        "queue_rule": {"seed": 20261002, "share": QUEUE_SHARE},
    }
    return data, chips


def build_demo(out_html: Path, max_chips_regional: int = 300, max_chips_detail: int = 400) -> Path:
    reg, chips_r = regional_data(max_chips_regional)
    det, det_img, chips_d = camau_data(max_chips_detail)
    data = {"regional": reg, "detail": det, "chips": {**chips_r, **chips_d}, "caveat": DARK_CAVEAT,
            "has_ml": "cnn" in det}
    html = TEMPLATE.read_text()
    leaflet_css = Path(__file__).with_name("leaflet-1.9.4.css").read_text()
    html = (html.replace("/*__LEAFLET_CSS__*/", leaflet_css)
                .replace("__DETAIL_IMG__", det_img)
                .replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":"))))
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html)
    return out_html
