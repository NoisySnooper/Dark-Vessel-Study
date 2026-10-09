"""CNN verifier on every radar object of the September 2026 regional run (detection verified before identification).

Purpose: give every high, medium and fixed object of the 12-day regional run a verifier score, so that every
contact in the product carries one (until now only the 35,626 objects in cells both satellites imaged had one).
A second phase scores the low class (weak VV only, clutter zone, near fixed, oversized).
Method: darkvessel.ml.regional_verify. Per scene, objects grouped by 1024 px COG tile; one remote VV/VH read per
group from the AWS mirror (no scene download); 64 x 64 px dB chips exactly as in training (thermal noise not
removed); data/models/verifier_v0.pt with the 8 dihedral views averaged. Scores of data/ml/shared_cells_cnn.parquet
are reused when det_id, scene and position match exactly; 200 of them are rescored as a check (tolerance 1e-4).
One checkpoint parquet per scene; a rerun skips done scenes. Runs at nice 10 (low phase nice 19) with 2 torch threads,
because the cores are shared with the live-pass pipeline.
Inputs: data/detections_regional_all.gpkg (row, col, scene of every object), data/detections_regional.gpkg
  (contact columns, high and medium), data/ml/shared_cells_cnn.parquet and .json, data/models/verifier_v0.pt,
  sentinel-s1-l1c on AWS (anonymous HTTPS).
Output: data/ml/regional_cnn.parquet (one row per scored object), data/detections_regional_verified.gpkg (layers
  detections_regional_verified_4326 and _utm49n with every contact column except lat, lon, acq_utc, ais_status
  and caveat, plus scenes and about), data/ml/regional_cnn.json, docs/figures/regional_cnn.png;
  checkpoints data/cache/regional_cnn/<phase>/<scene_id>.parquet, log run.log, pid run.pid there.
Usage (conda env with torch):
  python scripts/32_cnn_regional.py --detach [--phases main,low]   # background run, outputs rebuilt after each phase
  python scripts/32_cnn_regional.py --phases main --max-scenes 3    # foreground test on 3 scenes
  python scripts/32_cnn_regional.py --build                         # outputs from the checkpoints so far
  python scripts/32_cnn_regional.py --status | --stop
  python scripts/32_cnn_regional.py --detach --if-incomplete --phases main,low   # resume after a restart
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
# The AWS mirror answered about 1 request in 5 with 404 NoSuchBucket on 2026-10-09: let GDAL retry 404 too.
os.environ.setdefault("GDAL_HTTP_RETRY_CODES", "404,429,500,502,503,504")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "6")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")
os.environ.setdefault("GDAL_CACHEMAX", "256")  # MB; the default 5 % of RAM grows the resident size over many scenes

import darkvessel  # noqa: E402,F401 (sets PROJ_DATA before rasterio and pyogrio)
from darkvessel.config import DATA_DIR  # noqa: E402

CACHE = DATA_DIR / "cache" / "regional_cnn"
PID, LOG = CACHE / "run.pid", CACHE / "run.log"
ALL_GPKG = DATA_DIR / "detections_regional_all.gpkg"
LEAN_GPKG = DATA_DIR / "detections_regional.gpkg"
SHARED = DATA_DIR / "ml" / "shared_cells_cnn.parquet"
MODEL = DATA_DIR / "models" / "verifier_v0.pt"
OUT_PARQUET = DATA_DIR / "ml" / "regional_cnn.parquet"
OUT_JSON = DATA_DIR / "ml" / "regional_cnn.json"
OUT_GPKG = DATA_DIR / "detections_regional_verified.gpkg"
FIG = Path(__file__).resolve().parents[1] / "docs" / "figures" / "regional_cnn.png"
T0 = time.time()


def log(m):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} [{time.time() - T0:7.0f}s] {m}", flush=True)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def rss_gb() -> float:
    """Resident memory of this process in GB (Linux /proc)."""
    try:
        return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e9
    except (OSError, ValueError, IndexError):
        return 0.0


def running_pid() -> int | None:
    try:
        pid = int(PID.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    return pid if alive(pid) else None


def load_objects(classes):
    """Objects of the given classes from the full regional file (pixel position, scene, class, length)."""
    import pyogrio

    from darkvessel.ml.regional_verify import OBJECT_COLS

    where = "confidence IN ({})".format(", ".join(f"'{c}'" for c in classes))
    det = pyogrio.read_dataframe(ALL_GPKG, layer="detections_regional_4326", read_geometry=False, columns=OBJECT_COLS,
                                 where=where)
    assert det.det_id.is_unique, "duplicate det_id in the regional file"
    assert det[["row", "col"]].notna().all().all(), "objects without pixel position"
    return det[OBJECT_COLS].reset_index(drop=True)


def load_prior():
    import pandas as pd

    cols = ["det_id", "scene_id", "row", "col", "lon", "lat", "cnn_score", "cnn_vessel", "bg_vv_db", "bg_vh_db"]
    return pd.read_parquet(SHARED, columns=cols) if SHARED.exists() else None


# ----------------------------------------------------------------------------- run
def run(args):
    import torch

    from darkvessel.ml import regional_verify as rv
    from darkvessel.ml.model import load_model, predict_proba

    CACHE.mkdir(parents=True, exist_ok=True)
    other = running_pid()
    if other and other != os.getpid():
        sys.exit(f"another run is alive (pid {other}); --stop it first")
    PID.write_text(str(os.getpid()))
    try:
        os.nice(max(0, args.nice - os.nice(0)))  # --detach already starts under nice 10
    except OSError:
        pass
    torch.set_num_threads(args.threads)
    model, meta = load_model(MODEL)
    model_id = rv.model_id_for(MODEL)
    def score_fn(chips):
        with torch.inference_mode():  # no autograd graph: about 20 % faster, identical scores
            return predict_proba(model, chips, meta, tta=True)

    log(f"model {model_id} threshold {float(meta['threshold']):.4f}; torch threads {torch.get_num_threads()}; "
        f"nice {os.nice(0)}")
    prior_all = load_prior()
    try:
        for phase in args.phases:
            if phase == "low" and os.nice(0) < args.low_nice:
                os.nice(args.low_nice - os.nice(0))  # optional phase: yield the cores to the live-pass pipeline
                log(f"phase low runs at nice {os.nice(0)}")
            obj = load_objects(rv.PHASES[phase])
            prior = rv.align_prior(obj, prior_all)
            verify = rv.verification_mask(obj.det_id, prior.prior_score.notna())
            log(f"phase {phase}: {len(obj)} objects {obj.confidence.value_counts().to_dict()}; prior match "
                f"{int((prior.prior_status == 'match').sum())}, position differs "
                f"{int((prior.prior_status == 'position_differs').sum())}; rescore sample {int(verify.sum())}")
            st = rv.run_phase(obj, prior, verify, cache_dir=CACHE, phase=phase, model_id=model_id, score_fn=score_fn,
                              reader_factory=lambda sid: rv.SceneReader(sid, io_workers=args.io_workers),
                              workers=args.workers, max_scenes=args.max_scenes, log=log,
                              should_stop=lambda: rss_gb() > args.max_rss_gb)
            if st["stopped"]:
                # resident memory creeps up scene by scene (GDAL and allocator caches); start afresh in the same
                # process id, so the pid file stays valid, from the checkpoints
                rest = args.phases[args.phases.index(phase):]
                argv = [sys.executable, os.path.abspath(__file__), "--phases", ",".join(rest), *args.passthrough]
                log(f"resident memory {rss_gb():.1f} GB > {args.max_rss_gb} GB: restarting from the checkpoints")
                sys.stdout.flush()
                os.execve(sys.executable, argv, {**os.environ, "MALLOC_ARENA_MAX": "2"})
            log(f"phase {phase} finished: {st}")
            if not args.no_build:
                # a fresh process, so the build uses the script on disk now
                r = subprocess.run([sys.executable, __file__, "--build"], check=False)
                log(f"build exit code {r.returncode}")
    finally:
        if PID.exists() and PID.read_text().strip() == str(os.getpid()):
            PID.unlink()
    log("run done")


def phases_left(phases) -> list[str]:
    """Phases with a scene that has objects but no checkpoint file (a quick check for --if-incomplete)."""
    import pyogrio

    from darkvessel.ml.regional_verify import PHASES

    left = []
    for phase in phases:
        where = "confidence IN ({})".format(", ".join(f"'{c}'" for c in PHASES[phase]))
        sids = set(pyogrio.read_dataframe(ALL_GPKG, layer="detections_regional_4326", read_geometry=False,
                                          columns=["scene_id"], where=where).scene_id)
        done = {p.stem for p in (CACHE / phase).glob("*.parquet")}
        if sids - done:
            left.append(phase)
    return left


def detach(argv, phases, if_incomplete=False):
    CACHE.mkdir(parents=True, exist_ok=True)
    if (pid := running_pid()):
        sys.exit(f"already running (pid {pid}); log {LOG}")
    if if_incomplete and not phases_left(phases):
        print(f"every scene of {','.join(phases)} is checkpointed; nothing to start")
        return
    rest = [a for a in argv if a not in ("--detach", "--if-incomplete")]
    with open(LOG, "a") as fh:
        p = subprocess.Popen(["nohup", "nice", "-n", "10", sys.executable, os.path.abspath(__file__), *rest],
                             stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                             cwd=str(Path(__file__).resolve().parents[1]),
                             env={**os.environ, "MALLOC_ARENA_MAX": "2"})  # fewer glibc arenas, less RSS creep
    PID.write_text(str(p.pid))
    print(f"started pid {p.pid}; log {LOG}; stop with --stop")


def status():
    import pandas as pd

    from darkvessel.ml.regional_verify import PHASES

    pid = running_pid()
    print(f"run: {'alive pid ' + str(pid) if pid else 'not running'}")
    for phase in PHASES:
        d = CACHE / phase
        files = sorted(d.glob("*.parquet")) if d.exists() else []
        n = sum(len(pd.read_parquet(f, columns=["det_id"])) for f in files)
        print(f"{phase}: {len(files)} scene checkpoints, {n} objects")
    if LOG.exists():
        print("".join(LOG.read_text().splitlines(keepends=True)[-5:]), end="")


def stop():
    pid = running_pid()
    if not pid:
        print("not running")
        return
    os.killpg(os.getpgid(pid), signal.SIGTERM)
    print(f"sent SIGTERM to process group of {pid}; finished scenes stay checkpointed")


# ----------------------------------------------------------------------------- build
def build(args):
    import json

    import geopandas as gpd
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pyogrio

    from darkvessel.config import CRS_GEO, CRS_UTM_REGIONAL, DARK_CAVEAT, DARK_CAVEAT_SHORT
    from darkvessel.ml import regional_verify as rv
    from darkvessel.ml.model import load_model

    _, meta = load_model(MODEL)
    thr, model_id = float(meta["threshold"]), rv.model_id_for(MODEL)
    prior_all = load_prior()
    scored, tables, objs, reuse, timing = {}, [], [], None, {}
    for phase, classes in rv.PHASES.items():
        obj = load_objects(classes)
        ck = rv.load_checkpoints(CACHE, phase, obj.scene_id.unique())
        ck = ck[ck.cnn_model_id == model_id] if len(ck) else ck
        ck = ck[ck.det_id.isin(obj.det_id)]
        complete = bool(len(ck)) and obj.det_id.isin(ck.det_id).all()
        scored[phase] = {"classes": list(classes), "objects": int(len(obj)), "scored": int(len(ck)),
                         "scenes": int(obj.scene_id.nunique()),
                         "scenes_done": int(ck.scene_id.nunique()) if len(ck) else 0,
                         "complete": bool(complete), "in_outputs": False}
        if not len(ck) or (phase == "low" and not complete and not args.partial_low):
            log(f"build: phase {phase} {scored[phase]['scenes_done']}/{scored[phase]['scenes']} scenes, not in outputs")
            continue
        scored[phase]["in_outputs"] = True
        scored[phase]["by_source"] = ck.cnn_score_source.value_counts().to_dict()
        el = ck.drop_duplicates("scene_id")
        t0, t1 = pd.to_datetime(el.scored_utc).agg(["min", "max"])
        timing[phase] = {"scene_seconds_sum": round(float(el.scene_elapsed_s.sum()), 1),
                         "scene_seconds_median": round(float(el.scene_elapsed_s.median()), 1),
                         "first_scene_done_utc": str(t0), "last_scene_done_utc": str(t1),
                         "objects_per_second": round(len(ck) / float(el.scene_elapsed_s.sum()), 2)}
        if phase == "main":
            pa_ = rv.align_prior(obj, prior_all)
            reuse = rv.reuse_check(ck, pa_, obj, thr)
        tables.append(rv.score_table(ck, obj, thr))
        objs.append(obj)
    if not tables:
        sys.exit("no checkpoints to build from")
    table = pd.concat(tables, ignore_index=True)
    obj = pd.concat(objs, ignore_index=True)
    for c in ("mission", "confidence", "cnn_model_id", "cnn_score_source"):
        table[c] = table[c].astype(str).astype("category")
    table["caveat"] = pd.Categorical([DARK_CAVEAT_SHORT] * len(table))

    # 1. Score table, one row per scored object (file-level notes in the parquet metadata)
    pt = pa.Table.from_pandas(table, preserve_index=False)
    pt = pt.replace_schema_metadata({**(pt.schema.metadata or {}), b"model_id": model_id.encode(),
                                     b"threshold": str(thr).encode(), b"training_data": rv.TRAINING_NOTE.encode(),
                                     b"transfer_caveat": rv.TRANSFER_CAVEAT.encode(), b"caveat": DARK_CAVEAT.encode()})
    tmp = OUT_PARQUET.with_name(f".{OUT_PARQUET.name}.tmp")
    pq.write_table(pt, tmp, compression="zstd")
    os.replace(tmp, OUT_PARQUET)
    log(f"wrote {OUT_PARQUET} {len(table)} rows {OUT_PARQUET.stat().st_size / 1e6:.1f} MB")

    # 2. Verified contacts (high and medium), both CRS, plus the scenes table and about
    lean = gpd.read_file(LEAN_GPKG, layer="detections_regional_4326", engine="pyogrio")
    g = rv.verified_layer(lean, table)
    n_scored = int(g.cnn_score.notna().sum())
    scenes = pyogrio.read_dataframe(LEAN_GPKG, layer="scenes_processed_4326", read_geometry=False,
                                    columns=["scene_idx", "product_id", "mission", "start_utc", "pass_dir",
                                             "orbit_rel"])
    kept = [c for c in g.columns if c != "geometry"]
    about = pd.DataFrame([{
        "model_id": model_id, "threshold": thr, "threshold_rule": meta.get("threshold_rule", ""),
        "cnn_vessel_rule": f"cnn_vessel = cnn_score >= {thr:.6f}; null when not scored",
        "training_data": rv.TRAINING_NOTE, "transfer_caveat": rv.TRANSFER_CAVEAT, "caveat_full": DARK_CAVEAT,
        "caveat": DARK_CAVEAT_SHORT,
        "rows": f"{len(g)} high and medium contacts of data/detections_regional.gpkg; {n_scored} carry a CNN score. "
                "Fixed structures and the low class are scored in data/ml/regional_cnn.parquet",
        "columns": f"{', '.join(kept[:-2])} as in data/detections_regional.gpkg; cnn_score (vessel probability, mean "
                   "of 8 flip and rotation views, 4 decimals); cnn_vessel",
        "other_columns": "Left out to keep both CRS layers in one file under 20 MB: lat and lon (the geometry), "
                         "acq_utc (the scene start time: start_utc of the scenes table, join on scene_idx; also "
                         "characters 5 to 19 of det_id), ais_status ('not_checked' on every row; AIS identity is in "
                         "other files) and the per-row caveat (above). Chip features (valid fraction, background dB) "
                         "are in data/ml/regional_cnn.parquet; join on det_id",
        "ais": "No AIS in this file. Verification only: a CNN score says how a radar object looks, not who it is",
        "chips": "64 x 64 px (640 m) VV and VH sigma0 in dB, thermal noise not removed, read from the AWS COGs at the "
                 "object's row and col, as in training",
        "reuse": "scores of data/ml/shared_cells_cnn.parquet kept where det_id, scene and position match exactly; "
                 f"200 rescored, max abs difference {reuse['max_abs_diff'] if reuse else 'n/a'}",
        "data_credit": "Contains modified Copernicus Sentinel data 2026; model trained on AI2 Skylight labels "
                       "(Apache-2.0) and Copernicus Sentinel data 2020-2022",
        "script": "scripts/32_cnn_regional.py", "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}])
    tmp = OUT_GPKG.with_name(f".{OUT_GPKG.stem}.tmp.gpkg")
    tmp.unlink(missing_ok=True)
    for crs, suffix in ((CRS_GEO, "4326"), (CRS_UTM_REGIONAL, "utm49n")):
        g.to_crs(crs).to_file(tmp, layer=f"detections_regional_verified_{suffix}", driver="GPKG", engine="pyogrio",
                              layer_options={"SPATIAL_INDEX": "NO", "DESCRIPTION": DARK_CAVEAT})
    pyogrio.write_dataframe(scenes, tmp, layer="scenes", driver="GPKG")
    pyogrio.write_dataframe(about, tmp, layer="about", driver="GPKG")
    os.replace(tmp, OUT_GPKG)
    log(f"wrote {OUT_GPKG} {len(g)} rows x 2 layers {OUT_GPKG.stat().st_size / 1e6:.1f} MB")

    # 3. Summary
    shared_json = json.loads((DATA_DIR / "ml" / "shared_cells_cnn.json").read_text())
    shared_ids = prior_all.det_id if prior_all is not None else None
    wpath = DATA_DIR / "weather_context.parquet"
    weather = pd.read_parquet(wpath, columns=["det_id", "wind_ms", "deep_convection"]) if wpath.exists() else None
    summ = {"created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "script": "scripts/32_cnn_regional.py",
            "model_id": model_id, "threshold": thr, "training_data": rv.TRAINING_NOTE,
            "transfer_caveat": rv.TRANSFER_CAVEAT, "caveat": DARK_CAVEAT,
            "chip_background": "median dB of the 64 x 64 px chip outside its central 16 x 16 px",
            "intervals": "Wilson score 95 %", "length_bins_m": rv.length_bin_labels(), "regions": rv.REGIONS,
            "regions_note": "reporting boxes of scripts/21_viirs_regions.py, not boundaries; first box in order wins",
            "scored": scored, "run_time": timing, "reuse_check": reuse,
            "weather_source": "data/weather_context.parquet (GFS 10 m wind and Himawari cloud tops at the radar time, "
                              "scripts/16_weather_context.py)",
            "acceptance": rv.summarise(table, obj, shared_ids, weather),
            "shared_cells_reference": {"source": "data/ml/shared_cells_cnn.json", "by": shared_json["by"]}}
    tmp = OUT_JSON.with_name(f".{OUT_JSON.name}.tmp")
    tmp.write_text(json.dumps(summ, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    os.replace(tmp, OUT_JSON)
    log(f"wrote {OUT_JSON}")
    figure(table, obj, summ)
    log("build done")


def figure(table, obj, summ):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap, ListedColormap

    from darkvessel.aoi import aoi_gdf, natural_earth_land
    from darkvessel.config import DEFAULT_AOI
    from darkvessel.viz.style import BASELINE, GRID, INK, INK_2, MUTED, SERIES_LIGHT, apply_matplotlib_style

    apply_matplotlib_style()
    d = table.merge(obj[["det_id", "lon", "lat"]], on="det_id")
    c = d[d.confidence.astype(str).isin(("high", "medium"))]
    aoi = aoi_gdf(DEFAULT_AOI)
    w, s, e, n = aoi.total_bounds
    res = 0.25
    x0, y0 = np.floor(w / res) * res, np.floor(s / res) * res
    nx, ny = int(np.ceil((e - x0) / res)), int(np.ceil((n - y0) / res))
    ix = np.clip(((c.lon - x0) / res).astype(int), 0, nx - 1)
    iy = np.clip(((c.lat - y0) / res).astype(int), 0, ny - 1)
    tot = np.zeros((ny, nx))
    acc = np.zeros((ny, nx))
    np.add.at(tot, (iy, ix), 1)
    np.add.at(acc, (iy, ix), c.cnn_vessel.to_numpy(float))
    min_n = 10
    share = np.where(tot >= min_n, acc / np.maximum(tot, 1), np.nan)
    ramp = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
    cmap = LinearSegmentedColormap.from_list("blue", ramp)

    fig = plt.figure(figsize=(13, 7.6))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.45, 1], hspace=0.42, wspace=0.18, left=0.04, right=0.98, top=0.88,
                          bottom=0.09)
    ax = fig.add_subplot(gs[:, 0])
    land = natural_earth_land(bbox=(w - 1, s - 1, e + 1, n + 1))
    land.plot(ax=ax, color=GRID, linewidth=0)
    aoi.boundary.plot(ax=ax, color=BASELINE, linewidth=0.6)
    few = np.where((tot > 0) & (tot < min_n), 1.0, np.nan)
    ax.pcolormesh(x0 + np.arange(nx + 1) * res, y0 + np.arange(ny + 1) * res, few, cmap=ListedColormap([MUTED]),
                  alpha=0.35, shading="flat")
    im = ax.pcolormesh(x0 + np.arange(nx + 1) * res, y0 + np.arange(ny + 1) * res, share, cmap=cmap, vmin=0, vmax=1,
                       shading="flat")
    ax.set_xlim(w - 0.3, e + 0.3)
    ax.set_ylim(s - 0.3, n + 0.3)
    ax.set_aspect("equal")
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.text(0.01, 0.01, f"grey: fewer than {min_n} contacts", transform=ax.transAxes, fontsize=7, color=INK_2)
    ax.set_title(f"CNN-accepted share of high and medium contacts per {res} degree cell (cells with {min_n} or more)",
                 loc="left", fontsize=9.5, color=INK)
    ax.tick_params(labelsize=7)
    cb = fig.colorbar(im, ax=ax, orientation="horizontal", fraction=0.035, pad=0.06, aspect=40)
    cb.set_label("share accepted (cnn_score >= threshold)", fontsize=8, color=INK_2)
    cb.ax.tick_params(labelsize=7)

    acc_ = summ["acceptance"]
    classes = [r["confidence"] for r in acc_["by_class"]]
    order = [k for k in ("high", "medium", "fixed", "low") if k in classes]
    ax1 = fig.add_subplot(gs[0, 1])
    rows = {r["confidence"]: r for r in acc_["by_class"]}
    for i, k in enumerate(order):
        r = rows[k]
        ax1.barh(i, r["accept_share"], color=SERIES_LIGHT[0], height=0.55)
        lo, hi = r["accept_ci95"]
        ax1.plot([lo, hi], [i, i], color=INK, linewidth=1)
        label = f"{r['accept_share']:.3f} [{lo:.3f}, {hi:.3f}]  n = {r['n']:,}"
        if hi < 0.5:  # label right of the interval, else inside the bar so it is never cut at the axis edge
            ax1.text(hi + 0.02, i, label, va="center", fontsize=8, color=INK_2)
        else:
            ax1.text(lo - 0.02, i, label, va="center", ha="right", fontsize=8, color="white")
    ax1.set_yticks(range(len(order)), order, fontsize=8)
    ax1.invert_yaxis()
    ax1.set_xlim(0, 1)
    ax1.set_xlabel("share accepted, Wilson 95 % interval", fontsize=8)
    ax1.set_title("Acceptance by detector class", loc="left", fontsize=9.5, color=INK)
    ax1.tick_params(labelsize=7)
    ax1.grid(axis="x")

    ax2 = fig.add_subplot(gs[1, 1])
    bins = [b for b in summ["length_bins_m"]]
    for j, k in enumerate([k for k in ("high", "medium", "fixed") if k in order]):
        rr = {r["length_bin"]: r for r in acc_["by_length_bin_class"] if r["confidence"] == k}
        xs = [i + (j - 1) * 0.12 for i, b in enumerate(bins) if b in rr]
        ys = [rr[b]["accept_share"] for b in bins if b in rr]
        lo = [rr[b]["accept_ci95"][0] for b in bins if b in rr]
        hi = [rr[b]["accept_ci95"][1] for b in bins if b in rr]
        ax2.vlines(xs, lo, hi, color=SERIES_LIGHT[j], linewidth=1)
        ax2.plot(xs, ys, "o-", color=SERIES_LIGHT[j], linewidth=2, markersize=5, label=k)
    ax2.set_xticks(range(len(bins)), [b.replace(" and longer", "+") for b in bins], fontsize=7)
    ax2.set_ylim(0, 1)
    ax2.set_ylabel("share accepted", fontsize=8)
    ax2.set_xlabel("radar length estimate", fontsize=8)
    ax2.set_title("Acceptance by length estimate and class", loc="left", fontsize=9.5, color=INK)
    ax2.legend(fontsize=8, loc="upper left", ncol=3)
    ax2.tick_params(labelsize=7)
    ax2.grid(axis="y")

    sc = summ["scored"]
    fig.suptitle("CNN verifier on the September 2026 regional run (Sentinel-1C and 1D, 12 days)", x=0.04, ha="left",
                 fontsize=12, color=INK, fontweight="bold")
    fig.text(0.04, 0.925, f"Model {summ['model_id']}, threshold {summ['threshold']:.3f}. Scored: "
             + "; ".join(f"{', '.join(v['classes'])} {v['scored']:,} of {v['objects']:,}" for v in sc.values()
                         if v["in_outputs"])
             + ". Trained on Sentinel-1A/1B labels: no 1C/1D truth yet, so acceptance is not precision.",
             fontsize=8, color=INK_2)
    fig.text(0.04, 0.02, "Contains modified Copernicus Sentinel data 2026; Natural Earth. Region boxes are reporting "
             "boxes. Dark = no AIS match; not evidence of illegal activity. This figure shows radar verification "
             "only, no AIS.", fontsize=7, color=INK_2)
    FIG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG, dpi=150)
    plt.close(fig)
    log(f"wrote {FIG}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--phases", default="main", help="comma list of main (high, medium, fixed) and low")
    ap.add_argument("--max-scenes", type=int, default=None, help="test on the first N scenes still to do")
    ap.add_argument("--threads", type=int, default=2, help="torch threads")
    ap.add_argument("--workers", type=int, default=4, help="tile-group reads in flight per scene (I/O bound)")
    ap.add_argument("--io-workers", type=int, default=8, help="COG tile fetch threads per scene (I/O bound)")
    ap.add_argument("--nice", type=int, default=10)
    ap.add_argument("--low-nice", type=int, default=19, help="niceness of the low phase")
    ap.add_argument("--max-rss-gb", type=float, default=3.0, help="restart in place above this resident memory")
    ap.add_argument("--no-build", action="store_true", help="do not rebuild the outputs after each phase")
    ap.add_argument("--build", action="store_true", help="only build the outputs from the checkpoints")
    ap.add_argument("--partial-low", action="store_true", help="build: include the low phase although incomplete")
    ap.add_argument("--detach", action="store_true")
    ap.add_argument("--if-incomplete", action="store_true",
                    help="with --detach: start only when a scene of the phases has no checkpoint (session start)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--stop", action="store_true")
    args = ap.parse_args()
    args.phases = [p.strip() for p in args.phases.split(",") if p.strip()]
    args.passthrough = [f"--{k.replace('_', '-')}={getattr(args, k)}" for k in
                        ("threads", "workers", "io_workers", "nice", "low_nice", "max_rss_gb")]
    args.passthrough += ["--no-build"] if args.no_build else []
    if args.detach:
        return detach(sys.argv[1:], args.phases, args.if_incomplete)
    if args.status:
        return status()
    if args.stop:
        return stop()
    if args.build:
        return build(args)
    return run(args)


if __name__ == "__main__":
    main()
