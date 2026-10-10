"""Expected activity: how many lit boats (VIIRS) or radar vessel candidates (Sentinel-1C/1D) a 0.25 degree cell should
hold on a night or radar pass, given the sea and the weather; cross-validated against two baselines; activity-anomaly
cells (E15) only where the model earns them.

Targets (src/darkvessel/ocean/model.py; plan: docs/ocean_context_plan.md section 2):
  viirs   clear-sky lit vessel candidates per cell-night (data/viirs_lights_all.gpkg, class lit_vessel_candidate,
          quality clear; recurring lights and lights under cloud left out). Exposure = clear searched sea seen: for
          every satellite pass (granules less than 30 min apart), the cloud-mask share of clear pixels over the cell's
          searched sea (sum over the pass's granules of clear over sea pixel counts, scripts/15_viirs_lights.py --clear)
          times the cell's searched sea area (sea beyond about 2 km of land), summed over the night's passes.
  radar   radar vessel candidates (high and medium, after the clutter rules) per cell-scene. Exposure = AOI sea of the
          cell inside the scene footprint (0.01 degree sea cells of the static build), scaled per scene by the
          detector's tested sea over the footprint's AOI sea when that is below 1 (1 km land buffer, borders); a scene
          whose tested sea is under half of its footprint's AOI sea is left out (its exposure cannot be placed).
Rows with exposure of at least 25 km2 are tested; every cell-night of data/ocean_daily_cells.parquet still gets an
expected rate from the model fitted on all tested rows.

Features (never the Marine Regions EEZ attributes, never a shipping magnitude: model.check_features refuses them):
  position lon, lat; region (reporting box, categorical); static: depth (mean, sd, shares shallower than 50 and 200 m),
  slope, distance to coast and to a major port, World Bank/IMF presence shares (all, fishing, oil and gas, passenger);
  daily: SST mean and spatial sd, SST gradient, front share, distance to front, log10 chlorophyll, SSH anomaly and
  gradient, current speed, mixed layer; weather: 10 m wind, significant wave height; moon illumination (VIIRS);
  radar geometry (radar): pass direction, mission, incidence angle at the cell centre (a per-scene plane fitted to
  every detected object's incidence angle, so cells without objects get one too).
Models: global rate; climatology (cell rate shrunk to the region rate by empirical Bayes); static trees (position,
region, static); full trees (everything). Histogram gradient boosting, Poisson loss, exposure as weight.
Validation: leave-one-week-out and leave-one-region-out. D2 of every model against both baselines on out-of-fold
predictions, pooled and per fold (spread); calibration deciles; grouped permutation importance on held-out folds.
Anomalies: Poisson tails of the observed count against the full model's leave-one-week-out expectation, two-sided p,
Benjamini-Hochberg false discovery rate at 0.01 over every tested cell-night (cell-scene) in calm weather (wind below
12 m/s). Flags only if the full model beats the climatology out of sample (pooled D2 above 0 and above 0 in more than
half of the week folds); otherwise none, and the JSON says so. A negative binomial tail with the moment estimate of the
overdispersion is reported next to each p-value: flag_robust keeps the flags that are also significant (BH below
0.01) under it. The Poisson flags assume the model's only error is Poisson noise; where the counts are overdispersed
against the model their 1 % false-discovery statement does not hold, and flag_robust is the set to review.

Inputs: data/viirs_lights_all.gpkg, data/cache/viirs/*.json, *.clear.npz, seagrid.npz (scripts/15_viirs_lights.py);
        data/detections_regional_all.gpkg (objects, scenes_processed_4326); data/ocean_static_cells.parquet,
        data/ocean_daily_cells.parquet, data/ocean_radar_pass_cells.parquet; data/cache/ocean/fine/depth_m.npy
Output: data/expected_activity.parquet (cell-night and cell-scene expected, observed, exposure, p-values, flag, caveat),
        data/expected_activity.json (metrics, folds, calibration, importance, rules, sources, caveat),
        data/expected_activity_anomalies.gpkg (E15 events, layers expected_activity_anomalies_4326 and _utm49n, about;
        by default only the flag_robust cells, --events all writes every Poisson-BH flag; absent when nothing is
        flagged), docs/figures/expected_activity.png
        Checkpoints: data/cache/ocean/model/{viirs,radar}_table.parquet (the joined tables; --rebuild redoes them)
Usage: python scripts/34_expected_activity.py [--targets viirs,radar] [--threads 2] [--repeats 3] [--events robust|all]
                                             [--rebuild] [--figure-only]
"""

import argparse
import os

ap = argparse.ArgumentParser()
ap.add_argument("--targets", default="viirs,radar", help="comma list of viirs, radar")
ap.add_argument("--threads", type=int, default=2, help="OpenMP threads for the trees (the live watcher and CNN run need CPU)")
ap.add_argument("--repeats", type=int, default=3, help="permutation repeats per feature group and fold")
ap.add_argument("--rebuild", action="store_true", help="rebuild the joined tables instead of reading the checkpoints")
ap.add_argument("--figure-only", action="store_true", help="redraw the figure from data/expected_activity.json")
ap.add_argument("--events", choices=("robust", "all"), default="robust",
                help="E15 events written to the GeoPackage: flag_robust only (default) or every Poisson-BH flag")
args = ap.parse_args()
os.environ.setdefault("OMP_NUM_THREADS", str(args.threads))  # before numpy and sklearn load

import hashlib  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import darkvessel  # noqa: E402,F401  sets PROJ_DATA before rasterio and pyogrio load
import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
import pyogrio  # noqa: E402
from shapely.geometry import box  # noqa: E402

