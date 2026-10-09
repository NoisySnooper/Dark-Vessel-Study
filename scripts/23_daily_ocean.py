"""Daily ocean layers for the VIIRS nights and the regional Sentinel-1 days: SST and fronts, chlorophyll, currents and
sea level, waves and wind, as window rasters, a cell-night table and a radar-pass table.

For each night (local evening date in Vietnam, UTC+7; the VIIRS passes fall near 18 UTC) and every UTC date with a
regional Sentinel-1 scene (src/darkvessel/ocean/daily.py fetches and caches, fronts.py detects):
  SST          MUR v4.1 1 km (CoastWatch ERDDAP jplMURSST41, 09 UTC analysis) on the project's 0.01 degree fine grid:
               each fine cell is the mean of the 2 x 2 MUR cells whose centres sit on its corners. OISST v2.1 fallback.
  Fronts       3 x 3 median filter, gradient magnitude in degrees C per km (dx scaled by cos latitude), hysteresis mask
               with thresholds at the 90th and 97th percentiles of the window's pooled sea gradient, nothing within 2 km
               of the coast or on MUR land or ice. Lines of the showcase night (most clear sea in the VIIRS record).
  Chlorophyll  CoastWatch VIIRS DINEOF gap-filled chlorophyll-a (2 km science quality, then 9 km near-real-time for the
               latest days) in log10; share of days with a direct retrieval from the daily non-filled S-NPP product.
  Currents     RTOFS global nowcast valid 18 UTC: sea surface height, depth-averaged current speed, mixed-layer and
               boundary-layer thickness, SSH gradient (eddy-edge proxy); SSH anomaly against the window mean per cell.
  Waves, wind  GFS-Wave significant wave height and GFS 10 m wind at 18 UTC, and at the hour nearest each scene.
  Sampling     Daily SST and front statistics per 0.25 degree cell use only the cell's fine pixels inside the AOI polygon.
               Waves and wind are bilinear at the cell centre (the mean of the four GFS cells around it, NaN-aware);
               the chlorophyll valid-day share counts only sea pixels (AOI sea outside the 2 km coast buffer).
Region names are the reporting boxes of scripts/21_viirs_regions.py ('other' outside them), not boundaries or claims.

Inputs: data/detections_regional.gpkg (scenes_processed_4326), data/viirs_nightly_by_region.csv (showcase night),
        data/viirs_lights_all.gpkg (moon illumination per night), Natural Earth 10 m land (data/raw/natural_earth)
Output: data/outputs/small/{sst_mean_c, sst_grad_mean_c_per_km, front_freq, sst_showcase_c}_{4326,utm49n}.tif  (0.01 deg)
        data/outputs/small/{chl_mean_mg_m3, chl_valid_share, current_speed_mean_ms, mld_mean_m, ssh_grad_mean}_*.tif (0.05)
        data/outputs/small/{wave_hs_mean_m, wind_mean_ms}_*.tif (0.25 degree)
        data/ocean_fronts.gpkg (fronts_4326, fronts_utm49n, about), data/ocean_daily_cells.parquet,
        data/ocean_radar_pass_cells.parquet, data/ocean_daily_summary.json, docs/figures/ocean_showcase_fronts.png
Usage: python scripts/23_daily_ocean.py [--start 2026-09-05 --end 2026-10-01] [--workers 3]
"""

import argparse
import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor

import darkvessel  # noqa: F401  (sets PROJ_DATA before rasterio and pyogrio load)
import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
from rasterio import features

from darkvessel import weather
from darkvessel.aoi import aoi_gdf, natural_earth_land
from darkvessel.config import CRS_GEO, CRS_UTM_REGIONAL, DARK_CAVEAT, DATA_DIR, DEFAULT_AOI, FIG_DIR
from darkvessel.io import write_dual_crs
from darkvessel.ocean import daily, fronts
from darkvessel.ocean import static as st
from darkvessel.ocean.grid import (OCEAN_CACHE, OCEAN_CAVEAT, aoi_mask, bilinear, cell_index, centres,
                                   fine_grid, grid, model_grid, region_of, write_dual_cog)

ACCESS_DATE = "2026-10-08"  # downloads of 2026-10-08; RTOFS for 2026-09-18 (404 that day) fetched on 2026-10-09
Q_LOW, Q_HIGH = 0.90, 0.97
NODD_LICENCE = ("NOAA Open Data Dissemination (registry.opendata.aws text): 'NOAA data disseminated through NODD are open to "
                "the public and can be used as desired ... NOAA requests attribution for the use or dissemination of "
                "unaltered NOAA data' and no implied endorsement.")

ap = argparse.ArgumentParser()
ap.add_argument("--start", default="2026-09-05")
ap.add_argument("--end", default="2026-10-01")
ap.add_argument("--workers", type=int, default=3)
args = ap.parse_args()
t0 = time.time()
log = lambda m: print(f"[{time.time() - t0:6.0f}s] {m}", flush=True)  # noqa: E731

# ---------------------------------------------------------------------------------------------------------------------
# Window: nights, scenes, grids, static masks
nights = [str(d) for d in pd.date_range(args.start, args.end).date]
scenes = pyogrio.read_dataframe(DATA_DIR / "detections_regional.gpkg", layer="scenes_processed_4326")
scenes["t"] = pd.to_datetime(scenes.start_utc, utc=True)
scenes["utc_date"] = scenes.t.dt.strftime("%Y-%m-%d")
scene_dates = sorted(scenes.utc_date.unique())
days = sorted(set(nights) | set(scene_dates))
night_at = {d: daily.night_time(d) for d in days}
log(f"{len(nights)} nights {nights[0]}..{nights[-1]}, {len(scenes)} scenes on {len(scene_dates)} UTC dates, {len(days)} days in all")

ft, fs = fine_grid()
mt, ms = model_grid()
t5, s5 = grid(0.05)
aoi_f, aoi_m, aoi_5 = aoi_mask(ft, fs), aoi_mask(mt, ms), aoi_mask(t5, s5)
static = OCEAN_CACHE / "static" / "coast_buffer_2km_fine.npz"
if static.exists():
    coast = np.load(static)["coast"]
else:
    w, s, e, n = aoi_gdf(DEFAULT_AOI).geometry.iloc[0].bounds
    coast = fronts.coast_buffer_mask(ft, fs, natural_earth_land(bbox=(w - 1, s - 1, e + 1, n + 1)).geometry, 2000.0)
    static.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(static, coast=coast)
flon, flat = centres(ft, fs)
fr, fc, _ = cell_index(mt, ms, flon.ravel(), flat.ravel())
fcell = fr * ms[1] + fc  # model cell of every fine pixel (the fine grid lies inside the model grid)
NC = ms[0] * ms[1]
mlon, mlat = centres(mt, ms)
lon5, lat5 = centres(t5, s5)
region_cell = region_of(mlon.ravel(), mlat.ravel())


