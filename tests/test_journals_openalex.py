"""OpenAlex snapshot extraction on tiny in-memory Parquet parts (offline)."""

import io
import json
from datetime import datetime, timezone

import pytest

from darkvessel.journals import openalex
from darkvessel.journals.fetch import FetchError

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

A_PRINT, A_ONLINE = "1111-1119", "2222-2227"


def part(rows):
    """One Parquet part with the columns and nested types of the real OpenAlex sources snapshot."""
    schema = pa.schema(
        [
            ("id", pa.string()),
            ("issn_l", pa.string()),
            ("issn", pa.list_(pa.string())),
            ("display_name", pa.string()),
            ("host_organization_name", pa.string()),
            ("type", pa.string()),
            ("works_count", pa.int32()),
            ("cited_by_count", pa.int32()),
            ("summary_stats", pa.struct([("2yr_mean_citedness", pa.float64()), ("h_index", pa.int32()), ("i10_index", pa.int32())])),
            ("is_oa", pa.bool_()),
            ("is_in_doaj", pa.bool_()),
            ("is_in_doaj_since_year", pa.int32()),
            ("apc_prices", pa.list_(pa.struct([("price", pa.int32()), ("currency", pa.string())]))),
            ("apc_usd", pa.int32()),
            ("apc_usd_by_year", pa.list_(pa.struct([("year", pa.int32()), ("price", pa.int32())]))),
            ("homepage_url", pa.string()),
            ("updated_date", pa.timestamp("us", tz="UTC")),
            ("topics", pa.list_(pa.string())),  # a column the reader must skip
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def row(sid, name, issn_l, issns, **kw):
    base = {
        "id": f"https://openalex.org/{sid}", "issn_l": issn_l, "issn": issns, "display_name": name,
        "host_organization_name": "Fake Publisher", "type": "journal", "works_count": 100, "cited_by_count": 500,
        "summary_stats": {"2yr_mean_citedness": 2.5, "h_index": 40, "i10_index": 90}, "is_oa": False, "is_in_doaj": False,
        "is_in_doaj_since_year": None, "apc_prices": [{"price": 2000, "currency": "USD"}], "apc_usd": 2000,
        "apc_usd_by_year": [{"year": 2024, "price": 1900}, {"year": 2025, "price": 2000}], "homepage_url": "http://x.example",
        "updated_date": datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc), "topics": ["a"],
    }
    base.update(kw)
    return base


PART_1 = part(
    [
        row("S1", "Alpha Remote Sensing", A_PRINT, [A_PRINT, A_ONLINE]),
        row("S2", "Unrelated Journal", "9999-9994", ["9999-9994"]),
        row("S3", "Gamma Symposium", None, None, host_organization_name=None, type="conference", apc_prices=None, apc_usd=None, apc_usd_by_year=None),
    ]
)
PART_2 = part([row("S4", "Alpha Twin", None, [A_ONLINE]), row("S5", "Another Unrelated", "8888-8889", ["8888-8889"])])


def test_rows_are_selected_by_issn_or_by_id_and_converted_to_cache_rows():
    rows = openalex.rows_from_parquet(PART_1, {A_PRINT}, {"S3"}, "2026-09-23")
    assert [r["openalex_id"] for r in rows] == ["S1", "S3"]  # S2 matches neither
    alpha, gamma = rows
    assert alpha["issns"] == f"{A_PRINT};{A_ONLINE}" and alpha["h_index"] == "40" and alpha["two_year_mean_citedness"] == "2.5000"
    assert json.loads(alpha["apc_prices_json"]) == [{"price": 2000, "currency": "USD"}]
    assert json.loads(alpha["apc_usd_by_year_json"])[1] == {"year": 2025, "price": 2000}
    assert alpha["updated_date"] == "2026-09-23" and alpha["snapshot_date"] == "2026-09-23"
    assert gamma["issns"] == "" and gamma["host_organization_name"] == "" and gamma["apc_usd"] == "" and gamma["apc_prices_json"] == ""
    assert openalex.rows_from_parquet(PART_1, {"0000-0000"}, set(), "2026-09-23") == []


class FakeFetcher:
    def __init__(self, routes):
        self.routes = routes

    def get_bytes(self, url):
        return self.routes[url]


def manifest(sizes):
    return {
        "date": "2026-09-23", "record_count": 5,
        "files": [{"url": f"s3://openalex/data/parquet/sources/updated_date=2026-09-2{i}/part_0000.parquet", "meta": {"content_length": n}} for i, n in enumerate(sizes, start=1)],
    }


def test_collect_downloads_every_part_dedupes_and_sorts(tmp_path):
    man = manifest([len(PART_1), len(PART_2)])
    routes = {openalex.https_url(f["url"]): data for f, data in zip(man["files"], [PART_1, PART_2], strict=True)}
    assert next(iter(routes)).startswith("https://openalex.s3.amazonaws.com/data/parquet/sources/")
    seen = []
    rows = openalex.collect(man, FakeFetcher(routes), {A_ONLINE}, {"S3"}, workers=2, cache_dir=tmp_path, progress=lambda d, t: seen.append((d, t)))
    assert [r["openalex_id"] for r in rows] == ["S1", "S3", "S4"]
    assert seen[-1] == (2, 2) and len(list(tmp_path.glob("*.parquet"))) == 2
    # second run reads the saved parts and needs no download
    again = openalex.collect(man, FakeFetcher({}), {A_ONLINE}, {"S3"}, workers=1, cache_dir=tmp_path)
    assert again == rows


def test_a_part_with_the_wrong_size_is_an_error_not_silent_data_loss():
    man = manifest([len(PART_1) + 1])
    url = openalex.https_url(man["files"][0]["url"])
    with pytest.raises(FetchError, match="manifest says"):
        openalex.collect(man, FakeFetcher({url: PART_1}), {A_PRINT}, set(), workers=1)


def test_cache_round_trip_and_typed_view(tmp_path):
    rows = openalex.rows_from_parquet(PART_1, {A_PRINT}, {"S3"}, "2026-09-23")
    csv_path, meta_path = tmp_path / "oa.csv", tmp_path / "oa.json"
    openalex.write_cache(rows, manifest([1]), csv_path, meta_path)
    cached = openalex.read_cache(csv_path)
    assert set(cached) == {"S1", "S3"}
    typed = openalex.parse_cache_row(cached["S1"])
    assert typed["is_oa"] is False and typed["apc_usd"] == 2000 and typed["h_index"] == 40
    assert typed["issn_list"] == [A_PRINT, A_ONLINE] and typed["apc_prices"][0]["currency"] == "USD"
    assert typed["apc_usd_by_year"][-1]["year"] == 2025
    assert json.loads(meta_path.read_text())["rows_kept"] == 2
    assert openalex.parse_cache_row(cached["S3"])["apc_usd"] is None
    assert openalex.read_cache(tmp_path / "missing.csv") == {}


def test_bad_manifest_json_is_a_parse_error():
    class Bad:
        def get_bytes(self, url):
            return b"<html>not json</html>"

    with pytest.raises(FetchError) as info:
        openalex.fetch_manifest(Bad())
    assert info.value.kind == "parse"
