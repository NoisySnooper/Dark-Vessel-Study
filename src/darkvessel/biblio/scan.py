"""Scan the OpenAlex works snapshot for dark-vessel literature.

scan_file()  reads one remote Parquet file and returns the rows that match any
             loose theme (plus any watch-listed DOI such as the anchor papers).
run_scan()   drives a pool of worker processes over the manifest, writing one
             filtered Parquet file per input file. It is resumable: a file whose
             output already exists is skipped. Outputs are written atomically.

Per row group the scanner

  phase A  decodes year, title, abstract (inverted-index JSON) and DOI only,
           keeps rows with 2015 <= year <= 2026, runs a cheap RE2 anchor prefilter
           in Arrow, rebuilds the abstract text of the survivors and applies the
           loose theme regexes in Python;
  phase B  only if something matched, decodes the remaining columns (venue,
           authors, institutions, countries) and takes the matched rows.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import queue
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from . import themes as T
from .snapshot import WORKS_MANIFEST_URL, RangeClient, RemoteParquet, https_url

# ---------------------------------------------------------------------------
# columns
# ---------------------------------------------------------------------------
PHASE_A = ["publication_year", "title", "abstract_inverted_index", "doi"]
PHASE_B = [
    "id",
    "publication_date",
    "type",
    "cited_by_count",
    "is_retracted",
    "is_paratext",
    "is_xpac",
    "language",
    "primary_location.is_published",
    "primary_location.version",
    "primary_location.raw_type",
    "primary_location.source.id",
    "primary_location.source.display_name",
    "primary_location.source.issn_l",
    "primary_location.source.issn.list.element",
    "primary_location.source.type",
    "authorships.list.element.author.display_name",
    "authorships.list.element.institutions.list.element.id",
    "authorships.list.element.institutions.list.element.display_name",
    "authorships.list.element.institutions.list.element.country_code",
    "authorships.list.element.countries.list.element",
]
ALL_LEAVES = PHASE_A + PHASE_B

FILTERED_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("doi", pa.string()),
        ("title", pa.string()),
        ("publication_year", pa.int32()),
        ("publication_date", pa.string()),
        ("type", pa.string()),
        ("cited_by_count", pa.int32()),
        ("language", pa.string()),
        ("is_retracted", pa.bool_()),
        ("is_paratext", pa.bool_()),
        ("is_xpac", pa.bool_()),
        ("abstract", pa.string()),
        ("themes_loose", pa.string()),
        ("watch", pa.bool_()),
        ("source_id", pa.string()),
        ("source_name", pa.string()),
        ("source_type", pa.string()),
        ("source_issn_l", pa.string()),
        ("source_issn", pa.string()),
        ("loc_is_published", pa.bool_()),
        ("loc_version", pa.string()),
        ("loc_raw_type", pa.string()),
        ("authors", pa.list_(pa.string())),
        ("n_authors", pa.int32()),
        ("inst_ids", pa.list_(pa.string())),
        ("inst_names", pa.list_(pa.string())),
        ("inst_countries", pa.list_(pa.string())),
        ("countries", pa.list_(pa.string())),
        ("file", pa.string()),
        ("row_group", pa.int32()),
        ("row", pa.int32()),
    ]
)

# DOIs always kept, whatever the title and abstract say (anchor papers).
DEFAULT_WATCH_DOIS = (
    "10.1038/s41586-023-06825-8",  # Paolo et al. 2024, Nature
    "10.1126/sciadv.abb1197",  # Park et al. 2020, Science Advances
    "10.48550/arxiv.2206.00897",  # xView3-SAR, arXiv
    "10.3390/rs70303020",  # Elvidge et al. 2015, Remote Sensing
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def short_id(value: str | None) -> str | None:
    """https://openalex.org/W123 -> W123."""
    if not value:
        return None
    return value.rsplit("/", 1)[-1]


def bare_doi(value: str | None) -> str | None:
    """https://doi.org/10.1/X -> 10.1/x (lower case)."""
    if not value:
        return None
    v = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if v.startswith(prefix):
            v = v[len(prefix):]
            break
    return v or None


