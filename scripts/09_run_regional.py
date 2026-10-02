"""Regional detection over the South China Sea AOI: the most recent N days of scenes.

Each scene is processed block by block over sea inside the AOI (src/darkvessel/regional.py) and
checkpointed to data/cache/regional/. Re-running skips finished scenes. --merge builds products:
  data/detections_regional.gpkg       vessel candidates + fixed structures (lean, committed):
                                      detections_regional_4326 / _utm49n, scenes_processed_*, about
  data/detections_regional_all.gpkg   every object incl. low-confidence clutter (large, gitignored)
  data/outputs/small/vessel_density_regional_4326.tif  vessel candidates per 1,000 km2 tested, 0.25 deg
  data/regional_summary.json, docs/figures/regional_detections.png
Usage: python scripts/09_run_regional.py --days 6 --workers 3   then   --merge
"""

import argparse
import datetime as dt
import json
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely

from darkvessel.aoi import aoi_gdf
from darkvessel.config import CRS_UTM_REGIONAL, DARK_CAVEAT, DATA_DIR, DEFAULT_AOI

CACHE = DATA_DIR / "cache" / "regional"


def _work(args):
    path, pfa = args
    from darkvessel.regional import process_scene

    aoi = aoi_gdf(DEFAULT_AOI).geometry.iloc[0].simplify(0.01)
    shapely.prepare(aoi)
    sid = path.rsplit("/", 1)[-1]
    try:
        det, stats = process_scene(path, aoi, pfa=pfa)
        det.drop(columns="geometry").to_parquet(CACHE / f"{sid}.parquet")
        (CACHE / f"{sid}.json").write_text(json.dumps(stats, default=str))
        return sid, stats.get("tested_km2", 0), len(det), None
    except Exception as e:  # keep going; record the failure
        (CACHE / f"{sid}.error").write_text(repr(e))
        return sid, 0, 0, repr(e)


