"""Range-request access to the OpenAlex works Parquet snapshot on S3.

The snapshot is 707 GB. We never download whole files. For each Parquet file we

1. fetch the footer with one suffix-range request,
2. work out the byte range of every needed column chunk in every row group,
3. fetch only those ranges (merged when the gap between them is small),
4. write the bytes at their true file offsets into an anonymous, lazily
   allocated mmap the size of the file, and
5. let pyarrow read the needed leaf columns from that mmap as if it were the file.

Only touched pages of the mmap use RAM, and finished row groups are returned to
the OS with madvise, so memory stays at a few row groups per file.
"""

from __future__ import annotations

import io
import mmap
import os
import random
import re
import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import Executor, Future

import pyarrow as pa
import pyarrow.parquet as pq
import urllib3

S3_HTTPS = "https://openalex.s3.amazonaws.com/"
S3_PREFIX = "s3://openalex/"
WORKS_MANIFEST_URL = S3_HTTPS + "data/parquet/works/manifest.json"
WORKS_DELETED_URL = S3_HTTPS + "data/parquet/works/deleted_ids.csv.gz"

TAIL_GUESS = 1 << 20  # first footer fetch: last 1 MiB (footers are 50 to 400 KB)
MAX_GAP = 256 * 1024  # merge two needed ranges when the unwanted gap between them is smaller
READ_CHUNK = 4 << 20  # bytes per socket read loop iteration

_CONTENT_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+)")


def https_url(url: str) -> str:
    """Map s3://openalex/... (as used in the manifest) to the anonymous HTTPS URL."""
    if url.startswith(S3_PREFIX):
        return S3_HTTPS + url[len(S3_PREFIX):]
    return url


def _ca_bundle() -> str | None:
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "AWS_CA_BUNDLE"):
        path = os.environ.get(key)
        if path and os.path.exists(path):
            return path
    try:
        import certifi

        return certifi.where()
    except ImportError:  # pragma: no cover
        return None


class RangeClient:
    """Thread-safe HTTP range reader with retries and byte accounting.

    Uses urllib3 directly (not requests) so the body can be read straight into a
    caller-supplied buffer. Honours HTTPS_PROXY and the CA bundle env vars.
    """

    def __init__(
        self,
        pool_size: int = 8,
        connect_timeout: float = 30.0,
        read_timeout: float = 120.0,
        max_attempts: int = 7,
    ) -> None:
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        kwargs = dict(
            maxsize=pool_size,
            block=True,
            cert_reqs="CERT_REQUIRED",
            ca_certs=_ca_bundle(),
            timeout=urllib3.Timeout(connect=connect_timeout, read=read_timeout),
        )
        if proxy:
            self.http: urllib3.PoolManager = urllib3.ProxyManager(proxy, **kwargs)
        else:
            self.http = urllib3.PoolManager(**kwargs)
        self.max_attempts = max_attempts
        self._lock = threading.Lock()
        self.bytes_read = 0
        self.requests = 0
        self.retries = 0

    # -- low level ---------------------------------------------------------
    def _count(self, nbytes: int, retries: int = 0) -> None:
        with self._lock:
            self.bytes_read += nbytes
            self.requests += 1
            self.retries += retries

    def _sleep(self, attempt: int) -> None:
        time.sleep(min(30.0, 0.5 * (2**attempt)) * (0.5 + random.random()))

    def read_into(self, url: str, start: int, end: int, dest: memoryview) -> int:
        """GET bytes [start, end) into dest[:end-start]. Returns the byte count."""
        want = end - start
        if want <= 0:
            return 0
        last: Exception | None = None
        for attempt in range(self.max_attempts):
            resp = None
            try:
                resp = self.http.request(
                    "GET",
                    url,
                    headers={"Range": f"bytes={start}-{end - 1}"},
                    preload_content=False,
                    retries=False,
                )
                if resp.status != 206:
                    raise urllib3.exceptions.HTTPError(f"HTTP {resp.status} for range {start}-{end - 1}")
                got = 0
                while got < want:
                    chunk = resp.read(min(READ_CHUNK, want - got))
                    if not chunk:
                        break
                    dest[got : got + len(chunk)] = chunk
                    got += len(chunk)
                if got != want:
                    raise urllib3.exceptions.HTTPError(f"short read {got} of {want}")
                resp.release_conn()
                self._count(got, attempt)
                return got
            except (urllib3.exceptions.HTTPError, OSError) as exc:  # includes ssl and proxy errors
                last = exc
                if resp is not None:
                    try:
                        resp.close()
                        resp.release_conn()
                    except Exception:  # pragma: no cover
                        pass
                self._sleep(attempt)
        raise RuntimeError(f"range {start}-{end - 1} of {url} failed after {self.max_attempts} attempts: {last}")

    def get_bytes(self, url: str, start: int, end: int) -> bytes:
        buf = bytearray(end - start)
        self.read_into(url, start, end, memoryview(buf))
        return bytes(buf)

    def get_tail(self, url: str, nbytes: int) -> tuple[bytes, int]:
        """Last nbytes of the object (suffix range). Returns (data, total object size)."""
        last: Exception | None = None
        for attempt in range(self.max_attempts):
            resp = None
            try:
                resp = self.http.request(
                    "GET", url, headers={"Range": f"bytes=-{nbytes}"}, preload_content=True, retries=False
                )
                if resp.status != 206:
                    raise urllib3.exceptions.HTTPError(f"HTTP {resp.status} for suffix range")
                match = _CONTENT_RANGE.search(resp.headers.get("Content-Range", ""))
                if not match:
                    raise urllib3.exceptions.HTTPError("no Content-Range on suffix response")
                total = int(match.group(3))
                data = resp.data
                self._count(len(data), attempt)
                return data, total
            except (urllib3.exceptions.HTTPError, OSError) as exc:
                last = exc
                self._sleep(attempt)
        raise RuntimeError(f"tail of {url} failed after {self.max_attempts} attempts: {last}")

    def get_all(self, url: str) -> bytes:
        """Whole small object (manifest, deleted ids)."""
        last: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                resp = self.http.request("GET", url, retries=False)
                if resp.status != 200:
                    raise urllib3.exceptions.HTTPError(f"HTTP {resp.status}")
                self._count(len(resp.data), attempt)
                return resp.data
            except (urllib3.exceptions.HTTPError, OSError) as exc:
                last = exc
                self._sleep(attempt)
        raise RuntimeError(f"GET {url} failed: {last}")


