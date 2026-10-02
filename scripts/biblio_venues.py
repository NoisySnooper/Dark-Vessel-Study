"""Recover venues for works whose OpenAlex primary location has no source.

Re-reads, from the snapshot, only the row groups that hold stored works without a
primary source (types article, conference-paper, review, preprint) and keeps the
extra location fields. Writes data/cache/openalex/venue_recovery.parquet, which
biblio_build.py then uses. See src/darkvessel/biblio/venues.py for the recovery order.

    python scripts/biblio_venues.py                       # recover missing venues
    python scripts/biblio_venues.py --repository-check    # test whether repository-primary works have a journal location

The repository check re-reads the locations of corpus works whose primary location is a
repository and counts how many also list a journal or conference location. It writes
data/biblio/venue_repository_check.json and does not change the corpus.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from darkvessel.biblio import corpus, themes, venues  # noqa: E402

CACHE = REPO / "data" / "cache" / "openalex"


def recover() -> int:
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


def repository_check() -> int:
    import pandas as pd

    corpus_csv = REPO / "data" / "biblio" / "corpus.csv"
    ids = set(pd.read_csv(corpus_csv).query("venue_group == 'repository'")["openalex_id"])
    rows = corpus.load_filtered(CACHE)
    targets = [
        {"id": r["id"], "file": r["file"], "row_group": r["row_group"], "row": r["row"]}
        for r in rows
        if r["id"] in ids and r["source_type"] == "repository"
    ]
    scratch = CACHE / "repository_check"
    scratch.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    venues.fetch_locations(scratch, targets)
    found = []
    for rid, rec in venues.load_recovery(scratch).items():
        for loc in rec.get("locations") or []:
            if loc.get("source_name") and loc.get("source_type") in venues.GOOD_LOCATION_TYPES:
                found.append({"id": rid, "venue": loc["source_name"], "venue_type": loc["source_type"]})
                break
    out = {
        "repository_group_works_in_corpus": len(ids),
        "repository_primary_works_checked": len(targets),
        "with_journal_or_conference_location": len(found),
        "examples": found[:20],
        "seconds": round(time.time() - t0),
    }
    path = REPO / "data" / "biblio" / "venue_repository_check.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "examples"}))
    return 0


def main() -> int:
    return repository_check() if "--repository-check" in sys.argv[1:] else recover()


if __name__ == "__main__":
    raise SystemExit(main())
