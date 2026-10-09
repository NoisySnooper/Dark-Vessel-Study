"""CNN verifier on every radar object of the September 2026 regional run.

Chips are cut exactly as in training and as in scripts/14_cnn_shared_cells.py: 64 x 64 px VV/VH sigma0 in dB,
thermal noise not removed, read remotely from the AWS COGs, scored with data/models/verifier_v0.pt as the mean of
the 8 dihedral views. Detection is verified here; identification (AIS) comes after, in other modules.

Reads. Objects of one scene are grouped by the 1024 x 1024 px COG tile that holds their centre pixel. Each group is
one windowed read of VV and VH covering the group's chips (centre +- 32 px plus one pixel, clipped to the image),
so a chip that spills into a neighbouring tile is still read whole. Groups are read in tile order with a few reads
in flight while the CNN scores the previous ones; no scene is downloaded.

Reuse. A score in data/ml/shared_cells_cnn.parquet is kept when det_id, scene_id, row, col, lon and lat all match
exactly (same detection, same position). Reused objects are still read, for their chip features, and a fixed
random sample of them is rescored so the build can check that a rescore gives the reused value.

Checkpoints. One parquet per scene and phase, <cache>/<phase>/<scene_id>.parquet, written atomically. A scene is
skipped on a rerun only when its checkpoint holds the same model id and the same det_id set.

Chip features: cnn_chip_valid_frac = finite share of the central 8 x 8 px of both channels (as scripts/06 and the
live pass); chip_valid_frac_full = finite share of the whole chip; bg_vv_db and bg_vh_db = median dB of the chip
outside its central 16 x 16 px (as scripts/14).
"""

from __future__ import annotations

import hashlib
import os
import time
import warnings
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from darkvessel.ml.chips import CHIP_HALF, chips_db
from darkvessel.ml.evaluate import wilson
from darkvessel.ml.model import MODEL_ID

TILE_PX = 1024  # COG tile size of the AWS GRD measurement files (deflate, 1024 px tiles)
PHASES = {"main": ("high", "medium", "fixed"), "low": ("low",)}
OBJECT_COLS = ["det_id", "scene_id", "mission", "confidence", "row", "col", "lon", "lat", "length_est_m"]
FEATURE_COLS = ["cnn_chip_valid_frac", "chip_valid_frac_full", "bg_vv_db", "bg_vh_db"]
CHECKPOINT_COLS = ["det_id", "scene_id", "cnn_score", "cnn_score_source", "cnn_rescore_check", *FEATURE_COLS,
                   "cnn_model_id", "scene_elapsed_s", "scored_utc"]
SOURCE_SCORED, SOURCE_REUSED = "scored", "reused_shared_cells"
VERIFY_N, VERIFY_SEED, VERIFY_TOL = 200, 20261009, 1e-4

# Reporting boxes of scripts/21_viirs_regions.py (plain boxes, not boundaries or claims), tested in this order.
REGIONS = {"Gulf of Tonkin": (105.5, 17.0, 110.0, 22.5), "North shelf": (110.0, 18.0, 118.0, 23.5),
           "Gulf of Thailand": (99.0, 6.0, 105.0, 14.0), "South Vietnam shelf": (105.0, 6.0, 110.0, 12.0),
           "Central sea": (110.0, 6.0, 118.0, 17.0), "Southern sea": (102.0, -3.5, 110.0, 6.0)}
LENGTH_BINS = ((0, 25), (25, 50), (50, 100), (100, 200), (200, None))

TRAINING_NOTE = ("Trained on AI2 Skylight Sentinel-1 vessel point labels (allenai/vessel-detection-sentinels, "
                 "Apache-2.0) on Sentinel-1A/1B IW GRD of 2020 to 2022; contains modified Copernicus Sentinel data "
                 "2020-2022.")
TRANSFER_CAVEAT = ("Applied without retraining to Sentinel-1C and 1D of 2026. Precision and recall on 1C/1D are not "
                   "yet scored against truth; acceptance shares say how 1C/1D chips look to a 1A/1B-trained model, "
                   "not how many vessels are there. Fixed structures (platforms, turbines) look like ships in one "
                   "chip; the persistence class keeps precedence for them.")


