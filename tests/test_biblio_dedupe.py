"""Offline tests for the bibliometric scan: deduplication and theme matching.

Only tiny fake records are used. Nothing here touches the network or reads data files.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from darkvessel.biblio import dedupe, themes  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def rec(oid, title, year, doi=None, type_="article", cited=0, themes_=("sar_ship_detection",), venue="J", source_type="journal"):
    return {
        "id": oid,
        "doi": doi,
        "title": title,
        "year": year,
        "type": type_,
        "cited_by_count": cited,
        "themes": list(themes_),
        "venue": venue,
        "source_type": source_type,
    }


LONG_TITLE = "Ship detection in synthetic aperture radar imagery with a lightweight network"


# --------------------------------------------------------------------------
# dedupe
# --------------------------------------------------------------------------
def test_normalizers():
    assert dedupe.normalize_doi("https://doi.org/10.1000/ABC.1") == "10.1000/abc.1"
    assert dedupe.normalize_doi("DOI:10.1000/X") == "10.1000/x"
    assert dedupe.normalize_doi("") is None
    assert dedupe.normalize_id("https://openalex.org/w123") == "W123"
    a = dedupe.normalize_title("Ship <i>Detection</i>: A Review (SAR)!")
    b = dedupe.normalize_title("ship detection - a review  SAR")
    assert a == b == "ship detection a review sar"
    assert dedupe.normalize_title("Café radar") == "cafe radar"


def test_deleted_ids_are_dropped_first():
    records = [rec("W1", LONG_TITLE, 2020, "10.1/a"), rec("W2", "Another long title about dark vessels at sea", 2021)]
    kept, log = dedupe.dedupe(records, deleted_ids={"https://openalex.org/W1"})
    assert [r["id"] for r in kept] == ["W2"]
    assert log == [{"dropped": "W1", "kept": None, "reason": "deleted"}]


def test_same_id_and_same_doi_merge_with_theme_union():
    records = [
        rec("W1", LONG_TITLE, 2020, "https://doi.org/10.1/ABC", themes_=("sar_ship_detection",)),
        rec("W1", LONG_TITLE, 2020, "10.1/abc", themes_=("small_vessel",)),
        rec("W9", "Completely different words used here for a distinct paper", 2020, "10.1/abc", themes_=("dark_vessels",)),
    ]
    kept, log = dedupe.dedupe(records)
    assert len(kept) == 1
    assert kept[0]["themes"] == ["dark_vessels", "sar_ship_detection", "small_vessel"]
    assert {entry["reason"] for entry in log} == {"same_id", "same_doi"}
    assert len(kept[0]["merged_from"]) == 2


def test_title_and_year_merge_ignores_punctuation_and_case():
    a = rec("W1", LONG_TITLE, 2021, doi=None, cited=3)
    b = rec("W2", LONG_TITLE.upper() + ".", 2021, doi=None, cited=9)
    kept, log = dedupe.dedupe([a, b])
    assert len(kept) == 1
    assert kept[0]["id"] == "W2"  # same rank, higher citations wins
    assert log[0]["reason"] == "same_title_year"


def test_published_version_beats_preprint_and_merge_is_noted():
    pre = rec("W1", LONG_TITLE, 2022, "10.48550/arxiv.2201.00001", type_="preprint", cited=50,
              themes_=("xview3",), venue="arXiv", source_type="repository")
    pub = rec("W2", LONG_TITLE, 2023, "10.1109/lgrs.2023.1", type_="article", cited=4, themes_=("sar_ship_detection",))
    kept, log = dedupe.dedupe([pre, pub])
    assert len(kept) == 1
    survivor = kept[0]
    assert survivor["id"] == "W2" and survivor["type"] == "article"
    assert survivor["merged_from"] == ["W1"]
    assert survivor["merge_reasons"] == ["preprint_published_pair"]
    assert survivor["themes"] == ["sar_ship_detection", "xview3"]
    assert survivor["cited_by_count"] == 4  # the kept record's own count


def test_preprint_pair_needs_years_within_one():
    pre = rec("W1", LONG_TITLE, 2018, "10.48550/arxiv.1801.1", type_="preprint")
    pub = rec("W2", LONG_TITLE, 2021, "10.1109/x.1", type_="article")
    kept, _ = dedupe.dedupe([pre, pub])
    assert len(kept) == 2


def test_two_published_versions_in_different_years_are_not_merged():
    a = rec("W1", LONG_TITLE, 2019, "10.1/a")
    b = rec("W2", LONG_TITLE, 2020, "10.1/b")
    kept, _ = dedupe.dedupe([a, b])
    assert len(kept) == 2


def test_short_generic_titles_are_not_merged():
    a = rec("W1", "Introduction", 2020, "10.1/a")
    b = rec("W2", "Introduction", 2020, "10.1/b")
    kept, _ = dedupe.dedupe([a, b])
    assert len(kept) == 2


def test_input_records_are_not_modified():
    a = rec("W1", LONG_TITLE, 2020, "10.1/a")
    snapshot = json.dumps(a, sort_keys=True)
    dedupe.dedupe([a, dict(a, id="W2")])
    assert json.dumps(a, sort_keys=True) == snapshot


# --------------------------------------------------------------------------
# themes: positives
# --------------------------------------------------------------------------
def strict(title, abstract=""):
    return themes.match_themes(title, abstract, "strict")


def test_sar_ship_detection_positive():
    assert "sar_ship_detection" in strict("Ship detection in SAR images with YOLO")
    assert "sar_ship_detection" in strict("Vessel classification from Sentinel-1 data")
    assert "sar_ship_detection" in strict("Boats", "We detect boats in RADARSAT-2 scenes.")


def test_sar_means_search_and_rescue_is_not_sar_imagery():
    text = "Vessel detection for maritime search and rescue (SAR) with drones"
    assert "sar_ship_detection" not in strict(text)
    assert "sar_ship_detection" in themes.match_themes(text, "", "loose")  # loose mode keeps it for later review


def test_hong_kong_sar_and_blood_vessels_are_excluded():
    assert strict("Vessel traffic detection in Hong Kong SAR waters") == []
    assert "sar_ship_detection" not in strict("Retinal vessel segmentation", "SAR was measured near the vessel wall")


def test_dark_vessels_positive_and_medical_negative():
    assert "dark_vessels" in strict("Detecting dark vessels that switch off AIS", "Illegal fishing at sea")
    assert "dark_vessels" in strict("Going dark: AIS gaps in the fishing fleet")
    assert "dark_vessels" in strict("Non-broadcasting ships in the Gulf of Guinea")
    assert strict("Dark vessel sign on black-blood MRI", "Dark vessel wall enhancement in stroke patients") == []


def test_sar_ais_fusion_positive_and_needs_all_three_parts():
    assert "sar_ais_fusion" in strict("Matching AIS tracks to Sentinel-1 ship detections")
    assert "sar_ais_fusion" in strict("Fusion of automatic identification system and synthetic aperture radar data")
    assert "sar_ais_fusion" not in strict("AIS data fusion for port traffic", "No radar involved")
    assert "sar_ais_fusion" not in strict("Sentinel-1 ship detection", "Radar only")


def test_xview3_variants():
    for title in ("xView3-SAR dataset", "The xView-3 challenge", "xView 3 results"):
        assert "xview3" in strict(title)
    assert "xview3" not in strict("xView dataset of overhead imagery")


def test_iuu_and_viirs_and_small_vessel_positive():
    assert "iuu_remote_sensing" in strict("Mapping illegal fishing with satellite imagery")
    assert "iuu_remote_sensing" in strict("IUU fishing", "A remote sensing review")
    assert "viirs_boats" in strict("VIIRS Boat Detection of fishing vessels")
    assert "viirs_boats" in strict("Nighttime lights reveal ships")
    assert "small_vessel" in strict("Small ship detection in satellite imagery")
    assert "small_vessel" in strict("Artisanal fishing boats detected from imagery")


def test_small_target_needs_maritime_term_and_artisanal_needs_fishing_context():
    assert strict("Infrared small target detection", "in satellite imagery") == []
    assert "small_vessel" in strict("Small target detection for ships", "in radar")
    assert strict("Artisanal gold mining detection from satellite imagery") == []


def test_negatives_and_empty_inputs():
    assert strict("Deep learning for protein folding", "No maritime content") == []
    assert strict("", "") == []
    assert themes.match_themes(None, None, "loose") == []


def test_unicode_hyphen_in_sentinel_1():
    assert "sar_ship_detection" in strict("Ship detection with Sentinel‑1 data")


# --------------------------------------------------------------------------
# abstract reconstruction, prefilter, regions
# --------------------------------------------------------------------------
def test_reconstruct_abstract_orders_words_by_position():
    inv = {"detect": [1], "We": [0], "ships": [2], "and": [3, 5], "boats": [4]}
    assert themes.reconstruct_abstract(inv) == "We detect ships and boats and"
    assert themes.reconstruct_abstract(json.dumps(inv)) == "We detect ships and boats and"
    assert themes.reconstruct_abstract(None) == ""
    assert themes.reconstruct_abstract("not json") == ""


def _json_of(text):
    inv = {}
    for pos, word in enumerate(text.split()):
        inv.setdefault(word, []).append(pos)
    return json.dumps(inv)


POSITIVE_EXAMPLES = [
    "Ship detection in SAR images",
    "Dark vessels and AIS gaps",
    "Matching AIS with Sentinel-1 detections",
    "xView3 results",
    "Mapping illegal fishing from space",
    "Small boat detection with radar",
    "VIIRS boat detection of fishing",
    "Artisanal fisheries remote sensing detection",
    "Maritime domain awareness: going dark",
    "Automatic identification system gaps in the fishing fleet",
]


def test_arrow_prefilter_is_a_superset_of_the_theme_tests():
    """Every example that any loose theme matches must pass the (RE2-compatible) anchor prefilter."""
    pattern = re.compile(themes.ARROW_PREFILTER.replace("(?i)", ""), re.IGNORECASE)
    for text in POSITIVE_EXAMPLES:
        if themes.match_themes(text, "", "loose"):
            assert pattern.search(_json_of(text)) or pattern.search(text), text


def test_sea_and_vietnam_flags():
    flags = themes.sea_flags("Ship detection in the Gulf of Tonkin", "Sentinel-1 over Vietnamese waters", ["CN"])
    assert flags["vn_flag"] and flags["sea_flag"] and "Tonkin" in flags["vn_text"]
    flags = themes.sea_flags("Fishing vessels", "Seas around Japan, the East Sea", ["JP", "KR"])
    assert not flags["sea_flag"] and not flags["vn_flag"]  # plain "East Sea" does not count
    flags = themes.sea_flags("Fishing vessels", "No place named", ["TH", "US"])
    assert flags["sea_flag"] and not flags["vn_flag"] and flags["sea_affil"] == ["TH"]
    flags = themes.sea_flags("Boats", "", ["VN"])
    assert flags["vn_flag"] and flags["vn_affil"]
    flags = themes.sea_flags("Boats", "The Strait of Malacca and the South China Sea", [])
    assert flags["sea_flag"] and not flags["vn_flag"]
