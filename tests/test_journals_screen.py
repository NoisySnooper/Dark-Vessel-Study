"""ISSN matching and screening against fake discontinued and hijacked lists (offline)."""

from darkvessel.journals import lists
from darkvessel.journals.lists import (
    MATCH_ISSN,
    MATCH_TITLE,
    NO_MATCH,
    ListIndex,
    ListMeta,
    entries_from_records,
    not_checked,
)

# Fake ISSNs with valid check characters.
A_PRINT, A_ONLINE = "1111-1119", "2222-2227"
B_ONLINE = "3333-3335"
C_PRINT = "4444-4443"
OTHER = "5555-5551"
LOST_ZEROS = "0007-9235"  # Excel stores 00079235 as the number 79235

META = ListMeta("fake list", "fake.xlsx", "2026-10-02", 5)


def discontinued_records():
    return [
        {"Source Title": "Alpha Journal of Testing", "ISSN": "11111119", "E-ISSN": "", "Publisher": "Acme", "Reason": "Publication concerns", "Last year": "2025"},
        {"Source Title": "Beta Letters", "ISSN": "", "E-ISSN": B_ONLINE, "Publisher": "Foo", "Reason": "Low citation", "Last year": "2024"},
        {"Source Title": "Gamma Review", "ISSN": "", "E-ISSN": "", "Publisher": "Foo", "Reason": "Continuous scouting", "Last year": "2023"},
        {"Source Title": "Remote Sensing", "ISSN": OTHER, "E-ISSN": "", "Publisher": "Elsewhere Press", "Reason": "", "Last year": ""},
        {"Source Title": "Numeric Cell Journal", "ISSN": "79235", "E-ISSN": "", "Publisher": "Foo", "Reason": "", "Last year": ""},
    ]


def test_issn_match_ignores_hyphens_and_which_column_holds_the_issn():
    index = ListIndex(entries_from_records(discontinued_records(), "discontinued"))
    by_print = index.screen([A_PRINT], ["Some other title"])
    assert by_print.status == MATCH_ISSN
    assert by_print.entries[0].label == "Alpha Journal of Testing"
    assert "Reason: Publication concerns" in by_print.entries[0].info
    by_online = index.screen([B_ONLINE], [])
    assert by_online.status == MATCH_ISSN and by_online.entries[0].label == "Beta Letters"


def test_any_issn_of_the_venue_is_enough():
    index = ListIndex(entries_from_records(discontinued_records(), "discontinued"))
    res = index.screen(["9999-9990", A_PRINT, A_ONLINE], ["x"])  # first one is invalid and ignored
    assert res.status == MATCH_ISSN


def test_numeric_excel_issn_cell_is_restored_with_its_leading_zeros():
    index = ListIndex(entries_from_records(discontinued_records(), "discontinued"))
    assert index.screen([LOST_ZEROS], []).status == MATCH_ISSN


def test_title_only_match_is_possible_not_a_hit():
    index = ListIndex(entries_from_records(discontinued_records(), "discontinued"))
    no_issn_in_list = index.screen([C_PRINT], ["gamma review"])
    assert no_issn_in_list.status == MATCH_TITLE
    assert "no ISSN" in no_issn_in_list.notes[0]


def test_same_title_with_a_different_issn_is_flagged_as_probably_another_journal():
    index = ListIndex(entries_from_records(discontinued_records(), "discontinued"))
    res = index.screen([C_PRINT], ["Remote Sensing (MDPI)"])
    assert res.status == MATCH_TITLE
    assert "different journal" in res.notes[0] and OTHER in res.notes[0]


def test_no_match_and_empty_inputs():
    index = ListIndex(entries_from_records(discontinued_records(), "discontinued"))
    assert index.screen([C_PRINT], ["Unrelated Title"]).status == NO_MATCH
    assert index.screen([], []).status == NO_MATCH
    assert index.screen(["", None], [""]).status == NO_MATCH