def cell_stats(values: np.ndarray, valid: np.ndarray | None = None):
    """(mean, sd, n) per model cell of a fine-grid field, as flat arrays of length NC."""
    v = values.ravel().astype(np.float64)
    ok = np.isfinite(v) if valid is None else (valid.ravel() & np.isfinite(v))
    n = np.bincount(fcell[ok], minlength=NC)
    s1 = np.bincount(fcell[ok], weights=v[ok], minlength=NC)
    s2 = np.bincount(fcell[ok], weights=v[ok] ** 2, minlength=NC)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(n > 0, s1 / n, np.nan)
        sd = np.sqrt(np.maximum(np.where(n > 1, s2 / n - mean ** 2, np.nan), 0.0))
    return mean, sd, n


# ---------------------------------------------------------------------------------------------------------------------
# Downloads (checkpointed in data/cache/ocean)
mur_days = daily.erddap_times(daily.MUR_ID, "2026-09-01")
for ds_id, _ in (*daily.CHL_GAPFILLED, *daily.CHL_OBSERVED):  # time axes and the RTOFS window once, before the threads start
    daily.erddap_times(ds_id, "2026-09-01")
daily.rtofs_window(daily.rtofs_url(night_at[days[0]]))
scene_times = sorted({pd.Timestamp(t).to_pydatetime() for t in scenes.t})
jobs = [(src, d) for d in days for src in ("sst", "chl", "chl_obs", "rtofs", "wave", "wind")]
jobs += [(src, t) for t in scene_times for src in ("wave_t", "wind_t")]
failed = {}


def fetch(job):
    src, x = job
    try:
        if src == "sst":
            daily.fetch_sst(x, mur_days)
        elif src == "chl":
            if daily.fetch_chl(x)[0] is None:
                raise FileNotFoundError("no gap-filled chlorophyll for this day")
        elif src == "chl_obs":
            if daily.fetch_chl(x, daily.CHL_OBSERVED)[0] is None:
                raise FileNotFoundError("no daily chlorophyll for this day")
        elif src == "rtofs":
            daily.fetch_rtofs(night_at[x])
        elif src == "wave":
            daily.fetch_wave_hs(night_at[x])
        elif src == "wind":
            daily.load_wind(night_at[x])
        elif src == "wave_t":
            daily.fetch_wave_hs(x)
        elif src == "wind_t":
            daily.load_wind(x)
        return job, None
    except Exception as e:  # noqa: BLE001  keep going; the summary lists what is missing
        return job, repr(e)[:300]


with ThreadPoolExecutor(args.workers) as ex:
    for i, (job, err) in enumerate(ex.map(fetch, jobs), 1):
        if err:
            failed.setdefault(job[0], {})[str(job[1])[:19]] = err
            log(f"{job[0]} {str(job[1])[:19]} failed: {err[:160]}")
        if i % 25 == 0:
            log(f"{i}/{len(jobs)} downloads")
log(f"downloads done, failures: { {k: len(v) for k, v in failed.items()} }")

# ---------------------------------------------------------------------------------------------------------------------
# Fronts pass 1: gradients and the pooled threshold distribution (every 5th sea pixel of every day)
have_sst = [d for d in days if daily.mur_path(d).exists()]
pooled = []
for d in have_sst:
    if not fronts.grad_path(d).exists():
        sst, _ = daily.load_sst(d)
        fronts.save_grad(d, fronts.gradient_magnitude(sst, ft))
    g, _ = fronts.load_grad(d)
    sub = g[::5, ::5][(aoi_f & ~coast)[::5, ::5]]
    pooled.append(sub[np.isfinite(sub)])
low, high = fronts.thresholds(np.concatenate(pooled), Q_LOW, Q_HIGH)
log(f"gradients done for {len(have_sst)} days; hysteresis thresholds low {low:.4f} high {high:.4f} degC/km")

# ---------------------------------------------------------------------------------------------------------------------
# Daily pass: masks, cell stats, window sums
csv = pd.read_csv(DATA_DIR / "viirs_nightly_by_region.csv")
clear_total = csv.groupby("night").clear_sea_km2.sum()
showcase = str(clear_total.idxmax())
lights = pyogrio.read_dataframe(DATA_DIR / "viirs_lights_all.gpkg", layer="viirs_lights_4326", read_geometry=False,
                                columns=["night", "moon_illum_pct"])
moon = lights.groupby("night").moon_illum_pct.median().to_dict()

sea_meta = daily.load_sst_meta(showcase if showcase in have_sst else have_sst[0])
sea_fine = sea_meta["sea"] & ~coast & aoi_f
sea_count = np.bincount(fcell[sea_fine.ravel()], minlength=NC)
sea_cells = aoi_m.ravel() & (sea_count > 0)
cell_idx = np.nonzero(sea_cells)[0]
log(f"{len(cell_idx)} sea cells on the model grid, showcase night {showcase} ({clear_total.max():,.0f} km2 clear sea)")

acc = {"sst_sum": np.zeros(fs), "sst_n": np.zeros(fs, np.int16), "grad_sum": np.zeros(fs), "grad_n": np.zeros(fs, np.int16),
       "front_n": np.zeros(fs, np.int16)}
acc5 = {k: np.zeros(s5) for k in ("chl_sum", "chl_n", "obs_sum", "obs_n", "speed_sum", "speed_n", "mld_sum", "mld_n",
                                   "sshg_sum", "sshg_n")}
