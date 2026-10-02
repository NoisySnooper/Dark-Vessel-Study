"""VIIRS Day/Night Band lights at sea over the South China Sea AOI, night by night (S-NPP, NOAA-20, NOAA-21).

Night passes (about 01:30 local) are found from granule outlines, read from the NOAA JPSS buckets on
AWS, and searched for point lights over open sea (src/darkvessel/viirs). Each granule is checkpointed
in data/cache/viirs/. --merge separates recurring lights from the rest and writes the products:

  data/viirs_lights.gpkg            lights at sea, EPSG:4326 and UTM 49N (layers viirs_lights_*),
                                    plus viirs_granules_* (granule outlines) and about
  data/outputs/small/viirs_lit_density_4326.tif / _utm49n.tif
                                    mean lights per night per 1,000 km2 (persistent lights excluded), 0.25 degree
  data/viirs_summary.json, docs/figures/viirs_lights.png

Classes:
  lit_vessel_candidate  a light that does not recur at the same spot
  persistent_light      lights within 500 m on at least max(3, 30 %) of the nights processed:
                        platforms, gas flares, island and navigation lights, anchorages
A light at sea is not proof of a vessel, and a lit vessel is not a dark vessel: the light is only
evidence that something lit was there at about 01:30 local time.

Usage: python scripts/15_viirs_lights.py --start 2026-09-05 --end 2026-10-01 --workers 3   then   --merge
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
from scipy.spatial import cKDTree
from shapely.geometry import Polygon

from darkvessel.aoi import aoi_gdf, natural_earth_land
from darkvessel.config import CRS_UTM_REGIONAL, DARK_CAVEAT, DARK_CAVEAT_SHORT, DATA_DIR, DEFAULT_AOI

CACHE = DATA_DIR / "cache" / "viirs"
SATS = ("S-NPP", "NOAA-20", "NOAA-21")


def sea_grid():
    from darkvessel.viirs.pipeline import SeaGrid

    path = CACHE / "seagrid.npz"
    aoi = aoi_gdf(DEFAULT_AOI).geometry.iloc[0]
    if path.exists():
        z = np.load(path)
        g = SeaGrid.__new__(SeaGrid)
        g.res, g.west, g.north, g.mask = float(z["res"]), float(z["west"]), float(z["north"]), z["mask"]
        return g
    w, s, e, n = aoi.bounds
    g = SeaGrid(aoi, natural_earth_land(bbox=(w - 1, s - 1, e + 1, n + 1)).geometry)
    np.savez_compressed(path, res=g.res, west=g.west, north=g.north, mask=g.mask)
    return g


def index_night(day: dt.date, sat: str) -> list[str]:
    """Granules of one satellite's night pass(es) over the AOI, cached."""
    from darkvessel.viirs.pipeline import find_aoi_granules

    path = CACHE / "index" / f"{day:%Y%m%d}_{sat}.json"
    if path.exists():
        return json.loads(path.read_text())
    keys = find_aoi_granules(sat, day, aoi_gdf(DEFAULT_AOI).geometry.iloc[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys))
    return keys


_SEA = None


def _work(job):
    global _SEA
    sat, key = job
    from darkvessel.viirs import access
    from darkvessel.viirs.pipeline import detect_granule

    gid = key.rsplit("/", 1)[-1][:40]
    try:
        if _SEA is None:
            _SEA = sea_grid()
        g = access.read_granule(sat, key)
        det = detect_granule(g, _SEA)
        lat, lon, ad = access.gring(sat, key)
        meta = {"granule": gid, "satellite": sat, "start_utc": g.start.isoformat(), "moon_illum_pct": round(g.moon_illum, 1),
                "lunar_zenith_deg": round(g.lunar_zenith_median, 1), "cloud_mask": g.cloud is not None,
                "ring_lat": [round(float(x), 4) for x in lat], "ring_lon": [round(float(x), 4) for x in lon],
                "n_lights": int(len(det))}
        det.to_parquet(CACHE / f"{gid}.parquet")
        (CACHE / f"{gid}.json").write_text(json.dumps(meta))
        return gid, len(det), None
    except Exception as e:  # keep going; record the failure
        (CACHE / f"{gid}.error").write_text(repr(e))
        return gid, 0, repr(e)


