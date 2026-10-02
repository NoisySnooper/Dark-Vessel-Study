"""OpenAlex sources snapshot: pull the rows for the shortlisted venues.

The public snapshot is Parquet on S3, readable over HTTPS without a key:
https://openalex.s3.amazonaws.com/data/parquet/sources/ with a manifest.json
that lists one part file per updated_date. The whole entity is about 160 MB, so
this module downloads each part in memory, keeps only the columns it needs, keeps
only the rows whose ISSN or OpenAlex ID belongs to a venue on the shortlist, and
discards the rest. The kept rows are cached in data/journals/openalex_sources.csv,
which is small enough to commit.

pyarrow is imported lazily so the rest of the package stays importable without it.
"""

from __future__ import annotations

import csv
import io
import json
import os
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from darkvessel.journals.fetch import Fetcher, FetchError

SNAPSHOT_BASE = "https://openalex.s3.amazonaws.com/"
S3_PREFIX = "s3://openalex/"
MANIFEST_URL = SNAPSHOT_BASE + "data/parquet/sources/manifest.json"

# Columns read from each Parquet part. The heavy topic and per-year count columns are skipped.
PARQUET_COLUMNS = [
    "id", "issn_l", "issn", "display_name", "host_organization_name", "type", "works_count",
    "cited_by_count", "summary_stats", "is_oa", "is_in_doaj", "is_in_doaj_since_year",
    "apc_prices", "apc_usd", "apc_usd_by_year", "homepage_url", "updated_date",
]

CACHE_FIELDS = [
    "openalex_id", "display_name", "issn_l", "issns", "host_organization_name", "type",
    "is_oa", "is_in_doaj", "is_in_doaj_since_year", "apc_usd", "apc_prices_json",
    "apc_usd_by_year_json", "h_index", "two_year_mean_citedness", "works_count",
    "cited_by_count", "homepage_url", "updated_date", "snapshot_date",
]


def https_url(url: str) -> str:
    """Map s3://openalex/... (as written in the manifest) to the anonymous HTTPS address."""
    return SNAPSHOT_BASE + url[len(S3_PREFIX):] if url.startswith(S3_PREFIX) else url


def fetch_manifest(fetcher: Fetcher) -> dict:
    try:
        return json.loads(fetcher.get_bytes(MANIFEST_URL))
    except json.JSONDecodeError as exc:
        raise FetchError("parse", MANIFEST_URL, f"manifest.json is not valid JSON ({exc})") from exc


def short_id(openalex_id: str) -> str:
    return (openalex_id or "").rsplit("/", 1)[-1]


def _as_int(value: object) -> str:
    return "" if value is None or value != value else str(int(value))


def _record(row: dict, snapshot_date: str) -> dict:
    """One cache row from one Parquet row (as a dict of Python values)."""
    stats = row.get("summary_stats") or {}
    prices = [dict(p) for p in (row.get("apc_prices") or [])]
    by_year = [dict(p) for p in (row.get("apc_usd_by_year") or [])]
    updated = row.get("updated_date")
    mean = stats.get("2yr_mean_citedness")
    return {
        "openalex_id": short_id(row.get("id") or ""),
        "display_name": row.get("display_name") or "",
        "issn_l": row.get("issn_l") or "",
        "issns": ";".join(row.get("issn") or []),
        "host_organization_name": row.get("host_organization_name") or "",
        "type": row.get("type") or "",
        "is_oa": str(bool(row.get("is_oa"))),
        "is_in_doaj": str(bool(row.get("is_in_doaj"))),
        "is_in_doaj_since_year": _as_int(row.get("is_in_doaj_since_year")),
        "apc_usd": _as_int(row.get("apc_usd")),
        "apc_prices_json": json.dumps(prices, separators=(",", ":")) if prices else "",
        "apc_usd_by_year_json": json.dumps(by_year, separators=(",", ":")) if by_year else "",
        "h_index": _as_int(stats.get("h_index")),
        "two_year_mean_citedness": "" if mean is None else f"{mean:.4f}",
        "works_count": _as_int(row.get("works_count")),
        "cited_by_count": _as_int(row.get("cited_by_count")),
        "homepage_url": row.get("homepage_url") or "",
        "updated_date": updated.date().isoformat() if hasattr(updated, "date") else str(updated or "")[:10],
        "snapshot_date": snapshot_date,
    }