sea_f32 = sea_fine.astype(np.float32)  # sea mask for the chlorophyll valid-day share (AOI sea outside the coast buffer)
WAVE_WIND = "bilinear at the cell centre (mean of the four GFS 0.25 degree cells around it), NaN-aware"
accm = {k: np.zeros(ms) for k in ("wave_sum", "wave_n", "wind_sum", "wind_n")}
rows, per_day = [], {}
sst_showcase = None
for d in days:
    rec = pd.DataFrame({"night": d, "cell": cell_idx, "row": cell_idx // ms[1], "col": cell_idx % ms[1],
                        "lon": mlon.ravel()[cell_idx], "lat": mlat.ravel()[cell_idx], "region": region_cell[cell_idx],
                        "is_viirs_night": d in nights, "is_s1_date": d in scene_dates})
    info = {"night": d}
    if d in have_sst:
        meta = daily.load_sst_meta(d)
        sst, g = meta["sst"], fronts.load_grad(d)[0]
        exclude = ~aoi_f | coast | meta["ice"] | ~meta["sea"]
        if fronts.mask_path(d).exists() and fronts.load_mask(d)[2] == {"low": low, "high": high}:
            m = fronts.load_mask(d)[0]
        else:
            m = fronts.front_mask(g, low, high, exclude=exclude)
            fronts.save_mask(d, m, low, high)
        gv = np.isfinite(g) & ~exclude
        acc["sst_sum"] += np.where(np.isfinite(sst), sst, 0); acc["sst_n"] += np.isfinite(sst)
        acc["grad_sum"] += np.where(gv, g, 0); acc["grad_n"] += gv; acc["front_n"] += m
        if d == showcase:
            sst_showcase = sst
        mean, sd, _ = cell_stats(sst, aoi_f)
        gmean, _, gn = cell_stats(g, gv)
        fshare = np.bincount(fcell[m.ravel()], minlength=NC) / np.maximum(gn, 1)
        rec["sst_mean_c"], rec["sst_sd_c"] = mean[cell_idx].round(3), sd[cell_idx].round(3)
        rec["sst_grad_mean"] = np.where(gn > 0, gmean, np.nan)[cell_idx].round(5)
        rec["front_share"] = np.where(gn > 0, fshare, np.nan)[cell_idx].round(4)
        rec["dist_front_km"] = fronts.distance_to_front_km(m, ft, rec.lon.to_numpy(), rec.lat.to_numpy()).round(1)
        rec["sst_source"], rec["sst_date"] = meta["source"], meta["valid_time"][:10]
        info.update(front_share_sea=float(m[sea_fine].mean()), sst_min=float(np.nanmin(sst[sea_fine])), sst_max=float(np.nanmax(sst[sea_fine])),
                    sst_mean=float(np.nanmean(sst[sea_fine])), front_pixels=int(m.sum()), sst_source=meta["source"])
    chl = daily.load_chl(d)
    if chl is not None:
        a, ctr, name = chl
        rec["chl_log10_mean"] = daily.regrid(a, ctr, mt, ms).ravel()[cell_idx].round(4)
        rec["chl_dataset"], rec["chl_date"] = name, d
        c5 = daily.regrid(a, ctr, t5, s5)
        acc5["chl_sum"] += np.where(np.isfinite(c5), c5, 0); acc5["chl_n"] += np.isfinite(c5)
        info["chl_dataset"] = name
    obs = daily.load_chl(d, daily.CHL_OBSERVED)
    if obs is not None:
        a, otr, name = obs
        olon, olat = centres(otr, a.shape)
        osea = daily.sample(sea_f32, ft, olon, olat, fill=0.0) > 0.5  # retrieval pixels whose centre is AOI sea
        ind = np.where(osea, np.isfinite(a), np.nan).astype(np.float32)
        rec["chl_valid_share"] = daily.regrid(ind, otr, mt, ms).ravel()[cell_idx].round(3)
        rec["chl_obs_dataset"] = name
        o5 = daily.regrid(ind, otr, t5, s5)
        acc5["obs_sum"] += np.where(np.isfinite(o5), o5, 0); acc5["obs_n"] += np.isfinite(o5)
        info["chl_obs_dataset"] = name
    if daily.rtofs_path(night_at[d]).exists():
        f = daily.rtofs_on_grid(night_at[d], mt, ms)
        for k, c in (("ssh_m", "ssh_m"), ("ssh_grad", "ssh_grad"), ("current_speed_ms", "current_speed_ms"), ("mld_m", "mld_m"), ("sbl_m", "sbl_m")):
            rec[c] = f[k].ravel()[cell_idx].astype(np.float32).round(5 if k == "ssh_grad" else 3)
        rec["rtofs_valid_utc"] = daily.load_rtofs(night_at[d])["valid_time"]
        f5 = daily.rtofs_on_grid(night_at[d], t5, s5)
        for k, a5 in (("speed", f5["current_speed_ms"]), ("mld", f5["mld_m"]), ("sshg", f5["ssh_grad"])):
            ok5 = np.isfinite(a5)  # each field keeps its own count: the SSH gradient is NaN next to the coast
            acc5[f"{k}_sum"] += np.where(ok5, a5, 0); acc5[f"{k}_n"] += ok5
    if daily.wave_key(night_at[d])[1].exists():
        hs, htr, hvalid = daily.load_wave_hs(night_at[d])
        v = bilinear(hs, htr, mlon.ravel(), mlat.ravel(), wrap_lon=True).reshape(ms)
        rec["wave_hs_m"], rec["wave_valid_utc"] = v.ravel()[cell_idx].round(2), hvalid.isoformat()
        accm["wave_sum"] += np.where(np.isfinite(v), v, 0); accm["wave_n"] += np.isfinite(v)
    try:
        spd, wtr = daily.load_wind(night_at[d])
        v = bilinear(spd, wtr, mlon.ravel(), mlat.ravel(), wrap_lon=True).reshape(ms)
        rec["wind_ms"], rec["wind_valid_utc"] = v.ravel()[cell_idx].round(2), night_at[d].isoformat()
        accm["wind_sum"] += np.where(np.isfinite(v), v, 0); accm["wind_n"] += np.isfinite(v)
    except Exception as e:  # noqa: BLE001
        log(f"wind {d} failed: {e!r}"[:200])
    rec["moon_illum_pct"] = moon.get(d, np.nan)
    rows.append(rec)
    per_day[d] = info
    log(f"{d}: " + ", ".join(f"{k}={v:.3g}" if isinstance(v, float) else f"{k}={v}" for k, v in info.items() if k != "night"))

cells = pd.concat(rows, ignore_index=True)
if "ssh_m" in cells:
    cells["ssh_anom_m"] = (cells.ssh_m - cells.groupby("cell").ssh_m.transform("mean")).round(4)
cells["caveat"] = OCEAN_CAVEAT
order = ["night", "row", "col", "lon", "lat", "region", "is_viirs_night", "is_s1_date", "sst_mean_c", "sst_sd_c", "sst_grad_mean",
         "front_share", "dist_front_km", "chl_log10_mean", "chl_valid_share", "ssh_m", "ssh_anom_m", "ssh_grad", "current_speed_ms",
         "mld_m", "sbl_m", "wave_hs_m", "wind_ms", "moon_illum_pct", "sst_source", "sst_date", "chl_dataset", "chl_date",
         "chl_obs_dataset", "rtofs_valid_utc", "wave_valid_utc", "wind_valid_utc", "caveat"]
cells = cells[[c for c in order if c in cells]]


def write_parquet(df: pd.DataFrame, path, extra: dict):
    tbl = pa.Table.from_pandas(df, preserve_index=False)
    meta = {**(tbl.schema.metadata or {}), b"caveat": OCEAN_CAVEAT.encode(), **{k.encode(): str(v).encode() for k, v in extra.items()}}
    pq.write_table(tbl.replace_schema_metadata(meta), path, compression="zstd")