def _arr(x):
    """ChunkedArray -> Array (no copy when single chunk)."""
    if isinstance(x, pa.ChunkedArray):
        if x.num_chunks == 1:
            return x.chunk(0)
        if x.num_chunks == 0:
            return pa.array([], type=x.type)
        return x.combine_chunks()
    return x


def _row_to_record(rec: dict, themes: list[str], abstract: str, watch: bool, file_tag: str, rg: int, row: int) -> dict:
    primary = rec.get("primary_location") or {}
    source = primary.get("source") or {}
    issn = source.get("issn") or []
    authors, inst_ids, inst_names, inst_countries, countries = [], [], [], [], []
    seen_inst: set[str] = set()
    seen_country: set[str] = set()
    for a in rec.get("authorships") or []:
        name = (a.get("author") or {}).get("display_name")
        authors.append(name or "")
        for inst in a.get("institutions") or []:
            iid = short_id(inst.get("id"))
            if iid and iid not in seen_inst:
                seen_inst.add(iid)
                inst_ids.append(iid)
                inst_names.append(inst.get("display_name") or "")
                inst_countries.append(inst.get("country_code") or "")
            cc = inst.get("country_code")
            if cc and cc not in seen_country:
                seen_country.add(cc)
                countries.append(cc)
        for cc in a.get("countries") or []:
            if cc and cc not in seen_country:
                seen_country.add(cc)
                countries.append(cc)
    return {
        "id": short_id(rec.get("id")),
        "doi": bare_doi(rec.get("doi")),
        "title": rec.get("title"),
        "publication_year": rec.get("publication_year"),
        "publication_date": str(rec["publication_date"]) if rec.get("publication_date") else None,
        "type": rec.get("type"),
        "cited_by_count": rec.get("cited_by_count"),
        "language": rec.get("language"),
        "is_retracted": rec.get("is_retracted"),
        "is_paratext": rec.get("is_paratext"),
        "is_xpac": rec.get("is_xpac"),
        "abstract": abstract,
        "themes_loose": ";".join(themes),
        "watch": watch,
        "source_id": short_id(source.get("id")),
        "source_name": source.get("display_name"),
        "source_type": source.get("type"),
        "source_issn_l": source.get("issn_l"),
        "source_issn": ";".join(issn),
        "loc_is_published": primary.get("is_published"),
        "loc_version": primary.get("version"),
        "loc_raw_type": primary.get("raw_type"),
        "authors": authors,
        "n_authors": len(authors),
        "inst_ids": inst_ids,
        "inst_names": inst_names,
        "inst_countries": inst_countries,
        "countries": countries,
        "file": file_tag,
        "row_group": rg,
        "row": row,
    }


