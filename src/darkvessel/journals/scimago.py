"""Parser and lookup for the SCImago Journal and Country Rank CSV.

The official file (https://www.scimagojr.com/journalrank.php?out=xls) is
semicolon separated, uses a decimal comma, lists ISSNs without hyphens and gives
one 'Categories' cell such as 'Computers in Earth Sciences (Q1); Geology (Q1)'.
Category names may themselves contain parentheses, for example
'Earth and Planetary Sciences (miscellaneous) (Q1)', so the quartile is taken
from the last parenthesis of each ';' separated item.

The ranking year is read from the 'Total Docs. (YYYY)' header. All header
matching is case insensitive and tolerant of extra columns.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from darkvessel.journals.issn import extract_issns, normalize_title

_CATEGORY = re.compile(r"^(?P<name>.*?)\s*\((?P<q>Q[1-4]|-)\)\s*$")
_YEAR_HEADER = re.compile(r"total docs\.?\s*\((\d{4})\)", re.IGNORECASE)


@dataclass(frozen=True)
class ScimagoRecord:
    sourceid: str
    title: str
    type: str
    issns: tuple[str, ...]
    sjr: float | None
    best_quartile: str
    h_index: int | None
    publisher: str
    coverage: str
    categories: tuple[tuple[str, str], ...]  # (category name, quartile or '')
    areas: str


@dataclass
class ScimagoTable:
    year: int | None
    records: list[ScimagoRecord]
    by_issn: dict[str, list[ScimagoRecord]] = field(default_factory=dict)
    by_title: dict[str, list[ScimagoRecord]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for rec in self.records:
            for issn in rec.issns:
                self.by_issn.setdefault(issn, []).append(rec)
            key = normalize_title(rec.title)
            if key:
                self.by_title.setdefault(key, []).append(rec)

    def find(self, issns: Iterable[str], titles: Iterable[str]) -> tuple[ScimagoRecord | None, str]:
        """Best record for a venue and a short note on how it was matched.

        ISSN is tried first. If several records share an ISSN the one whose title also matches is
        preferred. Title-only matches are returned with a note, because they may be a different
        journal with the same name.
        """
        wanted_titles = {normalize_title(t) for t in titles} - {""}
        candidates: list[ScimagoRecord] = []
        for issn in issns:
            for rec in self.by_issn.get(issn, []):
                if rec not in candidates:
                    candidates.append(rec)
        if candidates:
            if len(candidates) == 1:
                return candidates[0], "ISSN"
            same_title = [r for r in candidates if normalize_title(r.title) in wanted_titles]
            return (same_title or candidates)[0], f"ISSN ({len(candidates)} records share it)"
        by_name: list[ScimagoRecord] = []
        for key in wanted_titles:
            for rec in self.by_title.get(key, []):
                if rec not in by_name:
                    by_name.append(rec)
        if by_name:
            return by_name[0], "title only (no ISSN match; confirm it is the same journal)"
        return None, ""


def parse_categories(text: str) -> tuple[tuple[str, str], ...]:
    """Split a SCImago 'Categories' cell into (name, quartile) pairs."""
    out = []
    for item in (text or "").split(";"):
        item = item.strip()
        if not item:
            continue
        m = _CATEGORY.match(item)
        if m:
            q = m.group("q")
            out.append((m.group("name").strip(), "" if q == "-" else q))
        else:
            out.append((item, ""))
    return tuple(out)


def _float(text: str) -> float | None:
    text = (text or "").strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _int(text: str) -> int | None:
    text = (text or "").strip()
    return int(text) if text.isdigit() else None


def parse_scimago_csv(text: str) -> ScimagoTable:
    """Parse the SCImago CSV text. Raises ValueError when it does not look like that file."""
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff"), newline=""), delimiter=";", quotechar='"')
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("SCImago file is empty") from None
    names = [h.strip().lower() for h in header]

    def col(*candidates: str) -> int | None:
        for cand in candidates:
            if cand in names:
                return names.index(cand)
        return None

    i_title, i_issn, i_sjr = col("title"), col("issn"), col("sjr")
    if i_title is None or i_issn is None or i_sjr is None:
        raise ValueError(
            "this does not look like the SCImago CSV: need 'Title', 'Issn' and 'SJR' columns, "
            f"found {header[:8]}"
        )
    i_id, i_type = col("sourceid"), col("type")
    i_q, i_h = col("sjr best quartile"), col("h index")
    i_pub, i_cov = col("publisher"), col("coverage")
    i_cat, i_area = col("categories"), col("areas")

    year = None
    for h in header:
        m = _YEAR_HEADER.search(h)
        if m:
            year = int(m.group(1))
            break

    def cell(row: list[str], idx: int | None) -> str:
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    records = []
    for row in reader:
        if not row or not any(c.strip() for c in row):
            continue
        quartile = cell(row, i_q)
        records.append(
            ScimagoRecord(
                sourceid=cell(row, i_id),
                title=cell(row, i_title),
                type=cell(row, i_type),
                issns=tuple(extract_issns(cell(row, i_issn), validate=False)),
                sjr=_float(cell(row, i_sjr)),
                best_quartile="" if quartile == "-" else quartile,
                h_index=_int(cell(row, i_h)),
                publisher=cell(row, i_pub),
                coverage=cell(row, i_cov),
                categories=parse_categories(cell(row, i_cat)),
                areas=cell(row, i_area),
            )
        )
    if not records:
        raise ValueError("SCImago file has a header but no data rows")
    return ScimagoTable(year=year, records=records)