# ----------------------------------------------------------------------------- model and scene access
def model_id_for(path: str | Path) -> str:
    """verifier_v0_<sha256 prefix of the weights>, as scripts/06 writes it."""
    return f"{MODEL_ID}_{hashlib.sha256(Path(path).read_bytes()).hexdigest()[:8]}"


def scene_path(product_id: str) -> str:
    """Bucket path of a product (same rule as darkvessel.viz.demo._scene_path, without importing the demo module)."""
    from darkvessel.s1.aws import parse_product_id

    p = parse_product_id(product_id)
    t = p["start"]
    return f"GRD/{t.year}/{t.month}/{t.day}/{p['mode']}/{p['pol']}/{product_id}"


def clear_remote_cache(prefix: str) -> None:
    """Forget GDAL's cached /vsicurl/ state under `prefix`, so a retry does not reuse a failed response."""
    try:
        from osgeo import gdal

        gdal.VSICurlPartialClearCache(prefix)
    except Exception:  # noqa: BLE001 (best effort; a retry still happens)
        pass


class SceneReader:
    """Remote VV and VH sigma0 (thermal noise not removed) of one scene, with retries on transient read errors.

    A dropped proxy makes GDAL see an error page instead of the TIFF ('not recognized as being in a supported file
    format'), and on 2026-10-09 the mirror answered about one request in five with HTTP 404 'NoSuchBucket'. Each retry
    clears GDAL's cached state for the product first and waits longer (2, 6, 18, 54 s). The annotation, manifest and
    calibration files (plain HTTPS, no retry on 4xx in darkvessel.s1.aws) are fetched here, under the same retries,
    before any pixel is read. Pixel reads go through GDAL, whose own retries need GDAL_HTTP_RETRY_CODES to include 404
    (set by scripts/32_cnn_regional.py).
    """

    def __init__(self, product_id: str, io_workers: int = 4, retries: int = 5, wait_s: float = 2.0):
        from darkvessel.s1.aws import vsicurl
        from darkvessel.s1.grd import GRDScene

        self.scene = GRDScene(scene_path(product_id))
        self.scene._pool = ThreadPoolExecutor(io_workers)  # COG tile fetches are I/O bound; keep the count modest
        self.prefix = vsicurl(self.scene.path, "")
        self.retries, self.wait_s, self.n_reads = retries, wait_s, 0
        self.shape = self._retry(lambda: self.scene.shape)
        self._retry(lambda: [self.scene.calibration(p) for p in ("VV", "VH")])

    def _retry(self, fn):
        for attempt in range(self.retries):
            try:
                return fn()
            except Exception:  # noqa: BLE001 (transient network reads)
                if attempt == self.retries - 1:
                    raise
                clear_remote_cache(self.prefix)
                self.scene._local = type(self.scene._local)()  # drop the per-thread dataset handles
                time.sleep(self.wait_s * 3 ** attempt)

    def read(self, win: tuple[int, int, int, int]) -> dict:
        from rasterio.windows import Window

        r0, r1, c0, c1 = win
        w = Window(c0, r0, c1 - c0, r1 - r0)
        out = self._retry(lambda: {p: self.scene.read_sigma0(p, w, denoise=False) for p in ("VV", "VH")})
        self.n_reads += 1
        return out

    def close(self) -> None:
        if self.scene._pool is not None:
            self.scene._pool.shutdown(wait=False)


# ----------------------------------------------------------------------------- grouping, chips, features
def tile_groups(rows, cols, shape: tuple[int, int], tile: int = TILE_PX, half: int = CHIP_HALF):
    """[(indices, (r0, r1, c0, c1)), ...]: objects grouped by the tile holding their centre, one window per group.

    Groups come in tile order (row-major). The window is the bounding box of the group's chips, the same bounds as
    scripts/14 (int(min) - half - 1 to int(max) + half + 2), clipped to the image.
    """
    rows, cols = np.asarray(rows, float), np.asarray(cols, float)
    if len(rows) == 0:
        return []
    H, W = shape
    key = np.floor(rows / tile).astype(np.int64) * 1_000_000 + np.floor(cols / tile).astype(np.int64)
    order = np.argsort(key, kind="stable")
    ks = key[order]
    starts = np.r_[0, np.nonzero(np.diff(ks))[0] + 1]
    ends = np.r_[starts[1:], len(ks)]
    out = []
    for a, b in zip(starts, ends):
        sel = np.sort(order[a:b])
        r0 = max(0, int(rows[sel].min()) - half - 1)
        r1 = min(H, int(rows[sel].max()) + half + 2)
        c0 = max(0, int(cols[sel].min()) - half - 1)
        c1 = min(W, int(cols[sel].max()) + half + 2)
        out.append((sel, (r0, r1, c0, c1)))
    return out


