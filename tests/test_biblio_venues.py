"""Offline tests for venue recovery, conference series grouping and the counting rules.

Tiny fake rows only. No network, no data files.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from darkvessel.biblio import corpus, venues  # noqa: E402


# --------------------------------------------------------------------------
# venues
# --------------------------------------------------------------------------
def test_series_name_merges_conference_years():
    names = [
        "IGARSS 2024 - 2024 IEEE International Geoscience and Remote Sensing Symposium",
        "2021 IEEE International Geoscience and Remote Sensing Symposium IGARSS",
        "2017 IEEE International Geoscience and Remote Sensing Symposium (IGARSS)",
    ]
    series = {venues.series_name(n, "conference") for n in names}
    assert series == {"IEEE International Geoscience and Remote Sensing Symposium (IGARSS)"}
    assert venues.series_name("2021 2nd China International SAR Symposium (CISS)", "conference").startswith("China International SAR")
    assert venues.series_name("Remote Sensing", "journal") == "Remote Sensing"


def test_series_name_generic_conference_strips_year_and_ordinal():
    a = venues.series_name("2019 6th International Conference on Image, Vision and Computing (ICIVC)", "conference")
    b = venues.series_name("2022 7th International Conference on Image, Vision and Computing (ICIVC)", "conference")
    assert a == b and "2019" not in a and "6th" not in a


def test_venue_group_moves_proceedings_series_typed_as_journals():
    assert venues.venue_group("journal", "IET conference proceedings.") == "conference"
    assert venues.venue_group("journal", "Remote Sensing") == "journal"
    assert venues.venue_group("conference", "OCEANS 2022") == "conference"
    assert venues.venue_group("repository", "arXiv") == "repository"
    assert venues.venue_group("", "") == "unattributed"


def test_venue_from_doi_rules():
    assert venues.venue_from_doi("10.1109/IGARSS.2019.8900000")[0].endswith("(IGARSS)")
    assert venues.venue_from_doi("10.1117/12.2500000") == ("Proceedings of SPIE", "conference")
    assert venues.venue_from_doi("10.48550/arXiv.2206.00897") == ("arXiv", "repository")
    assert venues.venue_from_doi("10.9999/unknown") is None
    assert venues.venue_from_doi(None) is None


def test_resolve_venue_order_of_methods():
    base = {"id": "W1", "doi": None, "source_name": None, "source_type": None}
    # 1. OpenAlex primary source wins
    row = dict(base, source_name="Remote Sensing", source_type="journal")
    assert venues.resolve_venue(row, {})["method"] == "openalex_primary_source"
    # 2. another location with a journal or conference source
    rec = {"W1": {"primary_raw_source_name": "raw name", "locations": [
        {"source_name": "Zenodo", "source_type": "repository", "raw_source_name": None},
        {"source_name": "IEEE Access", "source_type": "journal", "raw_source_name": None}]}}
    got = venues.resolve_venue(base, rec)
    assert (got["venue"], got["method"]) == ("IEEE Access", "other_location_source")
    # 3. raw primary source name, classified by its words
    rec = {"W1": {"primary_raw_source_name": "IGARSS 2020 - 2020 IEEE International Geoscience and Remote Sensing Symposium", "locations": []}}
    got = venues.resolve_venue(base, rec)
    assert got["method"] == "primary_raw_source_name" and got["venue_type"] == "conference"
    # 5. DOI prefix
    got = venues.resolve_venue(dict(base, doi="10.1109/igarss.2018.1"), {})
    assert got["method"] == "doi_prefix" and got["venue_type"] == "conference"
    # 6. nothing
    assert venues.resolve_venue(base, {})["method"] == "unattributed"


# --------------------------------------------------------------------------
# counting rules
# --------------------------------------------------------------------------
def work(oid, year, themes, venue, group, countries, insts, cited=0, venue_series=None, source_id=""):
    return {
        "openalex_id": oid, "year": year, "themes": themes, "venue": venue, "venue_group": group,
        "venue_series": venue_series or venue, "source_id": source_id, "venue_type": group, "cited_by_count": cited,
        "venue_method": "unattributed" if group == "unattributed" else "openalex_primary_source",
        "countries": countries, "inst_ids": [i for i, _ in insts], "inst_names": [n for _, n in insts],
        "issn": "", "doi": "", "title": "t", "authors": [], "sea_flag": False, "vn_flag": False,
    }


FAKE = [
    work("W1", 2020, ["sar_ship_detection"], "Remote Sensing", "journal", ["CN", "US"], [("I1", "A"), ("I2", "B")], 10, source_id="S1"),
    work("W2", 2020, ["sar_ship_detection", "small_vessel"], "Remote Sensing", "journal", ["CN"], [("I1", "A")], 5, source_id="S1"),
    work("W3", 2021, ["dark_vessels"], "IGARSS 2021", "conference", ["US"], [("I2", "B")], 1,
         venue_series="IEEE IGARSS"),
    work("W4", 2021, ["xview3"], "IGARSS 2022", "conference", [], [], 0, venue_series="IEEE IGARSS"),
    work("W5", 2022, ["iuu_remote_sensing"], "arXiv", "repository", ["GB"], [("I3", "C")], 2, source_id="S9"),
    work("W6", 2022, ["viirs_boats"], "", "unattributed", ["CN"], [("I1", "A")], 0),
]


def test_papers_per_year_counts_each_paper_once_per_theme_and_once_overall():
    df = corpus.papers_per_year(FAKE).set_index("year")
    assert df.loc[2020, "sar_ship_detection"] == 2
    assert df.loc[2020, "small_vessel"] == 1
    assert df.loc[2020, "all_papers"] == 2  # W2 is in two themes but is one paper
    assert df.loc[2022, "all_papers"] == 2


def test_country_and_institution_counting_is_full_counting():
    names = {"CN": "China", "US": "United States", "GB": "United Kingdom"}
    c = corpus.top_countries(FAKE, names).set_index("country_code")
    assert c.loc["CN", "n_papers"] == 3  # W1, W2, W6
    assert c.loc["US", "n_papers"] == 2  # W1, W3
    assert c.loc["US", "country"] == "United States"
    # denominator for the second share column excludes the paper with no country data (W4)
    assert round(c.loc["CN", "pct_of_papers_with_country_data"], 1) == 60.0
    inst = corpus.top_institutions(FAKE).set_index("institution_id")
    assert inst.loc["I1", "n_papers"] == 3 and inst.loc["I2", "n_papers"] == 2


def test_venue_ranking_excludes_repositories_and_unattributed_and_groups_conference_years():
    v = corpus.top_venues(FAKE)
    assert list(v["venue"]) == ["Remote Sensing", "IEEE IGARSS"]
    assert v.set_index("venue").loc["IEEE IGARSS", "n_papers"] == 2
    repos = corpus.top_repositories(FAKE)
    assert list(repos["venue"]) == ["arXiv"]
    cov = corpus.venue_coverage(FAKE)
    assert cov["unattributed"] == 1 and cov["by_group"]["repository"] == 1


def test_sampling_is_reproducible_and_independent_of_corpus_order():
    big = [work(f"W{i}", 2020, ["sar_ship_detection"], "J", "journal", [], []) for i in range(200)]
    a = corpus.draw_sample(big, n=10, seed=1)
    b = corpus.draw_sample(list(reversed(big)), n=10, seed=1)
    assert [x["openalex_id"] for x in a] == [x["openalex_id"] for x in b]
    c = corpus.draw_sample(big, n=10, seed=2)
    assert [x["openalex_id"] for x in a] != [x["openalex_id"] for x in c]
    # dropping a work that is not in the sample leaves the sample unchanged
    outside = next(x for x in big if x["openalex_id"] not in {y["openalex_id"] for y in a})
    d = corpus.draw_sample([x for x in big if x is not outside], n=10, seed=1)
    assert [x["openalex_id"] for x in a] == [x["openalex_id"] for x in d]


def test_precision_by_theme_counts_only_judged_records():
    sample = [FAKE[0], FAKE[2], FAKE[4]]
    judgments = {"W1": {"judgment": "relevant"}, "W3": {"judgment": "not relevant"}}
    p = corpus.precision_by_theme(sample, judgments)
    assert p["n_judged"] == 2 and p["relevant"] == 1 and p["precision"] == 0.5
    assert p["per_theme"]["sar_ship_detection"] == {"n": 1, "relevant": 1, "precision": 1.0}
    assert p["per_theme"]["dark_vessels"]["precision"] == 0.0
    assert p["per_theme"]["xview3"]["precision"] is None


# --------------------------------------------------------------------------
# anchors and the Southeast Asia judgments
# --------------------------------------------------------------------------
def test_anchor_flatten_picks_table_fields_and_tolerates_missing_parts():
    from darkvessel.biblio import anchors  # noqa: E402

    rec = {
        "primary_location": {"landing_page_url": "https://doi.org/10.1/x", "license": "cc-by", "version": "publishedVersion",
                             "source": {"host_organization_name": "Publisher"}},
        "locations": [{"landing_page_url": "https://doi.org/10.1/x"}, {"landing_page_url": None}],
        "open_access": {"is_oa": True, "oa_status": "gold"},
        "biblio": {"volume": "7", "issue": "3", "first_page": "3020", "last_page": "3036"},
        "primary_topic": {"display_name": "Topic", "subfield": {"display_name": "Sub"}, "field": {"display_name": "Field"}},
        "referenced_works_count": 21,
        "locations_count": 2,
        "fwci": 8.6,
    }
    flat = anchors.flatten(rec)
    assert flat["pages"] == "3020-3036" and flat["oa_status"] == "gold" and flat["host_organization"] == "Publisher"
    assert flat["location_urls"] == ["https://doi.org/10.1/x"]
    empty = anchors.flatten({})
    assert empty["pages"] == "" and empty["landing_page_url"] == "" and empty["location_urls"] == []


def test_sea_judgments_are_merged_and_counted():
    sea_rows = [
        dict(work("W1", 2020, ["sar_ship_detection"], "J", "journal", ["VN"], []), sea_flag=True, vn_flag=True,
             sea_places=[], vn_places=[], sea_affiliation_countries=["VN"], merged_from=[], merge_reasons=[], type="article"),
        dict(work("W2", 2021, ["iuu_remote_sensing"], "J", "journal", ["ID"], []), sea_flag=True, vn_flag=False,
             sea_places=["Java Sea"], vn_places=[], sea_affiliation_countries=["ID"], merged_from=[], merge_reasons=[], type="article",),
        dict(work("W3", 2021, ["small_vessel"], "J", "journal", ["US"], []), sea_flag=False, vn_flag=False,
             sea_places=[], vn_places=[], sea_affiliation_countries=[], merged_from=[], merge_reasons=[], type="article"),
    ]
    for r in sea_rows:
        r["inst_names"] = []
    judgments = {"W1": {"judgment": "relevant", "note": ""}, "W2": {"judgment": "not relevant", "note": "Oil spill."}}
    df = corpus.sea_vietnam(sea_rows, {}, judgments).set_index("openalex_id")
    assert list(df.index) == ["W1", "W2"]  # W3 is outside Southeast Asia
    assert bool(df.loc["W1", "judged_on_topic"]) is True and bool(df.loc["W2", "judged_on_topic"]) is False
    assert df.loc["W2", "judgment_note"] == "Oil spill."
    counts = corpus.sea_judged_counts(sea_rows, judgments)
    assert counts["sea"] == {"works": 2, "judged": 2, "relevant": 1}
    assert counts["vn"] == {"works": 1, "judged": 1, "relevant": 1}


def test_anchor_table_uses_the_actual_merge_reason_and_summary_by_id():
    prepared = [
        {"id": "W10", "doi": "10.48550/arxiv.2206.00897", "title": "xView3-SAR: Detecting Dark Fishing Activity", "publication_year": 2022,
         "publication_date": "2022-06-02", "type": "preprint", "venue": "arXiv", "venue_type": "repository", "cited_by_count": 17,
         "authors": ["A", "B"], "n_authors": 2, "themes_loose": "xview3", "themes_strict": ["xview3"], "abstract": "x"},
        {"id": "W11", "doi": "10.52202/068431-2726", "title": "xView3-SAR: Detecting Dark Fishing Activity Using Synthetic Aperture Radar Imagery",
         "publication_year": 2022, "publication_date": "2022-01-01", "type": "conference-paper", "venue": "NeurIPS", "venue_type": "conference",
         "cited_by_count": 3, "authors": ["A", "B"], "n_authors": 2, "themes_loose": "xview3", "themes_strict": ["xview3"], "abstract": ""},
    ]
    kept = [{"openalex_id": "W11", "merged_from": ["W10"], "merge_reasons": ["same_title_year"]}]
    df = corpus.anchors_table(prepared, kept, {"W11": {"summary": "S", "source": "preprint abstract"}}, {"W11": {"pages": "1-2"}})
    row = df[df.anchor_key == "xview3_sar_2022"].set_index("openalex_id")
    assert row.loc["W10", "status"] == "merged into W11 (same_title_year)"
    assert row.loc["W11", "status"] == "in corpus" and row.loc["W11", "summary"] == "S"
    assert row.loc["W11", "summary_source"] == "preprint abstract" and row.loc["W11", "pages"] == "1-2"
    assert row.loc["W10", "summary"] == ""
    # anchors that are absent from the stored rows are reported as not found
    assert (df[df.anchor_key == "paolo_2024_nature"]["found_in_snapshot"] == False).all()  # noqa: E712


def test_csv_output_has_no_em_dash(tmp_path):
    import pandas as pd

    em = chr(0x2014)  # written as a code point so this file has no em dash
    df = pd.DataFrame({"title": [f"A study {em} of ships", None], "n": [1, 2]})
    path = tmp_path / "t.csv"
    corpus._to_csv(df, path)
    text = path.read_text(encoding="utf-8")
    assert em not in text and "A study - of ships" in text
    assert df.loc[0, "title"].count(em) == 1  # the caller's frame is not modified


def test_rankings_break_ties_by_key_not_by_hash_order():
    docs = [work(f"W{i}", 2020, ["sar_ship_detection"], "J", "journal", ["CN"], [("I9", "Z"), ("I1", "A")]) for i in range(3)]
    inst = corpus.top_institutions(docs)
    assert list(inst["institution_id"]) == ["I1", "I9"]  # equal counts, ordered by ID
    docs = [work("W1", 2020, ["sar_ship_detection"], "J", "journal", ["US", "CN", "DE"], [])]
    assert list(corpus.top_countries(docs, {})["country_code"]) == ["CN", "DE", "US"]
