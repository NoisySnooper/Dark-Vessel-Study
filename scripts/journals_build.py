"""Rebuild data/journals.csv and refresh the verified parts from the official lists.

Inputs
    data/journals/seed.csv              hand-curated facts with provenance tags
    data/journals/openalex_sources.csv  OpenAlex rows (refresh with scripts/journals_openalex.py)
    data/journals/external_cache.json   results of the last successful list downloads

Downloads (each one is optional and independent; a blocked host is reported, not fatal)
    SCImago journal rank CSV            https://www.scimagojr.com/journalrank.php?out=xls
    Scopus discontinued sources         the .xlsx linked from the Elsevier Scopus content policy page
    Retraction Watch Hijacked Journal   the public Google Sheet linked from the Retraction Watch
    Checker                             checker page

Venues are matched to every list by ISSN (print, online and ISSN-L), then by normalised title.
A title-only match is reported as a possible match.

Outputs
    data/journals.csv                   rebuilt every run
    data/journals/external_cache.json   updated for each source that loaded
    docs/journals.md                    the table between the journals-table markers is refreshed

When a host is blocked, download the file in a browser on another machine and pass it:
    python scripts/journals_build.py --scimago-csv "scimagojr 2025.csv" \
        --scopus-xlsx discontinued.xlsx --hijacked-csv hijacked.csv

Exit code: 0 when every requested source loaded or was not attempted, 2 when at least one
download failed (the CSV is still rebuilt from the seed and from earlier cached results).
"""

import argparse
import sys
from pathlib import Path

from darkvessel.config import DATA_DIR, DOCS_DIR
from darkvessel.journals import build
from darkvessel.journals.fetch import RequestsFetcher


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    jdir = DATA_DIR / "journals"
    ap.add_argument("--seed", type=Path, default=jdir / "seed.csv")
    ap.add_argument("--openalex-csv", type=Path, default=jdir / "openalex_sources.csv")
    ap.add_argument("--out", type=Path, default=DATA_DIR / "journals.csv")
    ap.add_argument("--cache", type=Path, default=jdir / "external_cache.json")
    ap.add_argument("--doc", type=Path, default=DOCS_DIR / "journals.md")
    ap.add_argument("--no-doc", action="store_true", help="do not refresh the table in docs/journals.md")
    ap.add_argument("--scimago-csv", type=Path, help="local SCImago CSV instead of downloading")
    ap.add_argument("--scimago-url", default=build.SCIMAGO_URL)
    ap.add_argument("--scimago-year", type=int, help="ranking year to request, default is the latest the site offers")
    ap.add_argument("--scopus-xlsx", type=Path, help="local Scopus discontinued-sources workbook")
    ap.add_argument("--scopus-url", help="direct URL of the .xlsx, skips the lookup on the content policy page")
    ap.add_argument("--hijacked-csv", type=Path, help="local export of the Hijacked Journal Checker sheet")
    ap.add_argument("--hijacked-url", help="direct CSV URL of the sheet, skips the lookup on the checker page")
    ap.add_argument("--offline", action="store_true", help="no network at all; rebuild from seed, OpenAlex cache and saved results")
    ap.add_argument("--dry-run", action="store_true", help="build in memory and print the summary, write nothing")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds per download")
    args = ap.parse_args(argv)

    opts = build.Options(
        seed=args.seed,
        openalex_csv=args.openalex_csv,
        out_csv=args.out,
        cache_json=args.cache,
        doc_path=None if args.no_doc else args.doc,
        scimago_csv=args.scimago_csv,
        scimago_url=args.scimago_url,
        scimago_year=args.scimago_year,
        scopus_xlsx=args.scopus_xlsx,
        scopus_url=args.scopus_url,
        hijacked_csv=args.hijacked_csv,
        hijacked_url=args.hijacked_url,
        offline=args.offline,
        dry_run=args.dry_run,
    )
    try:
        rows, reports = build.run(opts, fetcher=RequestsFetcher(timeout=args.timeout, attempts=2))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    build.print_reports(reports)
    print(f"\n{len(rows)} venues. Verification status: {build.summarize(rows)}")
    if args.dry_run:
        print("dry run: nothing written")
    else:
        print(f"wrote {args.out}")
    failed = [r.name for r in reports if r.state == "failed"]
    if failed:
        print(
            "\nNot refreshed: " + ", ".join(failed) + ". Values from those lists stay as UNVERIFIED or NOT CHECKED "
            "in the seed text, or come from an earlier cached run. See the messages above.",
            file=sys.stderr,
        )
    return build.exit_code(reports)


if __name__ == "__main__":
    sys.exit(main())