_RING = np.ones((2 * CHIP_HALF, 2 * CHIP_HALF), bool)
_RING[CHIP_HALF - 8:CHIP_HALF + 8, CHIP_HALF - 8:CHIP_HALF + 8] = False


def chip_features(chips: np.ndarray) -> dict:
    """Valid fractions (centre 8 x 8 px and whole chip) and background dB outside the central 16 x 16 px."""
    c = chips.astype(np.float32)
    fin = np.isfinite(c)
    h = c.shape[-1] // 2
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN chips give NaN
        return {"cnn_chip_valid_frac": fin[:, :, h - 4:h + 4, h - 4:h + 4].mean(axis=(1, 2, 3)).astype(np.float32),
                "chip_valid_frac_full": fin.mean(axis=(1, 2, 3)).astype(np.float32),
                "bg_vv_db": np.nanmedian(c[:, 0][:, _RING], axis=1).astype(np.float32),
                "bg_vh_db": np.nanmedian(c[:, 1][:, _RING], axis=1).astype(np.float32)}


def iter_group_chips(reader, groups, rows, cols, workers: int = 2, max_pending: int = 6):
    """Yield (indices, chips) per group in order, with at most `max_pending` reads in flight."""
    rows, cols = np.asarray(rows, float), np.asarray(cols, float)

    def job(g):
        sel, (r0, r1, c0, c1) = g
        sigma = reader.read((r0, r1, c0, c1))
        return sel, chips_db(sigma, rows[sel], cols[sel], r0, c0)

    it = iter(groups)
    with ThreadPoolExecutor(max(1, workers)) as ex:
        pending = deque()
        for g in it:
            pending.append(ex.submit(job, g))
            if len(pending) >= max_pending:
                break
        while pending:
            sel, ch = pending.popleft().result()
            nxt = next(it, None)
            if nxt is not None:
                pending.append(ex.submit(job, nxt))
            yield sel, ch


# ----------------------------------------------------------------------------- reuse
def align_prior(obj: pd.DataFrame, prior: pd.DataFrame | None) -> pd.DataFrame:
    """Prior score and features per object row; NaN unless det_id, scene_id, row, col, lon, lat all match exactly.

    Columns: prior_score, prior_bg_vv_db, prior_bg_vh_db, prior_vessel (bool, False when NaN), prior_status
    ('match', 'position_differs', 'none'). Duplicated det_ids in the prior are never reused.
    """
    out = pd.DataFrame({"prior_score": np.nan, "prior_bg_vv_db": np.nan, "prior_bg_vh_db": np.nan,
                        "prior_vessel": False, "prior_status": "none"}, index=obj.index)
    if prior is None or not len(prior):
        return out
    p = prior.drop_duplicates("det_id", keep=False).set_index("det_id")
    p = p.reindex(obj.det_id.to_numpy())
    have = p.cnn_score.notna().to_numpy()
    same = have.copy()
    for c in ("scene_id", "row", "col", "lon", "lat"):
        same &= (p[c].to_numpy() == obj[c].to_numpy())
    out["prior_status"] = np.where(same, "match", np.where(have, "position_differs", "none"))
    out.loc[same, "prior_score"] = p.cnn_score.to_numpy()[same]
    for c in ("bg_vv_db", "bg_vh_db"):
        if c in p:
            out.loc[same, "prior_" + c] = p[c].to_numpy(float)[same]
    if "cnn_vessel" in p:
        out.loc[same, "prior_vessel"] = p.cnn_vessel.to_numpy()[same].astype(bool)
    return out


def verification_mask(det_ids, reused, n: int = VERIFY_N, seed: int = VERIFY_SEED) -> np.ndarray:
    """Fixed random sample of `n` reused objects to rescore; independent of row order (drawn on sorted det_ids)."""
    det_ids = np.asarray(det_ids, dtype=object)
    reused = np.asarray(reused, bool)
    cand = np.nonzero(reused)[0]
    cand = cand[np.argsort(det_ids[cand], kind="stable")]
    mask = np.zeros(len(det_ids), bool)
    if len(cand) <= n:
        mask[cand] = True
    else:
        mask[np.random.default_rng(seed).choice(cand, n, replace=False)] = True
    return mask


