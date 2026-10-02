"""Merge the seed, the OpenAlex cache and the optional official lists into data/journals.csv.

Cell convention. Most cells read '<value> | <provenance>'. The part before the first
' | ' is the value, the part after it says where the value came from and whether it was
checked. Provenance keys such as [sc] or [p1] point to URLs in the 'sources' column.

Status rule (computed, never typed by hand). A venue is
  VERIFIED    when none of the fact columns carries UNVERIFIED, NOT CHECKED or NOT RETRIEVED,
  PARTIAL     when the publisher and ISSNs come from the OpenAlex snapshot but some other
              fact column still carries one of those tags,
  UNVERIFIED  when even the publisher is only a search snippet.
To upgrade a seed cell, open the cited page, confirm the value and replace the tag
'UNVERIFIED (search snippet)' by 'VERIFIED (page opened YYYY-MM-DD)' in data/journals/seed.csv.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from darkvessel.journals import fetch, lists, openalex, scimago
from darkvessel.journals.fetch import Fetcher, FetchError
from darkvessel.journals.issn import extract_issns
from darkvessel.journals.lists import ListEntry, ListIndex, ListMeta, ScreenResult
from darkvessel.journals.scimago import ScimagoRecord, ScimagoTable
from darkvessel.journals.xlsx import XlsxError, read_xlsx, rows_to_records

SCIMAGO_URL = "https://www.scimagojr.com/journalrank.php?out=xls"
SCOPUS_POLICY_URL = "https://www.elsevier.com/products/scopus/content/content-policy-and-selection"
RW_CHECKER_URL = "https://retractionwatch.com/the-retraction-watch-hijacked-journal-checker/"
OPENALEX_MANIFEST_URL = openalex.MANIFEST_URL

OUTPUT_COLUMNS = [
    "venue", "type", "issn_print", "issn_online", "publisher", "sjr", "quartiles", "h_index",
    "oa_model", "apc_usd", "review_time", "accepts_letters_or_short", "ai_disclosure_policy",
    "fit_letter", "fit_flagship", "scopus_discontinued_check", "hijacked_check", "sources",
    "verification_status",
]
SEED_COLUMNS = [
    "key", "venue", "aliases", "type", "issn_print", "issn_online", "openalex_ids", "publisher_fallback",
    "sjr", "quartiles", "scimago_h", "oa_model", "oa_evidence", "apc_note", "review_time",
    "accepts_letters_or_short", "ai_disclosure_policy", "fit_letter", "fit_flagship",
    "scopus_discontinued_check", "hijacked_check", "sources",
]
FACT_COLUMNS = [
    "publisher", "sjr", "quartiles", "h_index", "oa_model", "apc_usd", "review_time",
    "accepts_letters_or_short", "ai_disclosure_policy", "scopus_discontinued_check", "hijacked_check",
]
UNCERTAIN_FLAGS = ("UNVERIFIED", "NOT CHECKED", "NOT RETRIEVED")
SEP = " | "

TABLE_BEGIN = "<!-- journals-table:begin -->"
TABLE_END = "<!-- journals-table:end -->"


# ---------------------------------------------------------------------------------------------
# small helpers

def split_cell(cell: str) -> tuple[str, str]:
    """('value', 'provenance') for a cell in '<value> | <provenance>' form."""
    value, _, prov = (cell or "").partition(SEP)
    return value.strip(), prov.strip()


def join_cell(value: str, provenance: str = "") -> str:
    return f"{value}{SEP}{provenance}" if provenance else value


def read_seed(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        for col in SEED_COLUMNS:
            row.setdefault(col, "")
    return rows


def venue_ids(seed_row: Mapping[str, str]) -> list[str]:
    return [x.strip() for x in (seed_row.get("openalex_ids") or "").split(";") if x.strip()]


def venue_issns(seed_row: Mapping[str, str], oa_rows: Iterable[Mapping] = ()) -> list[str]:
    """Distinct valid ISSNs from the seed cells and from the venue's OpenAlex rows."""
    found: list[str] = []
    for col in ("issn_print", "issn_online"):
        for issn in extract_issns(seed_row.get(col, ""), validate=True):
            if issn not in found:
                found.append(issn)
    for row in oa_rows:
        for issn in row.get("issn_list", []):
            if issn not in found and extract_issns(issn, validate=True):
                found.append(issn)
    return found


