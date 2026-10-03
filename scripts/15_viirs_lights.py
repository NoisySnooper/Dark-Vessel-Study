"""VIIRS Day/Night Band lights at sea over the South China Sea AOI, night by night (S-NPP, NOAA-20, NOAA-21).

Night passes (about 01:30 local) are found from granule outlines, read from the NOAA JPSS buckets on
AWS, and searched for point lights over open sea (src/darkvessel/viirs). Each granule is checkpointed
in data/cache/viirs/. --merge separates recurring lights from the rest and writes the products:

  data/viirs_lights_all.gpkg        every light at sea, EPSG:4326 and UTM 49N (viirs_lights_*), granule outlines
                                    (viirs_granules_*) and about. Large: gitignored, rebuilt by --merge from the cache.
  data/viirs_lights.gpkg            committed lean file: recurring-light sites (viirs_sites_*), the lit vessel
                                    candidates of the darkest nights up to LEAN_MAX_LIGHTS (viirs_lights_*, fewer
                                    fields), granule outlines, a per-night table (viirs_nights) and about
  data/outputs/small/viirs_lit_density_4326.tif / _utm49n.tif
                                    mean lights per night per 1,000 km2 (persistent lights excluded), 0.25 degree
  data/viirs_summary.json, docs/figures/viirs_lights.png

Two checks against the radar products: the share of lights where Sentinel-1 took no image in 90 days
(column s1_passes_90d, from data/outputs/small/s1_passes_4326.tif), and the rank correlation of the
light density with the radar vessel density on the same 0.25 degree grid (scripts/10_regional_density.py).

Classes:
  lit_vessel_candidate  a light that does not recur at the same spot
  persistent_light      lights within 500 m on at least max(3, 30 %) of the nights processed:
                        platforms, gas flares, island and navigation lights, anchorages
A light at sea is not proof of a vessel, and a lit vessel is not a dark vessel: the light is only
evidence that something lit was there at about 01:30 local time.

Usage: python scripts/15_viirs_lights.py --start 2026-09-05 --end 2026-10-01 --workers 3   then   --merge
       python scripts/15_viirs_lights.py --retry   (rerun granules that failed, then --merge)
       python scripts/15_viirs_lights.py --clear   (clear-sky sea per 0.25 degree cell and granule, from the cloud
                                                    masks; lets --merge normalise by clear sea, not passes)
"""

import argparse
import datetime as dt
import json
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
from scipy.spatial import cKDTree
from shapely.geometry import Polygon

from darkvessel.aoi import aoi_gdf, natural_earth_land
from darkvessel.config import CRS_UTM_REGIONAL, DARK_CAVEAT, DARK_CAVEAT_SHORT, DATA_DIR, DEFAULT_AOI

FIG_DIR = Path(__file__).resolve().parents[1] / "docs" / "figures"
LEAN_MAX_LIGHTS = 50_000  # keeps the committed GeoPackage near 30 MB (two CRS layers)
LEAN_COLS = ["light_id", "satellite", "time_utc", "night", "lat", "lon", "radiance_nw", "spike_nw", "isolation", "quality",
             "class", "nights_seen_500m", "clear_nights_cell", "moon_illum_pct", "satlas_infra_m", "s1_passes_90d", "caveat", "geometry"]

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
    with ThreadPoolExecutor(3) as ex:  # granule search is metadata reads: threads, not processes
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


GRID_RES = 0.25


def _grid():
    from darkvessel.coverage import grid_for

    return grid_for(aoi_gdf(DEFAULT_AOI).geometry.iloc[0].bounds, GRID_RES)


def _clear_work(job):
    """Sea and clear-sky pixel counts per 0.25 degree cell for one granule's cloud mask (cached as .clear.npz)."""
    global _SEA
    sat, key = job
    import h5py

    from darkvessel.viirs import access

    gid = key.rsplit("/", 1)[-1][:40]
    out = CACHE / f"{gid}.clear.npz"
    try:
        if _SEA is None:
            _SEA = sea_grid()
        ck = access.matching_key(sat, key, "cloud")
        if ck is None:
            return gid, "no_mask"
        with access._open(sat, ck, 4 * 1024 * 1024) as f, h5py.File(f, "r") as h:
            lat, lon, cm = h["Latitude"][:], h["Longitude"][:], h["CloudMask"][:]
        tr, shape = _grid()
        ok = (lat > -90) & _SEA.lookup(lon, lat)
        r = np.floor((lat[ok] - tr.f) / tr.e).astype(np.int64)
        c = np.floor((lon[ok] - tr.c) / tr.a).astype(np.int64)
        inside = (r >= 0) & (r < shape[0]) & (c >= 0) & (c < shape[1])
        flat = r[inside] * shape[1] + c[inside]
        sea_n = np.bincount(flat, minlength=shape[0] * shape[1])
        clear_n = np.bincount(flat, weights=np.isin(cm[ok][inside], (0, 1)).astype(float), minlength=shape[0] * shape[1])
        cells = np.nonzero(sea_n)[0]
        np.savez_compressed(out, cells=cells.astype(np.int32), sea_n=sea_n[cells].astype(np.int32), clear_n=clear_n[cells].astype(np.int32))
        return gid, "ok"
    except Exception as e:  # noqa: BLE001
        return gid, repr(e)[:120]


