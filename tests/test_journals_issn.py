"""ISSN and title normalisation for the journal shortlist (offline, no fixtures)."""

import pytest

from darkvessel.journals.issn import (
    check_digit,
    extract_issns,
    is_valid_issn,
    normalize_issn,
    normalize_title,
)


def make_issn(first_seven: str) -> str:
    return f"{first_seven[:4]}-{first_seven[4:]}{check_digit(first_seven)}"


@pytest.mark.parametrize(
    "raw",
    ["0034-4257", "00344257", " ISSN: 0034-4257 ", "e-ISSN 0034 4257", "0034‐4257", "0034–4257"],
)
def test_normalize_accepts_common_spellings(raw):
    assert normalize_issn(raw) == "0034-4257"


def test_normalize_upper_cases_the_x_and_rejects_non_issns():
    assert normalize_issn("1545-598x") == "1545-598X"
    for bad in ["", None, "1234", "1234-56789", "ABCD-EFGH", "0034-4257 and 1879-0704"]:
        assert normalize_issn(bad) is None


def test_check_digit_including_x():
    assert check_digit("0034425") == "7"
    assert check_digit("1545598") == "X"
    assert make_issn("1234567") == "1234-5679"
    with pytest.raises(ValueError):
        check_digit("123")


def test_is_valid_issn():
    assert is_valid_issn("0034-4257")
    assert is_valid_issn("1545-598X")
    assert not is_valid_issn("0034-4258")
    assert not is_valid_issn("not an issn")


def test_extract_issns_from_scimago_style_cell_and_free_text():
    assert extract_issns("15424863, 00079235") == ["1542-4863", "0007-9235"]
    text = "Print 0034-4257 / online 1879-0704; ISSN-L 0034-4257. Volume 20241234"
    assert extract_issns(text) == ["0034-4257", "1879-0704"]


def test_extract_drops_bad_check_digits_unless_asked():
    assert extract_issns("1234-5678") == []
    assert extract_issns("1234-5678", validate=False) == ["1234-5678"]


def test_extract_does_not_cut_issns_out_of_longer_numbers():
    assert extract_issns("order number 112345679001") == []


def test_normalize_title_ignores_case_accents_punctuation_and_qualifiers():
    assert normalize_title("Remote Sensing (Basel)") == normalize_title("remote  sensing")
    assert normalize_title("Ocean & Coastal Management") == normalize_title("Ocean and Coastal Management")
    assert normalize_title("The Journal of Rémote Sensing") == "journal of remote sensing"
    assert normalize_title("IEEE Access") == "ieee access"


def test_normalize_title_empty_never_matches():
    assert normalize_title(None) == ""
    assert normalize_title("  (Basel)") == ""
    assert normalize_title("遥感") == ""
