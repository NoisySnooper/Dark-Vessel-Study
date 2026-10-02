"""Resumable scan of the OpenAlex works snapshot (Parquet on S3) for dark-vessel literature.

Reads only the needed columns with HTTP range requests, filters each file to
works that match a loose theme (2015 to 2026), and writes one small Parquet file
per input file to data/cache/openalex/filtered/. Files already done are skipped.

Run it repeatedly in the foreground until it reports 0 files remaining:

    python scripts/biblio_scan.py --max-minutes 8.5

Useful flags:
    --workers N        concurrent files (default 8)
    --threads N        range-fetch threads per file (default 4)
    --limit N          only N pending files (smoke test)
    --only TEXT        only files whose partition/part name contains TEXT
    --status           print progress and exit
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from darkvessel.biblio import scan  # noqa: E402

CACHE = REPO / "data" / "cache" / "openalex"


def status() -> None:
    manifest = scan.load_manifest(CACHE)
    out_dir = CACHE / "filtered"
    done = len(list(out_dir.glob("*.parquet"))) if out_dir.exists() else 0
    total = len(manifest["files"])
    prog = CACHE / "progress.jsonl"
    gb = rows = matches = 0
    if prog.exists():
        for line in prog.read_text().splitlines():
            s = json.loads(line)
            gb += s["bytes"]
            rows += s["rows"]
            matches += s["matches"]
    print(
        f"snapshot {manifest['date']}: {done}/{total} files done, {gb / 1e9:.1f} GB read, "
        f"{rows / 1e6:.1f} M rows scanned, {matches} matched rows"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--max-minutes", type=float, default=8.5, help="stop starting new files so the run ends inside this budget")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", default=None)
    ap.add_argument("--refresh-manifest", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        status()
        return 0
    summary = scan.run_scan(
        CACHE,
        workers=args.workers,
        threads=args.threads,
        max_minutes=args.max_minutes,
        limit=args.limit,
        only=args.only,
        refresh_manifest=args.refresh_manifest,
    )
    return 0 if summary.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