from darkvessel.config import CRS_GEO, CRS_UTM_REGIONAL, DATA_DIR, DEFAULT_AOI, FIG_DIR  # noqa: E402
from darkvessel.coverage import cell_area_km2  # noqa: E402
from darkvessel.io import write_dual_crs  # noqa: E402
from darkvessel.ocean import model as m  # noqa: E402
from darkvessel.ocean.grid import OCEAN_CACHE, OCEAN_CAVEAT, aoi_mask, cell_index, fine_grid, model_grid  # noqa: E402

try:
    from darkvessel.config import PRODUCT_CAVEAT  # noqa: E402
except ImportError:  # board decision D4.2: identical fallback until the constant exists everywhere
    PRODUCT_CAVEAT = (
        "'Dark' means only that no AIS position was matched to this radar contact. It does not "
        "mean illegal. Many vessels are not required to carry AIS, AIS can be off for lawful "
        "reasons, and both satellite and terrestrial AIS have blind spots: satellite AIS misses "
        "messages in busy coastal waters, and shore receivers cover only the waters within their "
        "radio range. Treat every unmatched contact as a lead for review, not as evidence of "
        "wrongdoing. An AIS gap is not proof of intent."
    )

t0 = time.time()
log = lambda s: print(f"[{time.time() - t0:6.0f}s] {s}", flush=True)  # noqa: E731
VIIRS_CACHE = DATA_DIR / "cache" / "viirs"
CK = OCEAN_CACHE / "model"
OUT_PARQUET, OUT_JSON = DATA_DIR / "expected_activity.parquet", DATA_DIR / "expected_activity.json"
OUT_GPKG, OUT_FIG = DATA_DIR / "expected_activity_anomalies.gpkg", FIG_DIR / "expected_activity.png"
PASS_GAP_MIN = 30
MIN_TESTED_SHARE = 0.5  # radar: tested sea over the footprint's AOI sea below this -> scene left out
ANOMALY_CAVEAT = ("An activity anomaly is a difference between a count of detections in a cell and a model's expectation for "
                  "that cell, night or pass. It is not a count of vessels and not evidence of wrongdoing: model error, "
                  "weather, cloud, moonlight, fleet movements and the sensors' limits (unlit boats for VIIRS, small boats "
                  "for radar) all produce it. A lead for review only.")
CAVEAT = OCEAN_CAVEAT + " " + ANOMALY_CAVEAT
EVENT_CAVEAT = PRODUCT_CAVEAT + " " + ANOMALY_CAVEAT
STATIC_FEATURES = ["depth_mean_m", "depth_std_m", "share_shallower_50m", "share_shallower_200m", "slope_mean_m_per_km",
                   "dist_coast_km", "dist_port_km", "ship_presence_share_all", "ship_presence_share_fishing",
                   "ship_presence_share_oilgas", "ship_presence_share_passenger"]
DAILY_FEATURES = ["sst_mean_c", "sst_sd_c", "sst_grad_mean", "front_share", "dist_front_km", "chl_log10_mean", "ssh_anom_m",
                  "ssh_grad", "current_speed_ms", "mld_m", "wind_ms", "wave_hs_m"]
FEATURES = {"viirs": {"static": ["lon", "lat", "region", *STATIC_FEATURES],
                      "full": ["lon", "lat", "region", *STATIC_FEATURES, *DAILY_FEATURES, "moon_illum_pct"]},
            "radar": {"static": ["lon", "lat", "region", *STATIC_FEATURES],
                      "full": ["lon", "lat", "region", *STATIC_FEATURES, *DAILY_FEATURES, "pass_dir", "mission", "inc_angle_cell_deg"]}}
TARGET_LABEL = {"viirs": "clear-sky lit vessel candidates per 1,000 km2 of clear searched sea, per cell-night (VIIRS DNB, all passes)",
                "radar": "radar vessel candidates (high, medium) per 1,000 km2 of imaged AOI sea, per cell-scene (Sentinel-1C/1D)"}


def write_parquet(df: pd.DataFrame, path: Path, extra: dict | None = None) -> None:
    tbl = pa.Table.from_pandas(df, preserve_index=False)
    meta = {**(tbl.schema.metadata or {}), b"caveat": CAVEAT.encode(), **{k.encode(): str(v).encode() for k, v in (extra or {}).items()}}
    pq.write_table(tbl.replace_schema_metadata(meta), path, compression="zstd")


# ----------------------------------------------------------------------------------------------------------------------
# Tables


