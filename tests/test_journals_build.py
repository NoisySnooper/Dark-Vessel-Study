"""Merge, status rules and the build command, driven by fake rows and a fake downloader (offline)."""

import csv
import json

import pytest

from darkvessel.config import REPO_ROOT
from darkvessel.journals import build, openalex
from darkvessel.journals.fetch import FetchError
from darkvessel.journals.xlsx import write_xlsx

A_PRINT, A_ONLINE, B_ONLINE, C_PRINT = "1111-1119", "2222-2227", "3333-3335", "4444-4443"


def seed_row(**kw):
    row = {c: "" for c in build.SEED_COLUMNS}
    row.update(kw)
    return row


def fake_seed():
    unverified = "UNVERIFIED (search snippet) [p1]"
    return [
        seed_row(
            key="alpha", venue="Alpha Remote Sensing", type="journal", issn_print=A_PRINT, issn_online=A_ONLINE,
            openalex_ids="S1", sjr=f"4.266 (SJR 2025) | {unverified}", quartiles=f"Geology: Q1 | {unverified}",
            scimago_h=f"397 | {unverified}", oa_model="hybrid", oa_evidence=f"publisher page says hybrid {unverified}",
            apc_note=f"USD 2700 on the publisher page {unverified}", review_time=f"30 d | {unverified}",
            accepts_letters_or_short=f"yes: Letters, 5 pages | {unverified}", ai_disclosure_policy=f"Acknowledgments | {unverified}",
            fit_letter="3: short format", fit_flagship="0: too short",
            scopus_discontinued_check="NOT CHECKED | list blocked", hijacked_check="NOT CHECKED | list blocked", sources="[p1] https://example.org/alpha",
        ),
        seed_row(
            key="beta", venue="Beta Open Journal", type="journal", issn_online=B_ONLINE, openalex_ids="S2",
            sjr="1.000 (SJR 2025) | VERIFIED (page opened 2026-10-02)", quartiles="Oceanography: Q1 | VERIFIED (page opened 2026-10-02)",
            scimago_h="50 | VERIFIED (page opened 2026-10-02)", oa_model="gold", oa_evidence="VERIFIED (page opened 2026-10-02)",
            review_time="10 d | VERIFIED (page opened 2026-10-02)", accepts_letters_or_short="no | VERIFIED (page opened 2026-10-02)",
            ai_disclosure_policy="Methods | VERIFIED (page opened 2026-10-02)", fit_letter="1: ok", fit_flagship="2: ok",
            scopus_discontinued_check="NOT ON LIST | VERIFIED by hand", hijacked_check="NO ENTRY | VERIFIED by hand", sources="[p1] https://example.org/beta",
        ),
        seed_row(
            key="gamma", venue="Gamma Symposium proceedings", aliases="International Gamma Symposium (GAMMA)", type="proceedings",
            issn_print=C_PRINT, openalex_ids="S3;S4", publisher_fallback=f"Fake IEEE | {unverified}", sjr=f"0.263 (SJR 2024) | {unverified}",
            quartiles=f"NOT RETRIEVED | no quartile for proceedings {unverified}", scimago_h=f"87 | {unverified}",
            oa_model="n/a (proceedings)", apc_note=f"none (proceedings) | {unverified}", review_time=f"annual | {unverified}",
            accepts_letters_or_short=f"yes: 4-page paper | {unverified}", ai_disclosure_policy=f"Acknowledgments | {unverified}",
            fit_letter="3: ok", fit_flagship="0: no", scopus_discontinued_check="NOT CHECKED | list blocked",
            hijacked_check="NOT CHECKED | list blocked", sources="[p1] https://example.org/gamma",
        ),
    ]


def oa_row(openalex_id, name, publisher, issns, is_oa, doaj, apc, h):
    prices = [{"price": apc, "currency": "USD"}] if apc else []
    return {
        "openalex_id": openalex_id, "display_name": name, "issn_l": issns.split(";")[0] if issns else "", "issns": issns,
        "host_organization_name": publisher, "type": "journal", "is_oa": str(is_oa), "is_in_doaj": str(doaj),
        "is_in_doaj_since_year": "2020" if doaj else "", "apc_usd": str(apc or ""),
        "apc_prices_json": json.dumps(prices) if prices else "",
        "apc_usd_by_year_json": json.dumps([{"year": 2024, "price": apc - 100}, {"year": 2025, "price": apc}]) if apc else "",
        "h_index": str(h), "two_year_mean_citedness": "1.5", "works_count": "1000", "cited_by_count": "5000",
        "homepage_url": "", "updated_date": "2026-09-23", "snapshot_date": "2026-09-23",
    }


