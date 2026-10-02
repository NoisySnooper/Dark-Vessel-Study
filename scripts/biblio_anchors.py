"""Fetch extra OpenAlex metadata for the anchor papers (landing page, licence, volume, pages, topic).

Reads a few more columns for the anchor records from the snapshot with range requests
(a handful of row groups, well under 1 GB) and writes data/biblio/anchor_metadata.json.
biblio_build.py merges that file into anchors.csv when it exists.

    python scripts/biblio_anchors.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from darkvessel.biblio import anchors, corpus  # noqa: E402

CACHE = REPO / "data" / "cache" / "openalex"
OUT = REPO / "data" / "biblio" / "anchor_metadata.json"


def main() -> int:
    rows = corpus.load_filtered(CACHE)
    prepared = corpus.prepare_rows(rows)
    targets = []
    for anchor in corpus.ANCHORS:
        for r in corpus.find_anchor_rows(anchor, prepared):
            targets.append({"id": r["id"], "file": r["file"], "row_group": r["row_group"], "row": r["row"]})
    print(f"{len(targets)} anchor records in {len({t['file'] for t in targets})} files")
    got = anchors.fetch_anchor_metadata(CACHE, targets)
    nbytes = got.pop("_bytes_read")
    result = {rid: anchors.flatten(rec) for rid, rec in sorted(got.items())}
    OUT.write_text(json.dumps({"bytes_read": nbytes, "records": result}, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT} ({len(result)} records, {nbytes / 1e6:.0f} MB read)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
