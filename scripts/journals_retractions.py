"""Count Retraction Watch Database notices for each shortlisted venue.

Downloads the Crossref copy of the Retraction Watch Database (one CSV, about 67 MB, public
GitLab repository https://gitlab.com/crossref/retraction-watch-data) and writes

    data/journals/retractions_by_venue.csv   counts per venue
    data/journals/retractions_meta.json      source, retrieval date, dataset date

Venue names come from data/journals/seed.csv. Matching is by normalised exact journal name,
because the database has no ISSN column. This is context for the integrity screen, not a
substitute for the Scopus discontinued-sources list or the Hijacked Journal Checker.

Usage: python scripts/journals_retractions.py [--since 2023-01-01] [--csv LOCAL_FILE]
Exit code 0 on success, 1 when the download failed (nothing is overwritten).
"""

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

from darkvessel.config import DATA_DIR
from darkvessel.journals import retractions
from darkvessel.journals.fetch import FetchError, RequestsFetcher, decode_text

# Proceedings are listed under conference names that contain the year, so they need a pattern.
PATTERNS = {"igarss": r"\bIGARSS\b|Geoscience and Remote Sensing Symposium"}


def read_venues(seed_path: Path) -> list[dict]:
    venues = []
    with open(seed_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            names = [row["venue"]] + [a.strip() for a in (row.get("aliases") or "").split(";") if a.strip()]
            venue = {"key": row["key"], "venue": row["venue"], "names": names}
            if row["key"] in PATTERNS:
                venue["pattern"] = PATTERNS[row["key"]]
            venues.append(venue)
    return venues


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=Path, default=DATA_DIR / "journals" / "seed.csv")
    ap.add_argument("--out-dir", type=Path, default=DATA_DIR / "journals")
    ap.add_argument("--since", default="2023-01-01", help="count retractions dated on or after this day")
    ap.add_argument("--csv", type=Path, help="local copy of retraction_watch.csv instead of downloading")
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    since = date.fromisoformat(args.since)
    fetcher = RequestsFetcher(timeout=args.timeout, attempts=2)
    try:
        if args.csv:
            text, source = decode_text(args.csv.read_bytes()), str(args.csv)
        else:
            text, source = decode_text(fetcher.get_bytes(retractions.RW_CSV_URL)), retractions.RW_CSV_URL
        try:
            readme = decode_text(fetcher.get_bytes(retractions.RW_README_URL))
        except FetchError:
            readme = ""
    except (FetchError, OSError) as exc:
        detail = exc.describe("--csv PATH") if isinstance(exc, FetchError) else str(exc)
        print("Retraction Watch download failed. Existing files were left unchanged.\n" + detail, file=sys.stderr)
        return 1

    rows = retractions.count_by_venue(text, read_venues(args.seed), since)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    fields = [k for k in rows[0] if k != "rows_scanned"]
    with open(args.out_dir / "retractions_by_venue.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    meta = {
        "source": source,
        "retrieved": date.today().isoformat(),
        "dataset_generated": retractions.generated_on(readme),
        "rows_scanned": rows[0]["rows_scanned"],
        "retractions_since": since.isoformat(),
        "matching": "normalised exact journal name; IGARSS by regular expression",
    }
    (args.out_dir / "retractions_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    for r in rows:
        print(f"{r['key']:8s} notices={r['notices_total']:3d} retractions={r['retractions_total']:3d} since {since.year}={r['retractions_since']:3d}")
    print(f"wrote {args.out_dir / 'retractions_by_venue.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