def viirs_table() -> tuple[pd.DataFrame, dict]:
    """Cell-night rows of the daily table with the clear-sky lit-candidate count and the clear searched sea exposure."""
    ck = CK / "viirs_table.parquet"
    if ck.exists() and not args.rebuild:
        return pd.read_parquet(ck), json.loads((CK / "viirs_table.json").read_text())
    tr, shape = model_grid()
    z = np.load(VIIRS_CACHE / "seagrid.npz")
    res, west, north, mask = float(z["res"]), float(z["west"]), float(z["north"]), z["mask"]
    sr, sc = np.nonzero(mask)
    r25, c25, inside = cell_index(tr, shape, west + (sc + 0.5) * res, north - (sr + 0.5) * res)
    sea_n = np.zeros(shape)
    np.add.at(sea_n, (r25[inside], c25[inside]), 1)
    sea_km2 = (cell_area_km2(tr, shape) * sea_n / (abs(tr.a) / res) ** 2).ravel()

    metas = [json.loads(p.read_text()) for p in sorted(VIIRS_CACHE.glob("*.json"))]
    g = pd.DataFrame({"granule": [x["granule"] for x in metas], "satellite": [x["satellite"] for x in metas],
                      "start": pd.to_datetime([x["start_utc"] for x in metas], utc=True)}).sort_values(["satellite", "start"])
    g["pass_no"] = g.groupby("satellite").start.diff().gt(pd.Timedelta(minutes=PASS_GAP_MIN)).groupby(g.satellite).cumsum()
    g["night"] = (g.start + pd.Timedelta(hours=7) - pd.Timedelta(hours=12)).dt.strftime("%Y-%m-%d")
    expo, n_missing = {}, 0
    for (sat, pno), part in g.groupby(["satellite", "pass_no"]):
        clear = np.zeros(shape[0] * shape[1])
        seen = np.zeros(shape[0] * shape[1])
        for gid in part.granule:
            p = VIIRS_CACHE / f"{gid}.clear.npz"
            if not p.exists():
                n_missing += 1
                continue
            c = np.load(p)
            np.add.at(clear, c["cells"], c["clear_n"])
            np.add.at(seen, c["cells"], c["sea_n"])
        share = np.divide(clear, seen, out=np.zeros_like(clear), where=seen > 0)
        night = part.night.iloc[0]
        expo[night] = expo.get(night, 0.0) + share * sea_km2
    windows = g.groupby("night").start.agg(["min", "max"])

    lights = pyogrio.read_dataframe(DATA_DIR / "viirs_lights_all.gpkg", layer="viirs_lights_4326", read_geometry=False,
                                    columns=["night", "lat", "lon", "class", "quality"],
                                    where="class = 'lit_vessel_candidate' AND quality = 'clear'")
    lr, lc, lin = cell_index(tr, shape, lights.lon.to_numpy(float), lights.lat.to_numpy(float))
    lights = pd.DataFrame({"night": lights.night.astype(str).to_numpy()[lin], "flat": (lr * shape[1] + lc)[lin]})
    counts = lights.groupby(["night", "flat"]).size()

    daily = pd.read_parquet(DATA_DIR / "ocean_daily_cells.parquet")
    daily = daily[daily.is_viirs_night].copy()
    daily["night"] = daily.night.astype(str)
    static = pd.read_parquet(DATA_DIR / "ocean_static_cells.parquet")
    keep_static = ["row", "col", *STATIC_FEATURES]
    t = daily.merge(static[keep_static], on=["row", "col"], how="left")
    t["flat"] = t.row * shape[1] + t.col
    t["exposure_km2"] = [float(expo[n][f]) if n in expo else 0.0 for n, f in zip(t.night, t.flat)]
    t["count"] = counts.reindex(pd.MultiIndex.from_arrays([t.night, t.flat])).fillna(0).to_numpy()
    t["time_start_utc"] = t.night.map(windows["min"]).astype(str)
    t["time_end_utc"] = t.night.map(windows["max"] + pd.Timedelta(seconds=90)).astype(str)
    t["unit_id"] = t.night
    in_table = counts[counts.index.get_level_values(0).isin(set(t.night))]
    joined = int(t["count"].sum())
    meta = {"granules": int(len(g)), "granules_without_clear_counts": n_missing, "passes": int(g.groupby(["satellite", "pass_no"]).ngroups),
            "satellites": sorted(g.satellite.unique().tolist()), "nights": sorted(t.night.unique().tolist()),
            "clear_lit_candidates": int(len(lights)), "clear_lit_candidates_in_table_cells": joined,
            "clear_lit_candidates_outside_table_cells": int(in_table.sum()) - joined,
            "searched_sea_km2_aoi_cells": float(sea_km2.sum())}
    CK.mkdir(parents=True, exist_ok=True)
    t.drop(columns=["caveat"], errors="ignore").to_parquet(ck, index=False)
    (CK / "viirs_table.json").write_text(json.dumps(meta, indent=1))
    return t, meta