def fake_oa():
    return {
        "S1": oa_row("S1", "Alpha Remote Sensing", "Fake Elsevier BV", f"{A_PRINT};{A_ONLINE}", False, False, 2645, 100),
        "S2": oa_row("S2", "Beta Open Journal", "Fake MDPI", B_ONLINE, True, True, 3000, 40),
        "S3": oa_row("S3", "Gamma Symposium", "", "", False, False, 0, 13),
        "S4": oa_row("S4", "GAMMA 2022", "", "", False, False, 0, 21),
    }


def test_output_has_exactly_the_requested_columns_in_order():
    assert build.OUTPUT_COLUMNS == [
        "venue", "type", "issn_print", "issn_online", "publisher", "sjr", "quartiles", "h_index", "oa_model", "apc_usd",
        "review_time", "accepts_letters_or_short", "ai_disclosure_policy", "fit_letter", "fit_flagship",
        "scopus_discontinued_check", "hijacked_check", "sources", "verification_status",
    ]
    rows = build.build_rows(fake_seed(), fake_oa())
    assert all(list(r) == build.OUTPUT_COLUMNS for r in rows)


def test_openalex_fields_are_merged_with_their_provenance():
    alpha = build.build_rows(fake_seed(), fake_oa())[0]
    assert alpha["publisher"] == "Fake Elsevier BV | VERIFIED: OpenAlex snapshot 2026-09-23, source ID S1 [oa]"
    value, prov = build.split_cell(alpha["apc_usd"])
    assert value == "2645 (optional, hybrid)"
    assert "price year 2025" in prov and "USD 2645" in prov and "USD 2700 on the publisher page" in prov
    value, prov = build.split_cell(alpha["h_index"])
    assert value == "SCImago 397; OpenAlex 100" and "differs from SCImago" in prov
    assert "is_oa=False" in alpha["oa_model"] and "[oa] OpenAlex snapshot 2026-09-23, source ID S1" in alpha["sources"]
    assert alpha["sources"].startswith("[p1] https://example.org/alpha")


def test_status_is_computed_from_the_tags_in_the_cells():
    alpha, beta, gamma = build.build_rows(fake_seed(), fake_oa())
    assert alpha["verification_status"] == "PARTIAL"  # OpenAlex identity, snippet facts
    assert beta["verification_status"] == "VERIFIED"  # nothing flagged anywhere
    assert gamma["verification_status"] == "UNVERIFIED"  # even the publisher is a snippet


def test_fragmented_openalex_records_are_not_used_for_publisher_or_apc():
    gamma = build.build_rows(fake_seed(), fake_oa())[2]
    assert gamma["publisher"].startswith("Fake IEEE | UNVERIFIED")
    assert gamma["apc_usd"].startswith("none (proceedings)")
    assert "OpenAlex" not in build.split_cell(gamma["h_index"])[0]
    assert "source ID S3, S4" in gamma["sources"]


def test_seed_that_contradicts_the_openalex_flags_is_marked():
    seed = fake_seed()
    seed[0]["oa_model"] = "gold"
    assert "CONFLICT" in build.build_rows(seed, fake_oa())[0]["oa_model"]
    seed[1]["oa_model"] = "hybrid"
    assert "CONFLICT" in build.build_rows(seed, fake_oa())[1]["oa_model"]


# ---- external lists --------------------------------------------------------------------------

SCIMAGO = (
    "Rank;Sourceid;Title;Type;Issn;SJR;SJR Best Quartile;H index;Total Docs. (2025);Total Docs. (3years);Country;Publisher;Coverage;Categories;Areas\n"
    '1;100;"Alpha Remote Sensing";journal;"11111119, 22222227";4,266;Q1;397;439;1300;UK;"Fake Elsevier";"1969-2025";'
    '"Computers in Earth Sciences (Q1); Geology (Q1); Earth and Planetary Sciences (miscellaneous) (Q2)";"Earth and Planetary Sciences"\n'
)
POLICY_PAGE = '<a href="https://www.elsevier.com/files/Discontinued_sources.xlsx">Discontinued sources from Scopus</a>'
RW_PAGE = '<iframe src="https://docs.google.com/spreadsheets/d/e/2PACX-1vFAKE/pubhtml?widget=true"></iframe>'
HIJACKED = (
    "Journal title,Original URL,Hijacked URL,Notes\n"
    f"Alpha Remote Sensing,https://alpha.example,https://alpha-clone.example,copies ISSN {A_PRINT}\n"
    "Other Journal,https://other.example,https://other-clone.example,\n"
)


def discontinued_xlsx():
    return write_xlsx(
        {"Discontinued": [["Source Title", "ISSN", "E-ISSN", "Reason"], ["Gamma Symposium proceedings", C_PRINT.replace("-", ""), "", "Publication concerns"]]}
    )


