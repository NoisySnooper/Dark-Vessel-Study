"""Watch the AWS mirror for new Sentinel-1 scenes over the AOI, process those recorded while live AIS was on.

Cycle (every `poll_minutes` in --watch mode, once in --once mode), under an fcntl lock on data/cache/live/cycle.lock so a
--once or --scene run never races the detached watcher:
  1. List the mirror's IW DV products for every UTC day from `since` to now (one S3 listing per day, retried on
     truncated or failed responses), keep S1C/S1D scenes that start after `since`, fall in the AOI's time-of-day windows
     and are at least 35 min old (so the AIS window around them is complete). Fetch productInfo.json once per product
     (cached under data/cache/live/products/) and keep the scenes whose footprint overlaps the AOI by at least
     MIN_OVERLAP_KM2. Cached product records in the same time range are merged in, so a scene whose checkpoint says
     `error` is retried even when the listing fails that cycle. The predicted passes in data/s1_next_passes.json are
     re-read every cycle for logging and pass naming; the mirror listing is the source of truth, so a pass missing from
     the plan is not missed.
  2. For each new scene: if no ship-station AIS position was recorded in the plus or minus 30 min window the scene is
     marked skipped_no_ais (never retried: the recorder gap is final); otherwise detect (up to DETECT_ATTEMPTS attempts
     with backoff, on top of the per-tile retries of LiveGRDScene), verify, match, identify and write the checkpoint
     (data/cache/live/scenes/<product_id>.json, .parquet, .ais_only.parquet). A scene with a checkpoint is never redone;
     a crash resumes at the next scene; a scene in `error` is retried on the next cycle.
  3. Rebuild the products in data/live/ from every checkpoint, only when a scene was processed this cycle or the summary
     file is missing, so a cycle that finds nothing leaves the product files untouched.

Pass naming: run_id = live_<mission>_<yyyymmddThhmm> from the ESA plan segment that contains the scene start
(same mission), else from the start minute of the first scene seen on that absolute orbit; the choice is stored in
data/cache/live/passes.json so it never changes afterwards.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
import traceback
from pathlib import Path

import pandas as pd
import shapely
from shapely.geometry import shape

from darkvessel.ais import aisstream
from darkvessel.config import CRS_EQUAL_AREA, CRS_GEO, DATA_DIR, DEFAULT_AOI
from darkvessel.live import matching, scene as scene_mod
from darkvessel.live.rules import AIS_WINDOW_S, ship_stations
from darkvessel.live.schema import CYCLE_LOCK, LIVE_DIR, PASSES_PATH, PRODUCT_CACHE, SCENE_CACHE, SUMMARY_JSON
from darkvessel.s1 import aws

SINCE_DEFAULT = pd.Timestamp("2026-10-08T14:30:00Z")
MIN_OVERLAP_KM2 = 300.0          # same threshold as the regional run
SETTLE_S = AIS_WINDOW_S + 300    # a scene is considered once its AIS window is complete and flushed
PLAN_PATH = DATA_DIR / "s1_next_passes.json"
PLAN_TOLERANCE = pd.Timedelta(minutes=3)
LISTING_WAITS_S = (1.0, 2.0, 3.0, 5.0)   # retries of one day listing inside a cycle, each on a new connection
DETECT_ATTEMPTS = 3              # detection attempts per scene per cycle
DETECT_WAITS_S = (30.0, 90.0)


def log(msg: str) -> None:
    print(f"{pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M:%S}Z [live] {msg}", flush=True)


def _atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    os.replace(tmp, path)


class CycleLock:
    """fcntl lock around one cycle. `blocking=False` raises BlockingIOError at once when another process holds it."""

    def __init__(self, path: Path = CYCLE_LOCK, blocking: bool = True):
        self.path, self.blocking, self._fh = Path(path), blocking, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            self._fh.close()
            self._fh = None
            raise
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"{os.getpid()} {pd.Timestamp.now(tz='UTC').isoformat()}\n")
        self._fh.flush()
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
        return False


# ----------------------------------------------------------------------------------------------- plan and passes
def load_plan(path: Path = PLAN_PATH) -> list[dict]:
    """ESA-plan passes (start_utc, stop_utc, mission, footprint_bbox) from the pass file; [] when unreadable."""
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    out = []
    for p in d.get("passes", []):
        if p.get("source") != "esa_plan":
            continue
        try:
            out.append({"start_utc": pd.Timestamp(p["start_utc"]), "stop_utc": pd.Timestamp(p["stop_utc"]), "mission": p["mission"],
                        "footprint_bbox": p.get("footprint_bbox"), "relative_orbit": p.get("relative_orbit")})
        except (KeyError, ValueError):
            continue
    return sorted(out, key=lambda p: p["start_utc"])


def run_id_for(rec: dict, plan: list[dict], passes_path: Path | None = None) -> str:
    """Stable run_id for the scene's pass (see module docstring). `passes_path` defaults to PASSES_PATH at call time."""
    passes_path = PASSES_PATH if passes_path is None else passes_path
    try:
        passes = json.loads(Path(passes_path).read_text())
    except (OSError, ValueError):
        passes = {}
    key = f"{rec['mission']}_{int(rec['orbit'])}"
    if key in passes:
        return passes[key]
    start = pd.Timestamp(rec["start"]).tz_localize("UTC") if pd.Timestamp(rec["start"]).tzinfo is None else pd.Timestamp(rec["start"])
    run_id = None
    for p in plan:
        if p["mission"] == rec["mission"] and p["start_utc"] - PLAN_TOLERANCE <= start <= p["stop_utc"] + PLAN_TOLERANCE:
            run_id = f"live_{rec['mission']}_{p['start_utc']:%Y%m%dT%H%M}"
            break
    if run_id is None:
        run_id = f"live_{rec['mission']}_{start:%Y%m%dT%H%M}"
    passes[key] = run_id
    _atomic_json(passes_path, passes)
    return run_id