def merge_ranges(spans: Iterable[tuple[int, int]], max_gap: int = MAX_GAP) -> list[tuple[int, int]]:
    """Sort half-open byte ranges and merge those separated by less than max_gap."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start - merged[-1][1] < max_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def column_chunk_span(col) -> tuple[int, int]:
    """Absolute [start, end) byte span of a column chunk (dictionary page included)."""
    start = col.data_page_offset
    dict_off = col.dictionary_page_offset
    if dict_off:
        start = min(start, dict_off)
    return start, start + col.total_compressed_size


class RemoteParquet:
    """One remote Parquet file, readable column-by-column and row-group-by-row-group."""

    def __init__(self, client: RangeClient, url: str, leaves: Sequence[str], optional: Sequence[str] = ()):
        self.client = client
        self.url = https_url(url)
        self.leaves = list(leaves)
        self.optional = list(optional)
        self.size = 0
        self.md: pq.FileMetaData | None = None
        self._mm: mmap.mmap | None = None
        self._pf: pq.ParquetFile | None = None
        self._reader = None
        self.cols: list[str] = []
        self._leaf_idx: dict[str, int] = {}

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> RemoteParquet:
        tail, self.size = self.client.get_tail(self.url, TAIL_GUESS)
        if tail[-4:] != b"PAR1":
            raise RuntimeError(f"not a Parquet file: {self.url}")
        footer_len = int.from_bytes(tail[-8:-4], "little")
        need = footer_len + 8
        if need > len(tail):
            tail, self.size = self.client.get_tail(self.url, need + 1024)
        self.md = pq.read_metadata(io.BytesIO(tail))
        self._mm = mmap.mmap(-1, self.size, flags=mmap.MAP_PRIVATE)
        self._mm[self.size - len(tail) : self.size] = tail
        if self.md.num_row_groups:
            rg0 = self.md.row_group(0)
            self._leaf_idx = {rg0.column(i).path_in_schema: i for i in range(rg0.num_columns)}
        missing = [c for c in self.leaves if c not in self._leaf_idx]
        if missing and self.md.num_row_groups:
            raise RuntimeError(f"required columns missing in {self.url}: {missing}")
        self.cols = list(self.leaves) + [c for c in self.optional if c in self._leaf_idx]
        buf = pa.py_buffer(self._mm)
        self._reader = pa.BufferReader(buf)
        self._pf = pq.ParquetFile(self._reader, metadata=self.md)
        return self

    def close(self) -> None:
        self._pf = None
        self._reader = None
        if self._mm is not None:
            try:
                self._mm.close()
            except BufferError:  # Arrow still holds a view; the OS reclaims it at process exit
                pass
            self._mm = None

    def __enter__(self) -> RemoteParquet:
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- planning ----------------------------------------------------------
    @property
    def num_row_groups(self) -> int:
        return self.md.num_row_groups if self.md else 0

    def rg_ranges(self, rg: int) -> list[tuple[int, int]]:
        r = self.md.row_group(rg)
        spans = [column_chunk_span(r.column(self._leaf_idx[c])) for c in self.cols]
        return merge_ranges(spans)

    # -- fetching ----------------------------------------------------------
    def submit_rg(self, pool: Executor, rg: int) -> list[Future]:
        """Start fetching all needed ranges of row group rg. Returns the futures."""
        futures = []
        for start, end in self.rg_ranges(rg):
            futures.append(pool.submit(self._fetch, start, end))
        return futures

    def _fetch(self, start: int, end: int) -> int:
        view = memoryview(self._mm)[start:end]
        try:
            return self.client.read_into(self.url, start, end, view)
        finally:
            view.release()

    # -- decoding ----------------------------------------------------------
    def read_rg(self, rg: int, columns: Sequence[str] | None = None) -> pa.Table:
        return self._pf.read_row_group(rg, columns=list(columns or self.cols), use_threads=False)

    def release_rg(self, rg: int) -> None:
        """Hand the pages of a finished row group back to the OS."""
        page = mmap.PAGESIZE
        for start, end in self.rg_ranges(rg):
            a = -(-start // page) * page
            b = (end // page) * page
            if b > a:
                try:
                    self._mm.madvise(mmap.MADV_DONTNEED, a, b - a)
                except (OSError, ValueError):  # pragma: no cover
                    return