# ----------------------------------------------------------------------------- one scene
def score_scene(obj: pd.DataFrame, prior_score, verify, reader, score_fn, *, workers: int = 2, batch: int = 512,
                max_pending: int = 6) -> tuple[pd.DataFrame, dict]:
    """Chips, features and scores for the objects of one scene.

    `prior_score`: reused score per row (NaN = score anew). `verify`: rows rescored although a prior exists.
    `reader`: .shape and .read((r0, r1, c0, c1)) -> {'VV': sigma0, 'VH': sigma0}. `score_fn(chips)` -> scores.
    """
    n = len(obj)
    rows, cols = obj.row.to_numpy(float), obj.col.to_numpy(float)
    prior_score = np.asarray(prior_score, float)
    verify = np.asarray(verify, bool)
    reused = np.isfinite(prior_score)
    need = ~reused | verify
    new = np.full(n, np.nan, np.float32)
    feats = {k: np.full(n, np.nan, np.float32) for k in FEATURE_COLS}
    buf_idx, buf_chips, nbuf = [], [], 0

    def flush():
        if buf_idx:
            new[np.concatenate(buf_idx)] = np.asarray(score_fn(np.concatenate(buf_chips)), np.float32)
            buf_idx.clear()
            buf_chips.clear()

    groups = tile_groups(rows, cols, reader.shape)
    for sel, ch in iter_group_chips(reader, groups, rows, cols, workers=workers, max_pending=max_pending):
        for k, v in chip_features(ch).items():
            feats[k][sel] = v
        m = need[sel]
        if m.any():
            buf_idx.append(sel[m])
            buf_chips.append(ch[m])
            nbuf += int(m.sum())
        if nbuf >= batch:
            flush()
            nbuf = 0
    flush()
    out = pd.DataFrame({"det_id": obj.det_id.to_numpy(), "scene_id": obj.scene_id.to_numpy(),
                        "cnn_score": np.where(reused, prior_score, new).astype(np.float32),
                        "cnn_score_source": np.where(reused, SOURCE_REUSED, SOURCE_SCORED),
                        "cnn_rescore_check": np.where(verify & reused, new, np.nan).astype(np.float32), **feats})
    return out, {"n": n, "scored": int(need.sum()), "reused": int(reused.sum()), "reads": len(groups)}


# ----------------------------------------------------------------------------- checkpoints
def checkpoint_path(cache_dir: str | Path, phase: str, scene_id: str) -> Path:
    return Path(cache_dir) / phase / f"{scene_id}.parquet"