def hijacked_records():
    # Layout unknown in advance: no ISSN header, so ISSNs are found by scanning the cells.
    return [
        {"Journal title": "Alpha Journal of Testing", "Original URL": "https://alpha.example", "Hijacked URL": "https://alpha-journal.example; https://alpha2.example", "Notes": f"clone copies ISSN {A_PRINT}"},
        {"Journal title": "Delta Quarterly", "Original URL": "https://delta.example", "Hijacked URL": "https://delta-q.example", "Notes": ""},
    ]


def test_hijacked_list_without_issn_header_is_scanned_and_urls_are_kept():
    entries = entries_from_records(hijacked_records(), "hijacked")
    assert entries[0].issns == (A_PRINT,)
    assert "https://alpha-journal.example" in entries[0].urls
    index = ListIndex(entries)
    assert index.screen([A_PRINT, A_ONLINE], []).status == MATCH_ISSN
    title_only = index.screen([C_PRINT], ["Delta Quarterly"])
    assert title_only.status == MATCH_TITLE
    assert index.screen([C_PRINT], ["Epsilon"]).status == NO_MATCH


def test_cells_for_each_status_say_what_was_checked():
    d_index = ListIndex(entries_from_records(discontinued_records(), "discontinued"))
    hit = lists.render_discontinued(d_index.screen([A_PRINT], []), META, [A_PRINT])
    assert hit.startswith("ON DISCONTINUED LIST (ISSN match) | VERIFIED against the Scopus discontinued-sources list")
    assert "Alpha Journal of Testing" in hit
    clean = lists.render_discontinued(d_index.screen([C_PRINT], ["Unrelated"]), META, [C_PRINT])
    assert clean.startswith("NOT ON LIST | VERIFIED") and C_PRINT in clean and "5 rows" in clean
    maybe = lists.render_discontinued(d_index.screen([C_PRINT], ["Gamma Review"]), META, [C_PRINT])
    assert maybe.startswith("POSSIBLE MATCH (title only)")

    h_index = ListIndex(entries_from_records(hijacked_records(), "hijacked"))
    clone = lists.render_hijacked(h_index.screen([A_PRINT], []), META, [A_PRINT])
    assert clone.startswith("CLONE REPORTED (ISSN match)") and "https://alpha-journal.example" in clone
    assert "genuine journal is not itself hijacked" in clone
    none = lists.render_hijacked(h_index.screen([C_PRINT], ["Unrelated"]), META, [C_PRINT])
    assert none.startswith("NO ENTRY | VERIFIED") and "does not prove" in none


def test_not_checked_cells_carry_the_reason():
    cell = lists.render_discontinued(not_checked("elsevier.com blocked"), None, [A_PRINT])
    assert cell == "NOT CHECKED | elsevier.com blocked"
    cell = lists.render_hijacked(not_checked("retractionwatch.com blocked"), None, [A_PRINT])
    assert cell.startswith("NOT CHECKED | retractionwatch.com blocked")


def test_a_full_source_list_is_reduced_to_its_inactive_rows():
    records = [
        {"Source Title": "Alpha Journal of Testing", "Print-ISSN": "11111119", "E-ISSN": "", "Active or Inactive": "Active"},
        {"Source Title": "Beta Letters", "Print-ISSN": "", "E-ISSN": B_ONLINE, "Active or Inactive": "Inactive"},
    ]
    index = ListIndex(entries_from_records(records, "discontinued"))
    assert index.screen([A_PRINT], []).status == NO_MATCH  # active source is not a discontinued source
    assert index.screen([B_ONLINE], []).status == MATCH_ISSN
    only_discontinued = [{"Source Title": "Alpha Journal of Testing", "ISSN": "11111119", "Status": "Discontinued"}]
    assert ListIndex(entries_from_records(only_discontinued, "discontinued")).screen([A_PRINT], []).status == MATCH_ISSN


def test_type_and_id_columns_are_not_read_as_titles():
    records = [{"Sourcerecord ID": "123", "Source Title": "Alpha Journal of Testing", "Print-ISSN": "11111119", "Source Type": "Journal"}]
    entry = entries_from_records(records, "discontinued")[0]
    assert entry.titles == ("Alpha Journal of Testing",)
