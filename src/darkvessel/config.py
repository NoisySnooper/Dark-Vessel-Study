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

# Output CRS pair. Both candidate AOIs sit in UTM zone 48N (102E to 108E).
CRS_GEO = "EPSG:4326"
CRS_UTM = "EPSG:32648"  # WGS 84 / UTM zone 48N

# Candidate AOIs as (west, south, east, north) in EPSG:4326.
AOIS = {
    "ca_mau": {
        "bbox": (103.5, 7.5, 106.0, 9.8),
        "label": "Ca Mau waters, Gulf of Thailand and East Sea coasts",
    },
    "gulf_of_tonkin": {
        "bbox": (105.6, 19.0, 108.2, 21.6),
        "label": "Gulf of Tonkin, Vietnamese side",
    },
}
DEFAULT_AOI = "ca_mau"

# Rule 4 of the project brief: every vessel output must say this.
DARK_CAVEAT = (
    "'Dark' means only that no AIS position was matched to this radar detection. "
    "It does not mean illegal. Many vessels are not required to carry AIS, AIS can be "
    "off for lawful reasons, and satellite AIS misses messages in busy coastal waters. "
    "Treat every unmatched detection as a lead for review, not as evidence of wrongdoing."
)
DARK_CAVEAT_SHORT = "Dark = no AIS match. Not evidence of illegal activity."

# AWS Open Data mirror of Sentinel-1 GRD (anonymous reads worked on 2026-10-02).
AWS_S1_BUCKET_URL = "https://sentinel-s1-l1c.s3.amazonaws.com"
# ESA WorldCover 2021 v200, 10 m land cover COGs (class 80 = permanent water).
WORLDCOVER_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
