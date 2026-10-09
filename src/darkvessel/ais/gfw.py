"""Global Fishing Watch API v3 client: research build only (CC BY-NC 4.0, noncommercial).

What this module does
---------------------
* `GFWClient`: a small requests-based client for the GFW gateway (https://gateway.api.globalfishingwatch.org/v3/).
  Token from the GFW_API_TOKEN environment variable or the git-ignored .env at the repo root. Polite rate limiting
  (one request at a time, a minimum interval between requests, exponential backoff on 429 and 5xx), and a response
  cache under data/cache/gfw/ keyed by a hash of the request (method, path, query, body). The token is never part
  of the cache key or of any cached file, and Authorization headers are redacted from every saved error.
* `aoi_geojson`: the AOI polygon as GeoJSON, simplified until it has at most `max_vertices` vertices.
* Parsers that turn report, events and vessel responses into flat pandas frames.
* `cell_date_match`: cell-level comparison of two detection sets on the same dates (used for our radar against
  GFW's SAR detections; GFW's report endpoint gives cell counts, not individual positions).

Facts about the API used here, read on 2026-10-08 from the GFW documentation (URLs in SOURCES):
* 4Wings report: POST /v3/4wings/report with a custom GeoJSON polygon; query params spatial-resolution (HIGH =
  0.01 degree cells, LOW = 0.1 degree), temporal-resolution (HOURLY, DAILY, MONTHLY, YEARLY, ENTIRE), datasets[0],
  date-range "start,end", format JSON, filters[0], group-by (VESSEL_ID, FLAG, GEARTYPE, FLAGANDGEARTYPE, MMSI).
  Values are sums per cell. One report at a time per token (HTTP 429 whose body says "not currently enabled to
  perform more than one concurrent report"; the client waits and retries); reports over 100 s time out (524) and
  can be recovered with GET /v3/4wings/last-report for 30 minutes. date-range takes dates or ISO 8601 timestamps
  (the docs' own example: 2023-05-01T00:00:00.000Z,2023-06-01T00:00:00.000Z). Report rows carry entryTimestamp and
  exitTimestamp (ISO 8601), the identity of the vessel when the cell holds one vessel, and lat, lon = "center of the
  grid cell" (report response fields), so cells are centred on whole multiples of the cell size. The datasets endpoint
  lists the groupings each dataset accepts (reportGroupings): SAR presence vessel_id, flag, geartype,
  flagAndGearType, mmsi; AIS presence vessel_id, flag, mmsi (no geartype).
* Events: POST /v3/events with body {datasets, startDate, endDate, geometry}; paginated with limit, offset and
  nextOffset. Datasets: public-global-gaps-events, -encounters-events, -loitering-events, -port-visits-events.
* Vessels: GET /v3/vessels?datasets[0]=public-global-vessel-identity:latest&ids[0]=...
* Rate limits: 50,000 requests per day and 1,500,000 per month per user, with x-ratelimit-* response headers.
* Licence: CC BY-NC 4.0, noncommercial use only, attribution required (text in ATTRIBUTION_TEMPLATE).
* SAR detections are "industrial vessels"; the data caveats say "we miss most vessels under 15 m in length", and
  Paolo et al. 2024 (doi:10.1038/s41586-023-06825-8) report >70 % detection at 25 m and >90 % at 50 m, with the
  thresholds calibrated to 60 % detection of 15 to 20 m vessels.

Every output built from this module must carry RESEARCH_TAG, the licence URL, the attribution and DARK_CAVEAT.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from darkvessel.config import CACHE_DIR, DARK_CAVEAT, REPO_ROOT

BASE_URL = "https://gateway.api.globalfishingwatch.org/v3/"
GFW_CACHE = CACHE_DIR / "gfw"
TOKEN_ENV = "GFW_API_TOKEN"

LICENCE = "CC BY-NC 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by-nc/4.0/"
TERMS_URL = "https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits"
RESEARCH_TAG = "research build only, noncommercial (Global Fishing Watch data, CC BY-NC 4.0)"
# Attribution format A.3 of the GFW terms of use (TERMS_URL), filled per dataset.
ATTRIBUTION_TEMPLATE = ("Global Fishing Watch. {year}, updated daily. {dataset}, {date_range}. "
                        "Data set accessed {accessed} at https://globalfishingwatch.org/our-apis/ .")
GFW_CAVEAT = (
    "Global Fishing Watch detections and events are another model's output, not ground truth. GFW SAR detections "
    "cover industrial vessels and miss most vessels under 15 m (GFW data caveats; Paolo et al. 2024, "
    "doi:10.1038/s41586-023-06825-8). GFW's 'matched' flag is GFW's own AIS match, not ours. An AIS gap event is "
    "not proof of intent: GFW's own documentation calls the gaps dataset a prototype, and AIS can be off or "
    "unreceived for lawful and technical reasons. " + DARK_CAVEAT
)

SOURCES = {
    "docs_4wings": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/4wings",
    "docs_report": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/4wings/report",
    "docs_interaction": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/4wings/interaction",
    "docs_events": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/events",
    "docs_events_get_all": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/events/get-all-events",
    "docs_vessels": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/vessels",
    "docs_vessels_get": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/vessels/get-vessels",
    "docs_key_concepts": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/key-concepts",
    "docs_data_caveats": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/data-caveats",
    "docs_pagination": "https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/pagination",
    "docs_licence_rate_limits": TERMS_URL,
    "licence": LICENCE_URL,
    "paolo_2024": "https://doi.org/10.1038/s41586-023-06825-8",
}

DATASETS = {
    "sar": "public-global-sar-presence:latest",
    "presence": "public-global-presence:latest",
    "fishing_effort": "public-global-fishing-effort:latest",
    "gaps": "public-global-gaps-events:latest",
    "encounters": "public-global-encounters-events:latest",
    "loitering": "public-global-loitering-events:latest",
    "port_visits": "public-global-port-visits-events:latest",
    "vessels": "public-global-vessel-identity:latest",
}
RES_DEG = {"HIGH": 0.01, "LOW": 0.1}


class GFWError(RuntimeError):
    pass


def redact(text: str, token: str | None = None) -> str:
    """Remove bearer tokens and Authorization headers from a message before it is saved or shown."""
    text = re.sub(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)bearer\s+\S+", r"\1Bearer ***", text)
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}", "Bearer ***", text)
    if token:
        text = text.replace(token, "***")
    return text


def load_token(env_path: Path | None = None) -> str:
    """GFW_API_TOKEN from the environment, else from the git-ignored .env file (python-dotenv when available)."""
    tok = os.environ.get(TOKEN_ENV)
    if tok:
        return tok.strip()
    env_path = env_path or REPO_ROOT / ".env"
    if env_path.exists():
        try:
            from dotenv import dotenv_values
            tok = (dotenv_values(env_path) or {}).get(TOKEN_ENV)
        except ImportError:
            for line in env_path.read_text().splitlines():
                if line.strip().startswith(TOKEN_ENV + "="):
                    tok = line.split("=", 1)[1].strip().strip("'\"")
        if tok:
            return tok.strip()
    raise GFWError(f"{TOKEN_ENV} not set and not found in {env_path}")


def request_key(method: str, path: str, params: dict | None, body: dict | None) -> str:
    """Stable cache key for a request: sha1 of the canonical JSON of method, path, query and body. No token."""
    canon = json.dumps({"method": method.upper(), "path": path, "params": params or {}, "body": body}, sort_keys=True,
                       separators=(",", ":"), default=str)
    return hashlib.sha1(canon.encode()).hexdigest()


def is_concurrent_report_429(status: int, text: str) -> bool:
    """True for the 4Wings 'one concurrent report per token' refusal (HTTP 429 whose body names the running report).
    It is not a rate limit: the right response is to wait for the running report and send the request again."""
    return status == 429 and "concurrent report" in (text or "").lower()


def report_is_empty(resp: dict) -> bool:
    """True when a cached 4Wings report holds no cells (GFW had no data for that window when it was fetched)."""
    return not any((cells or []) for e in (resp or {}).get("entries", []) for cells in e.values())


@dataclass
class GFWClient:
    token: str | None = None
    cache_dir: Path = GFW_CACHE
    min_interval_s: float = 0.6
    max_retries: int = 6
    timeout_s: float = 170.0
    concurrent_wait_s: float = 20.0        # poll interval while another report of this token is running
    concurrent_max_wait_s: float = 1800.0  # give up waiting for the other report after this long
    offline: bool = False          # tests: never touch the network, only the cache
    session: requests.Session = field(default_factory=requests.Session, repr=False)
    sleep: Any = field(default=time.sleep, repr=False)   # injectable for offline tests of the retry logic
    _last_call: float = field(default=0.0, repr=False)
    last_headers: dict = field(default_factory=dict, repr=False)
    n_requests: int = field(default=0, repr=False)
    n_waits_concurrent: int = field(default=0, repr=False)

    def __post_init__(self):
        if self.token is None and not self.offline:
            self.token = load_token()
        self.cache_dir = Path(self.cache_dir)

    # -- transport -------------------------------------------------------------------------------------------
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def _cache_path(self, group: str, key: str) -> Path:
        return self.cache_dir / group / f"{key}.json"

    def _save(self, cpath: Path, method: str, path: str, params: dict | None, body: dict | None, data, status: int,
              recovered: str | None = None):
        cpath.parent.mkdir(parents=True, exist_ok=True)
        rec = {"request": {"method": method.upper(), "path": path, "params": params,
                           "body_sha1": request_key("", "", None, body) if body else None},
               "status": status, "headers": self.last_headers, "fetched_utc": pd.Timestamp.now("UTC").isoformat(),
               "licence": LICENCE, "licence_url": LICENCE_URL, "note": RESEARCH_TAG, "body": data}
        if recovered:
            rec["recovered"] = recovered
        cpath.write_text(json.dumps(rec, default=str))

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None,
                group: str = "misc", use_cache: bool = True, refresh_empty: bool = False) -> dict:
        """One API call with cache, rate limiting and retries. Returns the parsed JSON body.

        The cache file holds the request (without credentials), status, selected headers and the body.
        `refresh_empty` refetches a cached 4Wings report that holds no cells (GFW publishes SAR and AIS days late,
        so an empty answer for a recent day goes stale).
        Retries: HTTP 429 'one concurrent report' waits `concurrent_wait_s` between polls for up to
        `concurrent_max_wait_s` (not counted as attempts); other 429 and 5xx back off exponentially, honouring
        Retry-After; a 524 or a client-side timeout on a report recovers the result from GET /4wings/last-report.
        """
        key = request_key(method, path, params, body)
        cpath = self._cache_path(group, key)
        is_report = "4wings/report" in path
        if use_cache and cpath.exists():
            cached = json.loads(cpath.read_text())["body"]
            if not (refresh_empty and is_report and report_is_empty(cached)):
                return cached
        if self.offline:
            raise GFWError(f"offline client and no cached response for {method} {path}")
        wait, waited_concurrent, attempt = 2.0, 0.0, 0
        while attempt < self.max_retries:
            gap = self.min_interval_s - (time.monotonic() - self._last_call)
            if gap > 0:
                self.sleep(gap)
            try:
                r = self.session.request(method.upper(), BASE_URL + path.lstrip("/"), params=params, json=body,
                                         headers=self._headers(), timeout=self.timeout_s)
            except requests.RequestException as e:
                self._last_call = time.monotonic()
                self.n_requests += 1
                if is_report and isinstance(e, requests.Timeout):
                    # the server keeps computing the report after the client gives up: fetch it from last-report
                    data = self._recover_last_report(params)
                    if data is not None:
                        self._save(cpath, method, path, params, body, data, 200, recovered="last-report after client timeout")
                        return data
                attempt += 1
                if attempt >= self.max_retries:
                    raise GFWError(redact(f"{method} {path}: {e}", self.token)) from None
                self.sleep(wait)
                wait = min(wait * 2, 120)
                continue
            self._last_call = time.monotonic()
            self.n_requests += 1
            self.last_headers = {k.lower(): v for k, v in r.headers.items() if k.lower().startswith("x-")}
            if r.status_code in (200, 201):
                data = r.json()
                self._save(cpath, method, path, params, body, data, r.status_code)
                return data
            if is_concurrent_report_429(r.status_code, r.text):
                # another report of this token is still running (a parallel process, or our own request after a
                # client timeout): wait for it instead of failing, without spending retry attempts
                if waited_concurrent >= self.concurrent_max_wait_s:
                    raise GFWError(redact(f"{method} {path}: another report kept running for {waited_concurrent:.0f} s "
                                          f"(HTTP 429 'one concurrent report'); run reports strictly in sequence", self.token))
                self.n_waits_concurrent += 1
                self.sleep(self.concurrent_wait_s)
                waited_concurrent += self.concurrent_wait_s
                continue
            if r.status_code == 524 and is_report:
                data = self._recover_last_report(params)
                if data is not None:
                    self._save(cpath, method, path, params, body, data, 200, recovered="last-report after 524")
                    return data
            attempt += 1
            if r.status_code in (429, 500, 502, 503, 504, 524) and attempt < self.max_retries:
                retry_after = r.headers.get("Retry-After")
                self.sleep(float(retry_after) if retry_after and retry_after.isdigit() else wait)
                wait = min(wait * 2, 120)
                continue
            raise GFWError(redact(f"{method} {path} -> HTTP {r.status_code}: {r.text[:500]}", self.token))
        raise GFWError(f"{method} {path}: retries exhausted")

    def _recover_last_report(self, params: dict | None = None, polls: int = 40):
        """Poll GET /4wings/last-report until the report is done (docs: the last report is kept for 30 minutes).

        Returns the report body only when it is the report that `params` asked for: while it runs, the docs' running
        response carries the report's uri, whose datasets[0] and date-range must match ours; a finished body must hold
        our dataset (its entry keys are the resolved version of the requested id) and only dates inside our range.
        Otherwise None, and the caller sends the POST again. This keeps a stale report of another request out of the
        cache when the server never registered the timed-out one.
        """
        for _ in range(polls):
            self.sleep(self.concurrent_wait_s)
            try:
                r = self.session.get(BASE_URL + "4wings/last-report", headers=self._headers(), timeout=self.timeout_s)
            except requests.RequestException:
                continue
            self.n_requests += 1
            if r.status_code != 200:
                continue
            try:
                data = r.json()
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("status") in ("running", "pending", "in_progress"):
                if params and data.get("uri") and not uri_matches_params(str(data["uri"]), params):
                    return None
                continue
            if isinstance(data, dict) and "entries" in data:
                return data if params is None or report_matches_params(data, params) else None
            return None
        return None

    # -- endpoints -------------------------------------------------------------------------------------------
    def dataset(self, dataset_id: str) -> dict:
        return self.request("GET", f"datasets/{dataset_id}", group="datasets")

    def report(self, dataset: str, date_range: tuple[str, str], geojson: dict, spatial_resolution: str = "HIGH",
               temporal_resolution: str = "DAILY", filters: str | None = None, group_by: str | None = None,
               refresh_empty: bool = False) -> dict:
        """4Wings report over a custom polygon (sums per cell). `date_range` end is exclusive in the API's examples;
        both ends take a date (YYYY-MM-DD) or an ISO 8601 timestamp (2026-09-20T10:00:00.000Z), as in the docs."""
        params = report_params(dataset, date_range, spatial_resolution, temporal_resolution, filters, group_by)
        return self.request("POST", "4wings/report", params=params, body={"geojson": geojson}, group="report",
                            refresh_empty=refresh_empty)

    def events(self, dataset: str, start: str, end: str, geojson: dict, limit: int = 1000, max_pages: int | None = None,
               progress=None) -> list[dict]:
        """All events of one dataset in the polygon and window, following nextOffset. Each page is cached."""
        out, offset, page = [], 0, 0
        while True:
            body = {"datasets": [dataset], "startDate": start, "endDate": end, "geometry": geojson}
            data = self.request("POST", "events", params={"offset": offset, "limit": limit}, body=body, group="events")
            entries = data.get("entries", [])
            out.extend(entries)
            page += 1
            if progress:
                progress(dataset, len(out), data.get("total"))
            nxt = data.get("nextOffset")
            if not entries or nxt is None or nxt <= offset or (max_pages and page >= max_pages):
                break
            offset = nxt
        return out

    def vessels(self, ids: list[str], dataset: str = DATASETS["vessels"], batch: int = 20, progress=None,
                skip_missing: bool = False, use_index: bool = True) -> list[dict]:
        """Identity records for GFW vessel ids, in batches (ids[0], ids[1], ...). Each batch is cached.

        With `use_index`, ids already present in any cached batch are served from `vessel_cache_index` and only the
        rest are requested, in new batches; without it the batches are cut from the whole sorted set as before.
        `skip_missing` (offline use) drops batches that are not in the cache instead of raising. Ids the API does
        not know are absent from the result either way.
        """
        out, todo = [], [i for i in dict.fromkeys(ids) if i]
        if use_index:
            index = vessel_cache_index(self.cache_dir)
            seen = set()
            for i in todo:
                e = index.get(i)
                if e is not None and id(e) not in seen:
                    out.append(e), seen.add(id(e))
            todo = [i for i in todo if i not in index]
        batches = vessel_batches(todo, batch)
        for i, chunk in enumerate(batches):
            try:
                data = self.request("GET", "vessels", params=vessel_params(chunk, dataset), group="vessels")
            except GFWError:
                if skip_missing:
                    continue
                raise
            out.extend(data.get("entries", []))
            if progress:
                progress(min((i + 1) * batch, len(batches) * batch), len(batches) * batch)
        return out


def uri_matches_params(uri: str, params: dict) -> bool:
    """True when a last-report 'running' uri names the same datasets[0] and date-range as our request params."""
    from urllib.parse import parse_qs, unquote, urlsplit

    q = {k: v[0] for k, v in parse_qs(urlsplit(unquote(uri)).query).items()}
    for key in ("datasets[0]", "date-range"):
        if key in params and q.get(key) != str(params[key]):
            return False
    return True


def report_dates(resp: dict) -> list[str]:
    """ISO dates (YYYY-MM-DD) of every cell row of a 4Wings report body."""
    out = []
    for e in (resp or {}).get("entries", []):
        for cells in e.values():
            for c in cells or []:
                d = str(c.get("date") or "")[:10]
                if d:
                    out.append(d)
    return out


def report_matches_params(resp: dict, params: dict) -> bool:
    """True when a finished report body belongs to the request `params`: its entry keys resolve the requested
    dataset (public-global-sar-presence:latest -> public-global-sar-presence:v4.0) and every row date lies inside
    the requested date-range (end exclusive, as the API treats it)."""
    want = str(params.get("datasets[0]", "")).split(":")[0]
    keys = {k.split(":")[0] for e in (resp or {}).get("entries", []) for k in e}
    if want and keys and want not in keys:
        return False
    rng = str(params.get("date-range", ""))
    if "," in rng:
        a, b = (x.strip()[:10] for x in rng.split(",", 1))
        dates = report_dates(resp)
        if dates and (min(dates) < a or max(dates) > b):
            return False
    return True


def cache_fetch_dates(cache_dir: Path = GFW_CACHE, groups=("report", "events", "vessels", "datasets")) -> dict:
    """When each dataset was fetched: {dataset or path: {"first": date, "last": date, "n": files}} read from the
    head of every cached response (the request block and fetched_utc come before the body), without loading bodies.
    Keys: datasets[0] of a report, 'events' for the events endpoint, the dataset of a vessels query, the path of a
    datasets query. Used to stamp attributions with the real access dates instead of the day of an offline rebuild."""
    out: dict[str, dict] = {}
    for g in groups:
        for f in sorted(Path(cache_dir, g).glob("*.json")):
            with open(f, encoding="utf-8") as fh:
                head = fh.read(4000)
            fu = re.search(r'"fetched_utc":\s*"(\d{4}-\d{2}-\d{2})', head)
            if not fu:
                continue
            ds = re.search(r'"datasets\[0\]":\s*"([^"]+)"', head)
            path = re.search(r'"path":\s*"([^"]+)"', head)
            key = ds.group(1) if ds else ("events" if g == "events" else (path.group(1) if path else g))
            rec = out.setdefault(key, {"first": fu.group(1), "last": fu.group(1), "n": 0})
            rec["first"], rec["last"], rec["n"] = min(rec["first"], fu.group(1)), max(rec["last"], fu.group(1)), rec["n"] + 1
    return out


def accessed_text(dates: dict, *keys: str, default: str | None = None) -> str:
    """'YYYY-MM-DD' or 'YYYY-MM-DD to YYYY-MM-DD' over the datasets named by `keys` (prefix match on the dataset id),
    else `default` (today when None)."""
    firsts, lasts = [], []
    for k in keys:
        for name, rec in dates.items():
            if name == k or name.split(":")[0] == str(k).split(":")[0]:
                firsts.append(rec["first"]), lasts.append(rec["last"])
    if not firsts:
        return default or pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
    a, b = min(firsts), max(lasts)
    return a if a == b else f"{a} to {b}"


def vessel_cache_index(cache_dir: Path = GFW_CACHE) -> dict[str, dict]:
    """{vessel id: entry} over every cached /vessels batch, so an id already fetched in any earlier batch is served
    from the cache whatever batch it falls into now (batches are cut from the sorted id set, so a changed set would
    otherwise refetch everything). An entry is indexed under each of its selfReportedInfo ids."""
    out: dict[str, dict] = {}
    for f in sorted(Path(cache_dir, "vessels").glob("*.json")):
        try:
            body = json.loads(f.read_text()).get("body") or {}
        except (ValueError, OSError):
            continue
        for e in body.get("entries", []):
            for s in e.get("selfReportedInfo") or []:
                if s.get("id"):
                    out.setdefault(s["id"], e)
    return out


def vessel_batches(ids: list[str], batch: int) -> list[list[str]]:
    """Deterministic batches of unique, sorted vessel ids (so a rerun hits the same cache keys)."""
    ids = sorted(set(i for i in ids if i))
    return [ids[i:i + batch] for i in range(0, len(ids), batch)]


def vessel_params(chunk: list[str], dataset: str = DATASETS["vessels"]) -> dict:
    params = {"datasets[0]": dataset}
    params.update({f"ids[{k}]": v for k, v in enumerate(chunk)})
    return params


def report_params(dataset: str, date_range: tuple[str, str], spatial_resolution: str = "HIGH",
                  temporal_resolution: str = "DAILY", filters: str | None = None, group_by: str | None = None) -> dict:
    """Query parameters of a 4Wings report request (docs: SOURCES['docs_report'])."""
    if spatial_resolution not in RES_DEG:
        raise ValueError("spatial_resolution must be HIGH or LOW")
    if temporal_resolution not in ("HOURLY", "DAILY", "MONTHLY", "YEARLY", "ENTIRE"):
        raise ValueError("bad temporal_resolution")
    p = {"spatial-resolution": spatial_resolution, "temporal-resolution": temporal_resolution, "datasets[0]": dataset,
         "date-range": f"{date_range[0]},{date_range[1]}", "format": "JSON", "spatial-aggregation": "false"}
    if filters:
        p["filters[0]"] = filters
    if group_by:
        p["group-by"] = group_by.upper()
    return p


# -- AOI ---------------------------------------------------------------------------------------------------------
def aoi_geojson(geom, max_vertices: int = 1000, tolerances=(0.0, 0.005, 0.01, 0.02, 0.05, 0.1)) -> tuple[dict, float]:
    """GeoJSON mapping of `geom` (EPSG:4326), simplified with the smallest tolerance that keeps it under
    `max_vertices` vertices. Returns (geojson, tolerance_used)."""
    from shapely.geometry import mapping

    def n_vertices(g):
        if g.geom_type == "Polygon":
            return len(g.exterior.coords) + sum(len(r.coords) for r in g.interiors)
        if g.geom_type == "MultiPolygon":
            return sum(n_vertices(p) for p in g.geoms)
        return len(getattr(g, "coords", []))

    for tol in tolerances:
        s = geom if tol == 0 else geom.simplify(tol, preserve_topology=True)
        if n_vertices(s) <= max_vertices:
            return mapping(s), tol
    return mapping(s), tolerances[-1]


# -- parsers -----------------------------------------------------------------------------------------------------
def report_to_frame(resp: dict, resolution: str = "HIGH") -> pd.DataFrame:
    """Flatten a 4Wings report response into one row per (dataset version, cell, date, group)."""
    rows = []
    for entry in resp.get("entries", []):
        for dataset_version, cells in entry.items():
            for c in cells or []:
                r = {"dataset_version": dataset_version}
                r.update(c)
                rows.append(r)
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["dataset_version", "date", "lat", "lon"])
    for col in ("lat", "lon", "detections", "hours"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    # 4Wings returns cell coordinates as multiples of the cell size with float noise (109.52999877929688 for a
    # 0.01 degree cell). The docs call them cell centres, and the data agree: over 22,959 radar contacts paired
    # with a GFW SAR detection of the same scene (scripts/31_gfw_identity.py, 2026-10-09), the median offset of
    # contact minus value is +0.0005 degree in lon and lat with a median distance of 510 m, against -0.0036 degree
    # and 865 m when the value is read as the south-west corner. So the value is snapped to the grid and used as
    # the centre; the cell spans half a step on each side.
    step = RES_DEG[resolution]
    if "lat" in df and "lon" in df:
        df["lat"] = (np.round(df.lat / step) * step).round(4)
        df["lon"] = (np.round(df.lon / step) * step).round(4)
    df = df.replace({"": None})
    df = df.dropna(axis=1, how="all")
    return df


def _get(d: dict, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return default if d is None else d


def events_to_frame(entries: list[dict]) -> pd.DataFrame:
    """One row per event with the common fields plus the type-specific block flattened (prefix = type)."""
    rows = []
    for e in entries:
        v = e.get("vessel") or {}
        r = {"event_id": e.get("id"), "type": e.get("type"), "start": e.get("start"), "end": e.get("end"),
             "lat": _get(e, "position", "lat"), "lon": _get(e, "position", "lon"),
             "vessel_id": v.get("id"), "vessel_name": v.get("name"), "ssvid": v.get("ssvid"), "flag": v.get("flag"),
             "vessel_type": v.get("type"),
             "start_dist_shore_km": _get(e, "distances", "startDistanceFromShoreKm"),
             "end_dist_shore_km": _get(e, "distances", "endDistanceFromShoreKm"),
             "start_dist_port_km": _get(e, "distances", "startDistanceFromPortKm"),
             "eez": ",".join(map(str, _get(e, "regions", "eez", default=[]) or [])),
             "bbox_w": (e.get("boundingBox") or [None] * 4)[0], "bbox_s": (e.get("boundingBox") or [None] * 4)[1],
             "bbox_e": (e.get("boundingBox") or [None] * 4)[2], "bbox_n": (e.get("boundingBox") or [None] * 4)[3]}
        t = e.get("type")
        block = e.get(t) or {}
        if t == "gap":
            r.update({"gap_intentional_disabling": block.get("intentionalDisabling"),
                      "gap_duration_h": block.get("durationHours"), "gap_distance_km": block.get("distanceKm"),
                      "gap_implied_speed_kn": block.get("impliedSpeedKnots"),
                      "gap_positions_12h_before_sat": block.get("positions12HoursBeforeSat"),
                      "gap_positions_per_day_sat_reception": block.get("positionsPerDaySatReception"),
                      "off_lat": _get(block, "offPosition", "lat"), "off_lon": _get(block, "offPosition", "lon"),
                      "on_lat": _get(block, "onPosition", "lat"), "on_lon": _get(block, "onPosition", "lon")})
        elif t == "encounter":
            ov = block.get("vessel") or {}
            r.update({"encounter_type": block.get("type"), "encounter_vessel_id": ov.get("id"),
                      "encounter_vessel_name": ov.get("name"), "encounter_ssvid": ov.get("ssvid"),
                      "encounter_flag": ov.get("flag"), "encounter_vessel_type": ov.get("type"),
                      "encounter_median_distance_km": block.get("medianDistanceKilometers"),
                      "encounter_median_speed_kn": block.get("medianSpeedKnots")})
        elif t == "loitering":
            r.update({"loitering_total_time_h": block.get("totalTimeHours"),
                      "loitering_total_distance_km": block.get("totalDistanceKm"),
                      "loitering_avg_speed_kn": block.get("averageSpeedKnots"),
                      "loitering_avg_dist_shore_km": block.get("averageDistanceFromShoreKm")})
        elif t == "port_visit":
            r.update({"port_visit_id": block.get("visitId"), "port_visit_confidence": block.get("confidence"),
                      "port_visit_duration_h": block.get("durationHrs"),
                      "port_name": _get(block, "startAnchorage", "name"), "port_flag": _get(block, "startAnchorage", "flag"),
                      "port_id": _get(block, "startAnchorage", "id")})
        rows.append(r)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ("start", "end"):
        df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    df["duration_h"] = ((df.end - df.start).dt.total_seconds() / 3600).round(2)
    for c in [c for c in df.columns if c.startswith(("gap_", "encounter_median", "loitering_", "port_visit_duration",
                                                    "off_", "on_", "start_dist", "end_dist", "lat", "lon"))]:
        if c not in ("gap_intentional_disabling",):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def vessels_to_frame(entries: list[dict]) -> pd.DataFrame:
    """One row per GFW vessel id: AIS self-reported identity plus registry fields when present."""
    rows = []
    for e in entries:
        reg = e.get("registryInfo") or []
        combined = (e.get("combinedSourcesInfo") or [])
        selfs = e.get("selfReportedInfo") or []
        for s in selfs:
            vid = s.get("id")
            # registry records that name this vessel id; when the entry holds a single AIS identity, every registry
            # record of the entry belongs to it. Never borrow a record from another identity of the same entry.
            reg_match = [r for r in reg if r.get("vesselId") == vid] or (reg if len(selfs) == 1 else [])
            reg_match = sorted(reg_match, key=lambda r: (not r.get("latestVesselInfo"), str(r.get("transmissionDateTo") or "")),
                               reverse=False)
            rg = reg_match[0] if reg_match else {}

            def first(key):
                for r in reg_match:
                    if r.get(key) not in (None, "", []):
                        return r.get(key)
                return None

            cmb = [c for c in combined if c.get("vesselId") == vid]
            gear = ((cmb[0].get("geartypes") or [{}])[-1].get("name") if cmb else None)
            stype = ((cmb[0].get("shiptypes") or [{}])[-1].get("name") if cmb else None)
            reg_gear = rg.get("geartypes") or rg.get("geartype")
            reg_gear = ",".join(reg_gear) if isinstance(reg_gear, list) else reg_gear
            rows.append({"vessel_id": vid, "ssvid": s.get("ssvid"), "shipname": s.get("shipname"), "flag": s.get("flag"),
                         "callsign": s.get("callsign") or first("callsign"), "imo": s.get("imo") or first("imo"),
                         "geartype": gear or reg_gear, "shiptype": stype or rg.get("shiptype") or s.get("shiptype"),
                         "length_m": first("lengthM"), "tonnage_gt": first("tonnageGt"),
                         "registry_sources": ",".join(sorted({x for r in reg_match for x in (r.get("sourceCode") or [])})) or None,
                         "registry_records": len(reg_match),
                         "ais_messages": s.get("messagesCounter"), "ais_positions": s.get("positionsCounter"),
                         "transmission_from": s.get("transmissionDateFrom"), "transmission_to": s.get("transmissionDateTo"),
                         "dataset_version": e.get("dataset")})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["length_m"] = pd.to_numeric(df.get("length_m"), errors="coerce")
        df = df.drop_duplicates("vessel_id")
    return df


# -- comparison ---------------------------------------------------------------------------------------------------
def cell_key(lon, lat, res: float):
    """Integer (col, row) of the cell of size `res` degrees that holds each point, on GFW's grid: cell k is centred
    on k * res and spans [(k - 0.5) res, (k + 0.5) res). 4Wings reports give the cell centre (docs: 'Latitude of the
    center of the grid cell'), so a GFW value lands on its own cell exactly and a point lands in the GFW cell that
    holds it. A floor on a corner-anchored grid would put every GFW cell half a cell off."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    return np.floor(lon / res + 0.5).astype(np.int64), np.floor(lat / res + 0.5).astype(np.int64)


def cell_centre(cx, cy, res: float):
    """Centre (lon, lat) of the cells of `cell_key`."""
    return np.round(np.asarray(cx) * res, 6), np.round(np.asarray(cy) * res, 6)


def cell_date_match(ours: pd.DataFrame, gfw: pd.DataFrame, res: float, by_hour: bool = False) -> pd.DataFrame:
    """Join two detection sets on (date[, hour], cell) and count both sides per cell.

    ours: columns lon, lat, date (YYYY-MM-DD) [, hour]; one row per detection.
    gfw : columns lon, lat, date [, hour], detections (count), matched (bool or NaN); one row per GFW cell (centres).
    Cells are GFW's (`cell_key`): at GFW's own resolution each GFW row is one cell; at a coarser `res` the GFW cell
    centres are binned. Returns one row per joined cell with n_ours, n_gfw, n_gfw_matched, n_gfw_unmatched and the
    pairing estimate n_pair = min(n_ours, n_gfw). Cells with only one side present keep zeros on the other side.
    """
    keys = ["date", "hour"] if by_hour else ["date"]
    o = ours.copy()
    o["cx"], o["cy"] = cell_key(o.lon, o.lat, res)
    og = o.groupby(keys + ["cx", "cy"]).size().rename("n_ours").reset_index()
    g = gfw.copy()
    g["cx"], g["cy"] = cell_key(g.lon, g.lat, res)
    g["detections"] = pd.to_numeric(g.detections, errors="coerce").fillna(0)
    m = g.get("matched")
    g["n_m"] = np.where(m.astype(str).str.lower().isin(["true", "1"]), g.detections, 0) if m is not None else 0
    g["n_u"] = np.where(m.astype(str).str.lower().isin(["false", "0"]), g.detections, 0) if m is not None else 0
    gg = g.groupby(keys + ["cx", "cy"]).agg(n_gfw=("detections", "sum"), n_gfw_matched=("n_m", "sum"),
                                            n_gfw_unmatched=("n_u", "sum")).reset_index()
    j = og.merge(gg, on=keys + ["cx", "cy"], how="outer").fillna({"n_ours": 0, "n_gfw": 0, "n_gfw_matched": 0, "n_gfw_unmatched": 0})
    for c in ("n_ours", "n_gfw", "n_gfw_matched", "n_gfw_unmatched"):
        j[c] = j[c].astype(int)
    j["n_pair"] = np.minimum(j.n_ours, j.n_gfw)
    j["lon"], j["lat"] = cell_centre(j.cx.values, j.cy.values, res)
    j["res_deg"] = res
    return j


def match_summary(joined: pd.DataFrame) -> dict:
    """Shares from a cell_date_match frame: of our detections, the share in a cell where GFW also detected
    something (and the paired-count version); of GFW detections, the share in a cell where we detected something."""
    both = (joined.n_ours > 0) & (joined.n_gfw > 0)
    n_ours, n_gfw = int(joined.n_ours.sum()), int(joined.n_gfw.sum())
    out = {"n_ours": n_ours, "n_gfw": n_gfw, "cells_ours": int((joined.n_ours > 0).sum()), "cells_gfw": int((joined.n_gfw > 0).sum()),
           "cells_both": int(both.sum()), "n_pair": int(joined.n_pair.sum()),
           "ours_in_cells_with_gfw": round(float(joined.loc[both, "n_ours"].sum() / n_ours), 4) if n_ours else None,
           "ours_paired_share": round(float(joined.n_pair.sum() / n_ours), 4) if n_ours else None,
           "gfw_in_cells_with_ours": round(float(joined.loc[both, "n_gfw"].sum() / n_gfw), 4) if n_gfw else None,
           "gfw_paired_share": round(float(joined.n_pair.sum() / n_gfw), 4) if n_gfw else None}
    g_both = joined.loc[both]
    tot = int(g_both.n_gfw_matched.sum() + g_both.n_gfw_unmatched.sum())
    out["gfw_matched_share_in_shared_cells"] = round(float(g_both.n_gfw_matched.sum() / tot), 4) if tot else None
    tot_all = int(joined.n_gfw_matched.sum() + joined.n_gfw_unmatched.sum())
    out["gfw_matched_share_all"] = round(float(joined.n_gfw_matched.sum() / tot_all), 4) if tot_all else None
    return out


def write_parquet_parts(df: pd.DataFrame, path, tags: dict, max_mb: float = 18.0, compression: str = "zstd") -> list:
    """Write `df` as parquet with `tags` in the file metadata (licence, attribution, caveat), zstd-compressed, split
    into <stem>_partKofN.parquet files when one file would exceed `max_mb` (committed files stay under 20 MB).
    Any earlier single file or parts at the same stem are removed. Returns the paths written."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for old in [path] + sorted(path.parent.glob(f"{path.stem}_part*of*.parquet")):
        if old.exists():
            old.unlink()
    meta = {str(k): str(v) for k, v in tags.items()}

    def write(frame: pd.DataFrame, p: Path):
        table = pa.Table.from_pandas(frame, preserve_index=False)
        table = table.replace_schema_metadata({**(table.schema.metadata or {}), **{k.encode(): v.encode() for k, v in meta.items()}})
        pq.write_table(table, p, compression=compression)

    write(df, path)
    size_mb = path.stat().st_size / 1e6
    if size_mb <= max_mb or len(df) < 2:
        return [path]
    n = int(np.ceil(size_mb / max_mb))
    path.unlink()
    out = []
    for k, chunk in enumerate(np.array_split(np.arange(len(df)), n), 1):
        p = path.with_name(f"{path.stem}_part{k}of{n}.parquet")
        write(df.iloc[chunk], p)
        out.append(p)
    return out


def attribution(dataset_version: str, date_range: tuple[str, str], accessed: str, year: int = 2026) -> str:
    return ATTRIBUTION_TEMPLATE.format(year=year, dataset=dataset_version, date_range=f"{date_range[0]} to {date_range[1]}",
                                       accessed=accessed)


def research_tags(dataset_version: str, date_range: tuple[str, str], accessed: str, **extra: Any) -> dict:
    """Tag dictionary for every GFW-derived file (COG tags, GeoPackage 'about', JSON)."""
    t = {"use": RESEARCH_TAG, "licence": LICENCE, "licence_url": LICENCE_URL, "terms_url": TERMS_URL,
         "attribution": attribution(dataset_version, date_range, accessed), "dataset": dataset_version,
         "caveat": GFW_CAVEAT}
    t.update(extra)
    return t
