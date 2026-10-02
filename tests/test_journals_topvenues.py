"""Coverage check of scan venues against the shortlist, on fake files (offline)."""

import csv

import pytest

from darkvessel.config import REPO_ROOT
from darkvessel.journals import build, topvenues

A_PRINT, A_ONLINE, B_ONLINE, C_PRINT = "1111-1119", "2222-2227", "3333-3335", "4444-4443"


def seed_row(**kw):
    row = {c: "" for c in build.SEED_COLUMNS}
    row.update(kw)
    return row


SEED = [
    seed_row(key="alpha", venue="Alpha Remote Sensing", issn_print=A_PRINT, issn_online=A_ONLINE, openalex_ids="S1"),
    seed_row(key="gamma", venue="Gamma Symposium proceedings", aliases="International Gamma Symposium (GAMMA)", type="proceedings", openalex_ids="S3"),
]
OA = {
    "S1": {
        "openalex_id": "S1", "issns": f"{A_PRINT};{A_ONLINE}", "is_oa": "False", "is_in_doaj": "False", "apc_usd": "2000",
        "snapshot_date": "2026-09-23", "type": "journal",
    },
}


def write_top(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def test_columns_are_found_by_header_text_and_rows_are_ranked_by_count(tmp_path):
    path = tmp_path / "top.csv"
    write_top(
        path, ["source_name", "source_id", "issn_l", "n_works"],
        [["Small Journal", "https://openalex.org/S9", B_ONLINE, "5"], ["Big Journal", "https://openalex.org/S8", C_PRINT, "500"]],
    )
    venues, cols = topvenues.read_top_venues(path, n=1)
    assert cols == {"name": "source_name", "type": None, "id": "source_id", "issn": "issn_l", "count": "n_works", "rank": None}
    assert venues[0]["name"] == "Big Journal" and venues[0]["openalex_id"] == "S8" and venues[0]["issns"] == [C_PRINT]
    assert topvenues.pick_columns(["a", "b"], {"name": "a"})["name"] == "a"
    bad = tmp_path / "bad.csv"
    write_top(bad, ["x", "y"], [["1", "2"]])
    with pytest.raises(ValueError, match="cannot find"):
        topvenues.read_top_venues(bad)


def test_a_rank_column_wins_over_counts(tmp_path):
    path = tmp_path / "top.csv"
    write_top(path, ["rank", "venue", "works"], [["2", "Second", "900"], ["1", "First", "10"]])
    venues, _ = topvenues.read_top_venues(path)
    assert [v["name"] for v in venues] == ["First", "Second"]


REAL_HEADER = ["venue", "venue_type", "source_id", "issn", "n_papers", "cited_by_sum", "first_year", "last_year", "pct_of_corpus"]


def test_the_real_layout_of_the_scan_file_is_read_and_the_no_source_group_is_skipped(tmp_path):
    """Header and ordering as written by darkvessel.biblio.corpus.top_venues."""
    path = tmp_path / "top_venues.csv"
    write_top(
        path, REAL_HEADER,
        [
            ["(no source recorded)", "", "", "", "900", "10", "2015", "2025", "9.0"],
            ["Remote Sensing", "journal", "https://openalex.org/S1", A_PRINT, "400", "5000", "2015", "2025", "4.0"],
            ["arXiv (Cornell University)", "repository", "https://openalex.org/S2", "", "300", "100", "2015", "2025", "3.0"],
            ["IEEE International Geoscience and Remote Sensing Symposium", "conference", "https://openalex.org/S3", "", "200", "50", "2015", "2025", "2.0"],
            ["Delta Journal", "journal", "https://openalex.org/S5", C_PRINT, "100", "30", "2015", "2025", "1.0"],
        ],
    )
    venues, cols = topvenues.read_top_venues(path, n=2)
    assert cols == {"name": "venue", "type": "venue_type", "id": "source_id", "issn": "issn", "count": "n_papers", "rank": None}
    assert [v["name"] for v in venues] == ["(no source recorded)", "Remote Sensing", "arXiv (Cornell University)"]
    assert [v["no_source"] for v in venues] == [True, False, False]  # the no-source group does not use a place
    assert venues[1]["scan_type"] == "journal" and venues[1]["openalex_id"] == "S1" and venues[1]["issns"] == [A_PRINT]
    assert venues[2]["scan_type"] == "repository" and venues[2]["issns"] == []
    # a venue named by ID or ISSN only is still a venue
    assert topvenues.read_top_venues(path, n=15)[0][-1]["name"] == "Delta Journal"


def test_repositories_and_proceedings_get_their_own_type_and_repositories_score_zero():
    used: set[str] = set()
    repo = topvenues.skeleton_seed_row({"name": "arXiv (Cornell University)", "scan_type": "repository", "openalex_id": "S2", "issns": [], "count": "300"}, None, used)
    assert repo["type"] == "repository" and repo["oa_model"] == "not applicable (repository)"
    assert repo["fit_letter"].startswith("0:") and repo["fit_flagship"].startswith("0:") and "300 works" in repo["fit_flagship"]
    conf = topvenues.skeleton_seed_row({"name": "OCEANS Conference", "scan_type": "conference", "openalex_id": "S4", "issns": [], "count": "20"}, None, used)
    assert conf["type"] == "proceedings" and conf["fit_letter"].startswith("?:")
    # the type of the OpenAlex row is the fallback when the scan file has no type column
    series = topvenues.skeleton_seed_row({"name": "Lecture Notes of Things", "openalex_id": "S7", "issns": [], "count": "7"}, {"type": "book series"}, used)
    assert series["type"] == "proceedings"
    for kind, expected in (("journal", "journal"), ("", "journal"), ("metadata", "journal"), ("repository", "repository")):
        assert topvenues.venue_kind(kind, "") == expected
    # OpenAlex types some proceedings series as journals, so the name decides, but PNAS stays a journal
    assert topvenues.venue_kind("journal", "", "IET conference proceedings.") == "proceedings"
    assert topvenues.venue_kind("journal", "", "Journal of Physics Conference Series") == "proceedings"
    assert topvenues.venue_kind("journal", "", "Proceedings of SPIE, the International Society for Optical Engineering") == "proceedings"
    assert topvenues.venue_kind("journal", "", "Proceedings of the National Academy of Sciences") == "journal"
    assert topvenues.venue_kind("repository", "", "Workshop archive") == "repository"


def test_coverage_by_id_issn_name_alias_and_igarss_pattern():
    ids, issns, titles = topvenues.covered_sets(SEED, OA)
    cases = [
        ({"name": "x", "openalex_id": "S1", "issns": []}, True),
        ({"name": "x", "openalex_id": "", "issns": [A_ONLINE]}, True),
        ({"name": "Alpha Remote Sensing (Basel)", "openalex_id": "", "issns": []}, True),
        ({"name": "International Gamma Symposium", "openalex_id": "", "issns": []}, True),
        ({"name": "IGARSS 2024 - 2024 IEEE International Geoscience and Remote Sensing Symposium", "openalex_id": "S77", "issns": []}, True),
        ({"name": "Delta Journal", "openalex_id": "S5", "issns": [C_PRINT]}, False),
    ]
    for venue, expected in cases:
        assert topvenues.is_covered(venue, ids, issns, titles) is expected, venue["name"]


def test_skeleton_rows_hold_identity_and_mark_everything_else_not_retrieved():
    venue = {"name": "Delta Journal of Maritime Sensing", "openalex_id": "S5", "issns": [C_PRINT, B_ONLINE], "count": "120"}
    oa_row = {"type": "journal", "is_oa": True, "is_in_doaj": True, "apc_usd": 1500, "issn_list": [C_PRINT]}
    used = {"dj"}
    row = topvenues.skeleton_seed_row(venue, oa_row, used)
    assert list(row) == build.SEED_COLUMNS and row["key"] not in {"dj"} and row["key"] in used
    assert row["venue"] == "Delta Journal of Maritime Sensing" and row["issn_print"] == C_PRINT and row["issn_online"] == B_ONLINE
    assert row["oa_model"] == "gold" and "UNVERIFIED (inferred)" in row["oa_evidence"]
    assert row["fit_letter"].startswith("?:") and row["review_time"].startswith("NOT RETRIEVED")
    plain = topvenues.skeleton_seed_row({"name": "", "openalex_id": "S6", "issns": []}, None, used)
    assert plain["oa_model"] == "NOT RETRIEVED" and plain["key"]


def test_skeleton_rows_flow_through_the_build_as_partial_or_unverified(tmp_path):
    venue = {"name": "Delta Journal of Maritime Sensing", "openalex_id": "S5", "issns": [C_PRINT], "count": "120"}
    skeleton = topvenues.skeleton_seed_row(venue, None, set())
    rows = build.build_rows([skeleton], {})
    assert rows[0]["verification_status"] == "UNVERIFIED"  # no OpenAlex row, so even the publisher is unknown
    assert rows[0]["scopus_discontinued_check"].startswith("NOT CHECKED")
    seed_path = tmp_path / "seed.csv"
    with open(seed_path, "w", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=build.SEED_COLUMNS, lineterminator="\n").writeheader()
    topvenues.append_seed_rows(seed_path, [skeleton])
    assert build.read_seed(seed_path)[0]["venue"] == "Delta Journal of Maritime Sensing"


def test_command_line_reports_missing_file_and_missing_venues(tmp_path, capsys):
    import importlib.util

    path = REPO_ROOT / "scripts" / "journals_top_venues.py"
    spec = importlib.util.spec_from_file_location("journals_top_venues", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    seed_path = tmp_path / "seed.csv"
    with open(seed_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=build.SEED_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(SEED)
    oa_path = tmp_path / "oa.csv"
    oa_path.write_text("openalex_id\n", encoding="utf-8")
    top = tmp_path / "top.csv"
    common = ["--top", str(top), "--seed", str(seed_path), "--openalex-csv", str(oa_path)]
    assert script.main(common) == 1  # the scan has not written its file

    write_top(
        top, REAL_HEADER,
        [
            ["(no source recorded)", "", "", "", "99", "0", "2015", "2025", "9.0"],
            ["Alpha Remote Sensing", "journal", "S1", A_PRINT, "50", "0", "2015", "2025", "5.0"],
            ["Delta Journal", "journal", "S5", C_PRINT, "40", "0", "2015", "2025", "4.0"],
            ["Preprint Server", "repository", "S6", "", "30", "0", "2015", "2025", "3.0"],
        ],
    )
    assert script.main(common) == 3
    out = capsys.readouterr().out
    assert "MISSING  Delta Journal  [journal]" in out and "skipped  (no source recorded)" in out
    assert " 1 covered  Alpha Remote Sensing" in out  # the no-source group took no place
    assert script.main(common + ["--add"]) == 0
    added = build.read_seed(seed_path)[-2:]
    assert [(r["venue"], r["type"]) for r in added] == [("Delta Journal", "journal"), ("Preprint Server", "repository")]
    assert script.main(common) == 0  # now covered
    assert "all top venues are in the shortlist" in capsys.readouterr().out