def venue_titles(seed_row: Mapping[str, str]) -> list[str]:
    titles = [seed_row.get("venue", "")]
    titles += [a.strip() for a in (seed_row.get("aliases") or "").split(";") if a.strip()]
    return [t for t in titles if t]


# ---------------------------------------------------------------------------------------------
# external data containers and their JSON cache

@dataclass
class ExternalScimago:
    year: int | None
    meta: ListMeta
    matches: dict[str, tuple[ScimagoRecord | None, str]]


@dataclass
class ExternalList:
    meta: ListMeta
    results: dict[str, ScreenResult]


@dataclass
class External:
    scimago: ExternalScimago | None = None
    scopus: ExternalList | None = None
    hijacked: ExternalList | None = None


def _entry_to_dict(e: ListEntry) -> dict:
    return {"titles": list(e.titles), "issns": list(e.issns), "urls": list(e.urls), "info": e.info}


def _entry_from_dict(d: Mapping) -> ListEntry:
    return ListEntry(tuple(d.get("titles", ())), tuple(d.get("issns", ())), tuple(d.get("urls", ())), d.get("info", ""))


def _result_to_dict(r: ScreenResult) -> dict:
    return {"status": r.status, "entries": [_entry_to_dict(e) for e in r.entries], "notes": list(r.notes), "reason": r.reason}


def _result_from_dict(d: Mapping) -> ScreenResult:
    return ScreenResult(
        d["status"], tuple(_entry_from_dict(e) for e in d.get("entries", ())), tuple(d.get("notes", ())), d.get("reason", "")
    )


def _record_to_dict(rec: ScimagoRecord) -> dict:
    return {
        "sourceid": rec.sourceid, "title": rec.title, "type": rec.type, "issns": list(rec.issns), "sjr": rec.sjr,
        "best_quartile": rec.best_quartile, "h_index": rec.h_index, "publisher": rec.publisher,
        "coverage": rec.coverage, "categories": [list(c) for c in rec.categories], "areas": rec.areas,
    }


def _record_from_dict(d: Mapping) -> ScimagoRecord:
    return ScimagoRecord(
        d["sourceid"], d["title"], d["type"], tuple(d["issns"]), d["sjr"], d["best_quartile"], d["h_index"],
        d["publisher"], d["coverage"], tuple((c[0], c[1]) for c in d["categories"]), d["areas"],
    )


def _meta_to_dict(m: ListMeta) -> dict:
    return {"name": m.name, "source": m.source, "retrieved": m.retrieved, "total_rows": m.total_rows}