write_parquet(cells, DATA_DIR / "ocean_daily_cells.parquet",
              {"grid": "0.25 degree model grid of scripts/15_viirs_lights.py, origin 99.0E 24.0N, 109 x 94; row 0 is the north",
               "dark_caveat": DARK_CAVEAT, "region": "reporting box of the cell centre ('other' outside every box), not a boundary",
               "night": "local evening date in Vietnam (UTC+7); daily fields keyed by that UTC date, RTOFS, waves and wind at 18 UTC",
               "units": "sst degC; sst_grad degC per km; dist_front km; chl log10(mg m-3); ssh m; ssh_grad m per km; speed m/s "
                        "(depth averaged); mld, sbl, wave_hs m; wind m/s; moon percent"})
log(f"cell-night table: {len(cells)} rows")

# ---------------------------------------------------------------------------------------------------------------------
# Radar passes: model cells touched by each scene, waves and wind at the scene hour, same-UTC-date daily fields
daily_cols = [c for c in ("sst_mean_c", "sst_sd_c", "sst_grad_mean", "front_share", "dist_front_km", "chl_log10_mean", "chl_valid_share",
                          "ssh_m", "ssh_anom_m", "ssh_grad", "current_speed_ms", "mld_m", "sst_source", "chl_dataset", "rtofs_valid_utc") if c in cells]