def clear_sky(workers: int):
    """Clear-sky sea counts for every processed granule that has none yet."""
    keys = {}
    for p in sorted((CACHE / "index").glob("*.json")):
        sat = p.stem.split("_", 1)[1]
        for k in json.loads(p.read_text()):
            keys[k.rsplit("/", 1)[-1][:40]] = (sat, k)
    done = [m.stem for m in CACHE.glob("*.json")]
    jobs = [keys[g] for g in done if g in keys and not (CACHE / f"{g}.clear.npz").exists()]
    print(f"{len(jobs)} granules without clear-sky counts", flush=True)
    bad = 0
    with ProcessPoolExecutor(workers) as ex:
        for i, (gid, status) in enumerate(ex.map(_clear_work, jobs), 1):
            bad += status != "ok"
            if status != "ok" or i % 50 == 0:
                print(f"{i}/{len(jobs)} {gid} {status}", flush=True)
    print(f"done, {bad} without counts", flush=True)


def retry(workers: int):
    """Rerun granules that left an .error file (transient network failures), keep the rest."""
    errs = sorted(CACHE.glob("*.error"))
    keys = {}
    for p in sorted((CACHE / "index").glob("*.json")):
        sat = p.stem.split("_", 1)[1]
        for k in json.loads(p.read_text()):
            keys[k.rsplit("/", 1)[-1][:40]] = (sat, k)
    jobs = [keys[e.stem] for e in errs if e.stem in keys]
    print(f"{len(errs)} failed granules, {len(jobs)} found in the index", flush=True)
    for e in errs:
        e.unlink()
    with ProcessPoolExecutor(workers) as ex:
        for gid, n, err in ex.map(_work, jobs):
            print(f"{gid} {n} lights {err or ''}", flush=True)


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
    # Cloud-aware rule when the clear-sky counts exist (--clear): a light must recur on persist_frac of the nights on
    # which its 0.25 degree cell was mostly clear (at least half the searched sea), and on at least 3 nights.
    # A platform under a cloudy patch is then not mistaken for a boat because it was hidden on most nights.
    tr25, shape25 = _grid()
    clear_paths = [CACHE / f"{m['granule']}.clear.npz" for m in metas]
    cloud_aware = bool(metas) and float(np.mean([pth.exists() for pth in clear_paths])) >= 0.9
    if cloud_aware:
        g_night = night_of(pd.to_datetime(pd.Series([m["start_utc"] for m in metas]), utc=True)).astype(str).tolist()
        per_night_clear = {}
        for gn, pth in zip(g_night, clear_paths):
            if pth.exists():
                z = np.load(pth)
                arr = per_night_clear.setdefault(gn, np.zeros(shape25[0] * shape25[1]))
                np.maximum.at(arr, z["cells"], z["clear_n"] / np.maximum(z["sea_n"], 1))
        clear_nights = np.sum([a >= 0.5 for a in per_night_clear.values()], axis=0)
        r = np.clip(np.floor((det.lat.to_numpy() - tr25.f) / tr25.e).astype(int), 0, shape25[0] - 1)
        c = np.clip(np.floor((det.lon.to_numpy() - tr25.c) / tr25.a).astype(int), 0, shape25[1] - 1)
        det["clear_nights_cell"] = clear_nights[r * shape25[1] + c].astype(int)
        need_i = np.maximum(3, np.ceil(persist_frac * det.clear_nights_cell)).astype(int)
    else:
        det["clear_nights_cell"] = -1
        need_i = np.full(len(det), need)
    det["class"] = np.where(det.nights_seen_500m >= need_i, "persistent_light", "lit_vessel_candidate")

    # Distance to Satlas offshore infrastructure (platforms, turbines), if reachable
    try:
        from darkvessel import satlas

        sat = satlas.points()
        sxy = np.c_[np.radians(sat.geometry.x) * 6371008.8 * np.cos(lat0), np.radians(sat.geometry.y) * 6371008.8]
        det["satlas_infra_m"] = np.round(cKDTree(sxy).query(xy)[0], 0)
    except Exception as e:  # noqa: BLE001
        print("Satlas points not read:", repr(e)[:120], flush=True)
        det["satlas_infra_m"] = np.nan
    # Sentinel-1 passes in 90 days at each light (0 = the radar never imaged that spot; -1 = outside the AOI grid)
    import rasterio

    with rasterio.open(DATA_DIR / "outputs" / "small" / "s1_passes_4326.tif") as ds:
        passes, ptr = ds.read(1), ds.transform
    pr = np.floor((det.lat.to_numpy() - ptr.f) / ptr.e).astype(int)
    pc = np.floor((det.lon.to_numpy() - ptr.c) / ptr.a).astype(int)
    inside = (pr >= 0) & (pr < passes.shape[0]) & (pc >= 0) & (pc < passes.shape[1])
    s1 = np.full(len(det), -1, int)
    s1[inside] = passes[pr[inside], pc[inside]]
    det["s1_passes_90d"] = np.where(s1 == 65535, -1, s1)
    det["caveat"] = DARK_CAVEAT_SHORT + " A light at sea is not proof of a vessel."
    det = det.drop(columns=["row", "col"]).sort_values("time_utc").reset_index(drop=True)
    det.insert(0, "light_id", [f"{s[:1]}{s[-2:]}_{t:%Y%m%dT%H%M%S}_{i:06d}" for i, (s, t) in enumerate(zip(det.satellite, det.time_utc))])
    for c in ("radiance_nw", "background_nw", "spike_nw", "snr", "isolation", "neighbour_share"):
        det[c] = det[c].astype(float).round(2)
    det["lat"], det["lon"] = det.lat.round(5), det.lon.round(5)
    det["time_utc"] = det.time_utc.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    det["night"] = det.night.astype(str)
    g = gpd.GeoDataFrame(det, geometry=gpd.points_from_xy(det.lon, det.lat), crs="EPSG:4326")

    full, lean = DATA_DIR / "viirs_lights_all.gpkg", DATA_DIR / "viirs_lights.gpkg"
    for p in (full, lean):
        if p.exists():
            p.unlink()
    write_dual_crs(g, full, "viirs_lights", utm_crs=CRS_UTM_REGIONAL, spatial_index=False)
    gran = gpd.GeoDataFrame(
        [{k: m[k] for k in ("granule", "satellite", "start_utc", "moon_illum_pct", "lunar_zenith_deg", "n_lights")} for m in metas],
        geometry=[Polygon(zip(m["ring_lon"], m["ring_lat"])) for m in metas], crs="EPSG:4326")
    write_dual_crs(gran, full, "viirs_granules", utm_crs=CRS_UTM_REGIONAL)
    about = {"caveat_full": DARK_CAVEAT + " A VIIRS light at sea is not proof of a vessel: platforms, flares and island "
             "lights also shine, and a lit vessel is not a dark vessel.",
             "detector": "spike detector after Elvidge et al. 2015 (src/darkvessel/viirs/detect.py): 7 x 7 median background, "
                         "local noise 15 x 15, spike >= 5 x noise and >= 1.5 nW cm-2 sr-1, peak >= 2 x background, isolation >= 2",
             "cloud_screening": "VIIRS JRR cloud mask: clear or probably clear kept; under cloud kept only if spike >= 5 nW and isolation >= 8",
             "persistent_light": (f"lights within {persist_radius_m:.0f} m on at least {persist_frac:.0%} of the nights on which the "
                                  "0.25 degree cell was mostly clear (column clear_nights_cell), and on at least 3 nights"
                                  if cloud_aware else f"lights within {persist_radius_m:.0f} m on at least {need} of {len(nights)} nights"),
             "data_credit": "VIIRS DNB SDR, DNB GEO and JRR Cloud Mask from NOAA JPSS on the AWS Open Data Registry "
                            "(noaa-nesdis-snpp-pds, noaa-nesdis-n20-pds, noaa-nesdis-n21-pds); Natural Earth (public domain); "
                            "Satlas marine infrastructure (AI2, ODC-BY)"}
    pyogrio.write_dataframe(pd.DataFrame([about]), full, layer="about", driver="GPKG")

    # Lean file. Nights: the showcase night (most clear-sky lit candidates among nights with the moon at most
    # 30 % lit), then the others from the darkest moon up, while the total stays under LEAN_MAX_LIGHTS.
    lit = det["class"] == "lit_vessel_candidate"
    nightly = det.assign(clear_lit=lit & (det.quality == "clear")).groupby("night").agg(
        moon=("moon_illum_pct", "median"), clear_lit=("clear_lit", "sum"), lit=("class", lambda c: int((c == "lit_vessel_candidate").sum())))
    dark = nightly[nightly.moon <= 30]
    showcase = (dark if len(dark) else nightly).clear_lit.idxmax()
    keep, total = [], 0
    for n in [showcase] + [n for n in nightly.sort_values("moon").index if n != showcase]:
        if keep and total + nightly.loc[n, "lit"] > LEAN_MAX_LIGHTS:
            break
        keep.append(n)
        total += int(nightly.loc[n, "lit"])
    write_dual_crs(g.loc[lit & det.night.isin(keep), LEAN_COLS], lean, "viirs_lights", utm_crs=CRS_UTM_REGIONAL, spatial_index=False)
    from darkvessel.viirs.pipeline import light_sites

    sites = light_sites(det[det["class"] == "persistent_light"], link_m=persist_radius_m)
    if len(sites):
        sites.insert(0, "site_id", [f"VS{i:05d}" for i in range(len(sites))])
        sites = sites.drop(columns="site")
        sr = np.floor((sites.lat.to_numpy() - ptr.f) / ptr.e).astype(int)
        sc = np.floor((sites.lon.to_numpy() - ptr.c) / ptr.a).astype(int)
        ins = (sr >= 0) & (sr < passes.shape[0]) & (sc >= 0) & (sc < passes.shape[1])
        sp = np.full(len(sites), -1, int)
        sp[ins] = passes[sr[ins], sc[ins]]
        sites["s1_passes_90d"] = np.where(sp == 65535, -1, sp)
        sites["likely"] = np.where(sites.satlas_infra_m <= 1000, "platform or turbine (Satlas point within 1 km)",
                                   "other recurring light: platform, flare, island or navigation light, or anchorage")
        sites[["lat", "lon"]] = sites[["lat", "lon"]].round(5)
        sites["radiance_med_nw"], sites["radiance_max_nw"] = sites.radiance_med_nw.round(2), sites.radiance_max_nw.round(2)
        sites["caveat"] = DARK_CAVEAT_SHORT + " A recurring light is most likely a structure, not a vessel."
        write_dual_crs(gpd.GeoDataFrame(sites, geometry=gpd.points_from_xy(sites.lon, sites.lat), crs="EPSG:4326"),
                       lean, "viirs_sites", utm_crs=CRS_UTM_REGIONAL)
    write_dual_crs(gran, lean, "viirs_granules", utm_crs=CRS_UTM_REGIONAL)
    nights_tab = nightly.reset_index().rename(columns={"moon": "moon_illum_pct_median", "lit": "lit_candidates",
                                                       "clear_lit": "lit_candidates_clear"})
    nights_tab["night"] = nights_tab.night.astype(str)
    nights_tab["in_lean_file"] = nights_tab.night.isin([str(n) for n in keep])
    pyogrio.write_dataframe(nights_tab, lean, layer="viirs_nights", driver="GPKG")
    about_lean = dict(about, lean_subset=f"viirs_lights_*: lit vessel candidates of {len(keep)} of {len(nights)} nights (darkest moon "
                      f"first, showcase night {showcase}), {total:,} lights with fewer fields; viirs_sites_*: {len(sites):,} recurring-light "
                      "sites (persistent lights linked within the persistence radius). Every light is in data/viirs_lights_all.gpkg, "
                      "rebuilt by scripts/15_viirs_lights.py --merge.")
    pyogrio.write_dataframe(pd.DataFrame([about_lean]), lean, layer="about", driver="GPKG")

    # Density of clear-sky lit vessel candidates (persistent lights excluded) on a 0.25 degree grid, two ways:
    #   per satellite pass: counts / (passes x sea area of the cell)
    #   per clear sea:      counts / (sum over granules of the clear-sky share of the cell's sea x sea area),
    #                       from the cloud-mask counts of --clear; lights under thick cloud are invisible, so this
    #                       is the fair basis for comparing nights and areas
    aoi = aoi_gdf(DEFAULT_AOI).geometry.iloc[0]
    tr, shape = grid_for(aoi.bounds, res)
    looks = np.zeros(shape, np.float64)
    for m in metas:
        looks += features.rasterize([(Polygon(zip(m["ring_lon"], m["ring_lat"])), 1)], out_shape=shape, transform=tr, dtype="uint8")
    area = cell_area_km2(tr, shape)
    aoi_mask = features.rasterize([(aoi, 1)], out_shape=shape, transform=tr, dtype="uint8").astype(bool)
    sg = sea_grid()  # sea beyond about 2 km of land, 0.01 degree: the only water searched for lights
    sr, sc = np.nonzero(sg.mask)
    r25 = np.floor((sg.north - (sr + 0.5) * sg.res - tr.f) / tr.e).astype(int)
    c25 = np.floor((sg.west + (sc + 0.5) * sg.res - tr.c) / tr.a).astype(int)
    keep = (r25 >= 0) & (r25 < shape[0]) & (c25 >= 0) & (c25 < shape[1])
    sea_cells = np.zeros(shape)
    np.add.at(sea_cells, (r25[keep], c25[keep]), 1)
    sea_km2 = area * sea_cells / (res / sg.res) ** 2
    v = det[(det["class"] == "lit_vessel_candidate") & (det.quality == "clear")]
    rr = np.floor((v.lat.to_numpy() - tr.f) / tr.e).astype(int)
    cc = np.floor((v.lon.to_numpy() - tr.c) / tr.a).astype(int)
    okk = (rr >= 0) & (rr < shape[0]) & (cc >= 0) & (cc < shape[1])
    counts = np.zeros(shape)
    np.add.at(counts, (rr[okk], cc[okk]), 1)
    dens = np.full(shape, -1.0, np.float32)
    valid = aoi_mask & (looks > 0) & (sea_km2 >= 25)
    dens[valid] = 1000 * counts[valid] / (looks[valid] * sea_km2[valid])
    small = DATA_DIR / "outputs" / "small"

    def cogs(arr, name, units):
        tags = {"units": units, "nodata": "-1 = outside AOI, not observed, or under 25 km2 of searched sea in the cell",
                "source": "scripts/15_viirs_lights.py", "caveat": DARK_CAVEAT_SHORT + " A light at sea is not proof of a vessel."}
        write_cog(arr, tr, "EPSG:4326", small / f"{name}_4326.tif", nodata=-1, tags=tags)
        t2, w2, h2 = calculate_default_transform("EPSG:4326", CRS_UTM_REGIONAL, shape[1], shape[0], *aoi.bounds, resolution=25000)
        dst = np.full((h2, w2), -1, np.float32)
        reproject(arr, dst, src_transform=tr, src_crs="EPSG:4326", dst_transform=t2, dst_crs=CRS_UTM_REGIONAL,
                  resampling=Resampling.nearest, src_nodata=-1, dst_nodata=-1)
        write_cog(dst, t2, CRS_UTM_REGIONAL, small / f"{name}_utm49n.tif", nodata=-1, tags=tags)

    cogs(dens, "viirs_lit_density", "clear-sky lit vessel candidates per 1,000 km2 of searched sea per satellite pass "
                                    "(persistent lights excluded)")
    clear_files = [CACHE / f"{m['granule']}.clear.npz" for m in metas]
    have_clear = float(np.mean([p.exists() for p in clear_files])) if metas else 0.0
    dens_clear, gran_clear_km2 = None, {}
    if have_clear >= 0.9:
        clear_looks = np.zeros(shape[0] * shape[1])
        for m, pth in zip(metas, clear_files):
            if pth.exists():
                z = np.load(pth)
                frac = z["clear_n"] / np.maximum(z["sea_n"], 1)
                clear_looks[z["cells"]] += frac
                gran_clear_km2[m["granule"]] = float((frac * sea_km2.ravel()[z["cells"]]).sum())
        clear_looks = clear_looks.reshape(shape)
        dens_clear = np.full(shape, -1.0, np.float32)
        validc = aoi_mask & (clear_looks >= 0.5) & (sea_km2 >= 25)
        dens_clear[validc] = 1000 * counts[validc] / (clear_looks[validc] * sea_km2[validc])
        cogs(dens_clear, "viirs_lit_density_clear", "clear-sky lit vessel candidates per 1,000 km2 of clear searched sea "
                                                    "per satellite pass (persistent lights excluded)")
    else:
        print(f"clear-sky counts for {have_clear:.0%} of granules: run --clear for the clear-sea density", flush=True)

    per_night = (det.assign(clear_lit=(det["class"] == "lit_vessel_candidate") & (det.quality == "clear"))
                 .groupby(["night", "satellite"]).agg(lights=("light_id", "size"), clear=("quality", lambda q: int((q == "clear").sum())),
                                                      clear_lit=("clear_lit", "sum"), moon=("moon_illum_pct", "median")).reset_index())
    if gran_clear_km2:  # clear searched sea seen per night and satellite, from the cloud masks
        gm = pd.DataFrame({"granule": [m["granule"] for m in metas], "satellite": [m["satellite"] for m in metas],
                           "night": night_of(pd.to_datetime(pd.Series([m["start_utc"] for m in metas]), utc=True)).astype(str)})
        gm["clear_sea_km2"] = gm.granule.map(gran_clear_km2)
        per_night = per_night.merge(gm.groupby(["night", "satellite"]).clear_sea_km2.sum().round(0).reset_index(),
                                    on=["night", "satellite"], how="left")
        per_night["lit_per_1000km2_clear"] = (1000 * per_night.clear_lit / per_night.clear_sea_km2.where(per_night.clear_sea_km2 > 0)).round(3)
    summary = {"nights": [str(n) for n in nights], "granules": len(metas), "lights": int(len(det)),
               "classes": det["class"].value_counts().to_dict(), "quality": det.quality.value_counts().to_dict(),
               "persistent_rule_nights": need, "persistent_rule_cloud_aware": cloud_aware, "sharp_single_pixel": int(det.sharp.sum()),
               "near_satlas_1km": int((det.satlas_infra_m <= 1000).sum()) if det.satlas_infra_m.notna().any() else None,
               "persistent_near_satlas_1km_share": round(float((det[det["class"] == "persistent_light"].satlas_infra_m <= 1000).mean()), 3)
               if det.satlas_infra_m.notna().any() and (det["class"] == "persistent_light").any() else None,
               "showcase_night": str(showcase), "lean_nights": [str(n) for n in keep], "lean_lights": total,
               "sites": int(len(sites)), "sites_near_satlas_1km": int((sites.satlas_infra_m <= 1000).sum()) if len(sites) else 0,
               "per_night": per_night.to_dict(orient="records")}
    from scipy.stats import spearmanr

    clear_lit = det[(det["class"] == "lit_vessel_candidate") & (det.quality == "clear")]
    in_grid = clear_lit[clear_lit.s1_passes_90d >= 0]
    summary["clear_lit_candidates"] = int(len(clear_lit))
    summary["clear_lit_share_where_s1_never_imaged_90d"] = round(float((in_grid.s1_passes_90d == 0).mean()), 3) if len(in_grid) else None
    sar_path = small / "vessel_density_regional_4326.tif"
    if sar_path.exists():
        with rasterio.open(sar_path) as ds:
            sar, sar_tr = ds.read(1), ds.transform
        if sar.shape == dens.shape and np.allclose(tuple(sar_tr)[:6], tuple(tr)[:6]):
            both = (sar >= 0) & (dens >= 0)
            rho = spearmanr(sar[both], dens[both])[0] if both.sum() > 10 else np.nan
            summary["density_rank_correlation_viirs_vs_sar"] = {"spearman_rho": round(float(rho), 3), "cells": int(both.sum()),
                                                               "note": "0.25 degree cells observed by both; different hours and targets"}
    # Single-pixel ("sharp") lights against particle-hit noise: noise would ignore where the other lights are
    cell = (np.floor(det.lat / 0.5)).astype(int).astype(str) + "_" + (np.floor(det.lon / 0.5)).astype(int).astype(str)
    tab = pd.crosstab(cell, det.sharp.astype(bool))
    if tab.shape[1] == 2:
        tab.columns = ["broad", "sharp"]
        summary["sharp_check_0p5deg"] = {"spearman_sharp_vs_broad": round(float(spearmanr(tab.broad, tab.sharp)[0]), 3),
                                         "sharp_in_cells_without_broad": int(tab[tab.broad == 0].sharp.sum()),
                                         "sharp_total": int(tab.sharp.sum()),
                                         "radiance_median_sharp_nw": round(float(det[det.sharp.astype(bool)].radiance_nw.median()), 2),
                                         "radiance_median_broad_nw": round(float(det[~det.sharp.astype(bool)].radiance_nw.median()), 2)}
    if len(nights) >= 5:
        metric = "lit_per_1000km2_clear" if "lit_per_1000km2_clear" in per_night else "clear_lit"
        summary["moon_vs_lit_spearman"] = {"metric": metric, **{s: round(float(spearmanr(g.moon, g[metric], nan_policy="omit")[0]), 3)
                                                                for s, g in per_night.groupby("satellite") if len(g) >= 5}}
    if dens_clear is not None:
        summary["clear_sea_basis"] = {"granules_with_cloud_counts": round(have_clear, 3),
                                      "aoi_mean_lit_per_1000km2_clear_per_pass": round(float(dens_clear[dens_clear >= 0].mean()), 3)}
        # Lit activity where Sentinel-1 never looked against where it did (paper 2, lit-activity term): share of each
        # 0.25 degree cell's AOI cells (0.05 degree) with no Sentinel-1 pass in 90 days
        prow, pcol = np.nonzero(passes != 65535)
        r25 = np.floor((ptr.f + (prow + 0.5) * ptr.e - tr.f) / tr.e).astype(int)
        c25 = np.floor((ptr.c + (pcol + 0.5) * ptr.a - tr.c) / tr.a).astype(int)
        okp = (r25 >= 0) & (r25 < shape[0]) & (c25 >= 0) & (c25 < shape[1])
        n_all, n_never = np.zeros(shape), np.zeros(shape)
        np.add.at(n_all, (r25[okp], c25[okp]), 1)
        np.add.at(n_never, (r25[okp], c25[okp]), (passes[prow, pcol] == 0)[okp].astype(float))
        never_share = np.where(n_all > 0, n_never / np.maximum(n_all, 1), np.nan)
        ok_d = dens_clear >= 0
        groups = {"never_imaged": ok_d & (never_share >= 0.9), "imaged": ok_d & (never_share <= 0.1)}
        summary["lit_density_clear_by_radar_coverage"] = {
            k: {"cells": int(m.sum()), "mean": round(float(dens_clear[m].mean()), 3) if m.any() else None,
                "median": round(float(np.median(dens_clear[m])), 3) if m.any() else None,
                "sea_km2": round(float(sea_km2[m].sum()), 0)} for k, m in groups.items()}
    (DATA_DIR / "viirs_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: v for k, v in summary.items() if k != "per_night"}, indent=1, default=str))
    rule_label = f"{persist_frac:.0%} of clear nights, 3+" if cloud_aware else f"{need}+ of {len(nights)} nights"
    figure(det, dens if dens_clear is None else dens_clear, tr, aoi, rule_label, nights, per_night, clear_basis=dens_clear is not None)


def figure(det, dens, tr, aoi, need, nights, per_night, clear_basis=False):
    """Map of mean lit-vessel density per pass with recurring lights, and lights per night against the moon."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    from darkvessel.viz.style import INK, INK_2, MUTED, SERIES_EXTENDED, SERIES_LIGHT, apply_matplotlib_style

    apply_matplotlib_style()
    west, south, east, north = aoi.bounds
    land = natural_earth_land(bbox=(west - 1, south - 1, east + 1, north + 1))
    bins = [0, 1e-9, 0.5, 1, 2, 4, 8, 1e9]
    labels = ["0", "under 0.5", "0.5-1", "1-2", "2-4", "4-8", "8+"]
    colors = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
    cmap, norm = ListedColormap(colors), BoundaryNorm(bins, len(colors))
    fig = plt.figure(figsize=(10, 13.2))
    ax = fig.add_axes([0.08, 0.34, 0.89, 0.55])
    h, w = dens.shape
    extent = (tr.c, tr.c + w * tr.a, tr.f + h * tr.e, tr.f)
    gpd.GeoSeries([aoi], crs="EPSG:4326").plot(ax=ax, color="#e4e2dc", edgecolor="none", zorder=0.5)
    ax.imshow(np.ma.masked_where(dens < 0, dens), cmap=cmap, norm=norm, extent=extent, interpolation="nearest", zorder=1)
    land.plot(ax=ax, color="#c9c6bd", edgecolor="none", zorder=2)
    gpd.GeoSeries([aoi], crs="EPSG:4326").boundary.plot(ax=ax, color=INK, linewidth=0.6, zorder=3)
    pers = det[det["class"] == "persistent_light"]
    pers = pers.assign(k=(pers.lat / 0.01).round().astype(int).astype(str) + "_" + (pers.lon / 0.01).round().astype(int).astype(str)).drop_duplicates("k")
    ax.scatter(pers.lon, pers.lat, s=10, marker="o", facecolor=SERIES_EXTENDED[3], edgecolor=INK, linewidth=0.4, zorder=4)
    # Outline of the sea Sentinel-1 never imaged in the 90-day window
    import rasterio

    with rasterio.open(DATA_DIR / "outputs" / "small" / "s1_passes_4326.tif") as ds:
        pas, ptr = ds.read(1), ds.transform
    never = ((pas == 0)).astype(float)
    gx = ptr.c + (np.arange(pas.shape[1]) + 0.5) * ptr.a
    gy = ptr.f + (np.arange(pas.shape[0]) + 0.5) * ptr.e
    ax.contour(gx, gy, never, levels=[0.5], colors=[INK], linewidths=1.1, linestyles="--", zorder=5)
    pad = 0.6
    ax.set_xlim(west - pad, east + pad)
    ax.set_ylim(south - pad, north + pad)
    ax.set_aspect(1 / np.cos(np.radians((south + north) / 2)))
    ax.set_xlabel("Longitude (degrees E)", fontsize=9)
    ax.set_ylabel("Latitude (degrees N)", fontsize=9)
    ax.grid(True, zorder=0)
    handles = [Patch(facecolor=c, edgecolor="none", label=l) for c, l in zip(colors, labels)]
    handles.append(Line2D([], [], linestyle="none", marker="o", markersize=5, markerfacecolor=SERIES_EXTENDED[3], markeredgecolor=INK,
                          markeredgewidth=0.5, label=f"recurring light ({need})"))
    handles.append(Line2D([], [], color=INK, linewidth=1.1, linestyle="--", label="never imaged by Sentinel-1 (90 days)"))
    basis = "clear sea" if clear_basis else "sea"
    ax.legend(handles=handles, title=f"Lit vessel candidates per\n1,000 km2 of {basis} per pass", loc="lower right", fontsize=9,
              title_fontsize=9, frameon=True, facecolor="#fcfcfb", edgecolor="#e1e0d9")

    # Lights per night by satellite, with the moon above
    sats = [s for s in SATS if s in set(det.satellite)]
    ns = [str(n) for n in nights]
    metric = "lit_per_1000km2_clear" if clear_basis and "lit_per_1000km2_clear" in per_night else "clear_lit"
    per = per_night.pivot(index="night", columns="satellite", values=metric).reindex(ns)
    moon = det.groupby("night").moon_illum_pct.median().reindex(ns)
    x = pd.to_datetime(pd.Series(ns))
    axm = fig.add_axes([0.08, 0.215, 0.89, 0.055])
    axm.bar(x, moon.to_numpy(), width=0.8, color="#c3c2b7", zorder=2)
    axm.set_ylim(0, 100)
    axm.set_yticks([0, 50, 100])
    axm.set_ylabel("Moon\n% lit", fontsize=8)
    axm.tick_params(labelbottom=False, labelsize=8)
    axm.grid(True, axis="y", zorder=0)
    axn = fig.add_axes([0.08, 0.075, 0.89, 0.13], sharex=axm)
    for s, c in zip(sats, SERIES_LIGHT):
        if s in per:
            axn.plot(x, per[s].to_numpy(), color=c, marker="o", markersize=3.5, linewidth=1.4, label=s, zorder=3)
    axn.set_ylabel("Lit candidates per\n1,000 km2 clear sea" if metric != "clear_lit" else "Clear-sky lit\ncandidates", fontsize=8)
    axn.tick_params(labelsize=8)
    axn.grid(True, axis="y", zorder=0)
    axn.set_ylim(bottom=0)
    axn.legend(loc="upper left", fontsize=8, ncol=3)
    axn.xaxis.set_major_locator(mdates.DayLocator(interval=3))
    axn.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    fig.text(0.08, 0.975, "Lights at sea at night: South China Sea", fontsize=15, color=INK, fontweight="bold", va="top")
    fig.text(0.08, 0.948, f"VIIRS Day/Night Band, {len(nights)} night{'s' if len(nights) != 1 else ''} ({nights[0]} to {nights[-1]}), about 00:00 to 03:00 UTC+7. "
             f"Map: clear-sky lights that do not recur,\nper 1,000 km2 of {'clear ' if clear_basis else ''}sea per satellite pass, 0.25 degree cells. Recurring lights are "
             f"platforms, flares, island lights and anchorages.\nA light at sea is not proof of a vessel. {DARK_CAVEAT_SHORT}",
             fontsize=9.5, color=INK_2, va="top")
    fig.text(0.08, 0.012, "VIIRS DNB SDR, GEO and JRR cloud mask: NOAA JPSS on the AWS Open Data Registry. Detector after Elvidge "
             "et al. 2015 (doi:10.3390/rs70303020).\nAOI and land: Natural Earth (public domain). No maritime boundaries or "
             "claims are drawn. " + ("Nightly rates divide by the clear sea each satellite saw that night." if clear_basis else
                                     "Night counts depend on swath geometry and cloud."), fontsize=7.5, color=MUTED, va="bottom")
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG_DIR / "viirs_lights.png", dpi=150)
    plt.close(fig)
    print("wrote", FIG_DIR / "viirs_lights.png", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2026, 9, 5))
    ap.add_argument("--end", type=dt.date.fromisoformat, default=dt.date(2026, 10, 1))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--retry", action="store_true")
    ap.add_argument("--clear", action="store_true")
    a = ap.parse_args()
    if a.merge:
        merge()
    elif a.retry:
        retry(a.workers)
    elif a.clear:
        clear_sky(a.workers)
    else:
        run(a.start, a.end, a.workers)
