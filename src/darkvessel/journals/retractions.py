"""Retraction notices per venue, from the Retraction Watch Database.

Crossref publishes the database as one CSV in a public GitLab repository
(https://gitlab.com/crossref/retraction-watch-data). Its 'Journal' column holds the
journal name as written by Retraction Watch, there is no ISSN column, so venues are
matched by normalised exact name (and, for proceedings, by a regular expression).

This is a supplementary integrity signal, not one of the two screens in the brief.
Retraction Watch does not find every retraction, large journals have more notices
than small ones, and a notice shows that the venue acted, so the counts are context
and not a ranking.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime

from darkvessel.journals.issn import normalize_title

RW_CSV_URL = "https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv"
RW_README_URL = "https://gitlab.com/crossref/retraction-watch-data/-/raw/main/README.md"

_DATE_FORMATS = ("%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def parse_date(text: str) -> date | None:
    text = (text or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def generated_on(readme_text: str) -> str:
    """The 'generated on YYYY-MM-DD' date from the repository README, or ''."""
    m = re.search(r"generated on (\d{4}-\d{2}-\d{2})", readme_text or "")
    return m.group(1) if m else ""


def count_by_venue(
    csv_text: str,
    venues: Sequence[Mapping[str, object]],
    since: date,
) -> list[dict[str, object]]:
    """One result row per venue.

    Each venue mapping needs 'key', 'venue' and 'names' (journal names to match exactly after
    normalisation) and may carry 'pattern' (a regex searched in the journal name, case insensitive).
    Counts are of notices whose RetractionNature is 'Retraction', 'Expression of concern' or 'Correction';
    'retractions_since' counts retractions dated on or after 'since'.
    """
    wanted: dict[str, list[str]] = {}
    for v in venues:
        for name in v["names"]:  # type: ignore[union-attr]
            key = normalize_title(name)
            if key and str(v["key"]) not in wanted.setdefault(key, []):  # two spellings of one name count once
                wanted[key].append(str(v["key"]))
    patterns = {str(v["key"]): re.compile(str(v["pattern"]), re.IGNORECASE) for v in venues if v.get("pattern")}

    stats = {
        str(v["key"]): {"names": Counter(), "nature": Counter(), "since": 0, "reasons": Counter()} for v in venues
    }
    total_rows = 0
    for row in csv.DictReader(io.StringIO(csv_text, newline="")):
        total_rows += 1
        journal = row.get("Journal", "") or ""
        keys = list(wanted.get(normalize_title(journal), []))
        keys += [k for k, rx in patterns.items() if k not in keys and rx.search(journal)]
        if not keys:
            continue
        nature = (row.get("RetractionNature", "") or "").strip()
        when = parse_date(row.get("RetractionDate", ""))
        for k in keys:
            s = stats[k]
            s["names"][journal] += 1
            s["nature"][nature] += 1
            if nature == "Retraction":
                if when and when >= since:
                    s["since"] += 1
                for reason in (row.get("Reason", "") or "").split(";"):
                    reason = reason.strip()
                    if reason:
                        s["reasons"][reason] += 1

    out = []
    for v in venues:
        s = stats[str(v["key"])]
        out.append(
            {
                "key": v["key"],
                "venue": v["venue"],
                "matched_names": "; ".join(f"{n} ({c})" for n, c in s["names"].most_common()),
                "notices_total": sum(s["nature"].values()),
                "retractions_total": s["nature"]["Retraction"],
                "retractions_since": s["since"],
                "expressions_of_concern": s["nature"]["Expression of concern"],
                "corrections": s["nature"]["Correction"],
                "top_reasons": "; ".join(f"{r} ({c})" for r, c in s["reasons"].most_common(3)),
                "rows_scanned": total_rows,
            }
        )
    return out