def run(start: dt.date, end: dt.date, workers: int):
    CACHE.mkdir(parents=True, exist_ok=True)
    sea_grid()
    pairs = [(start + dt.timedelta(days=i), sat) for i in range((end - start).days + 1) for sat in SATS]

    def index(pair):
        try:
            return pair, index_night(*pair), None
        except Exception as e:  # noqa: BLE001
            return pair, [], repr(e)

    jobs = []
    with ThreadPoolExecutor(4) as ex:  # granule search is metadata reads: threads, not processes
        for (day, sat), keys, err in ex.map(index, pairs):
            if err:
                print(f"index {day} {sat} failed: {err}", flush=True)
            jobs += [(sat, k) for k in keys if not (CACHE / f"{k.rsplit('/', 1)[-1][:40]}.parquet").exists()]
    print(f"{len(pairs)} satellite-nights indexed, {len(jobs)} granules to process", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(workers) as ex:
        futs = [ex.submit(_work, j) for j in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            gid, n, err = f.result()
            if err or i % 20 == 0:
                print(f"[{time.time() - t0:6.0f}s] {i}/{len(jobs)} {gid} {n} lights {err or ''}", flush=True)


def night_of(t: pd.Series) -> pd.Series:
    """Local calendar night: the UTC date of a pass near 18:00 UTC is the evening date in Vietnam (UTC+7)."""
    return (t + pd.Timedelta(hours=7) - pd.Timedelta(hours=12)).dt.date


def merge(persist_frac: float = 0.3, persist_radius_m: float = 500.0, res: float = 0.25):
    from darkvessel.coverage import cell_area_km2, grid_for
    from darkvessel.io import write_dual_crs
    from darkvessel.s1.export import write_cog
    from rasterio import features
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    metas = [json.loads(p.read_text()) for p in sorted(CACHE.glob("*.json"))]
    parts = [pd.read_parquet(CACHE / f"{m['granule']}.parquet") for m in metas]
    det = pd.concat([p for p in parts if len(p)], ignore_index=True)
    det["time_utc"] = pd.to_datetime(det.time_utc, utc=True)
    det["night"] = night_of(det.time_utc)
    nights = sorted(det.night.unique())
    print(f"{len(det)} lights from {len(metas)} granules, {len(nights)} nights", flush=True)

    # Recurring lights: distinct nights with a light within persist_radius_m of each light
    lat0 = np.radians(float(det.lat.median()))
    xy = np.c_[np.radians(det.lon) * 6371008.8 * np.cos(lat0), np.radians(det.lat) * 6371008.8]
    tree = cKDTree(xy)
    night_code = pd.factorize(det.night)[0]
    nb = tree.query_ball_point(xy, persist_radius_m)
    det["nights_seen_500m"] = [len(set(night_code[j])) for j in nb]
    need = max(3, int(np.ceil(persist_frac * len(nights))))
    det["class"] = np.where(det.nights_seen_500m >= need, "persistent_light", "lit_vessel_candidate")

    # Distance to Satlas offshore infrastructure (platforms, turbines), if reachable
    try:
        sat = gpd.read_file("https://storage.googleapis.com/satlas-explorer-public/outputs/marine/latest.geojson")
        sxy = np.c_[np.radians(sat.geometry.x) * 6371008.8 * np.cos(lat0), np.radians(sat.geometry.y) * 6371008.8]
        det["satlas_infra_m"] = np.round(cKDTree(sxy).query(xy)[0], 0)
    except Exception as e:  # noqa: BLE001
        print("Satlas points not read:", repr(e)[:120], flush=True)
        det["satlas_infra_m"] = np.nan
    det["caveat"] = DARK_CAVEAT_SHORT + " A light at sea is not proof of a vessel."
    det = det.drop(columns=["row", "col"]).sort_values("time_utc").reset_index(drop=True)
    det.insert(0, "light_id", [f"{s[:1]}{s[-2:]}_{t:%Y%m%dT%H%M%S}_{i:06d}" for i, (s, t) in enumerate(zip(det.satellite, det.time_utc))])
    for c in ("radiance_nw", "background_nw", "spike_nw", "snr", "isolation", "neighbour_share"):
        det[c] = det[c].astype(float).round(2)
    det["lat"], det["lon"] = det.lat.round(5), det.lon.round(5)
    det["time_utc"] = det.time_utc.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    det["night"] = det.night.astype(str)
    g = gpd.GeoDataFrame(det, geometry=gpd.points_from_xy(det.lon, det.lat), crs="EPSG:4326")

    out = DATA_DIR / "viirs_lights.gpkg"
    if out.exists():
        out.unlink()
    write_dual_crs(g, out, "viirs_lights", utm_crs=CRS_UTM_REGIONAL, spatial_index=False)
    gran = gpd.GeoDataFrame(
        [{k: m[k] for k in ("granule", "satellite", "start_utc", "moon_illum_pct", "lunar_zenith_deg", "n_lights")} for m in metas],
        geometry=[Polygon(zip(m["ring_lon"], m["ring_lat"])) for m in metas], crs="EPSG:4326")
    write_dual_crs(gran, out, "viirs_granules", utm_crs=CRS_UTM_REGIONAL)
    about = {"caveat_full": DARK_CAVEAT + " A VIIRS light at sea is not proof of a vessel: platforms, flares and island "
             "lights also shine, and a lit vessel is not a dark vessel.",
             "detector": "spike detector after Elvidge et al. 2015 (src/darkvessel/viirs/detect.py): 7 x 7 median background, "
                         "local noise 15 x 15, spike >= 5 x noise and >= 1.5 nW cm-2 sr-1, peak >= 2 x background, isolation >= 2",
             "cloud_screening": "VIIRS JRR cloud mask: clear or probably clear kept; under cloud kept only if spike >= 5 nW and isolation >= 8",
             "persistent_light": f"lights within {persist_radius_m:.0f} m on at least {need} of {len(nights)} nights",
             "data_credit": "VIIRS DNB SDR, DNB GEO and JRR Cloud Mask from NOAA JPSS on the AWS Open Data Registry "
                            "(noaa-nesdis-snpp-pds, noaa-nesdis-n20-pds, noaa-nesdis-n21-pds); Natural Earth (public domain); "
                            "Satlas marine infrastructure (AI2, ODC-BY)"}
    pyogrio.write_dataframe(pd.DataFrame([about]), out, layer="about", driver="GPKG")

    # Mean lights per night per 1,000 km2 of observed sea (persistent lights excluded), clear-sky lights only
    aoi = aoi_gdf(DEFAULT_AOI).geometry.iloc[0]
    tr, shape = grid_for(aoi.bounds, res)
    looks = np.zeros(shape, np.float64)
    for m in metas:
        looks += features.rasterize([(Polygon(zip(m["ring_lon"], m["ring_lat"])), 1)], out_shape=shape, transform=tr, dtype="uint8")
    area = cell_area_km2(tr, shape)
    aoi_mask = features.rasterize([(aoi, 1)], out_shape=shape, transform=tr, dtype="uint8").astype(bool)
    v = det[(det["class"] == "lit_vessel_candidate") & (det.quality == "clear")]
    rr = np.floor((v.lat.to_numpy() - tr.f) / tr.e).astype(int)
    cc = np.floor((v.lon.to_numpy() - tr.c) / tr.a).astype(int)
    okk = (rr >= 0) & (rr < shape[0]) & (cc >= 0) & (cc < shape[1])
    counts = np.zeros(shape)
    np.add.at(counts, (rr[okk], cc[okk]), 1)
    dens = np.full(shape, -1.0, np.float32)
    valid = aoi_mask & (looks > 0)
    dens[valid] = 1000 * counts[valid] / (looks[valid] * area[valid])
    tags = {"units": "clear-sky lit vessel candidates per 1,000 km2 per satellite pass (persistent lights excluded)",
            "nodata": "-1 = outside AOI or not observed", "source": "scripts/15_viirs_lights.py", "caveat": DARK_CAVEAT_SHORT}
    small = DATA_DIR / "outputs" / "small"
    write_cog(dens, tr, "EPSG:4326", small / "viirs_lit_density_4326.tif", nodata=-1, tags=tags)
    t2, w2, h2 = calculate_default_transform("EPSG:4326", CRS_UTM_REGIONAL, shape[1], shape[0], *aoi.bounds, resolution=25000)
    dst = np.full((h2, w2), -1, np.float32)
    reproject(dens, dst, src_transform=tr, src_crs="EPSG:4326", dst_transform=t2, dst_crs=CRS_UTM_REGIONAL,
              resampling=Resampling.nearest, src_nodata=-1, dst_nodata=-1)
    write_cog(dst, t2, CRS_UTM_REGIONAL, small / "viirs_lit_density_utm49n.tif", nodata=-1, tags=tags)

    per_night = (det.groupby(["night", "satellite"]).agg(lights=("light_id", "size"), clear=("quality", lambda q: int((q == "clear").sum())),
                                                          moon=("moon_illum_pct", "median")).reset_index())
    summary = {"nights": [str(n) for n in nights], "granules": len(metas), "lights": int(len(det)),
               "classes": det["class"].value_counts().to_dict(), "quality": det.quality.value_counts().to_dict(),
               "persistent_rule_nights": need, "sharp_single_pixel": int(det.sharp.sum()),
               "near_satlas_1km": int((det.satlas_infra_m <= 1000).sum()) if det.satlas_infra_m.notna().any() else None,
               "persistent_near_satlas_1km_share": round(float((det[det["class"] == "persistent_light"].satlas_infra_m <= 1000).mean()), 3)
               if det.satlas_infra_m.notna().any() else None,
               "per_night": per_night.to_dict(orient="records")}
    (DATA_DIR / "viirs_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: v for k, v in summary.items() if k != "per_night"}, indent=1, default=str))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2026, 9, 5))
    ap.add_argument("--end", type=dt.date.fromisoformat, default=dt.date(2026, 10, 1))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--merge", action="store_true")
    a = ap.parse_args()
    if a.merge:
        merge()
    else:
        run(a.start, a.end, a.workers)
