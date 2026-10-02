"""Check the top venues of the bibliometric scan against the journal shortlist.

Reads data/biblio/top_venues.csv (written by the scan), prints for each of the top N venues
whether it is already in data/journals/seed.csv, and with --add appends a skeleton row for
every uncovered venue. A skeleton row holds only identity and OpenAlex flags; every other
fact is marked NOT RETRIEVED, so fill in the facts from the venue pages before using the row.
The group "(no source recorded)" is not a venue and is skipped. Repositories (preprint and
data servers such as arXiv or Zenodo) are added with type repository and fit 0, because
they are not peer-reviewed targets.

After --add:
    python scripts/journals_openalex.py   # pulls the OpenAlex rows for the new venues
    python scripts/journals_retractions.py
    python scripts/journals_build.py      # rebuilds data/journals.csv and the table in docs/journals.md

Usage: python scripts/journals_top_venues.py [--n 15] [--add]
       [--name-col C] [--type-col C] [--id-col C] [--issn-col C] [--count-col C]
Exit code 0 when everything is covered or rows were added, 1 when the scan file is missing,
3 when venues are missing and --add was not given.
"""

import argparse
import sys
from pathlib import Path

from darkvessel.config import DATA_DIR
from darkvessel.journals import build, openalex, topvenues


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--top", type=Path, default=DATA_DIR / "biblio" / "top_venues.csv")
    ap.add_argument("--seed", type=Path, default=DATA_DIR / "journals" / "seed.csv")
    ap.add_argument("--openalex-csv", type=Path, default=DATA_DIR / "journals" / "openalex_sources.csv")
    ap.add_argument("--n", type=int, default=15, help="how many top venues to check")
    ap.add_argument("--add", action="store_true", help="append skeleton rows for uncovered venues")
    for col in ("name", "type", "id", "issn", "count"):
        ap.add_argument(f"--{col}-col", help=f"header of the {col} column if it is not found automatically")
    args = ap.parse_args(argv)

    if not args.top.exists():
        print(f"{args.top} does not exist yet. The bibliometric scan has not written it.", file=sys.stderr)
        return 1
    overrides = {c: getattr(args, f"{c}_col") for c in ("name", "type", "id", "issn", "count") if getattr(args, f"{c}_col")}
    venues, cols = topvenues.read_top_venues(args.top, args.n, overrides)
    print("columns used:", {k: v for k, v in cols.items() if v})

    seed = build.read_seed(args.seed)
    oa = openalex.read_cache(args.openalex_csv)
    ids, issns, titles = topvenues.covered_sets(seed, oa)
    missing = []
    place = 0
    for v in venues:
        if v["no_source"]:
            print(f" - skipped  {v['name'] or '(no name)'}  count={v['count'] or '-'}  (papers with no recorded source, not a venue)")
            continue
        place += 1
        ok = topvenues.is_covered(v, ids, issns, titles)
        kind = v["scan_type"] or "type n/a"
        print(f"{place:2d} {'covered' if ok else 'MISSING'}  {v['name'] or '(no name)'}  [{kind}]  id={v['openalex_id'] or '-'}  issn={','.join(v['issns']) or '-'}  count={v['count'] or '-'}")
        if not ok:
            missing.append(v)
    if not missing:
        print("all top venues are in the shortlist")
        return 0
    if not args.add:
        print(f"\n{len(missing)} venues missing. Run again with --add to append skeleton rows.")
        return 3
    used = {r["key"] for r in seed}
    new_rows = [
        topvenues.skeleton_seed_row(v, openalex.parse_cache_row(oa[v["openalex_id"]]) if v["openalex_id"] in oa else None, used)
        for v in missing
    ]
    topvenues.append_seed_rows(args.seed, new_rows)
    print(f"\nappended {len(new_rows)} skeleton rows to {args.seed}. Next: scripts/journals_openalex.py, scripts/journals_retractions.py, scripts/journals_build.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
