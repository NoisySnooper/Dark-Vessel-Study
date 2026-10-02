"""Refresh the OpenAlex part of the journal shortlist.

Reads the venue ISSNs and OpenAlex source IDs from data/journals/seed.csv, downloads
the OpenAlex sources snapshot (Parquet on S3, about 160 MB, no key needed), keeps
the rows for those venues and writes

    data/journals/openalex_sources.csv    one row per OpenAlex source
    data/journals/openalex_manifest.json  snapshot date and counts

Run scripts/journals_build.py afterwards to rebuild data/journals.csv.

Usage: python scripts/journals_openalex.py [--cache-dir DIR] [--workers N]
  --cache-dir keeps the downloaded part files, so a second run needs no download.
Exit code 0 on success, 1 when a download failed (nothing is overwritten).
"""

import argparse
import csv
import sys
from pathlib import Path

from darkvessel.config import DATA_DIR
from darkvessel.journals import openalex
from darkvessel.journals.fetch import FetchError, RequestsFetcher
from darkvessel.journals.issn import extract_issns


def venue_keys(seed_path: Path) -> tuple[set[str], set[str]]:
    """ISSNs and OpenAlex IDs named in the seed file."""
    issns: set[str] = set()
    ids: set[str] = set()
    with open(seed_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            for col in ("issn_print", "issn_online"):
                issns.update(extract_issns(row.get(col, ""), validate=True))
            ids.update(x.strip() for x in (row.get("openalex_ids") or "").split(";") if x.strip())
    return issns, ids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=Path, default=DATA_DIR / "journals" / "seed.csv")
    ap.add_argument("--out-dir", type=Path, default=DATA_DIR / "journals")
    ap.add_argument("--cache-dir", type=Path, default=None, help="keep downloaded Parquet parts here")
    ap.add_argument("--workers", type=int, default=openalex.default_workers())
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    if not args.seed.exists():
        print(f"seed file not found: {args.seed}", file=sys.stderr)
        return 1
    issns, ids = venue_keys(args.seed)
    print(f"{len(issns)} ISSNs and {len(ids)} OpenAlex IDs from {args.seed.name}")

    fetcher = RequestsFetcher(timeout=args.timeout)
    try:
        manifest = openalex.fetch_manifest(fetcher)
        print(f"snapshot {manifest.get('date')}: {manifest.get('record_count')} sources in {len(manifest['files'])} parts")

        def progress(done: int, total: int) -> None:
            if done % 20 == 0 or done == total:
                print(f"  parts read: {done}/{total}", flush=True)

        rows = openalex.collect(
            manifest, fetcher, issns, ids, workers=args.workers, cache_dir=args.cache_dir, progress=progress
        )
    except FetchError as exc:
        print("OpenAlex refresh failed. Existing cache files were left unchanged.\n" + exc.describe(), file=sys.stderr)
        return 1

    openalex.write_cache(
        rows, manifest, args.out_dir / "openalex_sources.csv", args.out_dir / "openalex_manifest.json"
    )
    found_ids = {r["openalex_id"] for r in rows}
    missing = sorted(ids - found_ids)
    print(f"kept {len(rows)} source rows; wrote {args.out_dir / 'openalex_sources.csv'}")
    if missing:
        print("OpenAlex IDs named in the seed but not found in the snapshot: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