# ----------------------------------------------------------------------------------------------- mirror candidates
def product_record(path: str, aoi, cache: Path = PRODUCT_CACHE) -> dict:
    """productInfo footprint and the AOI test for one mirror product, cached as JSON."""
    pid = path.rsplit("/", 1)[-1]
    cp = Path(cache) / f"{pid}.json"
    if cp.exists():
        try:
            return json.loads(cp.read_text())
        except ValueError:
            pass
    info = scene_mod.http_retry(lambda: aws.fetch_product_info(path), what=f"productInfo {pid[-4:]}", log=log)
    geom = shape(info["footprint"])
    meta = aws.parse_product_id(pid)
    inter = geom.intersection(aoi)
    overlap = 0.0
    if not inter.is_empty:
        import geopandas as gpd

        overlap = float(gpd.GeoSeries([inter], crs=CRS_GEO).to_crs(CRS_EQUAL_AREA).area.iloc[0] / 1e6)
    rec = {"product_id": pid, "path": path, "mission": meta["mission"], "start": meta["start"].isoformat() + "+00:00",
           "stop": meta["stop"].isoformat() + "+00:00", "orbit": meta["orbit"], "footprint_wkt": geom.wkt,
           "intersects_aoi": bool(overlap > 0), "aoi_overlap_km2": round(overlap, 1),
           "s3_ingestion": info.get("s3Ingestion"), "fetched_utc": pd.Timestamp.now(tz="UTC").isoformat()}
    if rec["intersects_aoi"]:
        try:
            rec.update(scene_mod.http_retry(lambda: aws.fetch_manifest_meta(path), what=f"manifest {pid[-4:]}", log=log))
        except Exception as exc:  # noqa: BLE001 manifest is optional for the record
            rec["manifest_error"] = repr(exc)
    _atomic_json(cp, rec)
    return rec


