"""Retraction Watch counting on a tiny fake CSV (offline)."""

from datetime import date

from darkvessel.journals import retractions

CSV = (
    "Record ID,Title,Journal,Publisher,RetractionDate,RetractionNature,Reason\n"
    '1,Paper A,Alpha Remote Sensing,Fake,2/9/2024 0:00,Retraction,"Paper Mill;Fake Peer Review;"\n'
    "2,Paper B,Alpha Remote Sensing (Basel),Fake,5/1/2021 0:00,Retraction,Fake Peer Review;\n"
    "3,Paper C,alpha remote sensing,Fake,6/1/2025 0:00,Expression of concern,\n"
    "4,Paper D,Alpha Remote Sensing Letters,Fake,6/1/2025 0:00,Retraction,Error by Author;\n"
    "5,Paper E,2019 IEEE Gamma Symposium (IGARSS 2019),Fake,1/1/2020 0:00,Retraction,Plagiarism;\n"
    "6,Paper F,Beta Journal,Fake,not a date,Correction,\n"
)
VENUES = [
    {"key": "alpha", "venue": "Alpha Remote Sensing", "names": ["Alpha Remote Sensing"]},
    {"key": "beta", "venue": "Beta Journal", "names": ["Beta Journal"]},
    {"key": "gamma", "venue": "Gamma proceedings", "names": ["Gamma proceedings"], "pattern": r"\bIGARSS\b"},
    {"key": "delta", "venue": "Delta Journal", "names": ["Delta Journal"]},
]


def by_key(rows):
    return {r["key"]: r for r in rows}


def test_exact_normalised_name_matching_counts_each_nature_and_the_since_date():
    rows = by_key(retractions.count_by_venue(CSV, VENUES, date(2023, 1, 1)))
    alpha = rows["alpha"]
    assert alpha["notices_total"] == 3  # the Letters journal is a different name and is not counted
    assert alpha["retractions_total"] == 2 and alpha["retractions_since"] == 1
    assert alpha["expressions_of_concern"] == 1 and alpha["corrections"] == 0
    assert alpha["top_reasons"].startswith("Fake Peer Review (2)") and "Paper Mill (1)" in alpha["top_reasons"]
    assert "Alpha Remote Sensing (Basel) (1)" in alpha["matched_names"]


def test_two_spellings_of_the_same_name_do_not_double_count():
    venues = [{"key": "alpha", "venue": "Alpha Remote Sensing (MDPI)", "names": ["Alpha Remote Sensing (MDPI)", "Alpha Remote Sensing"]}]
    row = retractions.count_by_venue(CSV, venues, date(2023, 1, 1))[0]
    assert row["notices_total"] == 3


def test_pattern_for_proceedings_and_unmatched_venues_and_bad_dates():
    rows = by_key(retractions.count_by_venue(CSV, VENUES, date(2023, 1, 1)))
    assert rows["gamma"]["retractions_total"] == 1 and rows["gamma"]["retractions_since"] == 0
    assert rows["delta"]["notices_total"] == 0 and rows["delta"]["matched_names"] == ""
    assert rows["beta"]["corrections"] == 1 and rows["beta"]["retractions_total"] == 0
    assert all(r["rows_scanned"] == 6 for r in rows.values())


def test_date_and_readme_helpers():
    assert retractions.parse_date("2/9/2024 0:00") == date(2024, 2, 9)
    assert retractions.parse_date("2024-02-09") == date(2024, 2, 9)
    assert retractions.parse_date("") is None
    assert retractions.generated_on("This repository contains the latest dataset, generated on 2026-10-01.") == "2026-10-01"
    assert retractions.generated_on("nothing here") == ""
