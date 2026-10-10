"""Build, data paths, caveat and labels of the backend (app/CONTRACT.md sections 1.1, 2.2 and 5).

BUILD is `open` (default) or `research`, from `--build` or the BUILD environment variable. DATA_DIR defaults to the
repo's `data/` and can be pointed elsewhere with SCS_DATA_DIR (the tests use synthetic data in a temp dir).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import darkvessel  # noqa: F401  (sets PROJ_DATA before rasterio and pyogrio load)
from darkvessel import config as dv_config

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = dv_config.DATA_DIR
FRONTEND_DIST = REPO_ROOT / "app" / "frontend" / "dist"
BUILDS = ("open", "research")

# Contract 1.1. Held here until darkvessel.config carries the constant (board D4.2); a test asserts equality.
_PRODUCT_CAVEAT_LOCAL = (
    "'Dark' means only that no AIS position was matched to this radar contact. It does not mean illegal. Many vessels "
    "are not required to carry AIS, AIS can be off for lawful reasons, and both satellite and terrestrial AIS have blind "
    "spots: satellite AIS misses messages in busy coastal waters, and shore receivers cover only the waters within their "
    "radio range. Treat every unmatched contact as a lead for review, not as evidence of wrongdoing. An AIS gap is not "
    "proof of intent."
)
PRODUCT_CAVEAT = getattr(dv_config, "PRODUCT_CAVEAT", _PRODUCT_CAVEAT_LOCAL)
CAVEAT_SHORT = dv_config.DARK_CAVEAT_SHORT
RESEARCH_LINE = ("Research build, noncommercial, CC BY-NC 4.0. Contains Global Fishing Watch data. "
                 "Powered by Global Fishing Watch.")
RESEARCH_LABEL = "Research build, noncommercial, CC BY-NC 4.0"
ATTRIBUTION = "Powered by Global Fishing Watch."
GAP_NOTE = "An AIS gap is not proof of intent."
BUILD_LABELS = {
    "open": "Open build. Open-licensed sources and live AIS relayed by aisstream.io.",
    "research": RESEARCH_LINE,
}
AISSTREAM_LABEL = "live AIS relayed by aisstream.io; terms UNVERIFIED"  # board D4.7
DATA_CREDIT = "Contains modified Copernicus Sentinel data 2026"
EXPORT_RESEARCH_STAMP = "research build only, noncommercial (Global Fishing Watch data, CC BY-NC 4.0)"
EXPORT_RESEARCH_LICENCE_URL = "https://creativecommons.org/licenses/by-nc/4.0/"
CRS_NOTE = ("EPSG:4326; the GeoPackage adds UTM 49N (EPSG:32649) layers for regional objects and UTM 48N (EPSG:32648) "
            "layers for Ca Mau objects")
SHIPPING_LABEL = "values as published, not counts; presence only"  # board D4.3
EEZ_LABEL = "As published by Marine Regions"
EEZ_STATEMENT = (
    "Lines and polygons as published by Marine Regions (Flanders Marine Institute, VLIZ), World EEZ v12, CC BY 4.0, "
    "doi:10.14284/632. In this sea many zones overlap or are disputed; the source marks them. This product takes no "
    "position on any boundary or claim. VLIZ expresses no opinion about the legal state neither of any country, "
    "territory or area nor concerning its delimitation, frontier or borders. The data has no legal value whatsoever."
)


@dataclass
class Settings:
    build: str = "open"
    data_dir: Path = DEFAULT_DATA_DIR
    frontend_dist: Path = FRONTEND_DIST
    repo_root: Path = REPO_ROOT
    check_interval_s: float = 2.0  # how often a request may stat the files for an mtime change
    tracks_reload_s: float = 300.0  # the aisstream hour files change every minute; tracks reload at most this often
    # serve.py: hold the background work (research events, index warm-up) until this many seconds after the server
    # starts listening, so it does not slow the start and the first page; None starts it at once (tests, bundle builder)
    background_delay_s: float | None = None
    host: str = "127.0.0.1"
    port: int = 8750
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.build not in BUILDS:
            raise ValueError(f"BUILD must be one of {BUILDS}, not {self.build!r}")
        self.data_dir = Path(self.data_dir).resolve()
        self.frontend_dist = Path(self.frontend_dist)

    @property
    def research(self) -> bool:
        return self.build == "research"

    @property
    def research_dir(self) -> Path:
        return self.data_dir / "research"

    @property
    def labels_dir(self) -> Path:
        return self.data_dir / "labels"

    @property
    def decisions_path(self) -> Path:
        """Append-only decision log (contract 3.5): data/labels/ for open, data/research/ for research."""
        return (self.research_dir if self.research else self.labels_dir) / "lead_decisions.jsonl"

    @property
    def contact_labels_path(self) -> Path:
        return self.labels_dir / "contact_labels.csv"

    @property
    def chips_dir(self) -> Path:
        return self.data_dir / "cache" / "chips"

    @property
    def build_label(self) -> str:
        return BUILD_LABELS[self.build]

    def caveat(self, *extra: str | None) -> str:
        """PRODUCT_CAVEAT, then any type caveats, then the research line in the research build (contract 4.5)."""
        parts = [PRODUCT_CAVEAT, *[e for e in extra if e]]
        if self.research:
            parts.append(RESEARCH_LINE)
        return " ".join(parts)


def settings_from_env(**overrides) -> Settings:
    kw = {"build": os.environ.get("BUILD", "open")}
    if os.environ.get("SCS_DATA_DIR"):
        kw["data_dir"] = Path(os.environ["SCS_DATA_DIR"])
    if os.environ.get("SCS_FRONTEND_DIST"):
        kw["frontend_dist"] = Path(os.environ["SCS_FRONTEND_DIST"])
    kw.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**kw)