passes = []
wave_cache, wind_cache = {}, {}
for _, sc in scenes.iterrows():
    hit = features.rasterize([(sc.geometry, 1)], out_shape=ms, transform=mt, all_touched=True, dtype="uint8").astype(bool).ravel() & sea_cells
    idx = np.nonzero(hit)[0]
    t = sc.t.to_pydatetime()
    p = pd.DataFrame({"scene_id": sc.product_id, "mission": sc.mission, "acq_utc": sc.start_utc, "utc_date": sc.utc_date,
                      "row": idx // ms[1], "col": idx % ms[1], "lon": mlon.ravel()[idx], "lat": mlat.ravel()[idx], "region": region_cell[idx]})
    key = daily.wave_key(t)[1]
    if key.exists():
        if key not in wave_cache:
            hs, htr, hvalid = daily.load_wave_hs(t)
            wave_cache[key] = (bilinear(hs, htr, mlon.ravel(), mlat.ravel(), wrap_lon=True), hvalid.isoformat())
        p["wave_hs_m"], p["wave_valid_utc"] = wave_cache[key][0][idx].round(2), wave_cache[key][1]
    cyc = weather._nearest_gfs(t)
    try:
        if cyc not in wind_cache:
            spd, wtr = daily.load_wind(t)
            wind_cache[cyc] = (bilinear(spd, wtr, mlon.ravel(), mlat.ravel(), wrap_lon=True), (cyc[0] + dt.timedelta(hours=cyc[1])).isoformat())
        p["wind_ms"], p["wind_valid_utc"] = wind_cache[cyc][0][idx].round(2), wind_cache[cyc][1]
    except Exception as e:  # noqa: BLE001
        log(f"wind at {sc.product_id[:32]} failed: {e!r}"[:200])
    passes.append(p)
passes = pd.concat(passes, ignore_index=True)
passes = passes.merge(cells[["night", "row", "col", *daily_cols]].rename(columns={"night": "utc_date"}), on=["utc_date", "row", "col"], how="left")
passes["caveat"] = OCEAN_CAVEAT
write_parquet(passes, DATA_DIR / "ocean_radar_pass_cells.parquet",
              {"cells": "0.25 degree model cells touched by the scene footprint (all_touched) that hold sea", "dark_caveat": DARK_CAVEAT,
               "daily_fields": "SST, fronts, chlorophyll and RTOFS (18 UTC) of the scene's UTC date, from ocean_daily_cells.parquet",
               "waves_wind": "GFS-Wave and GFS wind at the hour nearest the scene start"})
log(f"radar-pass table: {len(passes)} rows for {passes.scene_id.nunique()} scenes")

# ---------------------------------------------------------------------------------------------------------------------
# Window rasters
window = f"{nights[0]} to {nights[-1]} ({len(have_sst)} days with SST)"
src_mur = f"MUR v4.1 analysed_sst via NOAA CoastWatch ERDDAP {daily.MUR_ID} (JPL PO.DAAC), daily 09 UTC, {window}"
with np.errstate(invalid="ignore", divide="ignore"):
    sst_mean = np.where(acc["sst_n"] > 0, acc["sst_sum"] / acc["sst_n"], np.nan)
    grad_mean = np.where(acc["grad_n"] > 0, acc["grad_sum"] / acc["grad_n"], np.nan)
    front_freq = np.where(acc["grad_n"] > 0, acc["front_n"] / acc["grad_n"], np.nan)
    chl_mean = np.where(acc5["chl_n"] > 0, 10 ** (acc5["chl_sum"] / acc5["chl_n"]), np.nan)
    obs_share = np.where(acc5["obs_n"] > 0, acc5["obs_sum"] / acc5["obs_n"], np.nan)
    speed_mean = np.where(acc5["speed_n"] > 0, acc5["speed_sum"] / acc5["speed_n"], np.nan)
    mld_mean = np.where(acc5["mld_n"] > 0, acc5["mld_sum"] / acc5["mld_n"], np.nan)
    sshg_mean = np.where(acc5["sshg_n"] > 0, acc5["sshg_sum"] / acc5["sshg_n"], np.nan)
    wave_mean = np.where(accm["wave_n"] > 0, accm["wave_sum"] / accm["wave_n"], np.nan)
    wind_mean = np.where(accm["wind_n"] > 0, accm["wind_sum"] / accm["wind_n"], np.nan)
front_rule = (f"3x3 median filter, gradient magnitude degC/km with dx scaled by cos(lat), hysteresis low {low:.4f} high {high:.4f} "
              f"(window {Q_LOW:.0%} and {Q_HIGH:.0%} quantiles of AOI sea gradient), no fronts within 2 km of the coast or on MUR land/ice")
ERDDAP_INFO = "https://coastwatch.pfeg.noaa.gov/erddap/info/{}/index.json"
LIC = {
    "mur": {"licence": "JPL PO.DAAC data policy as stated in the ERDDAP 'license' attribute: 'available free of charge ... may be used "
                       "and redistributed for free but is not intended for legal use'; acknowledgement: 'These data were provided by "
                       "JPL under support by NASA MEaSUREs program.'", "licence_url": ERDDAP_INFO.format(daily.MUR_ID),
            "version": "MUR-JPL-L4-GLOB-v04.1 (product_version 04.1), doi:10.5067/GHGMR-4FJ04"},
    "chl": {"licence": "ERDDAP 'license' attributes: 2 km and 9 km science quality CC0-1.0 (NOAA waives copyright); 9 km near-real-time "
                       "'may be used and redistributed for free but is not intended for legal use'",
            "licence_url": ERDDAP_INFO.format("noaacwNPPN20S3ASCIDINEOF2kmDaily"),
            "version": "NOAA CoastWatch DINEOF gap-filled chlorophyll-a, datasets as listed in source"},
    "chl_obs": {"licence": "ERDDAP 'license' attribute: NASA Earth science data policy (full and open sharing) plus the NOAA "
                           "no-warranty text; acknowledgement: 'These data were provided by NOAA's Center for Satellite "
                           "Applications and Research (STAR) and the CoastWatch program.'",
                "licence_url": "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-information-guidance",
                "version": "NOAA S-NPP VIIRS Level 3 daily chlorophyll-a, 4 km (science quality, then near-real-time)"},
    "nodd": {"licence": NODD_LICENCE, "licence_url": "https://registry.opendata.aws/noaa-rtofs/", "version": "operational NOAA model output"},
    "gfs": {"licence": NODD_LICENCE, "licence_url": "https://registry.opendata.aws/noaa-gfs-bdp-pds/", "version": "operational GFS and GFS-Wave"},
}


def tags(kind: str, **extra) -> dict:
    """COG tags: licence, licence URL, version, access date, window and the dark caveat, plus `extra`."""
    return {**LIC[kind], "access_date": ACCESS_DATE, "window": window, "dark_caveat": DARK_CAVEAT, **extra}


files = []
files += write_dual_cog(np.where(aoi_f, np.round(sst_mean, 2), np.nan), ft, "sst_mean_c", "degC", src_mur, tags=tags("mur"))
files += write_dual_cog(np.where(aoi_f, np.round(grad_mean, 4), np.nan), ft, "sst_grad_mean_c_per_km", "degC per km", src_mur,
                        tags=tags("mur", method="mean daily gradient magnitude of the 3x3 median-filtered SST; NaN within 2 km of the coast"))
files += write_dual_cog(np.where(aoi_f, np.round(front_freq, 3), np.nan), ft, "front_freq", "share of days", src_mur,
                        tags=tags("mur", method=front_rule, references="Belkin and O'Reilly 2009 doi:10.1016/j.jmarsys.2008.11.018; "
                                  "Cayula and Cornillon 1992 doi:10.1175/1520-0426(1992)009<0067:EDAFSI>2.0.CO;2"))
if sst_showcase is not None:
    files += write_dual_cog(np.where(aoi_f, np.round(sst_showcase, 2), np.nan), ft, "sst_showcase_c", "degC", src_mur,
                            tags=tags("mur", date=f"{showcase} 09 UTC analysis",
                                      why="VIIRS night with the most clear sea in data/viirs_nightly_by_region.csv"))
chl_names = sorted({v for i in per_day.values() for v in [i.get("chl_dataset")] if v})
obs_names = sorted({v for i in per_day.values() for v in [i.get("chl_obs_dataset")] if v})
files += write_dual_cog(np.where(aoi_5, np.round(chl_mean, 3), np.nan), t5, "chl_mean_mg_m3", "mg m-3 (geometric mean)",
                        f"NOAA CoastWatch VIIRS DINEOF gap-filled chlorophyll-a via ERDDAP: {', '.join(chl_names)}; {window}",
                        tags=tags("chl", method="10 ** mean(log10 chl) over the days with a value; 2 km days bin-averaged, 9 km days "
                                  "sampled, onto 0.05 degree"))
files += write_dual_cog(np.where(aoi_5, np.round(obs_share, 3), np.nan), t5, "chl_valid_share", "share of days",
                        f"NOAA CoastWatch daily VIIRS S-NPP chlorophyll-a (not gap-filled) via ERDDAP: {', '.join(obs_names)}; {window}",
                        tags=tags("chl_obs", method="share of window days with a direct chlorophyll retrieval, over the retrieval "
                                  "pixels whose centre is AOI sea outside the 2 km coast buffer (clouds and glint remove the rest)"))
src_rt = f"NOAA RTOFS global nowcast diagnostics (bucket noaa-nws-rtofs-pds, rtofs_glo_2ds_n018_diag.nc), valid 18 UTC; {window}"
files += write_dual_cog(np.where(aoi_5, np.round(speed_mean, 3), np.nan), t5, "current_speed_mean_ms", "m/s (depth-averaged, barotropic)",
                        src_rt, tags=tags("nodd", method="mean of the daily speed of the barotropic (depth-averaged) u and v, nearest "
                                          "native 1/12 degree cell"))
files += write_dual_cog(np.where(aoi_5, np.round(mld_mean, 2), np.nan), t5, "mld_mean_m", "m", src_rt,
                        tags=tags("nodd", method="mean of the daily mixed_layer_thickness, nearest native 1/12 degree cell"))
files += write_dual_cog(np.where(aoi_5, np.round(sshg_mean, 6), np.nan), t5, "ssh_grad_mean", "m per km", src_rt,
                        tags=tags("nodd", method="mean daily magnitude of the sea surface height gradient on the native 1/12 degree "
                                  "grid (eddy-edge proxy), each pixel averaged over the days it has a value"))
files += write_dual_cog(np.where(aoi_m, np.round(wave_mean, 2), np.nan), mt, "wave_hs_mean_m", "m",
                        f"NOAA GFS-Wave global 0.25 degree HTSGW (bucket noaa-gfs-bdp-pds), 18 UTC analyses; {window}",
                        tags=tags("gfs", method="significant height of combined wind waves and swell, " + WAVE_WIND))
files += write_dual_cog(np.where(aoi_m, np.round(wind_mean, 2), np.nan), mt, "wind_mean_ms", "m/s",
                        f"NOAA GFS 0.25 degree 10 m wind (bucket noaa-gfs-bdp-pds), 18 UTC analyses; {window}",
                        tags=tags("gfs", method="10 m wind speed from U and V, " + WAVE_WIND))
log(f"{len(files)} COGs written")

# ---------------------------------------------------------------------------------------------------------------------
# Front lines of the showcase night
fronts_path = DATA_DIR / "ocean_fronts.gpkg"
n_lines, km_lines = 0, 0.0
if showcase in have_sst:
    m, _, _ = fronts.load_mask(showcase)
    g, _ = fronts.load_grad(showcase)
    lines = fronts.front_lines(m, ft, g, min_length_km=10.0)
    gdf = gpd.GeoDataFrame({"date": showcase, "grad_c_per_km": [round(x[2], 4) for x in lines], "length_km": [round(x[1], 1) for x in lines],
                            "caveat": OCEAN_CAVEAT}, geometry=[x[0] for x in lines], crs=CRS_GEO)
    if fronts_path.exists():
        fronts_path.unlink()
    write_dual_crs(gdf, fronts_path, "fronts", utm_crs=CRS_UTM_REGIONAL)
    about = {"layers": "fronts_4326, fronts_utm49n: SST front lines of one night", "date": f"{showcase} (MUR analysis of 09 UTC)",
             "why_this_night": "the VIIRS night with the largest total clear sea over the six reporting boxes (data/viirs_nightly_by_region.csv)",
             "method": front_rule + "; mask skeletonised, 8-neighbour pixels joined and merged into lines, lines shorter than 10 km dropped",
             "attributes": "grad_c_per_km = mean gradient magnitude along the line; length_km on a local plane",
             "sst_source": src_mur, "sst_licence": "JPL PO.DAAC data policy as stated on the ERDDAP info page: free of charge, may be used and redistributed",
             "references": "Belkin and O'Reilly 2009 doi:10.1016/j.jmarsys.2008.11.018; Cayula and Cornillon 1992 doi:10.1175/1520-0426(1992)009<0067:EDAFSI>2.0.CO;2",
             "boundaries": "none; region names elsewhere are reporting boxes, not claims", "caveat": OCEAN_CAVEAT, "dark_caveat": DARK_CAVEAT,
             "crs": f"{CRS_GEO} and {CRS_UTM_REGIONAL}"}
    pyogrio.write_dataframe(pd.DataFrame([about]), fronts_path, layer="about", driver="GPKG")
    n_lines, km_lines = len(gdf), float(gdf.length_km.sum())
    log(f"fronts gpkg: {n_lines} lines, {km_lines:,.0f} km")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 9))
    w, s_, e, n = ft.c, ft.f + fs[0] * ft.e, ft.c + fs[1] * ft.a, ft.f
    ax.set_facecolor("#e4e4e4")  # land, outside the AOI or no data
    im = ax.imshow(np.where(aoi_f, sst_showcase, np.nan), extent=(w, e, s_, n), cmap="YlOrRd", interpolation="nearest")
    gdf.plot(ax=ax, color="#1b1b1b", linewidth=0.45)
    aoi_gdf(DEFAULT_AOI).boundary.plot(ax=ax, color="#555555", linewidth=0.6)
    ax.set_xlim(w, e); ax.set_ylim(s_, n)
    plt.colorbar(im, ax=ax, shrink=0.7, label="sea surface temperature, degC (MUR v4.1 analysis, 09 UTC)")
    ax.set_title(f"Sea surface temperature and fronts, {showcase} (night with the most clear sea in the VIIRS record)", fontsize=11)
    ax.set_xlabel("longitude, degrees E"); ax.set_ylabel("latitude, degrees N")
    fig.text(0.01, 0.01, f"Black lines: front lines (skeleton of the front mask, lines under 10 km dropped). Front rule: {front_rule}. "
             "Grey: land, outside the AOI or no data. No boundaries shown. Ocean context describes the sea, not what any vessel "
             "does; 'dark' means only no AIS match, not illegal.", fontsize=6.5, wrap=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG_DIR / "ocean_showcase_fronts.png", dpi=130, bbox_inches="tight")
    plt.close(fig)