def run(days: int, workers: int, pfa: float, max_scenes: int | None, min_overlap_km2: float = 2000.0):
    CACHE.mkdir(parents=True, exist_ok=True)
    sc = pd.read_csv(DATA_DIR / "s1_scenes.csv", parse_dates=["start_utc"])
    end = sc.start_utc.max().normalize() + pd.Timedelta(days=1)
    sel = sc[(sc.start_utc >= end - pd.Timedelta(days=days)) & (sc.aoi_overlap_km2 >= min_overlap_km2)]
    sel = sel.sort_values("start_utc", ascending=False)
    todo = [p for p in sel.path if not (CACHE / f"{p.rsplit('/', 1)[-1]}.parquet").exists()]
    if max_scenes:
        todo = todo[:max_scenes]
    print(f"{len(sel)} scenes in the last {days} days with >= {min_overlap_km2:g} km2 AOI overlap, {len(todo)} to process", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(workers) as ex:
        futs = [ex.submit(_work, (p, pfa)) for p in todo]
        for i, f in enumerate(as_completed(futs), 1):
            sid, km2, n, err = f.result()
            print(f"[{time.time() - t0:6.0f}s] {i}/{len(todo)} {sid} {km2:,.0f} km2 {n} objects {err or ''}", flush=True)


def merge(pfa: float, persist_workers: int = 6):
    from darkvessel.io import write_dual_crs
    from darkvessel.regional import recurs
    from darkvessel.s1.grd import GRDScene

    parts = [pd.read_parquet(p) for p in sorted(CACHE.glob("*.parquet"))]
    stats = [json.loads(p.read_text()) for p in sorted(CACHE.glob("*.json"))]
    det = pd.concat([p for p in parts if len(p)], ignore_index=True)
    det = gpd.GeoDataFrame(det, geometry=gpd.points_from_xy(det.lon, det.lat), crs="EPSG:4326")
    print(f"merged {len(det)} objects from {len(parts)} scenes", flush=True)

    # Targeted persistence for vessel candidates: same relative orbit and pass, 1 to 30 days earlier
    fp = gpd.read_file(DATA_DIR / "s1_footprints.gpkg", layer="s1_footprints_4326")
    fp["start_utc"] = pd.to_datetime(fp.start_utc, utc=True)
    det["acq"] = pd.to_datetime(det.acq_utc, utc=True)
    cand = det[det.confidence.isin(["high", "medium"])]
    scenes = {}

    def get_scene(path):
        if path not in scenes:
            scenes[path] = GRDScene(path)
        return scenes[path]

    jobs = []
    for idx, r in cand.iterrows():
        earlier = fp[(fp.orbit_rel == r.orbit_rel) & (fp.pass_dir == r.pass_dir)
                     & (fp.start_utc < r.acq - pd.Timedelta(days=1)) & (fp.start_utc >= r.acq - pd.Timedelta(days=30))]
        earlier = earlier[earlier.geometry.contains(r.geometry)].sort_values("start_utc", ascending=False).head(2)
        jobs.append((idx, list(earlier.path), r.lon, r.lat))
    for path in {p for _, ps, _, _ in jobs for p in ps}:
        get_scene(path).geocoder  # load geocoders up front (annotation XML), single-threaded

    def check(job):
        idx, paths, lon, lat = job
        res = [recurs(get_scene(p), lon, lat) for p in paths]
        res = [x for x in res if x is not None]
        return idx, sum(res), len(res)

    t0 = time.time()
    det["persist_dates"], det["persist_dates_checked"] = 0, 0
    with ThreadPoolExecutor(persist_workers) as ex:
        for i, (idx, hits, n) in enumerate(ex.map(check, jobs), 1):
            det.loc[idx, ["persist_dates", "persist_dates_checked"]] = [hits, n]
            if i % 500 == 0:
                print(f"  persistence {i}/{len(jobs)} [{time.time() - t0:.0f}s]", flush=True)
    fixed = det.confidence.isin(["high", "medium"]) & (det.persist_dates_checked > 0) & (det.persist_dates >= det.persist_dates_checked)
    det.loc[fixed, "confidence"] = "fixed"
    det = det.drop(columns=["acq"])
    det["ais_status"] = "not_checked: no AIS source connected"

    # Full product (all classes, all columns): large, gitignored, regenerate with --merge.
    full = DATA_DIR / "detections_regional_all.gpkg"
    if full.exists():
        full.unlink()
    write_dual_crs(det, full, "detections_regional", utm_crs=CRS_UTM_REGIONAL)
    # Lean committed product: vessel candidates and fixed structures, essential columns.
    out = DATA_DIR / "detections_regional.gpkg"
    if out.exists():
        out.unlink()
    lean_cols = ["det_id", "scene_id", "mission", "acq_utc", "confidence", "lat", "lon", "length_est_m", "scr_vv_db",
                 "scr_vh_db", "inc_angle_deg", "persist_dates", "persist_dates_checked", "ais_status", "caveat", "geometry"]
    lean = det.loc[det.confidence != "low", lean_cols].copy()
    for c in ("lat", "lon"):
        lean[c] = lean[c].round(5)
    for c in ("length_est_m", "scr_vv_db", "scr_vh_db", "inc_angle_deg"):
        lean[c] = lean[c].round(1)
    write_dual_crs(lean, out, "detections_regional", utm_crs=CRS_UTM_REGIONAL)
    st = pd.DataFrame(stats)
    proc = fp[fp.product_id.isin(st.scene_id)].merge(st[["scene_id", "tested_km2", "blocks_processed", "runtime_s"]],
                                                     left_on="product_id", right_on="scene_id")
    counts = det.groupby(["scene_id", "confidence"]).size().unstack(fill_value=0)
    proc = proc.merge(counts, left_on="product_id", right_index=True, how="left").fillna(0)
    for f in (out, full):
        write_dual_crs(proc, f, "scenes_processed", utm_crs=CRS_UTM_REGIONAL)
    about = {"caveat_full": DARK_CAVEAT, "detector": "ca_cfar_v0 regional, block streaming", "pfa": str(pfa),
             "guard_px": "81", "background_px": "161", "land_buffer_m": "1000",
             "confidence_classes": "high = VV and VH; medium = VH only or strong VV only; low = weak VV only or longer "
                                   "than 450 m; fixed = bright return at the same spot on every earlier same-orbit pass checked",
             "persistence": "20 m overview window, contrast >= 7 dB within ~60 m, up to 2 earlier passes 1 to 30 days before",
             "data_credit": "Contains modified Copernicus Sentinel data 2026; ESA WorldCover 2021 v200 (CC BY 4.0); "
                            "Natural Earth (public domain)"}
    for f in (out, full):
        pyogrio.write_dataframe(pd.DataFrame([about]), f, layer="about", driver="GPKG")

    vessels = det[det.confidence.isin(["high", "medium"])]
    summary = {
        "scenes": int(len(st)), "date_range": [str(det.acq_utc.min()), str(det.acq_utc.max())],
        "tested_km2": round(float(st.tested_km2.sum())), "objects": int(len(det)),
        "classes": det.confidence.value_counts().to_dict(), "vessel_candidates": int(len(vessels)),
        "by_mission": vessels.mission.value_counts().to_dict(),
        "candidates_per_1000km2": round(1000 * len(vessels) / max(float(st.tested_km2.sum()), 1), 2),
        "length_est_m_vessels": vessels.length_est_m.describe().round(1).to_dict(),
        "scene_runtime_s_median": float(st.runtime_s.median()), "persistence_checks": len(jobs),
    }
    (DATA_DIR / "regional_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=6)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--pfa", type=float, default=1e-6)
    ap.add_argument("--max-scenes", type=int, default=None)
    ap.add_argument("--merge", action="store_true")
    a = ap.parse_args()
    if a.merge:
        merge(a.pfa)
    else:
        run(a.days, a.workers, a.pfa, a.max_scenes)