def save_external(path: Path, ext: External) -> None:
    payload: dict = {}
    if ext.scimago:
        payload["scimago"] = {
            "meta": _meta_to_dict(ext.scimago.meta),
            "year": ext.scimago.year,
            "matches": {
                k: {"record": _record_to_dict(rec) if rec else None, "how": how}
                for k, (rec, how) in ext.scimago.matches.items()
            },
        }
    for name in ("scopus", "hijacked"):
        part: ExternalList | None = getattr(ext, name)
        if part:
            payload[name] = {"meta": _meta_to_dict(part.meta), "results": {k: _result_to_dict(r) for k, r in part.results.items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def load_external(path: Path) -> External:
    if not path.exists():
        return External()
    data = json.loads(path.read_text(encoding="utf-8"))
    ext = External()
    if "scimago" in data:
        s = data["scimago"]
        ext.scimago = ExternalScimago(
            s.get("year"), ListMeta(**s["meta"]),
            {k: (_record_from_dict(v["record"]) if v["record"] else None, v["how"]) for k, v in s["matches"].items()},
        )
    for name in ("scopus", "hijacked"):
        if name in data:
            d = data[name]
            setattr(ext, name, ExternalList(ListMeta(**d["meta"]), {k: _result_from_dict(r) for k, r in d["results"].items()}))
    return ext


# ---------------------------------------------------------------------------------------------
# screening against loaded lists

def screen_venues(seed_rows: Sequence[Mapping[str, str]], index: ListIndex, oa_by_key: Mapping[str, Sequence[Mapping]]) -> dict[str, ScreenResult]:
    return {
        row["key"]: index.screen(venue_issns(row, oa_by_key.get(row["key"], ())), venue_titles(row))
        for row in seed_rows
    }


def scimago_matches(seed_rows: Sequence[Mapping[str, str]], table: ScimagoTable, oa_by_key: Mapping[str, Sequence[Mapping]]) -> dict[str, tuple[ScimagoRecord | None, str]]:
    return {
        row["key"]: table.find(venue_issns(row, oa_by_key.get(row["key"], ())), venue_titles(row))
        for row in seed_rows
    }


# ---------------------------------------------------------------------------------------------
# cell builders

def _oa_rows_for(seed_row: Mapping[str, str], oa: Mapping[str, Mapping]) -> list[dict]:
    return [openalex.parse_cache_row(oa[i]) for i in venue_ids(seed_row) if i in oa]


def _single_oa(seed_row: Mapping[str, str], oa_rows: list[dict]) -> dict | None:
    """The one OpenAlex row to trust for this venue, or None when OpenAlex has none or several fragments."""
    return oa_rows[0] if len(venue_ids(seed_row)) == 1 and len(oa_rows) == 1 else None


def _oa_tag(row: Mapping) -> str:
    return f"OpenAlex snapshot {row['snapshot_date']}, source ID {row['openalex_id']}"


def publisher_cell(seed_row: Mapping[str, str], oa_row: Mapping | None) -> str:
    name = (oa_row or {}).get("host_organization_name", "")
    if oa_row and name:
        return join_cell(name, f"VERIFIED: {_oa_tag(oa_row)} [oa]")
    return seed_row.get("publisher_fallback", "") or join_cell("NOT RETRIEVED", "no publisher in the OpenAlex snapshot")


def oa_model_cell(seed_row: Mapping[str, str], oa_row: Mapping | None) -> str:
    value, prov = split_cell(seed_row.get("oa_model", ""))
    value = value or "NOT RETRIEVED"
    parts = []
    if oa_row:
        flags = f"OpenAlex is_oa={oa_row['is_oa']}, is_in_doaj={oa_row['is_in_doaj']}"
        if oa_row.get("is_in_doaj_since_year"):
            flags += f" (since {oa_row['is_in_doaj_since_year']})"
        flags += f", APC {'listed' if oa_row.get('apc_usd') else 'not listed'}"
        parts.append(f"VERIFIED flags: {flags} [oa]")
        word = value.split()[0].lower() if value else ""
        if word == "gold" and not oa_row["is_oa"]:
            parts.append("CONFLICT: seed says gold but OpenAlex says not fully OA")
        if word == "hybrid" and oa_row["is_oa"]:
            parts.append("CONFLICT: seed says hybrid but OpenAlex says fully OA")
    evidence = seed_row.get("oa_evidence", "").strip()
    if prov:
        parts.append(prov)
    if evidence:
        parts.append(evidence)
    return join_cell(value, "; ".join(parts))


def _price_year(oa_row: Mapping) -> str:
    years = [p.get("year") for p in oa_row.get("apc_usd_by_year", []) if p.get("year")]
    return str(max(years)) if years else "year not given"


def apc_cell(seed_row: Mapping[str, str], oa_row: Mapping | None) -> str:
    note = seed_row.get("apc_note", "").strip()
    if oa_row and oa_row.get("apc_usd"):
        value = str(oa_row["apc_usd"])
        model = split_cell(seed_row.get("oa_model", ""))[0].lower()
        if model.startswith("hybrid"):
            value += " (optional, hybrid)"
        listed = ", ".join(f"{p['currency']} {p['price']}" for p in oa_row.get("apc_prices", []))
        prov = f"VERIFIED: {_oa_tag(oa_row)}, price year {_price_year(oa_row)}, USD converted by OpenAlex; list prices {listed} [oa]"
        if note:
            prov += f"; {note}"
        return join_cell(value, prov)
    if note:
        return note
    return join_cell("NOT RETRIEVED", "no APC in the OpenAlex snapshot and none found by search")


def h_index_cell(seed_row: Mapping[str, str], oa_row: Mapping | None, sc: tuple[ScimagoRecord | None, str] | None, sc_ext: ExternalScimago | None) -> str:
    values, provs = [], []
    if sc and sc[0] and sc[0].h_index is not None and sc_ext:
        values.append(f"SCImago {sc[0].h_index}")
        provs.append(f"SCImago VERIFIED (SJR {sc_ext.year or 'year not stated'} file, retrieved {sc_ext.meta.retrieved})")
    else:
        sval, sprov = split_cell(seed_row.get("scimago_h", ""))
        if sval:
            values.append(f"SCImago {sval}")
            provs.append(f"SCImago {sprov}" if sprov else "SCImago value from seed")
    if oa_row and oa_row.get("h_index") is not None:
        values.append(f"OpenAlex {oa_row['h_index']}")
        provs.append(f"OpenAlex VERIFIED ({_oa_tag(oa_row)}; OpenAlex computes its own h-index, it differs from SCImago) [oa]")
    if not values:
        return join_cell("NOT RETRIEVED", "no h-index found")
    return join_cell("; ".join(values), "; ".join(provs))


def sjr_cell(seed_row: Mapping[str, str], sc: tuple[ScimagoRecord | None, str] | None, sc_ext: ExternalScimago | None) -> str:
    if sc_ext is None:
        return seed_row.get("sjr", "") or join_cell("NOT RETRIEVED", "SCImago blocked")
    rec, how = sc if sc else (None, "")
    if rec is None:
        earlier = split_cell(seed_row.get("sjr", ""))[0]
        return join_cell(
            "not in the SCImago file",
            f"VERIFIED absence: no ISSN or title match among {sc_ext.meta.total_rows} rows (SJR {sc_ext.year or 'year not stated'}, retrieved {sc_ext.meta.retrieved}). Value from the seed, not confirmed by the file: {earlier}",
        )
    value = f"{rec.sjr:.3f} (SJR {sc_ext.year})" if rec.sjr is not None and sc_ext.year else (f"{rec.sjr:.3f}" if rec.sjr is not None else "n/a")
    return join_cell(value, f"VERIFIED: SCImago CSV ({sc_ext.meta.source}, retrieved {sc_ext.meta.retrieved}), matched by {how}, SCImago source ID {rec.sourceid}")


def quartiles_cell(seed_row: Mapping[str, str], sc: tuple[ScimagoRecord | None, str] | None, sc_ext: ExternalScimago | None) -> str:
    if sc_ext is None:
        return seed_row.get("quartiles", "") or join_cell("NOT RETRIEVED", "SCImago blocked")
    rec, how = sc if sc else (None, "")
    if rec is None:
        return join_cell("not in the SCImago file", "VERIFIED absence, see sjr column")
    cats = "; ".join(f"{name}: {q or 'n/a'}" for name, q in rec.categories) or f"best quartile {rec.best_quartile or 'n/a'}"
    return join_cell(cats, f"VERIFIED: every SCImago category with its quartile, SJR {sc_ext.year or 'year not stated'} file, best quartile {rec.best_quartile or 'n/a'}")


def screening_cell(seed_row: Mapping[str, str], column: str, ext: ExternalList | None, result: ScreenResult | None, issns: Sequence[str]) -> str:
    if ext is None or result is None:
        return seed_row.get(column, "") or join_cell("NOT CHECKED", "list not loaded")
    if column == "scopus_discontinued_check":
        return lists.render_discontinued(result, ext.meta, issns)
    return lists.render_hijacked(result, ext.meta, issns)


def sources_cell(seed_row: Mapping[str, str], oa_rows: list[dict], ext: External) -> str:
    parts = [seed_row.get("sources", "").strip()]
    if oa_rows:
        ids = ", ".join(r["openalex_id"] for r in oa_rows)
        parts.append(f"[oa] OpenAlex snapshot {oa_rows[0]['snapshot_date']}, source ID {ids} ({OPENALEX_MANIFEST_URL})")
    if ext.scimago:
        parts.append(f"[scimago-csv] {ext.scimago.meta.source}, retrieved {ext.scimago.meta.retrieved}")
    if ext.scopus:
        parts.append(f"[scopus-list] {ext.scopus.meta.source}, retrieved {ext.scopus.meta.retrieved}")
    if ext.hijacked:
        parts.append(f"[hijack-list] {ext.hijacked.meta.source}, retrieved {ext.hijacked.meta.retrieved}")
    return "; ".join(p.rstrip("; ") for p in parts if p)


def compute_status(row: Mapping[str, str]) -> str:
    """VERIFIED, PARTIAL or UNVERIFIED, see the module docstring."""
    def flagged(col: str) -> bool:
        return any(flag in row.get(col, "") for flag in UNCERTAIN_FLAGS)

    if not any(flagged(c) for c in FACT_COLUMNS):
        return "VERIFIED"
    return "PARTIAL" if not flagged("publisher") else "UNVERIFIED"


def build_rows(seed_rows: Sequence[Mapping[str, str]], oa: Mapping[str, Mapping], ext: External | None = None) -> list[dict[str, str]]:
    """The output rows, in seed order, with exactly OUTPUT_COLUMNS."""
    ext = ext or External()
    out = []
    for seed in seed_rows:
        oa_rows = _oa_rows_for(seed, oa)
        oa_row = _single_oa(seed, oa_rows)
        key = seed["key"]
        issns = venue_issns(seed, oa_rows)
        sc = ext.scimago.matches.get(key) if ext.scimago else None
        row = {
            "venue": seed["venue"],
            "type": seed["type"],
            "issn_print": seed["issn_print"],
            "issn_online": seed["issn_online"],
            "publisher": publisher_cell(seed, oa_row),
            "sjr": sjr_cell(seed, sc, ext.scimago),
            "quartiles": quartiles_cell(seed, sc, ext.scimago),
            "h_index": h_index_cell(seed, oa_row, sc, ext.scimago),
            "oa_model": oa_model_cell(seed, oa_row),
            "apc_usd": apc_cell(seed, oa_row),
            "review_time": seed["review_time"],
            "accepts_letters_or_short": seed["accepts_letters_or_short"],
            "ai_disclosure_policy": seed["ai_disclosure_policy"],
            "fit_letter": seed["fit_letter"],
            "fit_flagship": seed["fit_flagship"],
            "scopus_discontinued_check": screening_cell(
                seed, "scopus_discontinued_check", ext.scopus, ext.scopus.results.get(key) if ext.scopus else None, issns
            ),
            "hijacked_check": screening_cell(
                seed, "hijacked_check", ext.hijacked, ext.hijacked.results.get(key) if ext.hijacked else None, issns
            ),
            "sources": sources_cell(seed, oa_rows, ext),
        }
        row["verification_status"] = compute_status(row)
        out.append(row)
    return out


def write_rows(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------------------------
# compact table for docs/journals.md

PUBLISHER_SHORT = {
    "institute of electrical and electronics engineers": "IEEE",
    "multidisciplinary digital publishing institute": "MDPI",
    "elsevier bv": "Elsevier",
    "taylor & francis": "T&F",
    "oxford university press": "OUP",
    "american association for the advancement of science": "AAAS",
    "frontiers media": "Frontiers",
}


def best_quartile(quartile_cell: str) -> str:
    value, _ = split_cell(quartile_cell)
    nums = [int(n) for n in re.findall(r"\bQ([1-4])\b", value)]
    return f"Q{min(nums)}" if nums else "n/r"


def _first_number(text: str) -> str:
    m = re.search(r"\d[\d.,]*", text or "")
    return m.group(0) if m else "n/r"


def _score(cell: str) -> str:
    m = re.match(r"\s*([0-3])\b", cell or "")
    return m.group(1) if m else "?"


def render_table(rows: Sequence[Mapping[str, str]]) -> str:
    """Compact markdown table: venue, publisher, SJR and best quartile, OA and APC, letters, fit, status."""
    head = (
        "| Venue | Publisher | SJR (year), best quartile | OA model, APC (USD) | Letters or short type | Fit letter | Fit flagship | Status |\n"
        "|---|---|---|---|---|---|---|---|"
    )
    lines = [head]
    for r in rows:
        pub, _ = split_cell(r["publisher"])
        pub = PUBLISHER_SHORT.get(pub.lower(), pub)
        sjr_value, sjr_prov = split_cell(r["sjr"])
        mark = "*" if "UNVERIFIED" in sjr_prov else ""
        sjr = f"{sjr_value}, {best_quartile(r['quartiles'])}{mark}"
        oa_value, _ = split_cell(r["oa_model"])
        apc_value, _ = split_cell(r["apc_usd"])
        apc = _first_number(apc_value) if re.search(r"\d", apc_value) else apc_value.split(" (")[0]
        apc = "n/r" if apc == "NOT RETRIEVED" else apc
        oa = f"{oa_value.split(' (')[0]}, {apc}"
        letters, _ = split_cell(r["accepts_letters_or_short"])
        status = {"VERIFIED": "V", "PARTIAL": "P", "UNVERIFIED": "U"}.get(r["verification_status"], "?")
        cells = [r["venue"], pub, sjr, oa, letters, _score(r["fit_letter"]), _score(r["fit_flagship"]), status]
        lines.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    return "\n".join(lines)


def replace_block(text: str, new_block: str, begin: str = TABLE_BEGIN, end: str = TABLE_END) -> str | None:
    """Text with the content between the two markers replaced, or None when a marker is missing."""
    i, j = text.find(begin), text.find(end)
    if i < 0 or j < 0 or j < i:
        return None
    return text[: i + len(begin)] + "\n" + new_block + "\n" + text[j:]


# ---------------------------------------------------------------------------------------------
# loading the official lists

@dataclass
class SourceReport:
    name: str
    state: str  # fresh, cached, unavailable or failed
    message: str = ""


@dataclass
class Options:
    seed: Path
    openalex_csv: Path
    out_csv: Path
    cache_json: Path
    doc_path: Path | None = None
    scimago_csv: Path | None = None
    scimago_url: str = SCIMAGO_URL
    scimago_year: int | None = None
    scopus_xlsx: Path | None = None
    scopus_url: str | None = None
    hijacked_csv: Path | None = None
    hijacked_url: str | None = None
    offline: bool = False
    dry_run: bool = False
    today: str = field(default_factory=lambda: date.today().isoformat())


def _file_date(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


def _read_bytes(path: Path, what: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise FetchError("parse", str(path), f"cannot read the {what} file: {exc}") from exc


def _fetch_scimago(opts: Options, fetcher: Fetcher) -> tuple[ScimagoTable, ListMeta]:
    if opts.scimago_csv:
        data, source, when = _read_bytes(opts.scimago_csv, "SCImago CSV"), str(opts.scimago_csv), _file_date(opts.scimago_csv)
    else:
        url = opts.scimago_url
        if opts.scimago_year:
            url += ("&" if "?" in url else "?") + f"year={opts.scimago_year}"
        data, source, when = fetcher.get_bytes(url), url, opts.today
    try:
        table = scimago.parse_scimago_csv(fetch.decode_text(data))
    except ValueError as exc:
        raise FetchError("parse", source, str(exc)) from exc
    if table.year is None:
        table.year = opts.scimago_year
    return table, ListMeta("SCImago CSV", source, when, len(table.records))


def _fetch_scopus(opts: Options, fetcher: Fetcher) -> tuple[ListIndex, ListMeta]:
    if opts.scopus_xlsx:
        data, source, when = _read_bytes(opts.scopus_xlsx, "Scopus discontinued-sources xlsx"), str(opts.scopus_xlsx), _file_date(opts.scopus_xlsx)
    else:
        url = opts.scopus_url
        if not url:
            page = fetch.decode_text(fetcher.get_bytes(SCOPUS_POLICY_URL))
            found = fetch.find_discontinued_xlsx(page, SCOPUS_POLICY_URL)
            if not found:
                raise FetchError("parse", SCOPUS_POLICY_URL, "no link to a 'discontinued' .xlsx file found on the Scopus content policy page")
            url = found[0]
        data, source, when = fetcher.get_bytes(url), url, opts.today
    try:
        sheets = read_xlsx(data)
    except XlsxError as exc:
        raise FetchError("parse", source, f"not a readable .xlsx workbook: {exc}") from exc
    best: list[dict[str, str]] = []
    for _name, rows in sheets:
        records = rows_to_records(rows, "issn")
        if len(records) > len(best):
            best = records
    if not best:
        raise FetchError("parse", source, "no sheet with an ISSN header row was found in the workbook")
    entries = lists.entries_from_records(best, "discontinued")
    return ListIndex(entries), ListMeta("Scopus discontinued sources", source, when, len(entries))


def _csv_records(text: str, source: str) -> list[dict[str, str]]:
    if text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        raise FetchError("parse", source, "got an HTML page instead of CSV (the sheet is not public or needs a login)")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    hint = re.compile(r"journal|title|issn|url|name", re.IGNORECASE)
    start = next(
        (i for i, r in enumerate(rows) if sum(1 for c in r if c.strip()) >= 2 and any(hint.search(c) for c in r)), None
    )
    if start is None:
        return []
    headers: list[str] = []
    for j, h in enumerate(rows[start]):  # a banner or title row above the header is skipped
        name = h.strip() or f"column_{j + 1}"
        while name in headers:
            name += "_2"
        headers.append(name)
    out = []
    for r in rows[start + 1 :]:
        if any(c.strip() for c in r):
            padded = list(r) + [""] * (len(headers) - len(r))
            out.append({h: padded[j].strip() for j, h in enumerate(headers)})
    return out


def _fetch_hijacked(opts: Options, fetcher: Fetcher) -> tuple[ListIndex, ListMeta]:
    if opts.hijacked_csv:
        text = fetch.decode_text(_read_bytes(opts.hijacked_csv, "hijacked-journal CSV"))
        source, when = str(opts.hijacked_csv), _file_date(opts.hijacked_csv)
        records = _csv_records(text, source)
    else:
        candidates: list[str] = []
        if opts.hijacked_url:
            candidates = [opts.hijacked_url]
        else:
            page = fetch.decode_text(fetcher.get_bytes(RW_CHECKER_URL))
            for url in fetch.find_google_sheet_urls(page):
                csv_url = fetch.sheet_csv_url(url)
                if csv_url and csv_url not in candidates:
                    candidates.append(csv_url)
            if not candidates:
                raise FetchError("parse", RW_CHECKER_URL, "no Google Sheets link found on the Hijacked Journal Checker page")
        records, source, last_error = [], "", None
        for url in candidates:
            try:
                records = _csv_records(fetch.decode_text(fetcher.get_bytes(url)), url)
            except FetchError as exc:
                last_error = exc
                continue
            if records:
                source = url
                break
        if not records:
            raise last_error or FetchError("parse", candidates[0], "the sheet had no rows")
        when = opts.today
    entries = lists.entries_from_records(records, "hijacked")
    if not entries:
        raise FetchError("parse", source, "no usable rows (titles or ISSNs) in the hijacked-journal list")
    return ListIndex(entries), ListMeta("Retraction Watch Hijacked Journal Checker", source, when, len(entries))


def run(opts: Options, fetcher: Fetcher | None = None, log: Callable[[str], None] = print) -> tuple[list[dict[str, str]], list[SourceReport]]:
    """Rebuild the output rows. Never raises for a blocked or broken list: that source is reported and skipped."""
    seed_rows = read_seed(opts.seed)
    oa = openalex.read_cache(opts.openalex_csv)
    if not oa:
        raise FileNotFoundError(f"{opts.openalex_csv} is missing or empty. Run scripts/journals_openalex.py first.")
    oa_by_key = {r["key"]: _oa_rows_for(r, oa) for r in seed_rows}
    ext = load_external(opts.cache_json)
    reports: list[SourceReport] = []
    fetcher = fetcher or fetch.RequestsFetcher()

    def attempt(name: str, option: str, wanted_local: bool, loader: Callable[[], object], apply: Callable[[object], None], have_cache: bool) -> None:
        if opts.offline and not wanted_local:
            reports.append(SourceReport(name, "cached" if have_cache else "unavailable", "offline mode, no download attempted"))
            return
        try:
            result = loader()
        except Exception as exc:  # noqa: BLE001 - one broken source must not stop the others
            message = exc.describe(option) if isinstance(exc, FetchError) else f"unexpected {type(exc).__name__}: {exc}"
            reports.append(SourceReport(name, "failed", message))
            if have_cache:
                reports.append(SourceReport(name, "cached", "using the copy saved by an earlier successful run"))
            return
        apply(result)
        reports.append(SourceReport(name, "fresh"))

    def apply_scimago(result: object) -> None:
        table, meta = result  # type: ignore[misc]
        ext.scimago = ExternalScimago(table.year, meta, scimago_matches(seed_rows, table, oa_by_key))

    def apply_list(attr: str) -> Callable[[object], None]:
        def inner(result: object) -> None:
            index, meta = result  # type: ignore[misc]
            setattr(ext, attr, ExternalList(meta, screen_venues(seed_rows, index, oa_by_key)))
        return inner

    attempt("SCImago CSV", "--scimago-csv PATH", bool(opts.scimago_csv), lambda: _fetch_scimago(opts, fetcher), apply_scimago, ext.scimago is not None)
    attempt("Scopus discontinued sources", "--scopus-xlsx PATH", bool(opts.scopus_xlsx), lambda: _fetch_scopus(opts, fetcher), apply_list("scopus"), ext.scopus is not None)
    attempt("Hijacked Journal Checker", "--hijacked-csv PATH", bool(opts.hijacked_csv), lambda: _fetch_hijacked(opts, fetcher), apply_list("hijacked"), ext.hijacked is not None)

    rows = build_rows(seed_rows, oa, ext)
    if not opts.dry_run:
        write_rows(opts.out_csv, rows)
        if ext.scimago or ext.scopus or ext.hijacked:  # nothing to save when every list was unavailable
            save_external(opts.cache_json, ext)
        if opts.doc_path and opts.doc_path.exists():
            text = opts.doc_path.read_text(encoding="utf-8")
            new = replace_block(text, render_table(rows))
            if new is None:
                log(f"note: {opts.doc_path} has no {TABLE_BEGIN} / {TABLE_END} markers, table not refreshed")
            elif new != text:
                opts.doc_path.write_text(new, encoding="utf-8")
    return rows, reports


def exit_code(reports: Sequence[SourceReport]) -> int:
    """0 when no download failed, 2 when at least one source failed (the CSV is still written)."""
    return 2 if any(r.state == "failed" for r in reports) else 0


def summarize(rows: Sequence[Mapping[str, str]]) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verification_status"]] = counts.get(r["verification_status"], 0) + 1
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))


def print_reports(reports: Sequence[SourceReport], out=sys.stdout) -> None:
    for r in reports:
        label = {"fresh": "OK     ", "cached": "CACHED ", "unavailable": "MISSING", "failed": "FAILED "}[r.state]
        out.write(f"[{label}] {r.name}\n")
        if r.message:
            for line in r.message.splitlines():
                out.write(f"          {line}\n")
