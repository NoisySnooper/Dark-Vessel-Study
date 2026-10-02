"""Screening of venues against external lists, matched by ISSN.

Two lists are supported:

* Scopus discontinued sources (Elsevier workbook, linked from the Scopus content policy page).
* Retraction Watch Hijacked Journal Checker (a public sheet of cloned journals).

Neither file has a documented, stable layout, so the parser finds columns by
header text: any header containing 'issn' is an ISSN column, headers containing
'title', 'journal' or 'name' give titles, and headers containing 'url', 'link'
or 'site' give addresses. When a list has no ISSN column at all, every cell is
scanned for ISSN-shaped values.

Matching order: ISSN first, then normalised title. A title-only match is
reported as a possible match, never as a hit, because different journals share
titles and because hijackers copy the ISSN of the genuine journal.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from darkvessel.journals.issn import extract_issns, normalize_issn, normalize_title

NOT_CHECKED = "NOT_CHECKED"
NO_MATCH = "NO_MATCH"
MATCH_ISSN = "MATCH_ISSN"
MATCH_TITLE = "MATCH_TITLE"

_TITLE_HEADER = re.compile(r"title|journal|name|source", re.IGNORECASE)
_NOT_TITLE_HEADER = re.compile(r"url|link|web|site|issn|publisher|country|reason|date|year|note|status|id\b", re.IGNORECASE)
_URL_HEADER = re.compile(r"url|link|web|site", re.IGNORECASE)
_INFO_HEADER = {
    "discontinued": re.compile(r"reason|discontinu|last|year|date|status|publisher", re.IGNORECASE),
    "hijacked": re.compile(r"note|publisher|country|status|date|comment", re.IGNORECASE),
}


@dataclass(frozen=True)
class ListEntry:
    titles: tuple[str, ...]
    issns: tuple[str, ...]
    urls: tuple[str, ...] = ()
    info: str = ""

    @property
    def label(self) -> str:
        return self.titles[0] if self.titles else "(untitled entry)"


@dataclass(frozen=True)
class ScreenResult:
    status: str  # NOT_CHECKED, NO_MATCH, MATCH_ISSN or MATCH_TITLE
    entries: tuple[ListEntry, ...] = ()
    notes: tuple[str, ...] = ()  # one note per entry, how it was matched
    reason: str = ""  # why a check could not be run (status NOT_CHECKED)


@dataclass(frozen=True)
class ListMeta:
    name: str  # short human name used in cells
    source: str  # URL or local path
    retrieved: str  # ISO date
    total_rows: int


def _issn_cell(value: str) -> list[str]:
    """ISSNs in one cell, tolerating hyphenless text and numeric cells that lost leading zeros."""
    value = (value or "").strip()
    found = extract_issns(value, validate=False)
    if found:
        return found
    if re.fullmatch(r"\d{5,7}", value):  # Excel stored the ISSN as a number
        padded = normalize_issn(value.zfill(8))
        if padded and extract_issns(padded, validate=True):
            return [padded]
    return []


def entries_from_records(records: Sequence[Mapping[str, str]], kind: str) -> list[ListEntry]:
    """Build ListEntry objects from rows read as dicts. kind is 'discontinued' or 'hijacked'."""
    if kind not in _INFO_HEADER:
        raise ValueError(f"kind must be one of {sorted(_INFO_HEADER)}")
    headers: list[str] = []
    for rec in records:
        for h in rec:
            if h not in headers:
                headers.append(h)
    issn_cols = [h for h in headers if "issn" in h.lower()]
    url_cols = [h for h in headers if _URL_HEADER.search(h) and "issn" not in h.lower()]
    clone_cols = [h for h in url_cols if re.search(r"hijack|clone|fake|fraud", h, re.IGNORECASE)]
    if kind == "hijacked" and clone_cols:  # show only the addresses of the clone, not of the genuine journal
        url_cols = clone_cols
    title_cols = [h for h in headers if _TITLE_HEADER.search(h) and not _NOT_TITLE_HEADER.search(h)]
    if not title_cols:  # fall back to any title-like header, even one that also looks like a URL
        title_cols = [h for h in headers if re.search(r"title|journal", h, re.IGNORECASE) and "issn" not in h.lower()]
    info_cols = [h for h in headers if _INFO_HEADER[kind].search(h) and h not in title_cols and h not in url_cols]

    out = []
    for rec in records:
        if issn_cols:
            issns: list[str] = []
            for h in issn_cols:
                for issn in _issn_cell(rec.get(h, "")):
                    if issn not in issns:
                        issns.append(issn)
        else:
            issns = extract_issns(" ".join(str(v) for v in rec.values()), validate=True)
        titles = tuple(dict.fromkeys(t.strip() for h in title_cols if (t := rec.get(h, "")) and t.strip()))
        urls: list[str] = []
        for h in url_cols:
            for token in re.split(r"[\s;,]+", rec.get(h, "") or ""):
                if token and token not in urls:
                    urls.append(token)
        info = "; ".join(f"{h}: {rec[h].strip()}" for h in info_cols if rec.get(h, "").strip())
        if titles or issns:
            out.append(ListEntry(titles=titles, issns=tuple(issns), urls=tuple(urls), info=info[:240]))
    return out


class ListIndex:
    """Entries of one list, indexed by ISSN and by normalised title."""

    def __init__(self, entries: Iterable[ListEntry]) -> None:
        self.entries = list(entries)
        self.by_issn: dict[str, list[ListEntry]] = {}
        self.by_title: dict[str, list[ListEntry]] = {}
        for entry in self.entries:
            for issn in entry.issns:
                self.by_issn.setdefault(issn, []).append(entry)
            for title in entry.titles:
                key = normalize_title(title)
                if key:
                    self.by_title.setdefault(key, []).append(entry)

    def screen(self, issns: Iterable[str], titles: Iterable[str]) -> ScreenResult:
        """Look a venue up by ISSN, then by title. Both inputs may be empty."""
        issn_list = [i for i in (normalize_issn(x) for x in issns) if i]
        hits: list[ListEntry] = []
        for issn in issn_list:
            for entry in self.by_issn.get(issn, []):
                if entry not in hits:
                    hits.append(entry)
        if hits:
            return ScreenResult(MATCH_ISSN, tuple(hits), tuple("ISSN match" for _ in hits))
        title_hits: list[ListEntry] = []
        notes: list[str] = []
        for title in titles:
            key = normalize_title(title)
            for entry in self.by_title.get(key, []) if key else []:
                if entry in title_hits:
                    continue
                title_hits.append(entry)
                if entry.issns:
                    notes.append(f"title match, but the list gives other ISSN(s) {', '.join(entry.issns)}: probably a different journal")
                else:
                    notes.append("title match, the list gives no ISSN: needs a manual check")
        if title_hits:
            return ScreenResult(MATCH_TITLE, tuple(title_hits), tuple(notes))
        return ScreenResult(NO_MATCH)


def not_checked(reason: str) -> ScreenResult:
    return ScreenResult(NOT_CHECKED, reason=reason)


def _entries_text(result: ScreenResult, limit: int = 3) -> str:
    parts = []
    for entry, note in list(zip(result.entries, result.notes))[:limit]:
        bits = [entry.label]
        if entry.issns:
            bits.append("ISSN " + ", ".join(entry.issns))
        if entry.info:
            bits.append(entry.info)
        if entry.urls:
            bits.append("URL " + ", ".join(entry.urls[:3]))
        if note != "ISSN match":
            bits.append(note)
        parts.append(" / ".join(bits))
    extra = len(result.entries) - limit
    text = " || ".join(parts)
    return text + (f" || and {extra} more" if extra > 0 else "")


def render_discontinued(result: ScreenResult, meta: ListMeta | None, issns: Sequence[str]) -> str:
    """CSV cell text in the form 'value | provenance' for the Scopus discontinued check."""
    if result.status == NOT_CHECKED or meta is None:
        return f"NOT CHECKED | {result.reason or 'Scopus discontinued-sources list not loaded'}"
    src = f"Scopus discontinued-sources list ({meta.total_rows} rows, {meta.source}, retrieved {meta.retrieved})"
    if result.status == NO_MATCH:
        return (
            "NOT ON LIST | VERIFIED against the " + src + ": no match by ISSN ("
            + ", ".join(issns) + ") or by normalised title."
        )
    if result.status == MATCH_ISSN:
        return (
            "ON DISCONTINUED LIST (ISSN match) | VERIFIED against the " + src + ": " + _entries_text(result)
            + ". Check the Scopus source page before choosing this venue."
        )
    return (
        "POSSIBLE MATCH (title only) | VERIFIED against the " + src + ": " + _entries_text(result)
        + ". Manual check needed."
    )


def render_hijacked(result: ScreenResult, meta: ListMeta | None, issns: Sequence[str]) -> str:
    """CSV cell text in the form 'value | provenance' for the Hijacked Journal Checker."""
    if result.status == NOT_CHECKED or meta is None:
        return f"NOT CHECKED | {result.reason or 'Hijacked Journal Checker not loaded'}"
    src = f"Retraction Watch Hijacked Journal Checker ({meta.total_rows} entries, {meta.source}, retrieved {meta.retrieved})"
    official = (
        " Submit only through the publisher's own site, opened from the publisher or society page and not from an"
        " email or a search advertisement."
    )
    if result.status == NO_MATCH:
        return (
            "NO ENTRY | VERIFIED against the " + src + ": no match by ISSN (" + ", ".join(issns)
            + ") or by normalised title. Absence from the list does not prove that a website is genuine." + official
        )
    if result.status == MATCH_ISSN:
        return (
            "CLONE REPORTED (ISSN match) | VERIFIED against the " + src + ": " + _entries_text(result)
            + ". The genuine journal is not itself hijacked, but its ISSN is used by a clone site." + official
        )
    return (
        "POSSIBLE CLONE (title only) | VERIFIED against the " + src + ": " + _entries_text(result)
        + ". Manual check needed." + official
    )