# ---------------------------------------------------------------------------------------------------------------------
# Summary
sea_rows = cells[cells.is_viirs_night]
by_region = sea_rows.groupby("region")
key = {
    "front_share_of_aoi_sea_mean": round(float(np.mean([i["front_share_sea"] for i in per_day.values() if "front_share_sea" in i])), 4),
    "front_freq_pixels_ge_0_25_share": round(float(np.nanmean(front_freq[sea_fine] >= 0.25)), 4),
    "sst_aoi_sea_min_c": round(min(i["sst_min"] for i in per_day.values() if "sst_min" in i), 2),
    "sst_aoi_sea_max_c": round(max(i["sst_max"] for i in per_day.values() if "sst_max" in i), 2),
    "sst_aoi_sea_mean_c": round(float(np.mean([i["sst_mean"] for i in per_day.values() if "sst_mean" in i])), 2),
    "sst_grad_median_c_per_km": round(float(np.nanmedian(grad_mean[sea_fine])), 4),
    "front_thresholds_c_per_km": {"low": round(low, 4), "high": round(high, 4)},
    "chl_median_mg_m3_by_region": {k: round(float(10 ** g.chl_log10_mean.median()), 3) for k, g in by_region if g.chl_log10_mean.notna().any()},
    "chl_valid_share_mean": round(float(sea_rows.chl_valid_share.mean()), 3) if "chl_valid_share" in sea_rows else None,
    "current_speed_median_ms": round(float(sea_rows.current_speed_ms.median()), 3) if "current_speed_ms" in sea_rows else None,
    "mld_median_m": round(float(sea_rows.mld_m.median()), 1) if "mld_m" in sea_rows else None,
    "ssh_anom_sd_m": round(float(sea_rows.ssh_anom_m.std()), 4) if "ssh_anom_m" in sea_rows else None,
    "wave_hs_median_m": round(float(sea_rows.wave_hs_m.median()), 2) if "wave_hs_m" in sea_rows else None,
    "wave_hs_max_m": round(float(sea_rows.wave_hs_m.max()), 2) if "wave_hs_m" in sea_rows else None,
    "wind_median_ms": round(float(sea_rows.wind_ms.median()), 2) if "wind_ms" in sea_rows else None,
    "dist_front_median_km": round(float(sea_rows.dist_front_km.median()), 1) if "dist_front_km" in sea_rows else None,
    "showcase_front_lines": n_lines, "showcase_front_km": round(km_lines),
    "sea_cells_model_grid": int(len(cell_idx)), "cell_nights": int(len(cells)), "radar_pass_cell_rows": int(len(passes)),
    "regional_medians": {k: {"sst_mean_c": round(float(g.sst_mean_c.median()), 2), "front_share": round(float(g.front_share.median()), 4),
                             "wave_hs_m": round(float(g.wave_hs_m.median()), 2) if "wave_hs_m" in g else None,
                             "current_speed_ms": round(float(g.current_speed_ms.median()), 3) if "current_speed_ms" in g else None}
                         for k, g in by_region if k},
}
found = {}
for src, test in (("mur_sst", lambda d: daily.mur_path(d).exists() and daily.load_sst_meta(d)["source"] == "mur"),
                  ("oisst_fallback", lambda d: daily.mur_path(d).exists() and daily.load_sst_meta(d)["source"] == "oisst"),
                  ("chl_gapfilled", lambda d: daily.load_chl(d) is not None), ("chl_daily_observed", lambda d: daily.load_chl(d, daily.CHL_OBSERVED) is not None),
                  ("rtofs_18utc", lambda d: daily.rtofs_path(night_at[d]).exists()), ("gfswave_18utc", lambda d: daily.wave_key(night_at[d])[1].exists()),
                  ("gfs_wind_18utc", lambda d: (daily.WEATHER_CACHE / f"gfs_{night_at[d]:%Y%m%d%H}_f000_wind10.grib2").exists())):
    ok = [d for d in days if test(d)]
    found[src] = {"requested": len(days), "found": len(ok), "missing": [d for d in days if d not in ok]}
found["gfswave_scene_hours"] = {"requested": len({daily.wave_key(t)[1] for t in scene_times}),
                                "found": len({daily.wave_key(t)[1] for t in scene_times if daily.wave_key(t)[1].exists()})}