def write_parquet_atomic(df: pd.DataFrame, path: str | Path, **kw) -> Path:
    """Write to a temporary name in the same folder, then rename, so readers never see half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    df.to_parquet(tmp, index=False, **kw)
    os.replace(tmp, path)
    return path


def checkpoint_ok(path: str | Path, model_id: str, det_ids) -> bool:
    """True when the checkpoint exists, was written with `model_id` and holds exactly `det_ids`."""
    path = Path(path)
    if not path.exists():
        return False
    try:
        ck = pd.read_parquet(path, columns=["det_id", "cnn_model_id", "cnn_score"])
    except Exception:  # noqa: BLE001 (truncated or foreign file: redo the scene)
        return False
    if not len(ck) or (ck.cnn_model_id != model_id).any() or ck.cnn_score.isna().any():
        return False
    return set(ck.det_id) == set(det_ids) and len(ck) == len(set(det_ids))


def run_phase(obj: pd.DataFrame, prior: pd.DataFrame, verify: np.ndarray, *, cache_dir, phase: str, model_id: str,
              score_fn, reader_factory, workers: int = 2, max_scenes: int | None = None, pause_s: float = 60.0,
              should_stop=None, log=print) -> dict:
    """Score the scenes of one phase, skipping checkpointed ones. `prior` and `verify` are aligned to `obj` rows.

    After 3 failed scenes in a row the run pauses (pause_s, doubling up to 16 x) instead of running through the list
    while the network is down; scenes that failed get one more try at the end. A rerun retries what is left.
    `should_stop()` is asked after every checkpointed scene; when true the phase returns early with stopped = True.
    """
    obj = obj.reset_index(drop=True)
    prior = prior.reset_index(drop=True)
    verify = np.asarray(verify, bool)
    scenes = sorted(obj.scene_id.unique())
    done, todo = [], []
    for sid in scenes:
        idx = np.nonzero(obj.scene_id.to_numpy() == sid)[0]
        (done if checkpoint_ok(checkpoint_path(cache_dir, phase, sid), model_id, obj.det_id.to_numpy()[idx])
         else todo).append((sid, idx))
    if max_scenes is not None:
        todo = todo[:max_scenes]
    log(f"phase {phase}: {len(scenes)} scenes, {len(done)} checkpointed, {len(todo)} to do "
        f"({sum(len(i) for _, i in todo)} objects)")
    t_all, n_left, failed, streak = time.time(), sum(len(i) for _, i in todo), [], 0
    secs, objs = 0.0, 0
    queue = [(k, sid, idx, 1) for k, (sid, idx) in enumerate(todo, 1)]
    stopped = False
    while queue:
        k, sid, idx, attempt = queue.pop(0)
        t0 = time.time()
        reader = None
        try:
            reader = reader_factory(sid)
            df, st = score_scene(obj.iloc[idx], prior.prior_score.to_numpy()[idx], verify[idx], reader, score_fn,
                                 workers=workers, max_pending=max(6, 2 * workers))
        except Exception as e:  # noqa: BLE001 (one bad scene must not stop the run; a rerun retries it)
            log(f"  FAILED {sid} (try {attempt}): {type(e).__name__}: {str(e)[:200]}")
            if attempt == 1:
                queue.append((k, sid, idx, 2))
            else:
                failed.append(sid)
            streak += 1
            if streak >= 3 and pause_s > 0:
                wait = pause_s * 2 ** min(streak - 3, 4)
                log(f"  {streak} failures in a row: pausing {wait:.0f} s")
                time.sleep(wait)
            continue
        finally:
            if reader is not None and hasattr(reader, "close"):
                reader.close()
        streak = 0
        el = time.time() - t0
        df["cnn_model_id"] = model_id
        df["scene_elapsed_s"] = np.float32(el)
        df["scored_utc"] = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        write_parquet_atomic(df[CHECKPOINT_COLS], checkpoint_path(cache_dir, phase, sid))
        secs, objs, n_left = secs + el, objs + len(idx), n_left - len(idx)
        eta_h = secs / max(objs, 1) * n_left / 3600
        log(f"  {phase} {k}/{len(todo)} {sid}: {st['n']} objects ({st['scored']} scored, {st['reused']} reused), "
            f"{st['reads']} reads, {el:.0f} s; ETA {eta_h:.1f} h by objects")
        if should_stop is not None and should_stop():
            stopped = True
            break
    return {"phase": phase, "scenes": len(scenes), "checkpointed_before": len(done), "attempted": len(todo),
            "failed": failed, "stopped": stopped, "elapsed_s": round(time.time() - t_all, 1)}


def load_checkpoints(cache_dir, phase: str, scene_ids=None) -> pd.DataFrame:
    """All checkpoint rows of a phase (optionally only `scene_ids`)."""
    d = Path(cache_dir) / phase
    paths = sorted(d.glob("*.parquet")) if d.exists() else []
    if scene_ids is not None:
        keep = set(scene_ids)
        paths = [p for p in paths if p.stem in keep]
    if not paths:
        return pd.DataFrame(columns=CHECKPOINT_COLS)
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


# ----------------------------------------------------------------------------- outputs
SCORE_COLS = ["det_id", "scene_id", "mission", "confidence", "cnn_score", "cnn_vessel", "cnn_threshold",
              "cnn_model_id", "cnn_score_source", "cnn_chip_valid_frac", "chip_valid_frac_full", "bg_vv_db",
              "bg_vh_db"]


def score_table(ck: pd.DataFrame, obj: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """One row per scored object: SCORE_COLS, float32 numbers, sorted by scene and det_id."""
    m = ck.drop(columns=["scene_id"]).merge(obj[["det_id", "scene_id", "mission", "confidence"]], on="det_id",
                                           how="inner", validate="one_to_one")
    m["cnn_vessel"] = m.cnn_score.astype(np.float64) >= float(threshold)
    m["cnn_threshold"] = float(threshold)  # float64, so cnn_score >= cnn_threshold reproduces cnn_vessel exactly
    for c in ("cnn_score", *FEATURE_COLS):
        m[c] = m[c].astype(np.float32)
    for c in ("mission", "confidence", "cnn_model_id", "cnn_score_source"):
        m[c] = m[c].astype("category")
    return m[SCORE_COLS].sort_values(["scene_id", "det_id"]).reset_index(drop=True)


def region_of(lon, lat) -> np.ndarray:
    """Reporting box of each point ('other' outside every box); the first box in REGIONS order wins."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    out = np.full(len(lon), "other", dtype=object)
    for name, (w, s, e, n) in reversed(list(REGIONS.items())):
        out[(lon >= w) & (lon < e) & (lat >= s) & (lat < n)] = name
    return out


