"""Project-wide constants: paths, CRS, AOIs, and the mandatory 'dark' caveat."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
OUTPUT_DIR = DATA_DIR
DOCS_DIR = REPO_ROOT / "docs"
FIG_DIR = DOCS_DIR / "figures"

# Output CRS: EPSG:4326 plus one UTM zone per product. Vietnamese coastal work uses
# zone 48N (102E to 108E); South China Sea-wide products use zone 49N (108E to 114E),
# the zone at the centre of the sea. Areas are computed in an equal-area CRS.
CRS_GEO = "EPSG:4326"
CRS_UTM = "EPSG:32648"  # WGS 84 / UTM zone 48N
CRS_UTM_REGIONAL = "EPSG:32649"  # WGS 84 / UTM zone 49N
CRS_EQUAL_AREA = "EPSG:6933"  # WGS 84 / NSIDC EASE-Grid 2.0 Global (equal area)

# AOIs: either a lon/lat box (west, south, east, north) or a union of Natural Earth
# 10 m marine areas (public domain), matched by their `name` field.
AOIS = {
    "south_china_sea": {
        "natural_earth": ["South China Sea", "Gulf of Tonkin", "Gulf of Thailand"],
        "label": "South China Sea with the Gulf of Tonkin and Gulf of Thailand",
        "utm": CRS_UTM_REGIONAL,
    },
    "ca_mau": {
        "bbox": (103.5, 7.5, 106.0, 9.8),
        "label": "Ca Mau waters, Gulf of Thailand and East Sea coasts",
        "utm": CRS_UTM,
    },
    "gulf_of_tonkin": {
        "bbox": (105.6, 19.0, 108.2, 21.6),
        "label": "Gulf of Tonkin, Vietnamese side",
        "utm": CRS_UTM,
    },
}
DEFAULT_AOI = "south_china_sea"
NE_MARINE_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
                 "geojson/ne_10m_geography_marine_polys.geojson")
NE_LAND_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson"


def aoi_suffix(name: str) -> str:
    """File suffix for per-AOI products: '' for the default AOI, '_<name>' otherwise."""
    return "" if name == DEFAULT_AOI else f"_{name}"

# Rule 4 of the project brief: every vessel output must say this.
DARK_CAVEAT = (
    "'Dark' means only that no AIS position was matched to this radar detection. "
    "It does not mean illegal. Many vessels are not required to carry AIS, AIS can be "
    "off for lawful reasons, and satellite AIS misses messages in busy coastal waters. "
    "Treat every unmatched detection as a lead for review, not as evidence of wrongdoing."
)
DARK_CAVEAT_SHORT = "Dark = no AIS match. Not evidence of illegal activity."
# Product caveat (app/CONTRACT.md section 1.1, board decision D4.2): DARK_CAVEAT extended to the
# terrestrial AIS blind spot, because the open build's live AIS comes from shore receivers. Used
# unchanged in every API record, export and lead view of SCS Vessel Watch.
PRODUCT_CAVEAT = (
    "'Dark' means only that no AIS position was matched to this radar contact. It does not "
    "mean illegal. Many vessels are not required to carry AIS, AIS can be off for lawful "
    "reasons, and both satellite and terrestrial AIS have blind spots: satellite AIS misses "
    "messages in busy coastal waters, and shore receivers cover only the waters within their "
    "radio range. Treat every unmatched contact as a lead for review, not as evidence of "
    "wrongdoing. An AIS gap is not proof of intent."
)

# AWS Open Data mirror of Sentinel-1 GRD (anonymous reads worked on 2026-10-02).
AWS_S1_BUCKET_URL = "https://sentinel-s1-l1c.s3.amazonaws.com"
# ESA WorldCover 2021 v200, 10 m land cover COGs (class 80 = permanent water).
WORLDCOVER_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