def cached_records(cache: Path = PRODUCT_CACHE) -> list[dict]:
    """Every product record already fetched (any day, AOI or not)."""
    out = []
    for p in sorted(Path(cache).glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return out


def _passes_time_filters(product_id: str, since: pd.Timestamp, now: pd.Timestamp, missions, windows, settle_s: int) -> bool:
    try:
        m = aws.parse_product_id(product_id)
    except ValueError:
        return False
    start = pd.Timestamp(m["start"]).tz_localize("UTC")
    if m["mission"] not in missions or start < since or start > now - pd.Timedelta(seconds=settle_s):
        return False
    return bool(aws.in_utc_windows(m["start"], windows))


def mirror_candidates(aoi, since: pd.Timestamp, now: pd.Timestamp, missions=("S1C", "S1D"), min_overlap_km2: float = MIN_OVERLAP_KM2,
                      list_day=aws.list_day_products, record=product_record, cached=cached_records, settle_s: int = SETTLE_S,
                      waits=LISTING_WAITS_S, log=log) -> list[dict]:
    """AOI scenes on the mirror with start in [since, now - settle_s], sorted by start.

    A day listing is retried `len(waits)` times; when it still fails, the products already recorded in the cache for
    that range are used, so a scene seen earlier is never dropped by one bad listing.
    """
    west, south, east, north = aoi.bounds
    windows = aws.utc_windows_for(west, east)
    days = pd.date_range(since.normalize(), now.normalize(), freq="D")
    paths = []
    for d in days:
        try:  # each retry on a new connection (the mirror's spurious 404s stick to a connection)
            paths += scene_mod.http_retry(lambda d=d: list_day(d.date()), attempts=len(waits) + 1, waits=waits or (1.0,),
                                          what=f"listing {d.date()}", log=log)
        except Exception as exc:  # noqa: BLE001 one bad listing must not stop the cycle
            log(f"listing {d.date()} failed {len(waits) + 1} times: {type(exc).__name__}: {str(exc)[:120]}; using cached records")
    recs: dict[str, dict] = {}
    for rec in cached():
        pid = rec.get("product_id", "")
        if _passes_time_filters(pid, since, now, missions, windows, settle_s):
            recs[pid] = rec
    for p in paths:
        pid = p.rsplit("/", 1)[-1]
        if pid in recs or not _passes_time_filters(pid, since, now, missions, windows, settle_s):
            continue
        try:
            recs[pid] = record(p, aoi)
        except Exception as exc:  # noqa: BLE001 try again next cycle
            log(f"productInfo {pid} failed: {type(exc).__name__}: {str(exc)[:400]}")
    out = [r for r in recs.values() if r.get("intersects_aoi") and r.get("aoi_overlap_km2", 0) >= min_overlap_km2]
    return sorted(out, key=lambda r: r["start"])


# ----------------------------------------------------------------------------------------------- AIS access
def ais_window_for(scene_time: pd.Timestamp, window_s: int = AIS_WINDOW_S, loader=None) -> pd.DataFrame:
    """Ship-station AIS positions recorded in the AOI within plus or minus `window_s` of the scene time."""
    loader = loader or aisstream.load_positions
    half = pd.Timedelta(seconds=window_s)
    return ship_stations(loader(start=scene_time - half, end=scene_time + half))


def ais_coverage(scene_time: pd.Timestamp, window_s: int = AIS_WINDOW_S, loader=None) -> tuple[int, int]:
    """(positions, distinct MMSI) recorded anywhere in the AOI within the window."""
    w = ais_window_for(scene_time, window_s, loader)
    return int(len(w)), int(w.mmsi.nunique()) if len(w) else 0


# ----------------------------------------------------------------------------------------------- checkpoints
class Checkpoint:
    """Per-scene state under data/cache/live/scenes/: <id>.json (status and stats), <id>.parquet, <id>.ais_only.parquet."""

    def __init__(self, root: Path = SCENE_CACHE):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self, product_id: str) -> dict | None:
        p = self.root / f"{product_id}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except ValueError:
            return None

    def done(self, product_id: str) -> bool:
        s = self.status(product_id)
        return s is not None and s.get("status") in ("done", "skipped_no_ais", "skipped_small_overlap")

    def attempts(self, product_id: str) -> int:
        s = self.status(product_id)
        return int(s.get("attempts_total", 0)) if s else 0

    def mark(self, product_id: str, status: str, **info) -> dict:
        rec = {"product_id": product_id, "status": status, "updated_utc": pd.Timestamp.now(tz="UTC").isoformat(), **info}
        _atomic_json(self.root / f"{product_id}.json", rec)
        return rec

    def write_tables(self, product_id: str, contacts: pd.DataFrame, ais_only: pd.DataFrame) -> None:
        for df, name in ((contacts, f"{product_id}.parquet"), (ais_only, f"{product_id}.ais_only.parquet")):
            tmp = self.root / f"{name}.{os.getpid()}.tmp"
            df.to_parquet(tmp, index=False)
            os.replace(tmp, self.root / name)

    def load_all(self) -> tuple[list[dict], pd.DataFrame, pd.DataFrame]:
        """(scene records, contacts, ais_only) of every scene with status done."""
        recs, cs, aos = [], [], []
        for p in sorted(self.root.glob("*.json")):
            s = self.status(p.stem)
            if not s or s.get("status") != "done":
                continue
            recs.append(s)
            cp, ap = self.root / f"{p.stem}.parquet", self.root / f"{p.stem}.ais_only.parquet"
            if cp.exists():
                cs.append(pd.read_parquet(cp))
            if ap.exists():
                aos.append(pd.read_parquet(ap))
        contacts = pd.concat([c for c in cs if len(c)], ignore_index=True) if any(len(c) for c in cs) else pd.DataFrame()
        ais_only = pd.concat([a for a in aos if len(a)], ignore_index=True) if any(len(a) for a in aos) else pd.DataFrame()
        return recs, contacts, ais_only


