"""Build the verifier training set: AI2 labels -> scene sample -> CFAR candidates + 64 px chips.

Usage: python scripts/04_build_training_set.py [--n-other 90] [--cap-labelled 12] [--cap-empty 3]
                                               [--workers 3] [--max-scenes N] [--region sea_asia|other]
Resumable: scenes with data/chips/<product_id>.npz and index/<product_id>.parquet are skipped.
Outputs:
  data/ml/ai2_s1_labels.parquet, data/ml/ai2_s1_windows.parquet   decoded AI2 tables (step 1)
  data/ml/scene_sample.csv, data/ml/window_sample.parquet         the selection with splits (step 2)
  data/chips/<product_id>.npz + data/chips/index/<product_id>.*   per-scene chips and indices (step 3)
  data/ml/candidates.parquet, data/ml/cfar_misses.parquet         concatenated indices
  data/ml/build_summary.json, data/ml/build_log.txt
"""

import argparse
import json
import multiprocessing as mp
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from darkvessel.config import DATA_DIR
from darkvessel.ml.chips import process_scene, scene_done
from darkvessel.ml.labels import build_label_table, build_window_table
from darkvessel.ml.sample import assign_scene_splits, select_scenes, select_windows

ML_DIR = DATA_DIR / "ml"
CHIP_DIR = DATA_DIR / "chips"


def log(msg: str, path: Path = ML_DIR / "build_log.txt"):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(path, "a") as f:
        f.write(line + "\n")


def _worker(product_id, aws_path, windows, labels, out_dir):
    try:
        return process_scene(product_id, aws_path, windows, labels, out_dir, log=lambda m: None)
    except Exception as e:
        return {"product_id": product_id, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-other", type=int, default=90, help="scenes sampled outside Southeast Asia")
    ap.add_argument("--cap-labelled", type=int, default=12)
    ap.add_argument("--cap-empty", type=int, default=3)
    ap.add_argument("--test-frac", type=float, default=0.2)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-scenes", type=int, default=None, help="process at most N not-yet-done scenes")
    ap.add_argument("--region", default=None, help="restrict this run to one region")
    ap.add_argument("--seed", type=int, default=20261002)
    args = ap.parse_args()
    ML_DIR.mkdir(parents=True, exist_ok=True)
    CHIP_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # Step 1: label tables (cheap, always rebuilt so they match the code)
    labels = build_label_table()
    windows = build_window_table()
    labels.to_parquet(ML_DIR / "ai2_s1_labels.parquet", index=False)
    windows.to_parquet(ML_DIR / "ai2_s1_windows.parquet", index=False)

    # Step 2: selection (deterministic; rewritten each run with the same seed)
    sample_path = ML_DIR / "window_sample.parquet"
    scenes = assign_scene_splits(select_scenes(windows, n_other=args.n_other, seed=args.seed), args.test_frac, args.seed)
    wsel = select_windows(windows, scenes, args.cap_labelled, args.cap_empty, args.seed)
    scenes.to_csv(ML_DIR / "scene_sample.csv", index=False)
    wsel.to_parquet(sample_path, index=False)
    log(f"selection: {len(scenes)} scenes ({scenes.region.value_counts().to_dict()}), {len(wsel)} windows "
        f"(use: {wsel.use.value_counts().to_dict()}), labels in selected windows {int(wsel.n_labels.sum())}")

    # Step 3: per-scene candidates and chips
    todo = scenes[~scenes.product_id.map(lambda p: scene_done(CHIP_DIR, p))]
    if args.region:
        todo = todo[todo.region == args.region]
    if args.max_scenes:
        todo = todo.head(args.max_scenes)
    log(f"{len(todo)} scenes to process, {len(scenes) - len(todo)} already done")
    ctx = mp.get_context("spawn")
    n_done = n_err = 0
    with ProcessPoolExecutor(args.workers, mp_context=ctx) as ex:
        futs = {}
        for rec in todo.itertuples(index=False):
            w = wsel[wsel.product_id == rec.product_id]
            lab = labels[labels.product_id == rec.product_id]
            futs[ex.submit(_worker, rec.product_id, w.aws_path.iloc[0], w, lab, CHIP_DIR)] = rec.product_id
        for fut in as_completed(futs):
            s = fut.result()
            if "error" in s:
                n_err += 1
                log(f"FAILED {s['product_id']}: {s['error']}")
            else:
                n_done += 1
                log(f"done {s['product_id']} windows {s['n_windows']} cands {s['n_candidates']} pos {s['n_positive']} "
                    f"labels {s['n_labels']} (sea {s['n_labels_on_sea']}, cfar hit {s['n_cfar_detected']}) "
                    f"{s['runtime_s']}s [{n_done}/{len(todo)}]")

    # Concatenate whatever is finished
    idx = sorted((CHIP_DIR / "index").glob("*.parquet"))
    cands = [pd.read_parquet(p) for p in idx if not p.name.endswith(".misses.parquet")]
    misses = [pd.read_parquet(p) for p in idx if p.name.endswith(".misses.parquet")]
    cands = pd.concat([c for c in cands if len(c)], ignore_index=True) if any(len(c) for c in cands) else pd.DataFrame()
    misses = pd.concat([m for m in misses if len(m)], ignore_index=True) if any(len(m) for m in misses) else pd.DataFrame()
    if len(cands):
        cands = cands.merge(scenes[["product_id", "scene_split"]], on="product_id", how="left")
        cands["use"] = cands.window_id.map(wsel.set_index("window_id").use)
        cands.to_parquet(ML_DIR / "candidates.parquet", index=False)
    if len(misses):
        misses = misses.merge(scenes[["product_id", "scene_split"]], on="product_id", how="left")
        misses["use"] = misses.window_id.map(wsel.set_index("window_id").use)
        misses.to_parquet(ML_DIR / "cfar_misses.parquet", index=False)
    done_scenes = [p for p in scenes.product_id if scene_done(CHIP_DIR, p)]
    summary = {
        "scenes_selected": int(len(scenes)), "scenes_done": len(done_scenes), "scenes_failed_this_run": n_err,
        "scenes_by_region": scenes.region.value_counts().to_dict(),
        "scenes_by_split": {f"{r}/{s}": int(n) for (r, s), n in scenes.groupby(["region", "scene_split"]).size().items()},
        "windows_selected": int(len(wsel)), "windows_by_use": wsel.use.value_counts().to_dict(),
        "candidates": int(len(cands)), "positives": int(cands.is_vessel.sum()) if len(cands) else 0,
        "candidates_by_use": cands.groupby("use").is_vessel.agg(["size", "sum"]).to_dict() if len(cands) else {},
        "labels_in_done_scenes": int(len(misses)),
        "labels_on_testable_sea": int(misses.on_testable_sea.sum()) if len(misses) else 0,
        "labels_cfar_detected": int(misses.cfar_detected.sum()) if len(misses) else 0,
        "args": vars(args), "runtime_s": round(time.time() - t0, 1),
    }
    (ML_DIR / "build_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    log(json.dumps({k: v for k, v in summary.items() if k not in ("args",)}, default=str))


if __name__ == "__main__":
    main()