# ---------------------------------------------------------------------------
# one file
# ---------------------------------------------------------------------------
def scan_file(
    client: RangeClient,
    pool: ThreadPoolExecutor,
    url: str,
    file_tag: str,
    watch_dois: tuple[str, ...] = DEFAULT_WATCH_DOIS,
    ahead: int = 2,
    year_min: int = T.YEAR_MIN,
    year_max: int = T.YEAR_MAX,
) -> tuple[pa.Table, dict]:
    """Scan one remote file. Returns (matched rows as a FILTERED_SCHEMA table, stats)."""
    t_start = time.time()
    bytes_before = client.bytes_read
    stats = {
        "file": file_tag,
        "rows": 0,
        "rows_in_range": 0,
        "candidates": 0,
        "matches": 0,
        "watch_hits": 0,
        "row_groups": 0,
        "fetch_wait_s": 0.0,
        "decode_s": 0.0,
        "match_s": 0.0,
    }
    watch_set = pa.array(["https://doi.org/" + d for d in watch_dois], type=pa.string())
    records: list[dict] = []

    with RemoteParquet(client, url, ALL_LEAVES) as rp:
        n_rg = rp.num_row_groups
        stats["row_groups"] = n_rg
        pending: dict[int, list] = {}

        def submit(i: int) -> None:
            if i < n_rg and i not in pending:
                pending[i] = rp.submit_rg(pool, i)

        for i in range(min(ahead, n_rg)):
            submit(i)

        for rg in range(n_rg):
            t0 = time.time()
            for fut in pending.pop(rg):
                fut.result()
            stats["fetch_wait_s"] += time.time() - t0
            submit(rg + ahead)

            t0 = time.time()
            n_rows = rp.md.row_group(rg).num_rows
            stats["rows"] += n_rows
            ta = rp.read_rg(rg, PHASE_A)
            stats["decode_s"] += time.time() - t0

            t0 = time.time()
            year = ta["publication_year"]
            in_range = pc.fill_null(
                pc.and_(pc.greater_equal(year, year_min), pc.less_equal(year, year_max)), False
            )
            stats["rows_in_range"] += pc.sum(in_range).as_py() or 0
            # regex over whole columns, then mask by year: avoids copying the big abstract column
            anchor = pc.fill_null(
                pc.or_kleene(
                    pc.match_substring_regex(ta["abstract_inverted_index"], T.ARROW_PREFILTER),
                    pc.match_substring_regex(ta["title"], T.ARROW_PREFILTER),
                ),
                False,
            )
            watch = pc.fill_null(pc.is_in(pc.utf8_lower(ta["doi"]), value_set=watch_set), False)
            keep = pc.or_(pc.and_(anchor, in_range), watch)
            keep_pos = _arr(pc.indices_nonzero(keep))
            stats["candidates"] += len(keep_pos)

            matched_rows: list[int] = []
            matched_meta: list[tuple[list[str], str, bool]] = []
            if len(keep_pos):
                c_titles = pc.take(ta["title"], keep_pos).to_pylist()
                c_abs = pc.take(ta["abstract_inverted_index"], keep_pos).to_pylist()
                c_watch = pc.take(watch, keep_pos).to_pylist()
                c_rows = keep_pos.to_pylist()
                for title, abs_json, is_watch, row in zip(c_titles, c_abs, c_watch, c_rows):
                    abstract = T.reconstruct_abstract(abs_json)
                    themes = T.match_themes(title, abstract, "loose")
                    if themes or is_watch:
                        matched_rows.append(row)
                        matched_meta.append((themes, abstract, bool(is_watch)))
                        stats["watch_hits"] += 1 if is_watch else 0
            stats["match_s"] += time.time() - t0

            if matched_rows:
                t0 = time.time()
                tb = rp.read_rg(rg, PHASE_B)
                take_idx = pa.array(matched_rows, type=pa.int64())
                sel_b = tb.take(take_idx).to_pylist()
                sel_a = ta.take(take_idx).to_pylist()
                for (themes, abstract, is_watch), row, rec_a, rec_b in zip(matched_meta, matched_rows, sel_a, sel_b):
                    rec = {**rec_a, **rec_b}
                    records.append(_row_to_record(rec, themes, abstract, is_watch, file_tag, rg, row))
                stats["matches"] += len(matched_rows)
                stats["decode_s"] += time.time() - t0

            rp.release_rg(rg)
            del ta

    stats["bytes"] = client.bytes_read - bytes_before
    stats["seconds"] = round(time.time() - t_start, 2)
    for key in ("fetch_wait_s", "decode_s", "match_s"):
        stats[key] = round(stats[key], 2)
    table = pa.Table.from_pylist(records, schema=FILTERED_SCHEMA) if records else FILTERED_SCHEMA.empty_table()
    return table, stats


# ---------------------------------------------------------------------------
# manifest and task list
# ---------------------------------------------------------------------------
def load_manifest(cache_dir: Path, refresh: bool = False) -> dict:
    path = cache_dir / "manifest.json"
    if refresh or not path.exists():
        client = RangeClient(pool_size=2)
        data = client.get_all(WORKS_MANIFEST_URL)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return json.loads(path.read_text())


def file_tag_of(url: str) -> str:
    """s3://.../updated_date=2026-09-22/part_0088.parquet -> updated_date=2026-09-22/part_0088."""
    parts = url.split("/")
    return f"{parts[-2]}/{parts[-1].removesuffix('.parquet')}"