# ----------------------------------------------------------------------------------------------- the pipeline
class Context:
    """Shared, lazily built inputs: AOI, grid, verifier, footprint archive; the output directory and the lock path."""

    def __init__(self, workers: int = 2, do_cnn: bool = True, do_persistence: bool = True, pfa: float = 1e-6, aoi_name: str = DEFAULT_AOI,
                 io_threads: int = scene_mod.IO_THREADS, out_dir: Path = LIVE_DIR, lock_path: Path = CYCLE_LOCK):
        self.workers, self.do_cnn, self.do_persistence, self.pfa, self.aoi_name = workers, do_cnn, do_persistence, pfa, aoi_name
        self.io_threads, self.out_dir, self.lock_path = io_threads, Path(out_dir), Path(lock_path)
        self._aoi = self._grid = self._model = self._fp = None

    @property
    def summary_path(self) -> Path:
        return self.out_dir / SUMMARY_JSON.name

    @property
    def aoi(self):
        if self._aoi is None:
            from darkvessel.aoi import aoi_gdf
            from darkvessel.regional import SEED_INSET_DEG

            g = aoi_gdf(self.aoi_name).geometry.iloc[0].simplify(0.01)
            inner = g.buffer(-SEED_INSET_DEG)
            shapely.prepare(g)
            shapely.prepare(inner)
            self._aoi = (g, inner)
        return self._aoi

    @property
    def grid(self):
        if self._grid is None:
            from darkvessel.ocean.grid import model_grid

            self._grid = model_grid()
        return self._grid

    @property
    def verifier(self):
        if self._model is None:
            self._model = scene_mod.load_verifier(threads=self.workers) if self.do_cnn else (None, None, None)
        return self._model

    @property
    def footprints(self):
        if self._fp is None and self.do_persistence:
            self._fp = scene_mod.load_footprints()
        return self._fp