def radar_table() -> tuple[pd.DataFrame, dict]:
    """Cell-scene rows of the radar pass table with the candidate count, the imaged AOI sea and the incidence angle."""
    from rasterio import features

    ck = CK / "radar_table.parquet"
    if ck.exists() and not args.rebuild:
        return pd.read_parquet(ck), json.loads((CK / "radar_table.json").read_text())
    mtr, mshape = model_grid()
    ftr, fshape = fine_grid()
    depth = np.load(OCEAN_CACHE / "fine" / "depth_m.npy")
    if depth.shape != fshape:
        raise ValueError(f"fine depth cache {depth.shape} does not match the fine grid {fshape}")
    sea_box = np.isfinite(depth)
    sea_aoi = sea_box & aoi_mask(ftr, fshape)
    farea = cell_area_km2(ftr, fshape)
    flon = ftr.c + (np.arange(fshape[1]) + 0.5) * ftr.a
    flat_ = ftr.f + (np.arange(fshape[0]) + 0.5) * ftr.e
    rr, cc, _ = cell_index(mtr, mshape, flon[None, :].repeat(fshape[0], 0), flat_[:, None].repeat(fshape[1], 1))
    fine_to_model = (rr * mshape[1] + cc).astype(np.int64)

    scenes = pyogrio.read_dataframe(DATA_DIR / "detections_regional_all.gpkg", layer="scenes_processed_4326",
                                    columns=["product_id", "scene_id", "mission", "pass_dir", "start_utc", "stop_utc", "tested_km2"])
    det = pyogrio.read_dataframe(DATA_DIR / "detections_regional_all.gpkg", layer="detections_regional_4326", read_geometry=False,
                                 columns=["scene_id", "confidence", "lon", "lat", "inc_angle_deg"])
    dr, dc, din = cell_index(mtr, mshape, det.lon.to_numpy(float), det.lat.to_numpy(float))
    det["flat"] = np.where(din, dr * mshape[1] + dc, -1)
    cand = det[det.confidence.isin(["high", "medium"]) & (det.flat >= 0)]
    counts = cand.groupby(["scene_id", "flat"]).size()

    by_scene = {k: v for k, v in det[["scene_id", "lon", "lat", "inc_angle_deg"]].groupby("scene_id")}
    rows, scale_rows = [], []
    for _, sc in scenes.iterrows():
        fp = features.rasterize([(sc.geometry, 1)], out_shape=fshape, transform=ftr, dtype="uint8").astype(bool)
        hit = fp & sea_aoi
        aoi_km2 = float(farea[hit].sum())
        ratio = float(sc.tested_km2) / aoi_km2 if aoi_km2 > 0 else float("nan")
        scale = min(1.0, ratio) if ratio >= MIN_TESTED_SHARE else 0.0
        area = np.bincount(fine_to_model[hit], weights=farea[hit], minlength=mshape[0] * mshape[1])
        # incidence angle: plane fitted to every object of the scene (all classes), evaluated at the cell centres
        o = by_scene.get(sc.scene_id, det.iloc[:0])
        o = o[np.isfinite(o.inc_angle_deg.to_numpy(float))]
        coef = None
        if len(o) >= 30:
            A = np.c_[np.ones(len(o)), o.lon.to_numpy(float), o.lat.to_numpy(float)]
            coef, *_ = np.linalg.lstsq(A, o.inc_angle_deg.to_numpy(float), rcond=None)
            resid = o.inc_angle_deg.to_numpy(float) - A @ coef
        idx = np.nonzero(area > 0)[0]
        clon = mtr.c + (idx % mshape[1] + 0.5) * mtr.a
        clat = mtr.f + (idx // mshape[1] + 0.5) * mtr.e
        inc = np.clip(coef[0] + coef[1] * clon + coef[2] * clat, 25.0, 50.0) if coef is not None else np.full(len(idx), np.nan)
        rows.append(pd.DataFrame({"scene_id": sc.scene_id, "flat": idx, "exposure_raw_km2": area[idx], "exposure_km2": area[idx] * scale,
                                  "inc_angle_cell_deg": inc, "pass_dir": sc.pass_dir, "time_start_utc": str(sc.start_utc),
                                  "time_end_utc": str(sc.stop_utc)}))
        scale_rows.append({"scene_id": sc.scene_id, "tested_km2": float(sc.tested_km2), "footprint_aoi_sea_km2": aoi_km2,
                           "tested_over_footprint_aoi_sea": ratio, "scale": scale,
                           "inc_plane_rmse_deg": float(np.sqrt(np.mean(resid ** 2))) if coef is not None else None})
    ex = pd.concat(rows, ignore_index=True)
    passes = pd.read_parquet(DATA_DIR / "ocean_radar_pass_cells.parquet")
    passes["flat"] = passes.row * mshape[1] + passes.col
    static = pd.read_parquet(DATA_DIR / "ocean_static_cells.parquet")
    t = passes.merge(ex, on=["scene_id", "flat"], how="left").merge(static[["row", "col", *STATIC_FEATURES]], on=["row", "col"], how="left")
    t["exposure_km2"] = t.exposure_km2.fillna(0.0)
    t["count"] = counts.reindex(pd.MultiIndex.from_arrays([t.scene_id, t.flat])).fillna(0).to_numpy()
    t["night"] = t.utc_date.astype(str)
    t["unit_id"] = t.scene_id
    sc_t = pd.DataFrame(scale_rows)
    meta = {"scenes": int(len(scenes)), "candidates_high_medium": int(len(cand)), "candidates_in_table_cells": int(t["count"].sum()),
            "tested_over_footprint_aoi_sea": {"median": float(sc_t.tested_over_footprint_aoi_sea.median()),
                                              "min": float(sc_t.tested_over_footprint_aoi_sea.min()),
                                              "max": float(sc_t.tested_over_footprint_aoi_sea.max()),
                                              "scenes_scaled_below_1": int((sc_t.scale < 1).sum())},
            "scenes_left_out": records(sc_t[sc_t.scale == 0][["scene_id", "tested_km2", "footprint_aoi_sea_km2"]]),
            "inc_plane_rmse_deg_median": float(sc_t.inc_plane_rmse_deg.median()), "dates": sorted(t.night.unique().tolist())}
    CK.mkdir(parents=True, exist_ok=True)
    t.drop(columns=["caveat"], errors="ignore").to_parquet(ck, index=False)
    (CK / "radar_table.json").write_text(json.dumps(meta, indent=1))
    return t, meta


# ----------------------------------------------------------------------------------------------------------------------
# Fit, validate, flag


def records(df: pd.DataFrame, digits: int = 4) -> list[dict]:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(digits)
    return json.loads(out.to_json(orient="records"))


def run_target(name: str, table: pd.DataFrame, tmeta: dict) -> tuple[pd.DataFrame, dict]:
    feats = {k: m.check_features(v) for k, v in FEATURES[name].items()}
    tested = (table.exposure_km2 >= m.EXPOSURE_MIN_KM2).to_numpy()
    fit = table[tested].reset_index(drop=True)
    log(f"{name}: {len(table):,} rows, {len(fit):,} tested (exposure >= {m.EXPOSURE_MIN_KM2:g} km2), count {fit['count'].sum():,.0f}, "
        f"exposure {fit.exposure_km2.sum():,.0f} km2")
    makers = {"global_rate": lambda: m.GlobalRate(), "climatology": lambda: m.Climatology(),
              "static_trees": lambda: m.RateTrees(feats["static"]), "full_trees": lambda: m.RateTrees(feats["full"])}
    designs = {"week": m.week_folds(fit.night), "region": m.region_folds(fit.region)}
    count, expo = fit["count"].to_numpy(float), fit.exposure_km2.to_numpy(float)
    res = {"target": TARGET_LABEL[name], "table": tmeta, "rows": int(len(table)), "tested_rows": int(len(fit)),
           "count_tested": float(count.sum()), "exposure_tested_km2": float(expo.sum()),
           "observed_rate_per_1000km2": float(count.sum() / expo.sum() * 1000), "zero_share_tested": float(np.mean(count == 0)),
           "features": {k: m.group_features(v) for k, v in feats.items()}, "cv": {}}
    oof_all = {}
    for dname, folds in designs.items():
        oof = {}
        for mname, mk in makers.items():
            ts = time.time()
            oof[mname] = m.cross_validate(fit, mk, folds).oof_rate
            log(f"{name} {dname}-CV {mname}: {time.time() - ts:.0f}s")
        pooled, per = m.compare_oof(count, expo, oof, folds)
        fold_rows = pd.Series(folds).value_counts().sort_index()
        res["cv"][dname] = {"folds": {str(k): int(v) for k, v in fold_rows.items()}, "pooled": records(pooled), "per_fold": records(per)}
        oof_all[dname] = oof
        for _, r in pooled[pooled.baseline == "climatology"].iterrows():
            log(f"  {dname}: {r.model} D2 vs climatology {r.d2:+.3f} (folds better {r.folds_better}/{r.folds})")
        for _, r in pooled[pooled.baseline == "global_rate"].iterrows():
            log(f"  {dname}: {r.model} D2 vs global {r.d2:+.3f}")

    wk = res["cv"]["week"]["pooled"]
    vs_clim = next(r for r in wk if r["model"] == "full_trees" and r["baseline"] == "climatology")
    fold_d2 = [r["d2"] for r in res["cv"]["week"]["per_fold"] if r["model"] == "full_trees" and r["baseline"] == "climatology"]
    gate = m.beats_baseline(vs_clim["d2"], fold_d2)
    res["gate"] = {"rule": "the full model beats the climatology out of sample: pooled leave-one-week-out D2 against the "
                           "climatology above 0 and above 0 in more than half of the week folds",
                   "d2_full_vs_climatology_week": vs_clim["d2"], "fold_d2": fold_d2, "passed": gate}
    log(f"{name}: gate {'passed' if gate else 'NOT passed'} (D2 vs climatology {vs_clim['d2']:+.3f}, folds {fold_d2})")

    # Calibration
    res["calibration"] = {
        "full_trees_week": records(m.calibration_deciles(count, expo, oof_all["week"]["full_trees"])),
        "climatology_week": records(m.calibration_deciles(count, expo, oof_all["week"]["climatology"])),
        "full_trees_region": records(m.calibration_deciles(count, expo, oof_all["region"]["full_trees"]))}
    # Grouped permutation importance (held-out folds)
    groups = m.group_features(feats["full"])
    res["permutation_importance"] = {}
    for dname, folds in designs.items():
        ts = time.time()
        imp = m.permutation_importance_groups(fit, makers["full_trees"], folds, groups, n_repeats=args.repeats, seed=0)
        res["permutation_importance"][dname] = records(imp)
        log(f"{name} importance ({dname}): {time.time() - ts:.0f}s; " + ", ".join(f"{r.group} {r.share_of_deviance:+.3f}" for r in imp.itertuples()))

    # Anomalies on the leave-one-week-out expectation
    mu = oof_all["week"]["full_trees"] * expo
    mu_clim = oof_all["week"]["climatology"] * expo
    calm = (fit.wind_ms < m.CALM_WIND_MS).to_numpy()
    disp = m.overdispersion(count, mu)
    tab, asum = m.gated_anomalies(count, mu, gate, eligible=calm, reason="" if gate else
                                  "the full model does not beat the climatology baseline out of sample; no anomaly is flagged")
    nb_p = m.nb_two_sided(count, mu, disp["nb_alpha"] or 0.0)
    nb_q = m.bh_adjust(np.where(tab.tested.to_numpy(), nb_p, np.nan))
    flagged = tab.flag.to_numpy() != "none"
    robust = np.where(flagged & (nb_q < m.P_FLAG), tab.flag.to_numpy(), "none")
    asum.update({"family": "tested rows (exposure >= 25 km2) with a finite expectation in calm weather (wind_ms < 12 m/s)",
                 "calm_rows": int(calm.sum()), "rows_not_calm": int((~calm).sum()), "overdispersion": disp,
                 "nb_check": "negative binomial two-sided tail with alpha = overdispersion.nb_alpha, BH over the same family",
                 "flags_also_significant_under_nb": int(np.sum(flagged & (nb_q < m.P_FLAG))),
                 "flag_robust_high": int(np.sum(robust == "high")), "flag_robust_low": int(np.sum(robust == "low")),
                 "poisson_fdr_valid": bool((disp["pearson_dispersion"] or 0) <= 1.5),  # project rule of thumb, stated in the JSON
                 "note": ("Pearson dispersion of the counts against the model's expectation is "
                          f"{disp['pearson_dispersion']:.2f} (1 for Poisson): the Poisson p-values are too small, the BH 1 % "
                          "false-discovery statement does not hold for flag, and flag_robust (also significant under the "
                          "negative binomial tail) is the set to review.") if (disp["pearson_dispersion"] or 0) > 1.5 else
                         "counts close to Poisson against the model (Pearson dispersion at most 1.5)",
                 "would_flag_under_nb_bh": int(np.sum(nb_q < m.P_FLAG)),
                 "high_z_at_least_3": int(np.sum(flagged & (tab.flag.to_numpy() == "high") & (tab.z.to_numpy() >= 3)))})
    res["anomalies"] = asum
    log(f"{name}: anomalies {asum}")

    # Final model on every tested row; expected rate for every row of the table
    ts = time.time()
    final = makers["full_trees"]().fit(fit)
    rate_all = final.predict_rate(table)
    log(f"{name}: final fit and prediction {time.time() - ts:.0f}s")
    sample = fit.sample(min(len(fit), 4000), random_state=0)
    pdp = {}
    for f in ["wind_ms", "moon_illum_pct", "depth_mean_m", "dist_coast_km", "sst_grad_mean", "chl_log10_mean", "inc_angle_cell_deg"]:
        if f in feats["full"]:
            pdp[f] = records(m.partial_dependence(final, sample, f, n_grid=8))
    pdp["wind_ms_by_region"] = {str(r): records(m.partial_dependence(final, part, "wind_ms", n_grid=6))
                                for r, part in fit.groupby("region") if len(part) >= 200}
    res["partial_dependence"] = pdp

    # Output rows
    out = table[["night", "unit_id", "row", "col", "lon", "lat", "region", "time_start_utc", "time_end_utc", "exposure_km2",
                 "wind_ms"]].copy()
    out.insert(0, "target", name)
    out["cell_id"] = "r" + out.row.astype(str) + "c" + out.col.astype(str)
    out["tested"] = tested
    out["observed"] = np.where(tested, table["count"].to_numpy(float), np.nan)
    out["expected_rate_all_per_1000km2"] = rate_all * 1000
    for c, v in (("expected", mu), ("expected_rate_per_1000km2", oof_all["week"]["full_trees"] * 1000), ("expected_climatology", mu_clim),
                 ("z", tab.z.to_numpy()), ("p_high", tab.p_high.to_numpy()), ("p_low", tab.p_low.to_numpy()),
                 ("p_two_sided", tab.p_two_sided.to_numpy()), ("q_bh", tab.q_bh.to_numpy()), ("p_two_sided_nb", nb_p), ("q_bh_nb", nb_q)):
        col = np.full(len(out), np.nan)
        col[tested] = v
        out[c] = col
    out["calm"] = out.wind_ms < m.CALM_WIND_MS
    fl = np.full(len(out), "none", dtype=object)
    fl[tested] = tab.flag.to_numpy()
    out["flag"] = fl.astype(str)
    fr = np.full(len(out), "none", dtype=object)
    fr[tested] = robust
    out["flag_robust"] = fr.astype(str)
    out["caveat"] = CAVEAT
    return out, res


def events(out: pd.DataFrame, results: dict, which: str = "robust") -> gpd.GeoDataFrame | None:
    ev = out[(out.flag_robust if which == "robust" else out.flag) != "none"].copy()
    if not len(ev):
        return None
    half = 0.125
    ev["event_id"] = ["E15-model-" + hashlib.sha1(f"{t}|{u}|{c}".encode()).hexdigest()[:12]
                      for t, u, c in zip(ev.target, ev.unit_id, ev.cell_id)]
    ev["code"], ev["event_type"], ev["source"], ev["research_only"] = "E15", "ACTIVITY_ANOMALY", "app", False
    ev["direction"] = ev.flag
    ev["start_utc"], ev["end_utc"] = ev.time_start_utc, ev.time_end_utc
    ev["cell_ids"] = ev.cell_id.map(lambda c: json.dumps([c]))
    ev["robust"] = ev.flag_robust != "none"
    ev["params"] = [json.dumps({"p_flag_fdr": m.P_FLAG, "control": "Benjamini-Hochberg", "calm_wind_ms_below": m.CALM_WIND_MS,
                                "exposure_min_km2": m.EXPOSURE_MIN_KM2, "expectation": "full trees, leave-one-week-out",
                                "nb_alpha": results[t]["anomalies"]["overdispersion"]["nb_alpha"]}) for t in ev.target]
    ev["rule_text"] = ("Observed count in the cell on this night or pass against the expected-activity model's leave-one-week-out "
                       "expectation: Poisson two-sided p, Benjamini-Hochberg false discovery rate below 0.01 over all tested "
                       "cell-nights (cell-scenes) in calm weather (wind below 12 m/s); only when the model beats the cell "
                       "climatology out of sample. robust = also significant (BH below 0.01) under a negative binomial tail "
                       "with the estimated overdispersion; review robust events first, because the counts are overdispersed "
                       "against the model and the Poisson false-discovery statement does not hold for the rest.")
    ev["model_id"] = MODEL_ID
    ev["src"] = "scripts/34_expected_activity.py"
    ev["caveat"] = EVENT_CAVEAT
    keep = ["event_id", "code", "event_type", "target", "direction", "start_utc", "end_utc", "unit_id", "cell_id", "cell_ids", "region",
            "lon", "lat", "observed", "expected", "expected_rate_per_1000km2", "expected_climatology", "exposure_km2", "z", "p_two_sided",
            "q_bh", "p_two_sided_nb", "q_bh_nb", "robust", "wind_ms", "params", "rule_text", "model_id", "source",
            "research_only", "src", "caveat"]
    geom = [box(x - half, y - half, x + half, y + half) for x, y in zip(ev.lon, ev.lat)]
    g = gpd.GeoDataFrame(ev[keep].reset_index(drop=True), geometry=geom, crs=CRS_GEO)
    return g.sort_values(["robust", "q_bh"], ascending=[False, True]).reset_index(drop=True)


# ----------------------------------------------------------------------------------------------------------------------
# Figure


def figure(summary: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    targets = [t for t in ("viirs", "radar") if t in summary["targets"]]
    ink, muted, grid = "#0b0b0b", "#52514e", "#e4e3df"
    design_col = {"week": "#2a78d6", "region": "#eb6834"}
    fig, axes = plt.subplots(len(targets), 2, figsize=(13, 5.0 * len(targets)), squeeze=False)
    for i, t in enumerate(targets):
        r = summary["targets"][t]
        ax = axes[i, 0]
        models = ["climatology", "static_trees", "full_trees"]
        x = np.arange(len(models))
        for j, (dname, col) in enumerate(design_col.items()):
            pooled = {p["model"]: p for p in r["cv"][dname]["pooled"] if p["baseline"] == "global_rate"}
            per = [p for p in r["cv"][dname]["per_fold"] if p["baseline"] == "global_rate"]
            xs = x + (j - 0.5) * 0.36
            ax.bar(xs, [pooled[mm]["d2"] for mm in models], width=0.32, color=col, label=f"leave-one-{dname}-out (pooled; dots = folds)",
                   edgecolor="white", linewidth=2)
            for k, mm in enumerate(models):
                vals = [p["d2"] for p in per if p["model"] == mm]
                ax.scatter(np.full(len(vals), xs[k]), vals, s=16, color=ink, zorder=3)
        ax.axhline(0, color=muted, linewidth=0.8)
        ax.annotate("region CV:\n= global rate\nby construction", (x[0] + 0.18, 0), xytext=(x[0] + 0.06, 0.05),
                    fontsize=7.5, color=muted, arrowprops={"arrowstyle": "-", "color": muted, "linewidth": 0.6})
        ax.set_xticks(x)
        ax.set_xticklabels(["cell climatology", "static trees", "full trees"], fontsize=9)
        ax.set_ylabel("D2 against the global rate (out of fold)", fontsize=9, color=ink)
        g = r["gate"]
        ax.set_title(f"{t.upper()}: deviance explained by fold. Full trees vs climatology (week CV): D2 {g['d2_full_vs_climatology_week']:+.3f}",
                     fontsize=9.5, loc="left", color=ink)
        ax.legend(fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=2)
        ax2 = axes[i, 1]
        for key, col, lab in (("full_trees_week", "#2a78d6", "full trees, week CV"), ("climatology_week", "#1baf7a", "cell climatology, week CV"),
                              ("full_trees_region", "#eb6834", "full trees, region CV")):
            cal = pd.DataFrame(r["calibration"][key])
            ax2.plot(cal.predicted_per_1000km2, cal.observed_per_1000km2, marker="o", markersize=5, linewidth=2, color=col, label=lab)
        vals = np.concatenate([np.r_[pd.DataFrame(r["calibration"][k]).predicted_per_1000km2, pd.DataFrame(r["calibration"][k]).observed_per_1000km2]
                               for k in r["calibration"]])
        vals = vals[vals > 0]
        lim = [vals.min() / 1.5, vals.max() * 1.5]
        ax2.plot(lim, lim, color=muted, linewidth=0.8, linestyle="--", label="observed = predicted")
        ax2.set_xscale("log")
        ax2.set_yscale("log")
        ax2.set_xlim(lim)
        ax2.set_ylim(lim)
        ax2.set_xlabel("predicted per 1,000 km2 (exposure-weighted decile)", fontsize=9)
        ax2.set_ylabel("observed per 1,000 km2", fontsize=9)
        ax2.set_title(f"{t.upper()}: calibration of out-of-fold predictions", fontsize=9.5, loc="left", color=ink)
        ax2.legend(fontsize=8, frameon=False)
        for a in (ax, ax2):
            a.grid(color=grid, linewidth=0.8)
            a.set_axisbelow(True)
            for side in ("top", "right"):
                a.spines[side].set_visible(False)
            a.tick_params(labelsize=8, colors=muted)
    fig.text(0.01, 0.005, "Expected activity describes where detections usually are, given the sea and the weather; it is not a count "
             "of vessels. Dark = no AIS match, not evidence of illegal activity. scripts/34_expected_activity.py", fontsize=7.5, color=muted)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_FIG, dpi=130)
    plt.close(fig)


# ----------------------------------------------------------------------------------------------------------------------
# Main

if args.figure_only:
    figure(json.loads(OUT_JSON.read_text()))
    log(f"wrote {OUT_FIG}")
    raise SystemExit(0)

targets = [t.strip() for t in args.targets.split(",") if t.strip()]
MODEL_ID = "expected_activity_v1_" + hashlib.sha1(json.dumps([FEATURES, m.HGB_PARAMS, m.P_FLAG, m.CALM_WIND_MS,
                                                              m.EXPOSURE_MIN_KM2], sort_keys=True, default=str).encode()).hexdigest()[:8]
outs, results = [], {}
for t in targets:
    table, tmeta = viirs_table() if t == "viirs" else radar_table()
    log(f"{t} table: {tmeta}")
    out, res = run_target(t, table, tmeta)
    outs.append(out)
    results[t] = res
out = pd.concat(outs, ignore_index=True)
for c in out.columns:
    if pd.api.types.is_float_dtype(out[c]) and c not in ("lon", "lat", "p_high", "p_low", "p_two_sided", "q_bh", "p_two_sided_nb", "q_bh_nb"):
        out[c] = out[c].astype(np.float32)
write_parquet(out, OUT_PARQUET, {"model_id": MODEL_ID, "units": "rates per 1,000 km2 of exposure; counts per cell and night (viirs) or scene (radar)"})
log(f"wrote {OUT_PARQUET.name}: {len(out):,} rows, {OUT_PARQUET.stat().st_size / 1e6:.1f} MB")

gdf = events(out, results, args.events)
if gdf is None:
    if OUT_GPKG.exists():
        OUT_GPKG.unlink()
    gpkg_note = f"not written: no cell was flagged ({args.events}; see targets.<target>.anomalies and gate)"
else:
    if OUT_GPKG.exists():
        OUT_GPKG.unlink()
    layers = write_dual_crs(gdf, OUT_GPKG, "expected_activity_anomalies", utm_crs=CRS_UTM_REGIONAL)
    about = {"title": "Activity-anomaly cells (E15) from the expected-activity model", "model_id": MODEL_ID, "events": args.events,
             "selection": ("flag_robust: Poisson-BH flags that are also significant under the negative binomial check"
                           if args.events == "robust" else "every Poisson-BH flag (robust marks the subset to review first)"),
             "script": "scripts/34_expected_activity.py", "rule": gdf.rule_text.iloc[0], "caveat": EVENT_CAVEAT,
             "ocean_caveat": OCEAN_CAVEAT, "generated_utc": pd.Timestamp.now("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")}
    pyogrio.write_dataframe(pd.DataFrame([about]), OUT_GPKG, layer="about", driver="GPKG")
    gpkg_note = f"{len(gdf)} events ({args.events}), layers {layers} and about"
log(f"anomaly GeoPackage: {gpkg_note}")

SOURCES = [
    {"name": "scikit-learn Poisson regression example (frequency with exposure as sample weight)",
     "url": "https://scikit-learn.org/stable/auto_examples/linear_model/plot_poisson_regression_non_normal_loss.html"},
    {"name": "Marshall, R. J. (1991). Mapping disease and mortality rates using empirical Bayes estimators. Applied Statistics 40, 283-294",
     "url": "https://doi.org/10.2307/2347593"},
    {"name": "Friedman, J. H. (2001). Greedy function approximation: a gradient boosting machine. Annals of Statistics 29, 1189-1232",
     "url": "https://doi.org/10.1214/aos/1013203451"},
    {"name": "Breiman, L. (2001). Random forests. Machine Learning 45, 5-32 (permutation importance)",
     "url": "https://doi.org/10.1023/A:1010933404324"},
    {"name": "Benjamini, Y. and Hochberg, Y. (1995). Controlling the false discovery rate. JRSS B 57, 289-300",
     "url": "https://doi.org/10.1111/j.2517-6161.1995.tb02031.x"},
    {"name": "Cameron, A. C. and Trivedi, P. K. (1990). Regression-based tests for overdispersion in the Poisson model. "
             "Journal of Econometrics 46, 347-364", "url": "https://doi.org/10.1016/0304-4076(90)90014-K"},
    {"name": "Inputs: ocean layers and their sources", "url": "data/ocean_static_summary.json, data/ocean_daily_summary.json (sources lists)"},
    {"name": "Inputs: VIIRS lights and clear-sky counts", "url": "scripts/15_viirs_lights.py, docs/viirs_lights.md"},
    {"name": "Inputs: regional radar detections", "url": "scripts/09_run_regional.py, data/detections_regional_all.gpkg about table"},
]
summary = {
    "generated_utc": pd.Timestamp.now("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"), "script": "scripts/34_expected_activity.py", "aoi": DEFAULT_AOI,
    "model_id": MODEL_ID, "threads": args.threads, "permutation_repeats": args.repeats,
    "rules": {"exposure_min_km2": m.EXPOSURE_MIN_KM2, "features": FEATURES,
              "forbidden_features": "Marine Regions EEZ attributes and any World Bank/IMF shipping magnitude (model.FORBIDDEN_FEATURE_RULES); "
                                    "shipping enters as presence shares only (value above 0)",
              "trees": m.HGB_PARAMS, "climatology": "cell rate shrunk to the region rate by exposure-weighted empirical Bayes (Marshall 1991); "
                                                    "region rate for cells unseen in training, global rate for unseen regions (so under "
                                                    "leave-one-region-out the climatology equals the global rate)",
              "cv": "leave-one-week-out (blocks of 7 dates from the first) and leave-one-region-out (reporting boxes, 'other' included)",
              "d2": "1 - Poisson deviance(model) / deviance(baseline) on the out-of-fold predictions of both, rows all models predict",
              "gate": "anomalies only when the full model beats the climatology: pooled week-CV D2 > 0 and > 0 in more than half of the week folds",
              "anomaly": f"Poisson two-sided p (twice the smaller tail) of the observed count against the week-CV expectation; "
                         f"Benjamini-Hochberg FDR < {m.P_FLAG} over tested rows in calm weather (wind < {m.CALM_WIND_MS:g} m/s)",
              "calm": f"wind_ms < {m.CALM_WIND_MS:g} (GFS 10 m wind at 18 UTC for VIIRS, at the scene hour for radar)",
              "robust": "flag_robust: the Poisson-BH flag that is also significant (BH q < 0.01 over the same family) under a "
                        "negative binomial tail with alpha = the moment estimate of the overdispersion (Cameron and Trivedi 1990)",
              "poisson_fdr_valid": "true only when the Pearson dispersion of the counts against the expectation is at most 1.5 "
                                   "(a rule of thumb of this project); otherwise the Poisson BH flags do not carry a 1 % "
                                   "false-discovery guarantee and flag_robust is the set to review"},
    "targets": results,
    "outputs": {"parquet": f"{OUT_PARQUET.name}: {len(out):,} rows", "geopackage": gpkg_note, "figure": str(OUT_FIG.relative_to(FIG_DIR.parent.parent))},
    "columns": {"target": "viirs or radar", "unit_id": "night (viirs) or scene_id (radar)", "night": "local evening date (viirs) or UTC date (radar)",
                "exposure_km2": "clear searched sea (viirs) or imaged AOI sea (radar) of the cell", "tested": "exposure >= 25 km2",
                "observed": "count in the cell (NaN when not tested)", "expected": "leave-one-week-out expected count of the full trees",
                "expected_rate_per_1000km2": "the same as a rate", "expected_climatology": "leave-one-week-out climatology count",
                "expected_rate_all_per_1000km2": "rate of the full trees fitted on all tested rows, for every row",
                "z": "(observed - expected) / sqrt(expected)", "p_high, p_low": "Poisson upper and lower tails",
                "p_two_sided, q_bh": "two-sided p and its BH q over the family (NaN outside it)",
                "p_two_sided_nb, q_bh_nb": "the negative binomial sensitivity check", "calm": "wind_ms < 12",
                "flag": "high, low or none (none for every row when the gate did not pass)",
                "flag_robust": "flag where the negative binomial check (q_bh_nb < 0.01) also holds, else none"},
    "sources": SOURCES,
    "caveat": CAVEAT,
}
OUT_JSON.write_text(json.dumps(summary, indent=1, default=str))
figure(summary)
log(f"wrote {OUT_JSON.name} and {OUT_FIG.name}; done")