def out_name_of(tag: str) -> str:
    return tag.replace("/", "__") + ".parquet"


def build_tasks(manifest: dict, out_dir: Path, only: str | None = None) -> tuple[list[dict], int]:
    """Pending tasks (largest first) and the number already done."""
    tasks, done = [], 0
    for entry in manifest["files"]:
        tag = file_tag_of(entry["url"])
        if only and only not in tag:
            continue
        if (out_dir / out_name_of(tag)).exists():
            done += 1
            continue
        tasks.append(
            {
                "url": entry["url"],
                "tag": tag,
                "size": entry["meta"]["content_length"],
                "records": entry["meta"]["record_count"],
            }
        )
    tasks.sort(key=lambda t: -t["size"])
    return tasks, done


# ---------------------------------------------------------------------------
# worker process
# ---------------------------------------------------------------------------
def worker_main(worker_id: int, task_q, result_q, out_dir: str, threads: int) -> None:
    """Process files from task_q until a None sentinel arrives."""
    import pyarrow as _pa

    _pa.set_cpu_count(1)
    _pa.set_io_thread_count(1)
    out = Path(out_dir)
    client = RangeClient(pool_size=threads + 2)
    with ThreadPoolExecutor(max_workers=threads) as pool:
        while True:
            task = task_q.get()
            if task is None:
                break
            result_q.put({"kind": "start", "worker": worker_id, "tag": task["tag"]})
            try:
                table, stats = scan_file(client, pool, task["url"], task["tag"])
                tmp = out / (out_name_of(task["tag"]) + f".tmp{worker_id}")
                pq.write_table(table, tmp, compression="zstd")
                os.replace(tmp, out / out_name_of(task["tag"]))
                stats["size"] = task["size"]
                stats["requests"] = client.requests
                result_q.put({"kind": "done", "worker": worker_id, "tag": task["tag"], "stats": stats})
            except Exception as exc:  # report and carry on; the file stays pending
                result_q.put(
                    {
                        "kind": "error",
                        "worker": worker_id,
                        "tag": task["tag"],
                        "error": f"{type(exc).__name__}: {exc}",
                        "trace": traceback.format_exc()[-1500:],
                    }
                )


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------
def run_scan(
    cache_dir: Path,
    workers: int = 8,
    threads: int = 4,
    max_minutes: float = 8.5,
    limit: int | None = None,
    only: str | None = None,
    refresh_manifest: bool = False,
    status_every: float = 30.0,
    log=print,
) -> dict:
    """Process pending manifest files until done or until max_minutes. Returns a run summary."""
    t_run = time.time()
    cache_dir = Path(cache_dir)
    out_dir = cache_dir / "filtered"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(cache_dir, refresh_manifest)
    total_files = len(manifest["files"])
    tasks, done_before = build_tasks(manifest, out_dir, only)
    if limit is not None:
        tasks = tasks[:limit]
    log(
        f"snapshot {manifest['date']}: {total_files} files, {done_before} done before this run, "
        f"{len(tasks)} queued; {workers} workers x {threads} fetch threads; budget {max_minutes} min"
    )
    if not tasks:
        return {"done_this_run": 0, "done_total": done_before, "remaining": 0, "bytes": 0, "matches": 0, "seconds": 0.0}

    # Workers decode many row groups back to back. Keeping freed memory in the allocator
    # avoids repeated page faults (a 3x decode slowdown on this host). Must be set before
    # the child imports pyarrow, so set it here and let spawn inherit it.
    os.environ.setdefault("MIMALLOC_PURGE_DELAY", "-1")
    ctx = mp.get_context("spawn")
    task_q = ctx.Queue()
    result_q = ctx.Queue()
    procs: dict[int, mp.Process] = {}
    next_wid = 0

    def spawn() -> None:
        nonlocal next_wid
        wid = next_wid
        next_wid += 1
        p = ctx.Process(target=worker_main, args=(wid, task_q, result_q, str(out_dir), threads), daemon=True)
        p.start()
        procs[wid] = p

    for _ in range(min(workers, len(tasks))):
        spawn()

    pending = deque(tasks)
    in_flight: dict[str, int] = {}  # tag -> worker id, for files a worker has started
    outstanding = 0  # dispatched but not yet done or failed
    finished = 0
    errors: list[dict] = []
    bytes_read = 0
    matches = 0
    rows_scanned = 0
    durations: deque = deque(maxlen=24)
    deadline = t_run + max_minutes * 60.0
    stopped_early = False
    last_status = 0.0
    progress_path = cache_dir / "progress.jsonl"

    def margin() -> float:
        """Seconds to keep free at the end so in-flight and queued files can finish."""
        if not durations:
            return 100.0
        ordered = sorted(durations)
        p90 = ordered[int(0.9 * (len(ordered) - 1))]
        return max(40.0, 2.2 * p90)

    def status(force: bool = False) -> None:
        nonlocal last_status
        now = time.time()
        if not force and now - last_status < status_every:
            return
        last_status = now
        el = now - t_run
        log(
            f"[{el:6.0f}s] files {done_before + finished}/{total_files} (+{finished} this run, "
            f"{len(in_flight)} in flight) | read {bytes_read / 1e9:7.1f} GB "
            f"({bytes_read / 1e6 / max(el, 1):5.0f} MB/s) | rows {rows_scanned / 1e6:7.1f} M | matches {matches}"
        )

    while True:
        can_dispatch = time.time() + margin() < deadline
        if pending and not can_dispatch:
            stopped_early = True
        while can_dispatch and pending and outstanding < workers + 1:
            task_q.put(pending.popleft())
            outstanding += 1
        if outstanding <= 0 and (not pending or not can_dispatch):
            break
        try:
            msg = result_q.get(timeout=2.0)
        except queue.Empty:
            msg = None
            for wid, p in list(procs.items()):  # detect crashed workers
                if not p.is_alive():
                    for tag in [t for t, w in in_flight.items() if w == wid]:
                        errors.append({"tag": tag, "error": "worker died", "trace": ""})
                        in_flight.pop(tag, None)
                        outstanding -= 1
                        log(f"worker {wid} died while scanning {tag}")
                    procs.pop(wid)
                    if pending and can_dispatch:
                        spawn()
        if msg is not None:
            if msg["kind"] == "start":
                in_flight[msg["tag"]] = msg["worker"]
            elif msg["kind"] == "done":
                in_flight.pop(msg["tag"], None)
                outstanding -= 1
                s = msg["stats"]
                finished += 1
                bytes_read += s["bytes"]
                matches += s["matches"]
                rows_scanned += s["rows"]
                durations.append(s["seconds"])
                with open(progress_path, "a") as fh:
                    fh.write(json.dumps(s) + "\n")
            elif msg["kind"] == "error":
                in_flight.pop(msg["tag"], None)
                outstanding -= 1
                errors.append(msg)
                log(f"ERROR {msg['tag']}: {msg['error']}")
        status()
        if time.time() > deadline + 90:
            log("hard stop: budget exceeded, terminating workers (unfinished files stay pending)")
            break

    for _ in procs:
        try:
            task_q.put(None, timeout=1)
        except queue.Full:
            pass
    for p in procs.values():
        p.join(timeout=20)
        if p.is_alive():
            p.terminate()
    status(force=True)
    elapsed = time.time() - t_run
    summary = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "snapshot": manifest["date"],
        "done_this_run": finished,
        "done_total": done_before + finished,
        "remaining": total_files - done_before - finished,
        "errors": len(errors),
        "bytes": bytes_read,
        "matches": matches,
        "rows": rows_scanned,
        "seconds": round(elapsed, 1),
        "workers": workers,
        "threads": threads,
    }
    with open(cache_dir / "runs.jsonl", "a") as fh:
        fh.write(json.dumps(summary) + "\n")
    for e in errors[:10]:
        log(f"  error {e.get('tag')}: {e.get('error')}")
    log(
        f"run finished in {elapsed:.0f}s: {finished} files, {bytes_read / 1e9:.1f} GB read, {matches} matched rows; "
        f"{summary['remaining']} files remaining"
        + (" (stopped for time budget)" if stopped_early else "")
    )
    return summary