def detect_with_retry(rec: dict, ctx: Context, attempts: int | None = None, waits=None, process=None, log=log):
    """scene_mod.process up to `attempts` times with backoff. Returns (objects, stats, attempts_used); raises the last error."""
    attempts = DETECT_ATTEMPTS if attempts is None else attempts   # resolved at call time so tests can shorten them
    waits = DETECT_WAITS_S if waits is None else waits
    aoi, inner = ctx.aoi
    model, meta, model_id = ctx.verifier
    process = process or scene_mod.process
    last = None
    for attempt in range(1, attempts + 1):
        try:
            objects, stats = process(rec["path"], aoi, inner, footprints=ctx.footprints, model=model, meta=meta, model_id=model_id,
                                     pfa=ctx.pfa, workers=ctx.workers, io_threads=ctx.io_threads, do_persistence=ctx.do_persistence,
                                     do_cnn=ctx.do_cnn, log=log)
            return objects, stats, attempt
        except Exception as exc:  # noqa: BLE001 transient reads: back off and try again
            last = exc
            if attempt < attempts:
                wait = waits[min(attempt - 1, len(waits) - 1)]
                log(f"{rec['product_id']}: detection attempt {attempt}/{attempts} failed: {type(exc).__name__}: {str(exc)[:160]}; retry in {wait:.0f} s")
                time.sleep(wait)
    raise last


def process_record(rec: dict, ctx: Context, ckpt: Checkpoint, plan: list[dict], *, force: bool = False, process=None, log=log) -> dict:
    """Detect, verify, match and identify one mirror scene; write its checkpoint. Returns the status record."""
    pid = rec["product_id"]
    start, stop = pd.Timestamp(rec["start"]), pd.Timestamp(rec["stop"])
    scene_time = start + (stop - start) / 2
    t0 = time.time()
    attempts_before = ckpt.attempts(pid)
    n_pos, n_mmsi = ais_coverage(scene_time)
    if n_pos == 0 and not force:
        log(f"{pid}: no ship-station AIS recorded within {AIS_WINDOW_S // 60} min of {scene_time:%H:%M:%S}; skipped")
        return ckpt.mark(pid, "skipped_no_ais", scene_time_utc=scene_time.isoformat(), ais_aoi_positions=0, ais_aoi_mmsi=0)
    run_id = run_id_for(rec, plan)
    log(f"{pid}: processing ({run_id}; {n_pos} AIS positions / {n_mmsi} MMSI in the AOI during the window"
        f"{'; retry ' + str(attempts_before + 1) if attempts_before else ''})")
    try:
        objects, stats, used = detect_with_retry(rec, ctx, process=process, log=log)
    except Exception as exc:  # noqa: BLE001 record and move on; the scene is retried next cycle
        log(f"{pid}: detection failed after {DETECT_ATTEMPTS} attempts: {type(exc).__name__}: {str(exc)[:200]}")
        return ckpt.mark(pid, "error", error=f"{type(exc).__name__}: {str(exc)[:300]}", traceback=traceback.format_exc()[-2000:],
                         attempts_total=attempts_before + DETECT_ATTEMPTS, run_id=run_id)
    if len(objects) and "acq_utc" not in objects:
        objects["acq_utc"] = start.isoformat()
    ais_window = ais_window_for(scene_time)
    positions_all = ship_stations(aisstream.load_positions())
    static_latest = aisstream.latest_static(aisstream.load_static())
    footprint = shapely.from_wkt(rec["footprint_wkt"])
    scene = {"scene_time": scene_time, "run_id": run_id, "footprint": footprint, "product_id": pid, "mission": rec["mission"],
             "acq_utc": start.isoformat(), "pass_dir": rec.get("pass_dir") or stats.get("pass_dir"), "orbit_rel": rec.get("orbit_rel") or stats.get("orbit_rel")}
    if objects.empty:
        contacts, ais_only = pd.DataFrame(), pd.DataFrame()
        counts = {"n_contacts": 0, "n_matched": 0, "n_unmatched": 0, "n_no_coverage": 0, "n_dark_leads": 0, "n_ais_only": 0,
                  "ais_aoi_positions": int(len(ais_window)), "ais_aoi_mmsi": int(ais_window.mmsi.nunique()) if len(ais_window) else 0,
                  "ais_footprint_positions": 0, "ais_footprint_mmsi": 0, "ais_near_footprint_mmsi": 0}
    else:
        contacts, ais_only, counts = matching.match_scene(objects, scene, ais_window, static_latest, positions_all, ctx.grid, ctx.aoi[0], log=log)
    ckpt.write_tables(pid, contacts, ais_only)
    cls = objects.confidence.value_counts().to_dict() if len(objects) else {}
    info = {
        "run_id": run_id, "mission": rec["mission"], "start_utc": start.isoformat(), "stop_utc": stop.isoformat(), "scene_time_utc": scene_time.isoformat(),
        "orbit_abs": int(rec["orbit"]), "orbit_rel": scene["orbit_rel"], "pass_dir": scene["pass_dir"], "aoi_overlap_km2": rec.get("aoi_overlap_km2"),
        "footprint_wkt": rec["footprint_wkt"], "path": rec["path"], "tested_km2": round(float(stats.get("tested_km2", 0.0)), 1),
        "blocks_processed": stats.get("blocks_processed"), "runtime_s": round(time.time() - t0, 1), "detect_runtime_s": stats.get("runtime_s"),
        "detect_attempts": int(used), "attempts_total": attempts_before + int(used), "io_retries": int(stats.get("io_retries", 0)),
        "n_objects": int(len(objects)), "n_high": int(cls.get("high", 0)), "n_medium": int(cls.get("medium", 0)), "n_fixed": int(cls.get("fixed", 0)),
        "n_low": int(cls.get("low", 0)), "n_candidates_pre_rules": stats.get("n_candidates_pre_rules", 0), "n_cnn_scored": stats.get("n_cnn_scored", 0),
        "n_cnn_vessel": int(contacts.cnn_vessel.fillna(False).astype(bool).sum()) if len(contacts) else 0, "cnn_model_id": stats.get("cnn_model_id"),
        "cnn_threshold": stats.get("cnn_threshold"), "enl_vv": stats.get("enl_vv"), "enl_vh": stats.get("enl_vh"),
        "ais_recorded_hours": int(len(aisstream.recorded_hours(positions_all))), "processed_utc": pd.Timestamp.now(tz="UTC").isoformat(), **counts,
    }
    log(f"{pid}: done in {info['runtime_s']:.0f} s ({used} detection attempt{'s' if used > 1 else ''}, {info['io_retries']} tile retries): "
        f"{info['n_contacts']} contacts ({info['n_matched']} matched, {info['n_unmatched']} unmatched, {info['n_no_coverage']} no_coverage), "
        f"{info['n_ais_only']} AIS-only, {info['tested_km2']:,.0f} km2 tested")
    return ckpt.mark(pid, "done", **info)