def length_bin(length_m) -> np.ndarray:
    L = np.asarray(length_m, float)
    out = np.full(len(L), "unknown", dtype=object)
    for lo, hi in LENGTH_BINS:
        lab = f"{lo}-{hi} m" if hi is not None else f"{lo} m and longer"
        out[(L >= lo) & (L < (np.inf if hi is None else hi))] = lab
    return out


def length_bin_labels() -> list[str]:
    return [f"{lo}-{hi} m" if hi is not None else f"{lo} m and longer" for lo, hi in LENGTH_BINS]


def accept_row(g: pd.DataFrame) -> dict:
    """n, accepted, share with Wilson 95 % interval, score quartiles, chip background medians, median length."""
    n, k = int(len(g)), int(g.cnn_vessel.sum())
    lo, hi = wilson(k, n)
    q = g.cnn_score.astype(float).quantile([0.25, 0.5, 0.75]) if n else pd.Series([np.nan] * 3)
    r = lambda x, d=3: None if x is None or not np.isfinite(x) else round(float(x), d)  # noqa: E731
    out = {"n": n, "accepted": k, "accept_share": r(k / n if n else np.nan), "accept_ci95": [r(lo), r(hi)],
           "score_q25_q50_q75": [r(x) for x in q],
           "bg_vv_db_median": r(g.bg_vv_db.astype(float).median(), 2) if n else None,
           "bg_vh_db_median": r(g.bg_vh_db.astype(float).median(), 2) if n else None}
    if "length_est_m" in g:
        out["length_median_m"] = r(g.length_est_m.astype(float).median(), 1) if n else None
    return out


