"""Recall check for the snapshot scan (prefilter and substring gates).

Re-reads a random sample of snapshot files, weighted by record count, with

  * a much broader anchor prefilter (plain substrings, no word boundaries, plus
    'dark', 'night', 'broadcast', 'fish') instead of the production anchors, and
  * the theme substring gates switched off,

then compares the loose matches with what the main scan stored for the same
files. Any work found here but not stored is a miss of the production prefilter
or gates. Result goes to data/biblio/scan_recall_check.json.

    python scripts/biblio_scan_check.py --files 24 --workers 4
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

CACHE = REPO / "data" / "cache" / "openalex"
OUT = REPO / "data" / "biblio" / "scan_recall_check.json"
SEED = 20260923

BROAD_PREFILTER = (
    r"(?i)(?:ship|vessel|boat|fish|ais|iuu|viirs|xview|dark|maritime|artisanal|unreported|"
    r"automatic.identification|night|low.light|broadcast)"
)


def check_one(task: dict) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from darkvessel.biblio import scan, themes
    from darkvessel.biblio.snapshot import RangeClient

    pa.set_cpu_count(1)
    client = RangeClient(pool_size=6)
    t0 = time.time()
    with ThreadPoolExecutor(4) as pool, themes.gates_disabled():
        table, stats = scan.scan_file(client, pool, task["url"], task["tag"], prefilter=BROAD_PREFILTER)
    found = {r["id"]: r for r in table.select(["id", "title", "themes_loose"]).to_pylist()}
    stored_path = CACHE / "filtered" / scan.out_name_of(task["tag"])
    stored = {r["id"] for r in pq.read_table(stored_path, columns=["id"]).to_pylist()}
    missing = [{"id": i, "title": found[i]["title"], "themes": found[i]["themes_loose"]} for i in found if i not in stored]
    extra = sorted(stored - set(found))
    return {
        "file": task["tag"],
        "rows": stats["rows"],
        "rows_in_range": stats["rows_in_range"],
        "broad_candidates": stats["candidates"],
        "stored_matches": len(stored),
        "independent_matches": len(found),
        "missing_from_stored": missing,
        "stored_not_found_here": extra,
        "seconds": round(time.time() - t0, 1),
        "bytes": stats["bytes"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", type=int, default=24)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    import numpy as np

    manifest = json.loads((CACHE / "manifest.json").read_text())
    entries = manifest["files"]
    weights = np.array([e["meta"]["record_count"] for e in entries], dtype=float)
    rng = np.random.default_rng(SEED)
    pick = rng.choice(len(entries), size=args.files, replace=False, p=weights / weights.sum())
    from darkvessel.biblio import scan

    tasks = [{"url": entries[i]["url"], "tag": scan.file_tag_of(entries[i]["url"])} for i in sorted(pick)]
    os.environ.setdefault("MIMALLOC_PURGE_DELAY", "-1")
    t0 = time.time()
    with mp.get_context("spawn").Pool(args.workers) as pool:
        results = pool.map(check_one, tasks, chunksize=1)
    summary = {
        "snapshot": manifest["date"],
        "seed": SEED,
        "files": len(results),
        "rows": sum(r["rows"] for r in results),
        "rows_in_range": sum(r["rows_in_range"] for r in results),
        "broad_candidates": sum(r["broad_candidates"] for r in results),
        "stored_matches": sum(r["stored_matches"] for r in results),
        "independent_matches": sum(r["independent_matches"] for r in results),
        "missing_from_stored": sum(len(r["missing_from_stored"]) for r in results),
        "bytes": sum(r["bytes"] for r in results),
        "seconds": round(time.time() - t0, 1),
        "per_file": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=1))
    print(
        f"{summary['files']} files, {summary['rows'] / 1e6:.1f} M rows, {summary['broad_candidates']} broad candidates; "
        f"stored {summary['stored_matches']}, independent {summary['independent_matches']}, "
        f"missing from stored {summary['missing_from_stored']}"
    )
    for r in results:
        for m in r["missing_from_stored"]:
            print("  MISSING", r["file"], m)
        if r["stored_not_found_here"]:
            print("  stored but not found here", r["file"], r["stored_not_found_here"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