def rebuild_outputs(ctx: Context, ckpt: Checkpoint, since: pd.Timestamp, log=log) -> dict:
    from darkvessel.live.outputs import rebuild

    recs, contacts, ais_only = ckpt.load_all()
    if not recs:
        log("no processed scene yet; nothing to write")
        return {}
    hours = max(int(r.get("ais_recorded_hours") or 0) for r in recs)
    model_id = next((r.get("cnn_model_id") for r in recs if r.get("cnn_model_id")), None)
    thr = next((r.get("cnn_threshold") for r in recs if r.get("cnn_threshold") is not None), None)
    return rebuild(recs, contacts, ais_only, model_id, thr, hours, since.isoformat(), out_dir=ctx.out_dir, log=log)


def next_passes_text(plan: list[dict], now: pd.Timestamp, n: int = 3) -> str:
    nxt = [p for p in plan if p["stop_utc"] >= now][:n]
    return "; ".join(f"{p['mission']} {p['start_utc']:%m-%d %H:%M}" for p in nxt) if nxt else "none in the plan file"


def cycle(ctx: Context, ckpt: Checkpoint, since: pd.Timestamp, *, scene_ids: list[str] | None = None, force: bool = False,
          dry_run: bool = False, blocking: bool = True, candidates=None, process=None, log=log) -> dict:
    """One pass over the mirror under the cycle lock: find, process, rebuild. Returns counts.

    With `blocking=False` and the lock held by another process (the detached watcher), nothing is done and
    {"locked": True} is returned. A dry run reads only and takes no lock. `candidates` and `process` are injection
    points for the offline tests.
    """
    if dry_run:
        return _cycle(ctx, ckpt, since, scene_ids=scene_ids, force=force, dry_run=True, candidates=candidates, process=process, log=log)
    try:
        lock = CycleLock(ctx.lock_path, blocking=blocking).__enter__()
    except BlockingIOError:
        log(f"another live-pass process holds {ctx.lock_path} (the watcher?); nothing done")
        return {"locked": True, "candidates": 0, "new": 0, "processed": 0}
    try:
        return _cycle(ctx, ckpt, since, scene_ids=scene_ids, force=force, dry_run=dry_run, candidates=candidates, process=process, log=log)
    finally:
        lock.__exit__(None, None, None)


