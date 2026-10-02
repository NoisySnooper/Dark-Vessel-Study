"""Draw the charts for docs/bibliometrics.md from data/biblio/*.csv.

Writes docs/figures/biblio_papers_per_year.png, biblio_top_venues.png,
biblio_top_countries.png and biblio_sea_vietnam_per_year.png.

    python scripts/biblio_figures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from darkvessel.biblio import figures  # noqa: E402


def main() -> int:
    summary = json.loads((REPO / "data" / "biblio" / "summary.json").read_text())
    for path in figures.make_all(REPO, summary["snapshot_date"], summary):
        print("wrote", path.relative_to(REPO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
