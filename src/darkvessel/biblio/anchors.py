"""Extra metadata for the anchor papers, read from the OpenAlex works snapshot.

The scan keeps only the columns needed to match themes and build the tables. For the
handful of anchor records this module re-reads a few more columns (landing page and PDF
URLs, licence, volume, issue, pages, open access status, primary topic) with the same
column-projected range requests, one row group per file. It never downloads a whole file.

    from darkvessel.biblio import anchors
    meta = anchors.fetch_anchor_metadata(cache_dir, targets)   # targets: id, file, row_group, row
"""

from __future__ import annotations

import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow as pa

from .snapshot import RangeClient, RemoteParquet

META_LEAVES = [
    "primary_location.landing_page_url",
    "primary_location.pdf_url",
    "primary_location.license",
    "primary_location.version",
    "primary_location.is_published",
    "primary_location.raw_source_name",
    "primary_location.raw_type",
    "primary_location.source.display_name",
    "primary_location.source.host_organization_name",
    "primary_location.source.type",
    "locations.list.element.landing_page_url",
    "locations.list.element.pdf_url",
    "locations.list.element.version",
    "locations.list.element.raw_source_name",
    "locations.list.element.source.display_name",
    "locations.list.element.source.type",
    "open_access.is_oa",
    "open_access.oa_status",
    "open_access.oa_url",
    "biblio.volume",
    "biblio.issue",
    "biblio.first_page",
    "biblio.last_page",
    "primary_topic.display_name",
    "primary_topic.subfield.display_name",
    "primary_topic.field.display_name",
    "referenced_works_count",
    "locations_count",
    "fwci",
]


def fetch_anchor_metadata(cache_dir: Path, targets: list[dict], workers: int = 4, log=print) -> dict:
    """Return {openalex_id: nested metadata dict} for the stored rows in targets.

    targets: dicts with id, file ("updated_date=YYYY-MM-DD/part_NNNN"), row_group, row.
    Also returns the byte count under the key "_bytes_read".
    """
    by_file: dict[str, dict[int, list[tuple[int, str]]]] = defaultdict(lambda: defaultdict(list))
    for t in targets:
        by_file[t["file"]][t["row_group"]].append((t["row"], t["id"]))

    client = RangeClient(pool_size=16)
    out: dict[str, dict] = {}
    t0 = time.time()
    with ThreadPoolExecutor(16) as fetch_pool, ThreadPoolExecutor(workers) as outer:

        def one_file(tag: str) -> dict[str, dict]:
            got: dict[str, dict] = {}
            url = f"s3://openalex/data/parquet/works/{tag}.parquet"
            with RemoteParquet(client, url, META_LEAVES) as rp:
                for rg, items in by_file[tag].items():
                    for fut in rp.submit_rg(fetch_pool, rg):
                        fut.result()
                    tb = rp.read_rg(rg, META_LEAVES)
                    sel = tb.take(pa.array([r for r, _ in items], type=pa.int64())).to_pylist()
                    for (_, rid), rec in zip(items, sel):
                        got[rid] = rec
                    rp.release_rg(rg)
            return got

        for fut in [outer.submit(one_file, tag) for tag in by_file]:
            out.update(fut.result())
    out["_bytes_read"] = client.bytes_read
    log(f"  anchor metadata: {len(out) - 1} works, {client.bytes_read / 1e6:.0f} MB read in {time.time() - t0:.0f}s")
    return out


def flatten(rec: dict) -> dict:
    """Pick the fields worth showing in a table from one nested record."""
    prim = rec.get("primary_location") or {}
    psrc = prim.get("source") or {}
    oa = rec.get("open_access") or {}
    bib = rec.get("biblio") or {}
    topic = rec.get("primary_topic") or {}
    first, last = bib.get("first_page"), bib.get("last_page")
    pages = "-".join(str(p) for p in (first, last) if p) if first or last else ""
    locs = rec.get("locations") or []
    return {
        "landing_page_url": prim.get("landing_page_url") or "",
        "pdf_url": prim.get("pdf_url") or "",
        "license": prim.get("license") or "",
        "version": prim.get("version") or "",
        "primary_raw_source_name": prim.get("raw_source_name") or "",
        "primary_raw_type": prim.get("raw_type") or "",
        "host_organization": psrc.get("host_organization_name") or "",
        "is_oa": oa.get("is_oa"),
        "oa_status": oa.get("oa_status") or "",
        "oa_url": oa.get("oa_url") or "",
        "volume": bib.get("volume") or "",
        "issue": bib.get("issue") or "",
        "pages": pages,
        "primary_topic": topic.get("display_name") or "",
        "primary_subfield": (topic.get("subfield") or {}).get("display_name") or "",
        "primary_field": (topic.get("field") or {}).get("display_name") or "",
        "referenced_works_count": rec.get("referenced_works_count"),
        "locations_count": rec.get("locations_count"),
        "fwci": rec.get("fwci"),
        "location_urls": [loc.get("landing_page_url") for loc in locs if loc.get("landing_page_url")],
    }
