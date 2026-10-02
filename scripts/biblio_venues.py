"""Recover venues for works whose OpenAlex primary location has no source.

Re-reads, from the snapshot, only the row groups that hold stored works without a
primary source (types article, conference-paper, review, preprint) and keeps the
extra location fields. Writes data/cache/openalex/venue_recovery.parquet, which
biblio_build.py then uses. See src/darkvessel/biblio/venues.py for the recovery order.

    python scripts/biblio_venues.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from darkvessel.biblio import corpus, themes, venues  # noqa: E402

CACHE = REPO / "data" / "cache" / "openalex"


def main() -> int:
    rows = corpus.load_filtered(CACHE)
    targets = [
        {"id": r["id"], "file": r["file"], "row_group": r["row_group"], "row": r["row"]}
        for r in rows
        if not r["source_id"] and r["type"] in themes.KEEP_TYPES
    ]
    n_rg = len({(t["file"], t["row_group"]) for t in targets})
    print(f"{len(targets)} stored works without a primary source, {n_rg} row groups to re-read")
    t0 = time.time()
    path = venues.fetch_locations(CACHE, targets)
    print(f"wrote {path} in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
