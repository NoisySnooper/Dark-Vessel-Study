"""Compare the venues named by the bibliometric scan with the venues in the shortlist.

The scan writes data/biblio/top_venues.csv (src/darkvessel/biblio/corpus.py, top_venues). Its
columns are venue, venue_type, source_id, issn, n_papers, cited_by_sum, first_year, last_year and
pct_of_corpus, sorted by n_papers. Columns are still found by header text so that a renamed
column does not break the check: a name column (source_name, venue, journal, display_name, name),
a type column (venue_type, source_type, type), an OpenAlex ID column (source_id, openalex), an
ISSN column (issn_l, issn) and a count column (n_papers, works, papers, count, n). If the file has
a rank column it is used, otherwise the rows are ranked by count, otherwise by file order.

Papers with no recorded source are grouped by the scan under "(no source recorded)". That group
is not a venue. It is reported and skipped, and it does not use up one of the n places.

A top venue is covered when its OpenAlex ID or any ISSN is already in the seed (or in the
OpenAlex rows of a seed venue), or when its normalised name equals a seed title, or when it
is an IGARSS record (OpenAlex splits that series into fragments with no ISSN).

Skeleton seed rows for uncovered venues carry only what the OpenAlex snapshot gives
(identity, open access flags) and mark every other fact NOT RETRIEVED, so that nothing in
them can be mistaken for a checked value.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from darkvessel.journals import build
from darkvessel.journals.issn import extract_issns, normalize_title

IGARSS_PATTERN = re.compile(r"\bIGARSS\b|Geoscience and Remote Sensing Symposium", re.IGNORECASE)

_NAME = re.compile(r"source_name|venue|display_name|journal|name", re.IGNORECASE)
_ID = re.compile(r"source_id|openalex", re.IGNORECASE)
_ISSN = re.compile(r"issn", re.IGNORECASE)
_COUNT = re.compile(r"^(n_|num_|total_)?(works|papers|publications|count|n)$|works|papers", re.IGNORECASE)
_RANK = re.compile(r"^rank$", re.IGNORECASE)
_TYPE = re.compile(r"venue_type|source_type|^type$", re.IGNORECASE)
_NO_SOURCE = re.compile(r"^\(?\s*(no|unknown)\s+source|^$", re.IGNORECASE)


def _number(text: str) -> float | None:
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return None


def pick_columns(headers: Sequence[str], overrides: Mapping[str, str] | None = None) -> dict[str, str | None]:
    """Header names for name, type, id, issn, count and rank (None when absent). overrides wins."""
    overrides = overrides or {}

    def find(pattern: re.Pattern[str], key: str, exclude: re.Pattern[str] | None = None) -> str | None:
        if overrides.get(key):
            return overrides[key]
        for h in headers:
            if pattern.search(h) and not (exclude and exclude.search(h)):
                return h
        return None

    return {
        "name": find(_NAME, "name", exclude=re.compile(r"issn|id$|type", re.IGNORECASE)),
        "type": find(_TYPE, "type"),
        "id": find(_ID, "id"),
        "issn": find(_ISSN, "issn"),
        "count": find(_COUNT, "count"),
        "rank": find(_RANK, "rank"),
    }


def _venue(row: Mapping[str, str], cols: Mapping[str, str | None]) -> dict:
    def get(key: str) -> str:
        return (row.get(cols[key], "") or "").strip() if cols.get(key) else ""

    name, openalex_id = get("name"), get("id").rsplit("/", 1)[-1]
    issns = extract_issns(get("issn"), validate=False)
    return {
        "name": name,
        "scan_type": get("type").lower(),
        "openalex_id": openalex_id,
        "issns": issns,
        "count": get("count"),
        "no_source": not openalex_id and not issns and bool(_NO_SOURCE.search(name)),
        "raw": dict(row),
    }


def read_top_venues(path: Path, n: int = 15, overrides: Mapping[str, str] | None = None) -> tuple[list[dict], dict[str, str | None]]:
    """The top n real venues as dicts (name, scan_type, openalex_id, issns, count, no_source, raw), plus the columns used.

    The no-source group is kept in the list, flagged no_source, when it ranks among the top n,
    but it does not count towards n.
    """
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    cols = pick_columns(headers, overrides)
    if cols["name"] is None and cols["id"] is None and cols["issn"] is None:
        raise ValueError(f"cannot find a name, ID or ISSN column in {path.name}: headers are {headers}")
    ranked = list(rows)
    if cols["rank"]:
        ranked.sort(key=lambda r: _number(r.get(cols["rank"], "")) or 1e9)
    elif cols["count"]:
        ranked.sort(key=lambda r: -(_number(r.get(cols["count"], "")) or 0))
    out: list[dict] = []
    real = 0
    for r in ranked:
        v = _venue(r, cols)
        out.append(v)
        if not v["no_source"]:
            real += 1
            if real == n:
                break
    return out, cols


def covered_sets(seed_rows: Sequence[Mapping[str, str]], oa: Mapping[str, Mapping]) -> tuple[set[str], set[str], set[str]]:
    """(OpenAlex IDs, ISSNs, normalised titles) already in the shortlist."""
    from darkvessel.journals import openalex

    ids: set[str] = set()
    issns: set[str] = set()
    titles: set[str] = set()
    for row in seed_rows:
        parsed = [openalex.parse_cache_row(oa[i]) for i in build.venue_ids(row) if i in oa]
        ids.update(build.venue_ids(row))
        issns.update(build.venue_issns(row, parsed))
        titles.update(normalize_title(t) for t in build.venue_titles(row))
    return ids, issns, titles - {""}


def is_covered(venue: Mapping, ids: set[str], issns: set[str], titles: set[str]) -> bool:
    if venue["openalex_id"] and venue["openalex_id"] in ids:
        return True
    if any(i in issns for i in venue["issns"]):
        return True
    if normalize_title(venue["name"]) in titles:
        return True
    return bool(IGARSS_PATTERN.search(venue["name"]))


def slug(name: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", normalize_title(name)).split()
    return "".join(w[0] for w in words[:8]) if len(words) > 3 else "_".join(words) or "venue"


# OpenAlex types some proceedings series as journals, so the name is checked as well. A bare
# "Proceedings of" is not enough: PNAS is a journal.
_PROCEEDINGS_NAME = re.compile(r"conference (proceedings|series)|\bsymposium\b|\bworkshop\b|\bproceedings of spie\b", re.IGNORECASE)


def venue_kind(scan_type: str, oa_type: str, name: str = "") -> str:
    """The seed type column: journal, proceedings or repository (preprint and data servers)."""
    kind = (scan_type or oa_type or "").strip().lower()
    if kind == "repository":
        return "repository"
    if kind in ("conference", "book series", "ebook platform") or _PROCEEDINGS_NAME.search(name):
        return "proceedings"
    return "journal"


def skeleton_seed_row(venue: Mapping, oa_row: Mapping | None, used_keys: set[str]) -> dict[str, str]:
    """A seed row that holds identity from the scan and OpenAlex and marks every other fact NOT RETRIEVED."""
    key = slug(venue["name"] or venue["openalex_id"])
    while key in used_keys:
        key += "x"
    used_keys.add(key)
    issns = list(venue["issns"]) or (oa_row or {}).get("issn_list", [])
    kind = venue_kind(venue.get("scan_type", ""), (oa_row or {}).get("type", ""), venue["name"])
    why = "added from the bibliometric scan, not researched"
    unknown = f"NOT RETRIEVED | {why}"
    rank = f"; rank in the scan: {venue.get('count') or 'n/a'} works"
    if kind == "repository":
        fit = "0: preprint or data repository (OpenAlex source type repository), not a peer-reviewed target venue"
        fit_letter, fit_flagship = fit, fit + rank
    else:
        fit_letter, fit_flagship = "?: not scored, " + why, "?: not scored, " + why + rank
    row = dict.fromkeys(build.SEED_COLUMNS, "")
    row.update(
        {
            "key": key,
            "venue": venue["name"] or (oa_row or {}).get("display_name", ""),
            "type": kind,
            "issn_print": issns[0] if issns else "",
            "issn_online": issns[1] if len(issns) > 1 else "",
            "openalex_ids": venue["openalex_id"],
            "sjr": unknown,
            "quartiles": unknown,
            "scimago_h": f"NOT RETRIEVED | {why}",
            "review_time": unknown,
            "accepts_letters_or_short": unknown,
            "ai_disclosure_policy": unknown,
            "fit_letter": fit_letter,
            "fit_flagship": fit_flagship,
            "scopus_discontinued_check": "NOT CHECKED | list not downloaded; " + why,
            "hijacked_check": "NOT CHECKED | list not downloaded; " + why,
            "sources": f"scan file data/biblio/top_venues.csv; OpenAlex source ID {venue['openalex_id'] or 'not given'}",
            "apc_note": "",
        }
    )
    if kind == "repository":
        row["oa_model"] = "not applicable (repository)"
        row["oa_evidence"] = "source type repository in the scan file UNVERIFIED (inferred)"
    else:
        row["oa_model"] = unknown  # the build infers it from the OpenAlex flags once the OpenAlex rows are loaded
    return row


def append_seed_rows(seed_path: Path, new_rows: Sequence[Mapping[str, str]]) -> None:
    """Append rows to the seed CSV, keeping its header."""
    with open(seed_path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=build.SEED_COLUMNS, lineterminator="\n")
        writer.writerows(new_rows)
