"""Build the self-contained HTML demo page from pipeline outputs.

Inputs: data/detections_baseline.gpkg, data/baseline_run_summary.json, data/s1_scenes.csv,
data/s1_footprints.gpkg, data/outputs/small/sigma0_vv_db_utm48n_40m_u8.tif, and optionally
data/detections_ml.gpkg (CNN scores). Imagery chips are re-read from the AWS mirror.
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
from shapely.geometry import box

from darkvessel.config import AOIS, CRS_UTM, DARK_CAVEAT, DATA_DIR, DEFAULT_AOI
from darkvessel.landmask import sea_mask_on_grid
from darkvessel.s1.grd import GRDScene

TEMPLATE = Path(__file__).with_name("demo_template.html")


def _b64_png(arr_u8: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(arr_u8).save(buf, format="PNG", optimize=True)
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
    return rgb.astype(np.uint8), tr, meta, (lon, lat)


def chip_png(scene: GRDScene, row: float, col: float, half: int = 24) -> str:
    win = Window(int(col) - half, int(row) - half, 2 * half, 2 * half)
    pair = []
    for pol in ("VV", "VH"):
        db = 10 * np.log10(scene.read_sigma0(pol, win, denoise=False))
        lo, hi = np.nanpercentile(db, 2), np.nanmax(db)
        pair.append(np.clip((db - lo) / max(hi - lo, 1e-3), 0, 1))
    gap = np.ones((2 * half, 2))
    img = (np.hstack([pair[0], gap, pair[1]]) * 255).astype(np.uint8)
    return _b64_png(img)


def locator_svg(footprints: gpd.GeoDataFrame, window_poly, demo_id: str, land_geojson: Path,
                extent=(102.6, 6.6, 108.4, 11.6), width: int = 520) -> str:
    """Equirectangular locator: land, AOI, scene footprints, processing window."""
    w0, s0, e0, n0 = extent
    k = np.cos(np.radians((s0 + n0) / 2))
    height = int(width * (n0 - s0) / ((e0 - w0) * k))

    def px(x, y):
        return (x - w0) / (e0 - w0) * width, (n0 - y) / (n0 - s0) * height

    def path(geom, tol=0.01):
        geom = geom.intersection(box(*extent)).simplify(tol)
        polys = getattr(geom, "geoms", [geom])
        out = []
        for p in polys:
            if p.is_empty or p.geom_type != "Polygon":
                continue
            for ring in [p.exterior, *p.interiors]:
                pts = [px(x, y) for x, y in ring.coords]
                out.append("M" + "L".join(f"{a:.1f},{b:.1f}" for a, b in pts) + "Z")
        return "".join(out)

    land = gpd.read_file(land_geojson, bbox=extent)
    land_d = "".join(path(g) for g in land.geometry)
    fps = []
    for r in footprints.itertuples():
        cls = "fp fp-demo" if r.product_id == demo_id else "fp"
        fps.append(f'<path class="{cls}" d="{path(r.geometry, 0.02)}"><title>{r.product_id}</title></path>')
    aw, as_, ae, an = AOIS[DEFAULT_AOI]["bbox"]
    (ax0, ay0), (ax1, ay1) = px(aw, an), px(ae, as_)
    ticks = []
    for lo in range(int(np.ceil(w0)), int(e0) + 1):
        x, _ = px(lo, s0)
        ticks.append(f'<line class="grat" x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height}"/>'
                     f'<text class="gl" x="{x + 3:.1f}" y="{height - 4}">{lo}°E</text>')
    for la in range(int(np.ceil(s0)), int(n0) + 1):
        _, y = px(w0, la)
        ticks.append(f'<line class="grat" x1="0" y1="{y:.1f}" x2="{width}" y2="{y:.1f}"/>'
                     f'<text class="gl" x="4" y="{y - 3:.1f}">{la}°N</text>')
    win_d = path(window_poly, 0.002)
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Locator map: AOI, Sentinel-1D footprints and the processed window">'
        f'<rect class="water" x="0" y="0" width="{width}" height="{height}"/>{"".join(ticks)}'
        f'<path class="land" d="{land_d}"/>{"".join(fps)}'
        f'<rect class="aoi" x="{ax0:.1f}" y="{ay0:.1f}" width="{ax1 - ax0:.1f}" height="{ay1 - ay0:.1f}"/>'
        f'<path class="win" d="{win_d}"/>'
        f'<text class="lbl" x="{ax0 + 6:.1f}" y="{ay0 + 16:.1f}">AOI</text></svg>'
    )


def build_demo(out_html: Path, land_geojson: Path, max_chips: int = 1500) -> Path:
    summary = json.loads((DATA_DIR / "baseline_run_summary.json").read_text())
    dets = gpd.read_file(DATA_DIR / "detections_baseline.gpkg", layer="detections_baseline_utm48n")
    ml_path = DATA_DIR / "detections_ml.gpkg"
    has_ml = ml_path.exists()
    if has_ml:
        ml = gpd.read_file(ml_path, layer="detections_verified_utm48n")[["det_id", "cnn_score", "cnn_vessel"]]
        dets = dets.merge(ml, on="det_id", how="left")
    rgb, tr, img_meta, _ = render_map_image(DATA_DIR / "outputs" / "small" / "sigma0_vv_db_utm48n_40m_u8.tif")
    h, w = rgb.shape[:2]

    import mgrs

    m = mgrs.MGRS()
    to_ll = Transformer.from_crs(CRS_UTM, "EPSG:4326", always_xy=True)
    lon, lat = to_ll.transform(dets.geometry.x.values, dets.geometry.y.values)
    dets["px"] = (dets.geometry.x - tr.c) / tr.a
    dets["py"] = (dets.geometry.y - tr.f) / tr.e
    scene = GRDScene(summary.get("scene_path") or _scene_path(summary["scene_id"]))
    keep = dets[dets.confidence != "low"].copy()
    keep["score"] = keep[["scr_vv_db", "scr_vh_db"]].max(axis=1)
    keep = keep.sort_values("score", ascending=False).head(max_chips)
    with ThreadPoolExecutor(8) as ex:
        chips = dict(zip(keep.det_id, ex.map(lambda r: chip_png(scene, r[0], r[1]), zip(keep.row, keep.col))))

    def rec(i, r):
        d = {
            "id": r.det_id, "c": r.confidence, "x": round(r.px, 1), "y": round(r.py, 1),
            "lat": round(float(lat[i]), 5), "lon": round(float(lon[i]), 5),
            "dms": f"{_dms(lat[i], 'N', 'S')} {_dms(lon[i], 'E', 'W')}",
            "mgrs": m.toMGRS(float(lat[i]), float(lon[i]), MGRSPrecision=4),
            "len": round(float(r.length_est_m)), "px_n": int(r.n_pixels),
            "vv": None if pd.isna(r.peak_vv_db) else round(float(r.peak_vv_db), 1),
            "vh": None if pd.isna(r.peak_vh_db) else round(float(r.peak_vh_db), 1),
            "svv": None if pd.isna(r.scr_vv_db) else round(float(r.scr_vv_db), 1),
            "svh": None if pd.isna(r.scr_vh_db) else round(float(r.scr_vh_db), 1),
            "inc": round(float(r.inc_angle_deg), 1), "per": int(r.persist_dates),
            "perN": int(r.persist_dates_checked),
        }
        if has_ml and not pd.isna(r.get("cnn_score", np.nan)):
            d["cnn"] = round(float(r.cnn_score), 3)
        return d

    full = [rec(i, r) for i, r in enumerate(dets.itertuples()) if r.confidence != "low"]
    low = [[round(r.px, 1), round(r.py, 1)] for r in dets.itertuples() if r.confidence == "low"]

    scenes = pd.read_csv(DATA_DIR / "s1_scenes.csv", parse_dates=["start_utc"]).sort_values("start_utc")
    scen = [{"t": s.start_utc.strftime("%Y-%m-%dT%H:%M"), "pass": s.pass_dir, "orbit": int(s.orbit_rel),
             "cov": round(float(s.aoi_coverage) * 100, 1), "id": s.product_id,
             "demo": s.product_id == summary["scene_id"]} for s in scenes.itertuples()]
    fps = gpd.read_file(DATA_DIR / "s1_footprints.gpkg", layer="s1_footprints_4326")
    win = gpd.read_file(DATA_DIR / "detections_baseline.gpkg", layer="processing_window_4326").geometry.iloc[0]
    svg = locator_svg(fps, win, summary["scene_id"], land_geojson)

    gx = np.linspace(0, w, 9)
    gy = np.linspace(0, h, 9)
    GX, GY = np.meshgrid(gx, gy)
    glon, glat = to_ll.transform(tr.c + GX * tr.a, tr.f + GY * tr.e)
    search = json.loads((DATA_DIR / "s1_search_summary.json").read_text())
    data = {
        "summary": summary, "search_window": [s.strip() for s in search["window"].split(" to ")],
        "img": {"w": w, "h": h, "res_m": abs(tr.a), **img_meta},
        "grid": {"nx": 9, "ny": 9, "w": w, "h": h, "lon": np.round(glon, 6).tolist(), "lat": np.round(glat, 6).tolist()},
        "dets": full, "low": low, "chips": chips, "scenes": scen, "has_ml": has_ml, "caveat": DARK_CAVEAT,
    }
    html = TEMPLATE.read_text()
    leaflet_css = (Path(__file__).with_name("leaflet-1.9.4.css")).read_text()
    html = (html.replace("/*__LEAFLET_CSS__*/", leaflet_css)
                .replace("__MAP_IMG__", "data:image/jpeg;base64," + _b64_jpeg(rgb))
                .replace("__LOCATOR_SVG__", svg)
                .replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":"))))
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html)
    return out_html


def _scene_path(product_id: str) -> str:
    from darkvessel.s1.aws import parse_product_id

    p = parse_product_id(product_id)
    t = p["start"]
    return f"GRD/{t.year}/{t.month}/{t.day}/{p['mode']}/{p['pol']}/{product_id}"