chl_by_dataset = pd.Series([i.get("chl_dataset") for i in per_day.values()]).value_counts().to_dict()
sources = [
    {"name": "MUR v4.1 sea surface temperature (JPL PO.DAAC MEaSUREs), analysed_sst and mask", "dataset_id": daily.MUR_ID,
     "url": f"https://coastwatch.pfeg.noaa.gov/erddap/info/{daily.MUR_ID}/index.json", "version": "04.1 (id MUR-JPL-L4-GLOB-v04.1)",
     "resolution": "0.01 degree, daily 09 UTC", "dataset_doi": "https://doi.org/10.5067/GHGMR-4FJ04",
     "licence": "ERDDAP 'license' attribute: 'These data are available free of charge under the JPL PO.DAAC data policy. The data may be used and "
                "redistributed for free but is not intended for legal use, since it may contain inaccuracies ...' Acknowledgement requested: "
                "'These data were provided by JPL under support by NASA MEaSUREs program.'", "access_date": ACCESS_DATE,
     "days_used": found["mur_sst"]["found"]},
    {"name": "NOAA OISST v2.1 (fallback for a missing MUR day)", "dataset_id": "noaa-cdr-sea-surface-temp-optimum-interpolation-pds, data/v2.1/avhrr",
     "url": "https://noaa-cdr-sea-surface-temp-optimum-interpolation-pds.s3.amazonaws.com/?list-type=2&prefix=data/v2.1/avhrr/202609/",
     "version": "v02r01, preliminary files from 2026-09-23", "resolution": "0.25 degree daily", "licence": NODD_LICENCE,
     "licence_url": "https://registry.opendata.aws/noaa-cdr-oceanic/", "access_date": ACCESS_DATE,
     "days_used": found["oisst_fallback"]["found"]},
    {"name": "NOAA CoastWatch chlorophyll-a, DINEOF gap-filled, 2 km, science quality (VIIRS S-NPP, NOAA-20, Sentinel-3A OLCI)",
     "dataset_id": "noaacwNPPN20S3ASCIDINEOF2kmDaily", "url": "https://coastwatch.pfeg.noaa.gov/erddap/info/noaacwNPPN20S3ASCIDINEOF2kmDaily/index.json",
     "version": "time_coverage_end 2026-09-26 on the access date", "resolution": "1/48 degree (about 2 km), daily",
     "licence": "ERDDAP 'license' attribute: produced by NOAA, not subject to copyright in the United States, NOAA waives rights worldwide through "
                "CC0-1.0", "access_date": ACCESS_DATE, "days_used": chl_by_dataset.get("noaacwNPPN20S3ASCIDINEOF2kmDaily", 0)},
    {"name": "NOAA CoastWatch chlorophyll-a, DINEOF gap-filled, 9 km, science quality (VIIRS S-NPP, NOAA-20, Sentinel-3A OLCI)",
     "dataset_id": "nesdisNPPN20S3ASCIDINEOFDaily", "url": "https://coastwatch.pfeg.noaa.gov/erddap/info/nesdisNPPN20S3ASCIDINEOFDaily/index.json",
     "version": "time_coverage_end 2026-09-27 on the access date", "resolution": "1/12 degree, daily", "licence": "ERDDAP 'license' attribute: CC0-1.0 with the NOAA no-warranty text",
     "access_date": ACCESS_DATE, "days_used": chl_by_dataset.get("nesdisNPPN20S3ASCIDINEOFDaily", 0)},
    {"name": "NOAA CoastWatch chlorophyll-a, DINEOF gap-filled, 9 km, near-real-time (VIIRS S-NPP, NOAA-20)",
     "dataset_id": "nesdisVHNnoaaSNPPnoaa20NRTchlaGapfilledDaily",
     "url": "https://coastwatch.pfeg.noaa.gov/erddap/info/nesdisVHNnoaaSNPPnoaa20NRTchlaGapfilledDaily/index.json",
     "version": "time_coverage_end 2026-10-06 on the access date", "resolution": "1/12 degree, daily",
     "licence": "ERDDAP 'license' attribute: 'The data may be used and redistributed for free but is not intended for legal use ...' (NOAA no-warranty text)",
     "access_date": ACCESS_DATE, "days_used": chl_by_dataset.get("nesdisVHNnoaaSNPPnoaa20NRTchlaGapfilledDaily", 0)},
    {"name": "NOAA CoastWatch chlorophyll-a, daily 4 km, science quality, VIIRS S-NPP, not gap-filled (valid-day share)",
     "dataset_id": "nesdisVHNSQchlaDaily", "url": "https://coastwatch.pfeg.noaa.gov/erddap/info/nesdisVHNSQchlaDaily/index.json",
     "version": "time_coverage_end 2026-09-28 on the access date", "resolution": "0.0375 degree, daily",
     "licence": "ERDDAP 'license' attribute points to the NASA Earth science data policy (the URL it gives, "
                "science.nasa.gov/earth-science/earth-science-data/data-information-policy/, answered 404 on 2026-10-09; the NASA Earthdata "
                "page 'NASA promotes the full and open sharing of data' resolves) and asks for the acknowledgement "
                "'These data were provided by NOAA's Center for Satellite Applications and Research (STAR) and the CoastWatch program.'",
     "licence_url": "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-information-guidance",
     "access_date": ACCESS_DATE},
    {"name": "NOAA CoastWatch chlorophyll-a, daily 4 km, near-real-time, VIIRS S-NPP, not gap-filled (valid-day share, last days)",
     "dataset_id": "nesdisVHNchlaDaily", "url": "https://coastwatch.pfeg.noaa.gov/erddap/info/nesdisVHNchlaDaily/index.json",
     "version": "time_coverage_end 2026-10-05 on the access date", "resolution": "0.0375 degree, daily",
     "licence": "ERDDAP 'license' attribute: NASA Earth science data policy link plus the NOAA no-warranty text", "access_date": ACCESS_DATE},
    {"name": "NOAA RTOFS global nowcast, 2-D diagnostics (ssh, barotropic u and v, mixed-layer and boundary-layer thickness)",
     "dataset_id": "noaa-nws-rtofs-pds, rtofs.YYYYMMDD/rtofs_glo_2ds_nHHH_diag.nc", "url": f"{daily.RTOFS}/?list-type=2&prefix=rtofs.20260921/rtofs_glo_2ds_n",
     "version": "files of 2026-09-06 to 2026-10-02 (valid time read from each file's Date variable)", "resolution": "1/12 degree (0.08 degree) in the AOI, hourly nowcast",
     "licence": NODD_LICENCE, "licence_url": "https://registry.opendata.aws/noaa-rtofs/", "access_date": ACCESS_DATE},
    {"name": "NOAA GFS-Wave global 0.25 degree, significant height of combined wind waves and swell (HTSGW)",
     "dataset_id": "noaa-gfs-bdp-pds, gfs.YYYYMMDD/HH/wave/gridded/gfswave.tHHz.global.0p25.fFFF.grib2 (+ .idx)",
     "url": f"{daily.GFS}/gfs.20260920/18/wave/gridded/gfswave.t18z.global.0p25.f000.grib2.idx", "version": "operational GFS, cycles 00/06/12/18 UTC",
     "resolution": "0.25 degree, hourly steps", "licence": NODD_LICENCE, "licence_url": "https://registry.opendata.aws/noaa-gfs-bdp-pds/",
     "access_date": ACCESS_DATE},
    {"name": "NOAA GFS 0.25 degree 10 m wind (darkvessel.weather.gfs_wind)", "dataset_id": "noaa-gfs-bdp-pds, gfs.YYYYMMDD/HH/atmos/gfs.tHHz.pgrb2.0p25.fFFF",
     "url": "https://registry.opendata.aws/noaa-gfs-bdp-pds/", "version": "operational GFS", "resolution": "0.25 degree", "licence": NODD_LICENCE,
     "access_date": ACCESS_DATE},
    {"name": "Natural Earth 10 m land (coast buffer) and marine polygons (AOI)", "dataset_id": "ne_10m_land, ne_10m_geography_marine_polys",
     "url": "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/LICENSE.md", "version": "master branch of nvkelso/natural-earth-vector",
     "licence": "Public domain ('All versions of Natural Earth raster + vector map data found on this website are in the public domain', LICENSE.md; "
                "terms page https://www.naturalearthdata.com/about/terms-of-use/ says the same)", "access_date": ACCESS_DATE},
    {"name": "Belkin, I. M. and O'Reilly, J. E. (2009), An algorithm for oceanic front detection in chlorophyll and SST satellite imagery, "
             "Journal of Marine Systems 78, 319-326", "url": "https://doi.org/10.1016/j.jmarsys.2008.11.018",
     "version": "bibliographic record from https://api.crossref.org/works/10.1016/j.jmarsys.2008.11.018; full text not read in this session",
     "licence": "not applicable (reference)", "access_date": ACCESS_DATE},
    {"name": "Cayula, J.-F. and Cornillon, P. (1992), Edge detection algorithm for SST images, Journal of Atmospheric and Oceanic Technology 9, 67-80",
     "url": "https://doi.org/10.1175/1520-0426(1992)009<0067:EDAFSI>2.0.CO;2",
     "version": "bibliographic record from Crossref (api.crossref.org/works/...); the publisher page answered 403 to this session, full text not read",
     "licence": "not applicable (reference)", "access_date": ACCESS_DATE},
    {"name": "ecCodes Python bindings (GRIB2 JPEG2000 decoding of GFS-Wave), new pip dependency", "url": "https://pypi.org/project/eccodes/",
     "version": "eccodes 2.49.0 with eccodeslib 2.49.0.30", "licence": "Apache License 2.0 (PyPI page)", "access_date": ACCESS_DATE},
]
summary = {
    "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
    "window": {"nights": nights, "scene_utc_dates": scene_dates, "days": days,
               "scenes": [{"product_id": r.product_id, "start_utc": str(r.start_utc), "mission": r.mission} for r in scenes.itertuples()]},
    "showcase_night": {"night": showcase, "clear_sea_km2_all_regions": int(clear_total.max()),
                       "why": "largest total clear_sea_km2 over the six reporting boxes in data/viirs_nightly_by_region.csv",
                       "moon_illum_pct": moon.get(showcase), "front_lines": n_lines, "front_km": round(km_lines)},
    "front_rule": {"text": front_rule, "median_filter_px": 3, "quantiles": [Q_LOW, Q_HIGH], "low_c_per_km": round(low, 4), "high_c_per_km": round(high, 4),
                   "coast_exclusion_km": 2.0, "min_component_px": 3, "min_line_km": 10.0,
                   "pooled_from": f"every 5th fine pixel of AOI sea outside the coast buffer on {len(have_sst)} days"},
    "grids": {"fine": "0.01 degree, origin 99.16E 23.76N, 2698 x 2311 (SST, gradient, fronts)",
              "0.05": "0.05 degree, origin 99.15E 23.80N, 541 x 463 (chlorophyll, RTOFS)",
              "model": "0.25 degree, origin 99.0E 24.0N, 109 x 94 (tables, waves, wind)"},
    "sources": st.check_sources(sources, ACCESS_DATE), "days_found": found, "download_failures": failed,
    "per_day": per_day,
    "files": [{"path": str(p.relative_to(DATA_DIR.parent)), "bytes": p.stat().st_size} for p in
              [*files, fronts_path, DATA_DIR / "ocean_daily_cells.parquet", DATA_DIR / "ocean_radar_pass_cells.parquet", FIG_DIR / "ocean_showcase_fronts.png"]
              if p.exists()],
    "key_numbers": key,
    "caveat": OCEAN_CAVEAT,
    "limits": ["MUR is a merged multi-sensor analysis (ERDDAP comment: 'Multi-Resolution Variational Analysis (MRVA) method for "
               "interpolation'); where cloud hides the sea its 1 km detail is interpolated, so fronts on such days are likely smoother "
               "than reality (not quantified)",
               "front thresholds are relative (window quantiles of this sea's gradient), not an absolute front strength",
               "DINEOF chlorophyll is gap-filled; chl_valid_share says how often a direct retrieval anchored it",
               "RTOFS currents are depth-averaged (barotropic) model output, not surface currents; the SSH gradient is used as a proxy "
               "for eddy edges, not as a detection",
               "waves and wind are model fields at 0.25 degree, bilinear at cell centres",
               "no political boundaries in any layer; region names are reporting boxes"],
    "not_used": ["Global Fishing Watch fishing effort: research build only (CC BY-NC 4.0), pulled by scripts/27_gfw_pull.py, never mixed "
                 "into these open layers",
                 "Copernicus Marine surface currents (would need CMEMS credentials) as an alternative to RTOFS barotropic currents: not attempted",
                 "NASA PO.DAAC direct MUR access (EARTHDATA_TOKEN) not needed: the CoastWatch ERDDAP copy served every day"],
    "dark_caveat": DARK_CAVEAT,
    "sampling": {"sst_front_cells": "daily SST and gradient statistics per model cell over the cell's fine pixels inside the AOI polygon",
                 "chl_valid_share": "retrieval pixels whose centre is AOI sea outside the 2 km coast buffer",
                 "waves_wind": WAVE_WIND, "rtofs": "nearest native cell for 0.05 degree rasters, bin mean of native cells for 0.25 degree cells",
                 "region": "reporting box of the cell centre, 'other' outside every box"},
}
(DATA_DIR / "ocean_daily_summary.json").write_text(json.dumps(summary, indent=1, default=str))
print(json.dumps(key, indent=1))
log("done")
