"""SCImago CSV parsing and ISSN lookup on a small fake file (offline)."""

import pytest

from darkvessel.journals.scimago import parse_categories, parse_scimago_csv

HEADER = (
    "Rank;Sourceid;Title;Type;Issn;SJR;SJR Best Quartile;H index;Total Docs. (2025);Total Docs. (3years);"
    "Country;Publisher;Coverage;Categories;Areas"
)
ROWS = [
    '1;100;"Alpha Remote Sensing";journal;"11111119, 22222227";4,266;Q1;397;439;1300;United Kingdom;"Fake Elsevier";"1969-2025";'
    '"Computers in Earth Sciences (Q1); Geology (Q1); Earth and Planetary Sciences (miscellaneous) (Q2)";"Earth and Planetary Sciences; Agricultural and Biological Sciences"',
    '2;200;"Beta Symposium";conference and proceedings;"33333335";0,263;-;87;2593;5817;United States;"Fake IEEE";"1998-2024";"Earth and Planetary Sciences (miscellaneous) (-)";"Earth and Planetary Sciences"',
    '3;300;"Gamma Letters";journal;"";0,434;Q2;64;120;380;United Kingdom;"Fake T&F";"2010-2025";"Remote Sensing (Q2)";"Earth and Planetary Sciences"',
]
TEXT = "\ufeff" + HEADER + "\n" + "\n".join(ROWS) + "\n"


def test_parses_decimal_comma_year_and_hyphenless_issns():
    table = parse_scimago_csv(TEXT)
    assert table.year == 2025
    alpha = table.records[0]
    assert alpha.title == "Alpha Remote Sensing" and alpha.sjr == pytest.approx(4.266)
    assert alpha.issns == ("1111-1119", "2222-2227")
    assert alpha.best_quartile == "Q1" and alpha.h_index == 397
    assert table.records[1].best_quartile == ""  # '-' means no quartile


def test_categories_with_nested_parentheses_keep_their_quartile():
    assert parse_categories("Computers in Earth Sciences (Q1); Earth and Planetary Sciences (miscellaneous) (Q2)") == (
        ("Computers in Earth Sciences", "Q1"),
        ("Earth and Planetary Sciences (miscellaneous)", "Q2"),
    )
    assert parse_categories("Oncology (-); Odd name") == (("Oncology", ""), ("Odd name", ""))
    assert parse_categories("") == ()


def test_lookup_by_any_issn_then_by_title():
    table = parse_scimago_csv(TEXT)
    rec, how = table.find(["2222-2227"], ["Something else"])  # online ISSN only
    assert rec.sourceid == "100" and how == "ISSN"
    rec, how = table.find(["9999-9994"], ["gamma letters"])
    assert rec.sourceid == "300" and how.startswith("title only")
    assert table.find(["9999-9994"], ["Unknown"]) == (None, "")


def test_shared_issn_prefers_the_record_with_the_same_title():
    dup = ROWS[0].replace("Alpha Remote Sensing", "Alpha Twin").replace("100;", "101;", 1)
    table = parse_scimago_csv(HEADER + "\n" + ROWS[0] + "\n" + dup + "\n")
    rec, how = table.find(["1111-1119"], ["Alpha Twin"])
    assert rec.sourceid == "101" and "2 records share it" in how


def test_rejects_files_that_are_not_the_scimago_csv():
    with pytest.raises(ValueError, match="SCImago"):
        parse_scimago_csv("<html><body>Please log in</body></html>")
    with pytest.raises(ValueError, match="empty"):
        parse_scimago_csv("")
    with pytest.raises(ValueError, match="no data rows"):
        parse_scimago_csv(HEADER + "\n")
