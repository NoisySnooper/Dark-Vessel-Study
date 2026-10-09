"""Global Fishing Watch client: request building, response parsing (fixtures, no token, no network) and matching."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Polygon

from darkvessel.ais import gfw as G

FIX = Path(__file__).parent / "fixtures"


def test_request_key_is_stable_and_has_no_token():
    k1 = G.request_key("POST", "4wings/report", {"b": 1, "a": "x"}, {"geojson": {"type": "Point"}})
    k2 = G.request_key("post", "4wings/report", {"a": "x", "b": 1}, {"geojson": {"type": "Point"}})
    assert k1 == k2 and len(k1) == 40
    assert k1 != G.request_key("POST", "4wings/report", {"a": "y", "b": 1}, {"geojson": {"type": "Point"}})


def test_redact_removes_bearer_tokens():
    msg = "GET x -> 401: headers {'Authorization': 'Bearer abcdefghijklmnopqrstuvwxyz0123456789'} token=abcdefghijklmnopqrstuvwxyz0123456789"
    out = G.redact(msg, "abcdefghijklmnopqrstuvwxyz0123456789")
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in out and "Bearer ***" in out


def test_report_params_match_the_documented_query():
    p = G.report_params("public-global-sar-presence:latest", ("2026-09-20", "2026-09-21"), "HIGH", "HOURLY",
                        filters="matched='false'", group_by="vessel_id")
    assert p["spatial-resolution"] == "HIGH" and p["temporal-resolution"] == "HOURLY"
    assert p["datasets[0]"] == "public-global-sar-presence:latest"
    assert p["date-range"] == "2026-09-20,2026-09-21" and p["filters[0]"] == "matched='false'"
    assert p["group-by"] == "VESSEL_ID" and p["format"] == "JSON"
    with pytest.raises(ValueError):
        G.report_params("x", ("a", "b"), "MEDIUM")


def test_aoi_geojson_simplifies_under_the_vertex_limit():
    t = np.linspace(0, 2 * np.pi, 3000, endpoint=False)
    ring = Polygon(zip(105 + 3 * np.cos(t), 10 + 3 * np.sin(t)))
    gj, tol = G.aoi_geojson(ring, max_vertices=500)
    assert gj["type"] == "Polygon" and len(gj["coordinates"][0]) <= 500 and tol > 0
    gj0, tol0 = G.aoi_geojson(ring, max_vertices=5000)
    assert tol0 == 0 and len(gj0["coordinates"][0]) == 3001


def test_report_to_frame_flattens_and_snaps_cells():
    resp = json.loads((FIX / "gfw_report_sar.json").read_text())
    f = G.report_to_frame(resp, "HIGH")
    assert len(f) == 3 and f.dataset_version.unique().tolist() == ["public-global-sar-presence:v4.0"]
    assert f.detections.sum() == 4
    # float noise in the API's cell centres is snapped to the 0.01 degree grid (the value is the centre)
    assert f.lon.tolist() == [109.53, 109.33, 104.12] and f.lat.tolist() == [21.31, 20.22, 7.05]
    assert "shipName" not in f or f.shipName.isna().all()  # empty strings become missing and all-empty columns drop


def test_events_to_frame_flattens_each_event_type():
    resp = json.loads((FIX / "gfw_events.json").read_text())
    f = G.events_to_frame(resp["entries"])
    assert f.type.tolist() == ["gap", "encounter", "port_visit"]
    gap = f.iloc[0]
    assert gap.vessel_id == "v-gap-0001" and gap.gap_intentional_disabling is True or gap.gap_intentional_disabling == True  # noqa: E712
    assert gap.off_lon == 113.0 and gap.on_lat == 13.0 and abs(gap.duration_h - 49.72) < 0.01
    enc = f.iloc[1]
    assert enc.encounter_vessel_id == "v-enc-b" and enc.encounter_type == "fishing-fishing"
    pv = f.iloc[2]
    assert pv.port_name == "TESTPORT" and pv.port_visit_confidence == "4"
    assert str(f.start.dtype).startswith("datetime64") and f.start.dt.tz is not None


def test_vessels_to_frame_merges_self_reported_and_registry():
    resp = json.loads((FIX / "gfw_vessels.json").read_text())
    f = G.vessels_to_frame(resp["entries"])
    assert len(f) == 1
    r = f.iloc[0]
    assert r.vessel_id == "v-pv-1" and r.ssvid == "000000004" and r.flag == "ZZZ"
    assert r.length_m == 42.5 and r.imo == "0000000" and r.shiptype == "PASSENGER"
    assert r.registry_sources == "TEST-REGISTRY"


def test_offline_client_reads_cache_and_never_calls_the_network(tmp_path):
    c = G.GFWClient(token="unused", cache_dir=tmp_path, offline=True)
    params = G.report_params("public-global-sar-presence:latest", ("2026-09-20", "2026-09-21"), "HIGH", "HOURLY", "matched='false'")
    body = {"geojson": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}
    with pytest.raises(G.GFWError):
        c.request("POST", "4wings/report", params=params, body=body, group="report")
    key = G.request_key("POST", "4wings/report", params, body)
    p = tmp_path / "report" / f"{key}.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"status": 200, "body": json.loads((FIX / "gfw_report_sar.json").read_text())}))
    resp = c.report("public-global-sar-presence:latest", ("2026-09-20", "2026-09-21"), body["geojson"], "HIGH", "HOURLY", filters="matched='false'")
    assert G.report_to_frame(resp).detections.sum() == 4
    assert "unused" not in p.read_text()


def test_cell_key_is_gfw_grid_centre_anchored():
    # GFW gives cell centres on whole multiples of the cell size; cell k spans [(k - 0.5) res, (k + 0.5) res)
    cx, cy = G.cell_key([109.53, 109.5251, 109.5349, 109.5351], [21.31, 21.3051, 21.3149, 21.2949], 0.01)
    assert cx.tolist() == [10953, 10953, 10953, 10954] and cy.tolist() == [2131, 2131, 2131, 2129]
    lon, lat = G.cell_centre(cx, cy, 0.01)
    assert lon.tolist() == [109.53, 109.53, 109.53, 109.54] and lat.tolist() == [21.31, 21.31, 21.31, 21.29]
    # LOW cells: 108.8 holds 108.76 and 108.84, not 108.86
    assert G.cell_key([108.8, 108.76, 108.84, 108.86], [11.2] * 4, 0.1)[0].tolist() == [1088, 1088, 1088, 1089]


def test_cell_date_match_counts_both_sides():
    # ours: two contacts in the GFW cell centred on 109.53, 21.31 (one of them west of 109.53, where a floor would lose it),
    # one in the cell centred on 109.33, 20.22, one far away
    ours = pd.DataFrame({"lon": [109.5261, 109.532, 109.3339, 104.9], "lat": [21.3061, 21.312, 20.2211, 7.9],
                         "date": ["2026-09-20"] * 4, "hour": [10, 10, 10, 10]})
    gfw = pd.DataFrame({"lon": [109.53, 109.33, 110.0], "lat": [21.31, 20.22, 20.0], "date": ["2026-09-20"] * 3,
                        "hour": [10, 10, 22], "detections": [1, 2, 1], "matched": [True, False, False]})
    j = G.cell_date_match(ours, gfw, 0.01, by_hour=True)
    assert sorted(j.loc[j.n_gfw > 0, "lon"].tolist()) == [109.33, 109.53, 110.0]      # joined cells are GFW's own cells
    both = j[(j.n_ours > 0) & (j.n_gfw > 0)]
    assert len(both) == 2 and both.n_pair.sum() == 2        # cell A: 2 ours vs 1 gfw -> 1 pair; cell B: 1 vs 2 -> 1 pair
    s = G.match_summary(j)
    assert s["n_ours"] == 4 and s["n_gfw"] == 4 and s["cells_both"] == 2
    assert s["ours_in_cells_with_gfw"] == 0.75 and s["gfw_in_cells_with_ours"] == 0.75
    assert s["ours_paired_share"] == 0.5 and s["gfw_matched_share_in_shared_cells"] == round(1 / 3, 4)
    # coarser cells by date only: everything near 109.5/21.3 and 109.3/20.2 lands in two 0.25 cells
    j25 = G.cell_date_match(ours, gfw, 0.25)
    assert j25.res_deg.iloc[0] == 0.25 and j25.n_pair.sum() == 2


def test_research_tags_carry_licence_attribution_and_caveat():
    t = G.research_tags("public-global-sar-presence:v4.0", ("2026-09-01", "2026-10-03"), "2026-10-08")
    assert t["licence"] == "CC BY-NC 4.0" and t["licence_url"].startswith("https://creativecommons.org/licenses/by-nc/4.0")
    assert "Global Fishing Watch. 2026, updated daily. public-global-sar-presence:v4.0, 2026-09-01 to 2026-10-03" in t["attribution"]
    assert "does not mean illegal" in t["caveat"] and "not proof of intent" in t["caveat"]
    assert t["use"].startswith("research build only")


# -- retry logic (fake session, no network, no sleeping) -------------------------------------------------------
class _Resp:
    def __init__(self, status, body=None, text=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = text if text is not None else json.dumps(body or {})

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Session:
    """Replays a scripted list of responses (or exceptions) for session.request; session.get serves last-report."""

    def __init__(self, script, last_report=None):
        self.script, self.calls, self.last_report, self.gets = list(script), [], last_report, 0

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("params")))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kw):
        self.gets += 1
        return self.last_report


CONCURRENT = {"statusCode": 429, "error": "Too Many Requests",
              "messages": [{"title": "Too Many Requests", "detail": "Your application token is not currently enabled to perform more "
                            "than one concurrent report."}]}
REPORT = {"entries": [{"public-global-sar-presence:v4.0": [{"lat": 21.31, "lon": 109.53, "date": "2026-09-20 10:00", "detections": 1}]}]}


def _client(tmp_path, session):
    sleeps = []
    c = G.GFWClient(token="unused-token", cache_dir=tmp_path, session=session, sleep=sleeps.append, min_interval_s=0)
    return c, sleeps


def test_429_one_concurrent_report_waits_and_retries_without_spending_attempts(tmp_path):
    s = _Session([_Resp(429, CONCURRENT), _Resp(429, CONCURRENT), _Resp(200, REPORT)])
    c, sleeps = _client(tmp_path, s)
    c.concurrent_wait_s = 7
    out = c.report("public-global-sar-presence:latest", ("2026-09-20", "2026-09-21"), {"type": "Point", "coordinates": [0, 0]}, "HIGH", "HOURLY")
    assert out == REPORT and len(s.calls) == 3 and c.n_waits_concurrent == 2
    assert sleeps.count(7) == 2
    assert G.is_concurrent_report_429(429, json.dumps(CONCURRENT)) and not G.is_concurrent_report_429(429, "rate limit")
    # the response was cached under the request key and never holds the token
    files = list((tmp_path / "report").glob("*.json"))
    assert len(files) == 1 and "unused-token" not in files[0].read_text()


def test_429_concurrent_gives_up_after_the_maximum_wait(tmp_path):
    s = _Session([_Resp(429, CONCURRENT)] * 10)
    c, sleeps = _client(tmp_path, s)
    c.concurrent_wait_s, c.concurrent_max_wait_s = 10, 25
    with pytest.raises(G.GFWError, match="one concurrent report"):
        c.request("POST", "4wings/report", params={"a": 1}, body={"geojson": {}}, group="report")
    assert len(s.calls) == 4 and sleeps == [10, 10, 10]       # waited 30 s >= 25 s, then stopped


def test_plain_429_honours_retry_after_and_5xx_backs_off(tmp_path):
    s = _Session([_Resp(429, {"error": "rate limit"}, headers={"Retry-After": "3"}), _Resp(503, {"x": 1}), _Resp(200, {"entries": []})])
    c, sleeps = _client(tmp_path, s)
    assert c.request("GET", "vessels", params={"ids[0]": "v"}, group="vessels") == {"entries": []}
    assert sleeps == [3.0, 4.0] and len(s.calls) == 3      # Retry-After honoured, then the doubled backoff


def test_retries_exhaust_into_a_redacted_error(tmp_path):
    s = _Session([_Resp(500, text="boom Bearer unused-token")] * 6)
    c, _ = _client(tmp_path, s)
    c.max_retries = 3
    with pytest.raises(G.GFWError) as e:
        c.request("GET", "datasets/x", group="datasets")
    assert "unused-token" not in str(e.value) and len(s.calls) == 3


def test_client_timeout_on_a_report_recovers_from_last_report(tmp_path):
    import requests

    s = _Session([requests.Timeout("read timed out")], last_report=_Resp(200, REPORT))
    c, sleeps = _client(tmp_path, s)
    out = c.request("POST", "4wings/report", params={"a": 1}, body={"geojson": {}}, group="report")
    assert out == REPORT and s.gets == 1 and len(s.calls) == 1
    rec = json.loads(next((tmp_path / "report").glob("*.json")).read_text())
    assert rec["recovered"].startswith("last-report")


def test_524_recovers_from_last_report_and_refresh_empty_refetches(tmp_path):
    s = _Session([_Resp(524, text="gateway timeout")], last_report=_Resp(200, REPORT))
    c, _ = _client(tmp_path, s)
    params = G.report_params("public-global-sar-presence:latest", ("2026-09-20", "2026-09-21"), "HIGH", "HOURLY")   # REPORT's date
    assert c.request("POST", "4wings/report", params=params, body={"geojson": {}}, group="report") == REPORT
    # an empty cached report is reused by default and refetched with refresh_empty
    empty = {"entries": [{"public-global-sar-presence:v4.0": []}]}
    key = G.request_key("POST", "4wings/report", params, {"geojson": {}})
    (tmp_path / "report" / f"{key}.json").write_text(json.dumps({"status": 200, "body": empty}))
    assert G.report_is_empty(empty) and not G.report_is_empty(REPORT)
    assert c.request("POST", "4wings/report", params=params, body={"geojson": {}}, group="report") == empty
    s.script = [_Resp(200, REPORT)]
    assert c.request("POST", "4wings/report", params=params, body={"geojson": {}}, group="report", refresh_empty=True) == REPORT


def test_last_report_recovery_accepts_only_the_requested_report(tmp_path):
    import requests

    params = G.report_params("public-global-sar-presence:latest", ("2026-09-20", "2026-09-21"), "HIGH", "HOURLY")
    other = {"entries": [{"public-global-sar-presence:v4.0": [{"lat": 21.31, "lon": 109.53, "date": "2026-09-25 10:00", "detections": 1}]}]}
    assert G.report_matches_params(REPORT, params) and not G.report_matches_params(other, params)
    assert not G.report_matches_params({"entries": [{"public-global-presence:v4.0": []}]}, params)
    uri = "/v3/4wings/report?format=JSON&datasets%5B0%5D=public-global-sar-presence%3Alatest&date-range=2026-09-20%2C2026-09-21&spatial-resolution=HIGH"
    assert G.uri_matches_params(uri, params) and not G.uri_matches_params(uri.replace("09-20", "09-22"), params)
    # a stale last-report body of another date range is refused: the POST is sent again and nothing stale is cached
    s = _Session([requests.Timeout("read timed out"), _Resp(200, REPORT)], last_report=_Resp(200, other))
    c, _ = _client(tmp_path, s)
    assert c.request("POST", "4wings/report", params=params, body={"geojson": {}}, group="report") == REPORT
    assert len(s.calls) == 2 and s.gets == 1
    rec = json.loads(next((tmp_path / "report").glob("*.json")).read_text())
    assert "recovered" not in rec and rec["body"] == REPORT
    # a running response whose uri names another request also ends the recovery
    s2 = _Session([requests.Timeout("read timed out"), _Resp(200, REPORT)],
                  last_report=_Resp(200, {"uri": uri.replace("09-20", "09-22"), "status": "running"}))
    c2, _ = _client(tmp_path / "b", s2)
    assert c2.request("POST", "4wings/report", params=params, body={"geojson": {}}, group="report") == REPORT and s2.gets == 1


def test_cache_fetch_dates_and_vessel_index(tmp_path):
    c = G.GFWClient(token="unused-token", cache_dir=tmp_path, session=_Session([]), sleep=lambda s: None, min_interval_s=0)
    params = G.report_params("public-global-sar-presence:latest", ("2026-09-20", "2026-09-21"), "HIGH", "HOURLY")
    c._save(c._cache_path("report", "k1"), "POST", "4wings/report", params, {"geojson": {}}, REPORT, 200)
    rec = json.loads(c._cache_path("report", "k1").read_text())
    rec["fetched_utc"] = "2026-10-01T12:00:00+00:00"
    c._cache_path("report", "k1").write_text(json.dumps(rec))
    c._save(c._cache_path("report", "k2"), "POST", "4wings/report", params, {"geojson": {}}, REPORT, 200)
    c._save(c._cache_path("events", "e1"), "POST", "events", {"offset": 0}, {"datasets": ["x"]}, {"entries": []}, 200)
    d = G.cache_fetch_dates(tmp_path)
    today = pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
    assert d["public-global-sar-presence:latest"] == {"first": "2026-10-01", "last": today, "n": 2} and d["events"]["n"] == 1
    assert G.accessed_text(d, "public-global-sar-presence:v4.0") == f"2026-10-01 to {today}"     # prefix match on the dataset id
    assert G.accessed_text(d, "nothing", default="2026-10-08") == "2026-10-08"
    # vessel index: an id cached in any batch is served without a request; only unknown ids are fetched
    entry = {"dataset": "public-global-vessel-identity:v4.0", "registryInfo": [], "combinedSourcesInfo": [],
             "selfReportedInfo": [{"id": "v-a", "ssvid": "100000001", "shipname": "A", "flag": "ZZZ"}]}
    c._save(c._cache_path("vessels", "b1"), "GET", "vessels", G.vessel_params(["v-a", "v-zzz"]), None, {"entries": [entry]}, 200)
    assert set(G.vessel_cache_index(tmp_path)) == {"v-a"}
    off = G.GFWClient(token="unused", cache_dir=tmp_path, offline=True)
    assert [e["selfReportedInfo"][0]["id"] for e in off.vessels(["v-a", "v-b"], batch=100, skip_missing=True)] == ["v-a"]
    with pytest.raises(G.GFWError):
        off.vessels(["v-a", "v-b"], batch=100)                 # v-b is in no cached batch
    assert off.vessels(["v-a"], batch=100, use_index=False, skip_missing=True) == []   # the batch key of {v-a} alone was never fetched


def test_write_parquet_parts_carries_tags_and_splits(tmp_path):
    import pyarrow.parquet as pq

    df = pd.DataFrame({"a": np.arange(5000), "b": np.random.default_rng(0).random(5000)})
    tags = G.research_tags("public-global-sar-presence:v4.0", ("2026-09-01", "2026-10-05"), "2026-10-08", table="t")
    paths = G.write_parquet_parts(df, tmp_path / "x.parquet", tags)
    assert paths == [tmp_path / "x.parquet"]
    meta = pq.read_schema(paths[0]).metadata
    assert meta[b"licence"] == b"CC BY-NC 4.0" and b"Global Fishing Watch. 2026" in meta[b"attribution"] and b"not proof of intent" in meta[b"caveat"]
    parts = G.write_parquet_parts(df, tmp_path / "x.parquet", tags, max_mb=0.02)
    assert len(parts) > 1 and not (tmp_path / "x.parquet").exists() and sum(len(pd.read_parquet(p)) for p in parts) == 5000
    assert all(pq.read_schema(p).metadata[b"use"].startswith(b"research build only") for p in parts)


def test_vessels_to_frame_never_borrows_another_identitys_registry_record():
    entry = {"registryInfoTotalRecords": 1, "dataset": "public-global-vessel-identity:v4.0",
             "registryInfo": [{"vesselId": "v-a", "sourceCode": ["IMO"], "imo": "1111111", "lengthM": 80.0, "latestVesselInfo": True}],
             "combinedSourcesInfo": [],
             "selfReportedInfo": [{"id": "v-a", "ssvid": "100000001", "shipname": "A", "flag": "ZZZ"},
                                  {"id": "v-b", "ssvid": "100000002", "shipname": "B", "flag": "ZZZ"}]}
    f = G.vessels_to_frame([entry]).set_index("vessel_id")
    assert f.loc["v-a", "length_m"] == 80.0 and f.loc["v-a", "imo"] == "1111111"
    assert np.isnan(f.loc["v-b", "length_m"]) and pd.isna(f.loc["v-b", "imo"]) and f.loc["v-b", "registry_records"] == 0
    # a single identity takes the entry's registry records even without a vesselId on them
    single = {"registryInfo": [{"sourceCode": ["X"], "lengthM": 12.5}], "combinedSourcesInfo": [],
              "selfReportedInfo": [{"id": "v-c", "ssvid": "3"}], "dataset": "d"}
    assert G.vessels_to_frame([single]).length_m.iloc[0] == 12.5