class FakeFetcher:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get_bytes(self, url):
        self.calls.append(url)
        value = self.routes.get(url)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise FetchError("http", url, "HTTP 404: file not found")
        return value


def all_routes():
    return {
        build.SCIMAGO_URL: SCIMAGO.encode(),
        build.SCOPUS_POLICY_URL: POLICY_PAGE.encode(),
        "https://www.elsevier.com/files/Discontinued_sources.xlsx": discontinued_xlsx(),
        build.RW_CHECKER_URL: RW_PAGE.encode(),
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vFAKE/pub?output=csv": HIJACKED.encode(),
    }


def blocked_routes():
    urls = [build.SCIMAGO_URL, build.SCOPUS_POLICY_URL, build.RW_CHECKER_URL]
    return {u: FetchError("blocked", u, "host is blocked by the network proxy (HTTP 403 on the proxy CONNECT request)") for u in urls}


@pytest.fixture
def opts(tmp_path):
    seed_path = tmp_path / "seed.csv"
    with open(seed_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=build.SEED_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(fake_seed())
    oa_path = tmp_path / "openalex_sources.csv"
    with open(oa_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=openalex.CACHE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(fake_oa().values())
    doc = tmp_path / "journals.md"
    doc.write_text(f"intro\n{build.TABLE_BEGIN}\nold table\n{build.TABLE_END}\noutro\n", encoding="utf-8")
    return build.Options(seed=seed_path, openalex_csv=oa_path, out_csv=tmp_path / "journals.csv", cache_json=tmp_path / "external.json", doc_path=doc)


def test_blocked_hosts_give_clear_messages_and_leave_seed_values_in_place(opts):
    rows, reports = build.run(opts, fetcher=FakeFetcher(blocked_routes()))
    assert [r.state for r in reports] == ["failed", "failed", "failed"]
    assert all("blocked by the network proxy" in r.message for r in reports)
    assert build.exit_code(reports) == 2
    alpha = rows[0]
    assert alpha["scopus_discontinued_check"] == "NOT CHECKED | list blocked"
    assert alpha["sjr"].startswith("4.266 (SJR 2025) | UNVERIFIED")
    written = list(csv.DictReader(open(opts.out_csv, encoding="utf-8")))
    assert [r["venue"] for r in written] == ["Alpha Remote Sensing", "Beta Open Journal", "Gamma Symposium proceedings"]
    text = opts.doc_path.read_text(encoding="utf-8")
    assert "old table" not in text and "| Alpha Remote Sensing |" in text and text.startswith("intro\n") and text.endswith("outro\n")


def test_successful_downloads_replace_seed_values_with_verified_ones(opts):
    fetcher = FakeFetcher(all_routes())
    rows, reports = build.run(opts, fetcher=fetcher)
    assert [r.state for r in reports] == ["fresh", "fresh", "fresh"] and build.exit_code(reports) == 0
    alpha, beta, gamma = rows
    assert alpha["sjr"].startswith("4.266 (SJR 2025) | VERIFIED: SCImago CSV")
    assert build.split_cell(alpha["quartiles"])[0] == "Computers in Earth Sciences: Q1; Geology: Q1; Earth and Planetary Sciences (miscellaneous): Q2"
    assert build.split_cell(alpha["h_index"])[0] == "SCImago 397; OpenAlex 100"
    assert alpha["scopus_discontinued_check"].startswith("NOT ON LIST | VERIFIED")
    assert alpha["hijacked_check"].startswith("CLONE REPORTED (ISSN match) | VERIFIED") and "https://alpha-clone.example" in alpha["hijacked_check"]
    assert gamma["scopus_discontinued_check"].startswith("ON DISCONTINUED LIST (ISSN match)")  # matched through the hyphenless ISSN
    assert beta["sjr"].startswith("not in the SCImago file | VERIFIED absence")
    assert alpha["verification_status"] == "PARTIAL"  # review time and policies are still snippets
    cache = json.loads(opts.cache_json.read_text(encoding="utf-8"))
    assert set(cache) == {"scimago", "scopus", "hijacked"}
    assert "Alpha Remote Sensing" in opts.doc_path.read_text(encoding="utf-8")


def test_saved_results_are_reused_when_a_later_run_has_no_network(opts):
    first, _ = build.run(opts, fetcher=FakeFetcher(all_routes()))
    offline = build.Options(**{**opts.__dict__, "offline": True})
    again, reports = build.run(offline, fetcher=FakeFetcher({}))
    assert again == first
    assert [r.state for r in reports] == ["cached", "cached", "cached"] and build.exit_code(reports) == 0

    blocked = build.Options(**{**opts.__dict__})
    third, reports = build.run(blocked, fetcher=FakeFetcher(blocked_routes()))
    assert third == first  # a failed refresh keeps the earlier verified values
    assert [r.state for r in reports].count("failed") == 3 and [r.state for r in reports].count("cached") == 3
    assert build.exit_code(reports) == 2


def test_one_broken_source_does_not_stop_the_others(opts):
    routes = all_routes()
    routes[build.SCIMAGO_URL] = b"<html><body>Please log in</body></html>"
    rows, reports = build.run(opts, fetcher=FakeFetcher(routes))
    assert [r.state for r in reports] == ["failed", "fresh", "fresh"]
    assert "does not look like the SCImago CSV" in reports[0].message
    assert rows[0]["hijacked_check"].startswith("CLONE REPORTED")
    assert rows[0]["sjr"].startswith("4.266 (SJR 2025) | UNVERIFIED")


def test_local_files_work_without_any_network(opts, tmp_path):
    scimago_file = tmp_path / "scimagojr 2025.csv"
    scimago_file.write_text(SCIMAGO, encoding="utf-8")
    local = build.Options(**{**opts.__dict__, "offline": True, "scimago_csv": scimago_file})
    rows, reports = build.run(local, fetcher=FakeFetcher({}))
    assert reports[0].state == "fresh" and reports[1].state == "unavailable"
    assert rows[0]["sjr"].startswith("4.266 (SJR 2025) | VERIFIED") and "scimagojr 2025.csv" in rows[0]["sources"]


def test_dry_run_writes_nothing(opts):
    dry = build.Options(**{**opts.__dict__, "dry_run": True})
    build.run(dry, fetcher=FakeFetcher(all_routes()))
    assert not opts.out_csv.exists() and not opts.cache_json.exists()
    assert "old table" in opts.doc_path.read_text(encoding="utf-8")


def test_missing_openalex_cache_is_an_actionable_error(opts):
    opts.openalex_csv.unlink()
    with pytest.raises(FileNotFoundError, match="journals_openalex.py"):
        build.run(opts, fetcher=FakeFetcher({}))


def test_table_and_marker_replacement():
    rows = build.build_rows(fake_seed(), fake_oa())
    table = build.render_table(rows)
    lines = table.splitlines()
    assert lines[0].startswith("| Venue | Publisher |") and len(lines) == 2 + len(rows)
    assert "| Alpha Remote Sensing | Fake Elsevier BV | 4.266 (SJR 2025), Q1* | hybrid, 2645 | yes: Letters, 5 pages | 3 | 0 | P |" in lines[2]
    assert lines[4].endswith("| U |")
    assert build.best_quartile("Geology: Q2; Soil Science: Q1 | UNVERIFIED") == "Q1"
    assert build.best_quartile("NOT RETRIEVED | x") == "n/r"
    text = f"a\n{build.TABLE_BEGIN}\nold\n{build.TABLE_END}\nb\n"
    assert build.replace_block(text, "NEW") == f"a\n{build.TABLE_BEGIN}\nNEW\n{build.TABLE_END}\nb\n"
    assert build.replace_block("no markers", "NEW") is None


def test_repo_files_contain_no_em_dashes():
    em = chr(0x2014)
    names = ["data/journals.csv", "data/journals/seed.csv", "data/journals/openalex_sources.csv", "docs/journals.md"]
    paths = [REPO_ROOT / n for n in names]
    paths += sorted((REPO_ROOT / "src/darkvessel/journals").glob("*.py"))
    paths += sorted((REPO_ROOT / "scripts").glob("journals_*.py")) + sorted((REPO_ROOT / "tests").glob("test_journals*.py"))
    for path in paths:
        if path.exists():
            assert em not in path.read_text(encoding="utf-8"), f"em dash in {path}"


def test_hijack_sheet_with_a_banner_row_and_blank_rows_is_still_read():
    text = (
        "Retraction Watch Hijacked Journal Checker,,,\n"
        ",,,\n"
        "Journal title,Authentic URL,Hijacked URL,ISSN\n"
        f"Alpha Remote Sensing,https://alpha.example,https://alpha-clone.example,{A_PRINT}\n"
        ",,,\n"
        "Other Journal,https://other.example,https://other-clone.example,\n"
    )
    records = build._csv_records(text, "fake.csv")
    assert [r["Journal title"] for r in records] == ["Alpha Remote Sensing", "Other Journal"]
    assert records[0]["ISSN"] == A_PRINT
    with pytest.raises(FetchError, match="HTML page"):
        build._csv_records("<!DOCTYPE html><html></html>", "fake.csv")
    assert build._csv_records("just one column\nvalue\n", "fake.csv") == []
