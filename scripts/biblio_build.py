"""Build the corpus and summary tables from the scan cache.

Reads data/cache/openalex/filtered/*.parquet (written by biblio_scan.py) and writes
the CSV tables and queries files under data/biblio/. Precision judgments, if present
in data/biblio/precision_judgments.json, are merged into precision_sample.csv.

    python scripts/biblio_build.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from darkvessel.biblio import corpus  # noqa: E402


def main() -> int:
    path = REPO / "data" / "biblio" / "precision_judgments.json"
    judgments = json.loads(path.read_text()) if path.exists() else {}
    summary = corpus.build_all(REPO, judgments)
    print(json.dumps({k: summary[k] for k in ("snapshot_date", "corpus_size", "theme_counts", "sea_papers", "vn_papers", "precision")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