def rows_from_parquet(data: bytes, issns: set[str], ids: set[str], snapshot_date: str) -> list[dict]:
    """Rows of one Parquet part that match a wanted ISSN or OpenAlex ID."""
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise FetchError("dependency", "", "pyarrow is not installed (pip install pyarrow)") from exc
    table = pq.read_table(io.BytesIO(data), columns=PARQUET_COLUMNS)
    issn_l = table.column("issn_l").to_pylist()
    issn = table.column("issn").to_pylist()
    oa_id = table.column("id").to_pylist()
    keep = [
        i
        for i in range(table.num_rows)
        if short_id(oa_id[i] or "") in ids
        or (issn_l[i] and issn_l[i] in issns)
        or any(x in issns for x in (issn[i] or []))
    ]
    if not keep:
        return []
    return [_record(r, snapshot_date) for r in table.take(keep).to_pylist()]


def collect(
    manifest: dict,
    fetcher: Fetcher,
    issns: Iterable[str],
    ids: Iterable[str],
    workers: int = 6,
    cache_dir: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """Download every part of the sources snapshot and return the matching rows (one per OpenAlex ID)."""
    wanted_issns, wanted_ids = set(issns), set(ids)
    snapshot_date = str(manifest.get("date", ""))
    parts = [(https_url(f["url"]), int(f.get("meta", {}).get("content_length", 0))) for f in manifest["files"]]

    def work(item: tuple[str, int]) -> list[dict]:
        url, size = item
        local = None
        if cache_dir is not None:
            local = Path(cache_dir) / url.split("updated_date=")[-1].replace("/", "_")
            if local.exists() and (not size or local.stat().st_size == size):
                return rows_from_parquet(local.read_bytes(), wanted_issns, wanted_ids, snapshot_date)
        data = fetcher.get_bytes(url)
        if size and len(data) != size:
            raise FetchError("network", url, f"download is {len(data)} bytes, manifest says {size}")
        if local is not None:
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
        return rows_from_parquet(data, wanted_issns, wanted_ids, snapshot_date)

    found: dict[str, dict] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(work, p) for p in parts]
        try:
            for fut in as_completed(futures):
                for rec in fut.result():  # re-raises the first FetchError
                    found[rec["openalex_id"]] = rec
                done += 1
                if progress:
                    progress(done, len(parts))
        except BaseException:
            for f in futures:  # stop queued downloads, running ones finish on their own
                f.cancel()
            raise
    return sorted(found.values(), key=lambda r: r["openalex_id"])


def write_cache(rows: list[dict], manifest: dict, csv_path: Path, meta_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CACHE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    meta = {
        "snapshot_date": manifest.get("date"),
        "manifest_url": MANIFEST_URL,
        "record_count": manifest.get("record_count"),
        "part_files": len(manifest.get("files", [])),
        "rows_kept": len(rows),
        "retrieved": date.today().isoformat(),
        "note": "Rows cut from the OpenAlex sources snapshot for the shortlisted venues only.",
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def read_cache(csv_path: Path) -> dict[str, dict]:
    """Cached rows keyed by OpenAlex source ID. Empty dict when the file does not exist."""
    if not Path(csv_path).exists():
        return {}
    with open(csv_path, newline="", encoding="utf-8") as fh:
        return {row["openalex_id"]: row for row in csv.DictReader(fh)}


def parse_cache_row(row: dict) -> dict:
    """Typed view of a cache row: bools, ints and the APC lists decoded."""
    def to_int(text: str) -> int | None:
        return int(text) if str(text or "").strip().lstrip("-").isdigit() else None

    return {
        **row,
        "is_oa": row.get("is_oa") == "True",
        "is_in_doaj": row.get("is_in_doaj") == "True",
        "is_in_doaj_since_year": to_int(row.get("is_in_doaj_since_year", "")),
        "apc_usd": to_int(row.get("apc_usd", "")),
        "apc_prices": json.loads(row["apc_prices_json"]) if row.get("apc_prices_json") else [],
        "apc_usd_by_year": json.loads(row["apc_usd_by_year_json"]) if row.get("apc_usd_by_year_json") else [],
        "h_index": to_int(row.get("h_index", "")),
        "issn_list": [x for x in (row.get("issns") or "").split(";") if x],
    }


def default_workers() -> int:
    return min(8, (os.cpu_count() or 2) * 2)