def _cycle(ctx: Context, ckpt: Checkpoint, since: pd.Timestamp, *, scene_ids, force, dry_run, candidates, process, log) -> dict:
    now = pd.Timestamp.now(tz="UTC")
    plan = load_plan()
    aoi, _ = ctx.aoi
    candidates = candidates or mirror_candidates
    cands = candidates(aoi, since, now, log=log)
    todo = [c for c in cands if not ckpt.done(c["product_id"])]
    if scene_ids:
        todo = [c for c in todo if c["product_id"] in set(scene_ids)]
        missing = set(scene_ids) - {c["product_id"] for c in cands}
        for pid in missing:  # a named scene outside the time filters: fetch it directly
            try:
                meta = aws.parse_product_id(pid)
                path = f"GRD/{meta['start'].year}/{meta['start'].month}/{meta['start'].day}/{meta['mode']}/{meta['pol']}/{pid}"
                rec = product_record(path, aoi)
                if not ckpt.done(pid) or force:
                    todo.append(rec)
            except Exception as exc:  # noqa: BLE001
                log(f"{pid}: cannot fetch: {type(exc).__name__}: {str(exc)[:120]}")
    log(f"mirror: {len(cands)} AOI scenes since {since:%m-%d %H:%M}, {len(todo)} new; next planned passes: {next_passes_text(plan, now)}")
    if dry_run:
        for c in cands:
            st = ckpt.status(c["product_id"])
            t = pd.Timestamp(c["start"])
            n_pos, n_mmsi = ais_coverage(t + (pd.Timestamp(c["stop"]) - t) / 2)
            log(f"  {c['product_id']} overlap {c['aoi_overlap_km2']:,.0f} km2, AIS in window {n_pos} pos / {n_mmsi} MMSI, "
                f"state {st['status'] if st else 'new'}")
        return {"candidates": len(cands), "new": len(todo), "processed": 0}
    done = 0
    for rec in todo:
        try:
            st = process_record(rec, ctx, ckpt, plan, force=force, process=process, log=log)
            done += st.get("status") == "done"
        except Exception as exc:  # noqa: BLE001 keep the loop alive
            log(f"{rec['product_id']}: unexpected failure: {type(exc).__name__}: {str(exc)[:200]}\n{traceback.format_exc()[-1500:]}")
            ckpt.mark(rec["product_id"], "error", error=f"{type(exc).__name__}: {str(exc)[:300]}", attempts_total=ckpt.attempts(rec["product_id"]) + 1)
    if done or not ctx.summary_path.exists():
        rebuild_outputs(ctx, ckpt, since, log=log)
    return {"candidates": len(cands), "new": len(todo), "processed": done}


def watch(ctx: Context, ckpt: Checkpoint, since: pd.Timestamp, poll_minutes: float = 10.0, once: bool = False, pid_path: Path | None = None,
          log=log, **kw) -> None:
    if pid_path is not None:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()))
    try:
        while True:
            try:
                cycle(ctx, ckpt, since, log=log, **kw)
            except Exception as exc:  # noqa: BLE001 the watcher must survive a bad cycle
                log(f"cycle failed: {type(exc).__name__}: {str(exc)[:200]}\n{traceback.format_exc()[-1500:]}")
            if once:
                break
            time.sleep(poll_minutes * 60)
    finally:
        if pid_path is not None and pid_path.exists():
            try:
                if pid_path.read_text().strip() == str(os.getpid()):
                    pid_path.unlink()
            except OSError:
                pass