def accept_table(df: pd.DataFrame, by: list[str]) -> list[dict]:
    """accept_row per group of `by` (observed groups only), keys first."""
    rows = []
    for key, g in df.groupby(by, observed=True, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        rows.append({**{b: (v if not isinstance(v, (np.integer, np.floating)) else v.item()) for b, v in zip(by, key)},
                     **accept_row(g)})
    return rows


def reuse_check(ck: pd.DataFrame, prior_aligned: pd.DataFrame, obj: pd.DataFrame, threshold: float,
                tol: float = VERIFY_TOL) -> dict:
    """Rescore sample against reused scores, and chip background of every reused object against the prior's."""
    p = prior_aligned.assign(det_id=obj.det_id.to_numpy())
    m = ck.merge(p, on="det_id", how="inner")
    reused = m[m.cnn_score_source == SOURCE_REUSED]
    s = reused[reused.cnn_rescore_check.notna()]
    d = (s.cnn_rescore_check.astype(float) - s.prior_score.astype(float)).abs()
    bg = pd.concat([(reused.bg_vv_db.astype(float) - reused.prior_bg_vv_db).abs(),
                    (reused.bg_vh_db.astype(float) - reused.prior_bg_vh_db).abs()]).dropna()
    return {"reused": int(len(reused)), "rescored_sample": int(len(s)), "tolerance": tol,
            "max_abs_diff": None if not len(d) else float(d.max()),
            "within_tolerance": int((d <= tol).sum()),
            "verdict_agree": int(((s.cnn_rescore_check.astype(float) >= threshold)
                                  == s.prior_vessel.astype(bool)).sum()),
            "pass": bool(len(s) > 0 and (d <= tol).all()),
            "bg_compared": int(len(bg)), "bg_max_abs_diff_db": None if not len(bg) else float(bg.max()),
            "position_differs": int((prior_aligned.prior_status == "position_differs").sum())}


# Regional contact columns left out of the verified GeoPackage, so both CRS layers fit in one file under 20 MB:
# lat and lon repeat the geometry; acq_utc is the scene start time (in det_id, and in the file's `scenes` table by
# scene_idx); ais_status ('not_checked' on every row) and caveat (one constant text) go to the about layer.
VERIFIED_DROP = ("lat", "lon", "acq_utc", "ais_status", "caveat")
CNN_GPKG_COLS = ["cnn_score", "cnn_vessel"]
CONTACT_CLASSES = ("high", "medium")


def verified_layer(contacts, scores: pd.DataFrame, drop=VERIFIED_DROP):
    """Contact rows (a GeoDataFrame with det_id) with cnn_score (rounded to 4 decimals) and cnn_vessel joined.

    Every contact column except `drop` is kept, in its order, then the CNN columns and the geometry. Unscored
    contacts keep a null score and verdict; scores of objects that are not contacts are ignored.
    """
    s = scores[["det_id", "cnn_score", "cnn_vessel"]].drop_duplicates("det_id")
    g = contacts.drop(columns=[c for c in (*CNN_GPKG_COLS, *drop) if c in contacts]).merge(
        s, on="det_id", how="left", validate="one_to_one")
    g["cnn_score"] = g.cnn_score.astype(float).round(4)
    g["cnn_vessel"] = pd.array(np.where(g.cnn_score.isna(), None, g.cnn_vessel), dtype="boolean")
    cols = [c for c in g.columns if c not in (*CNN_GPKG_COLS, "geometry")]
    return g[[*cols, *CNN_GPKG_COLS, "geometry"]]


WIND_BINS_MS = (0.0, 3.0, 6.0, 9.0, np.inf)


def summarise(table: pd.DataFrame, obj: pd.DataFrame, shared_ids=None, weather: pd.DataFrame | None = None) -> dict:
    """Acceptance with Wilson 95 % intervals by class, mission, length bin and reporting box, plus the comparison
    inside and outside the shared 1C/1D cells (`shared_ids`: det_ids of data/ml/shared_cells_cnn.parquet) and, when
    `weather` (det_id, wind_ms, deep_convection) is given, by wind speed bin and deep convection."""
    d = table.merge(obj[["det_id", "lon", "lat", "length_est_m"]], on="det_id", how="left", validate="one_to_one")
    d["confidence"] = d.confidence.astype(str)
    d["mission"] = d.mission.astype(str)
    d["length_bin"] = pd.Categorical(length_bin(d.length_est_m), categories=length_bin_labels() + ["unknown"],
                                     ordered=True)
    d["region"] = pd.Categorical(region_of(d.lon, d.lat), categories=list(REGIONS) + ["other"], ordered=True)
    c = d[d.confidence.isin(CONTACT_CLASSES)]
    out = {"by_class": accept_table(d, ["confidence"]),
           "contacts_high_medium": accept_row(c),
           "contacts_by_mission": accept_table(c, ["mission"]),
           "by_mission_class": accept_table(d, ["mission", "confidence"]),
           "contacts_by_length_bin": accept_table(c, ["length_bin"]),
           "by_length_bin_class": accept_table(d, ["length_bin", "confidence"]),
           "contacts_by_region": accept_table(c, ["region"]),
           "by_region_class": accept_table(d, ["region", "confidence"])}
    if weather is not None and len(weather):
        w = c.merge(weather[["det_id", "wind_ms", "deep_convection"]], on="det_id", how="inner")
        labels = [f"{a:g}-{b:g} m/s" if np.isfinite(b) else f"{a:g} m/s and more" for a, b in
                  zip(WIND_BINS_MS[:-1], WIND_BINS_MS[1:])]
        w["wind_bin"] = pd.cut(w.wind_ms, WIND_BINS_MS, labels=labels, right=False)
        w["deep_convection"] = w.deep_convection.astype("boolean").astype(str)
        out["contacts_by_wind_bin_class"] = accept_table(w.dropna(subset=["wind_bin"]), ["wind_bin", "confidence"])
        out["contacts_by_deep_convection_class"] = accept_table(w, ["deep_convection", "confidence"])
    if shared_ids is not None:
        inside = d.det_id.isin(set(shared_ids))
        out["inside_shared_cells"] = accept_table(d[inside], ["mission", "confidence"])
        out["outside_shared_cells"] = accept_table(d[~inside], ["mission", "confidence"])
    return out
