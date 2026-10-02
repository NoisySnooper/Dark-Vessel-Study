"""Blocked hosts, link discovery and the xlsx reader (offline, no network)."""

import pytest

from darkvessel.journals import fetch
from darkvessel.journals.fetch import FetchError, RequestsFetcher, classify_exception
from darkvessel.journals.xlsx import XlsxError, read_xlsx, rows_to_records, write_xlsx


class ProxyError(Exception):
    """Stands in for requests.exceptions.ProxyError (same class name, same message shape)."""


def test_egress_proxy_refusal_is_reported_as_blocked_with_the_host_name():
    exc = ProxyError(
        "HTTPSConnectionPool(host='www.scimagojr.com', port=443): Max retries exceeded (Caused by "
        "ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden')))"
    )
    err = classify_exception(exc, "https://www.scimagojr.com/journalrank.php?out=xls")
    assert err.kind == "blocked"
    assert "www.scimagojr.com is blocked by the network proxy" in str(err)
    text = err.describe()
    assert text.startswith("BLOCKED:") and "https://www.scimagojr.com/journalrank.php?out=xls" in text
    assert "download the file in a browser" in text


def test_other_failures_are_classified_without_a_stack_trace():
    assert classify_exception(TimeoutError("read timed out"), "https://a.example/x").kind == "network"
    assert classify_exception(OSError("certificate verify failed"), "https://a.example/x").kind == "network"
    assert classify_exception(ValueError("boom"), "https://a.example/x").kind == "network"
    with pytest.raises(ValueError):
        FetchError("nonsense", "u", "m")


class FakeResponse:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def get(self, url, timeout=None, headers=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_fetcher_does_not_retry_a_policy_block():
    session = FakeSession([ProxyError("Tunnel connection failed: 403 Forbidden")])
    with pytest.raises(FetchError) as info:
        RequestsFetcher(attempts=3, session=session).get_bytes("https://www.elsevier.com/x")
    assert info.value.kind == "blocked" and session.calls == 1


def test_fetcher_maps_http_statuses(monkeypatch):
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    ok = RequestsFetcher(attempts=3, session=FakeSession([FakeResponse(503), FakeResponse(200, b"data")]))
    assert ok.get_bytes("https://a.example/x") == b"data"
    for status, word in [(403, "refuses scripted downloads"), (404, "not found"), (429, "rate limited")]:
        with pytest.raises(FetchError) as info:
            RequestsFetcher(attempts=1, session=FakeSession([FakeResponse(status)])).get_bytes("https://a.example/x")
        assert info.value.kind == "http" and word in str(info.value)


PAGE = """
<html><body>
<a href="/products/scopus/content/title-list.xlsx">Scopus title list</a>
<a href="/docs/Discontinued_sources_2026-09.xlsx">Discontinued sources from Scopus</a>
<a href="https://elsevier.example/about">About</a>
<iframe src="https://docs.google.com/spreadsheets/d/e/2PACX-1vFAKE/pubhtml?widget=true&amp;headers=false"></iframe>
<p>Edit link https://docs.google.com/spreadsheets/d/1FakeSheetId_9/edit#gid=0.</p>
</body></html>
"""


def test_finds_the_discontinued_xlsx_link_by_name_not_the_first_xlsx():
    urls = fetch.find_discontinued_xlsx(PAGE, "https://www.elsevier.example/products/scopus/content/content-policy")
    assert urls == ["https://www.elsevier.example/docs/Discontinued_sources_2026-09.xlsx"]
    assert fetch.find_discontinued_xlsx("<a href='x.pdf'>discontinued</a>", "https://e.example/") == []


def test_finds_google_sheets_and_builds_csv_export_addresses():
    found = fetch.find_google_sheet_urls(PAGE)
    assert found[0] == "https://docs.google.com/spreadsheets/d/e/2PACX-1vFAKE/pubhtml?widget=true&headers=false"
    assert found[1] == "https://docs.google.com/spreadsheets/d/1FakeSheetId_9/edit#gid=0"
    assert fetch.sheet_csv_url(found[0]) == "https://docs.google.com/spreadsheets/d/e/2PACX-1vFAKE/pub?output=csv"
    assert fetch.sheet_csv_url(found[1]) == "https://docs.google.com/spreadsheets/d/1FakeSheetId_9/export?format=csv&gid=0"
    assert fetch.sheet_csv_url("https://example.org/spreadsheets/d/abc/edit") is None


def test_decode_text_handles_bom_and_latin1():
    assert fetch.decode_text("﻿Title;Issn".encode()) == "Title;Issn"
    assert fetch.decode_text("café".encode("latin-1")) == "café"


def test_xlsx_round_trip_and_header_detection():
    data = write_xlsx(
        {
            "Notes": [["Scopus discontinued sources"], ["generated 2026-09"]],
            "Discontinued": [["Title", "ISSN", "E-ISSN", "Reason"], ["Alpha & Co", "11111119", "", "Low citation"], [], ["Beta", "", "2222-2227", ""]],
        }
    )
    sheets = dict(read_xlsx(data))
    assert rows_to_records(sheets["Notes"], "issn") == []
    records = rows_to_records(sheets["Discontinued"], "issn")
    assert records == [
        {"Title": "Alpha & Co", "ISSN": "11111119", "E-ISSN": "", "Reason": "Low citation"},
        {"Title": "Beta", "ISSN": "", "E-ISSN": "2222-2227", "Reason": ""},
    ]


def test_xlsx_rejects_other_files():
    with pytest.raises(XlsxError):
        read_xlsx(b"<html>login</html>")
    with pytest.raises(XlsxError):
        read_xlsx(write_xlsx({"a": [["x"]]})[:50])
