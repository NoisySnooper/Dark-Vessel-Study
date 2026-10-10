"""File registry of the product (app/CONTRACT.md section 3): status, rows and mtime per file, and the build guard.

Every read of a product file goes through the catalog. With BUILD=open the catalog refuses any path under
`data/research/` (contract 1, Builds; board D2): `guard()` raises ResearchPathError before anything is opened, and the
research-only specs are never registered. `signature()` is the mtime map the store compares to reload changed files.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import darkvessel  # noqa: F401  (PROJ_DATA before pyogrio)
import pandas as pd
import pyarrow.parquet as pq
import pyogrio

from .config import Settings

BOTH = ("open", "research")
OPEN = ("open",)
RESEARCH = ("research",)


class ResearchPathError(PermissionError):
    """Raised when the open build tries to touch a path under data/research/."""


@dataclass(frozen=True)
class FileSpec:
    key: str
    rel: str  # path relative to the data dir; for kind glob/dir: the directory
    layer: str | None = None
    kind: str = "gpkg"  # gpkg, parquet, json, jsonl, csv, geojson, tif, glob, dir
    builds: tuple = BOTH
    pattern: str | None = None  # glob/dir: pattern under rel
    count_key: str | None = None  # json: top-level list whose length is the row count


SPECS: list[FileSpec] = [
    # contacts (3.1)
    FileSpec("live_contacts", "live/live_contacts.gpkg", "contacts_4326"),
    FileSpec("live_scenes", "live/live_contacts.gpkg", "scenes_4326"),
    FileSpec("live_about", "live/live_contacts.gpkg", "about"),
    FileSpec("live_summary", "live/live_summary.json", kind="json"),
    FileSpec("regional_contacts", "detections_regional.gpkg", "detections_regional_4326"),
    FileSpec("regional_scenes", "detections_regional.gpkg", "scenes_processed_4326"),
    FileSpec("regional_identity", "research/regional_identity.parquet", kind="parquet", builds=RESEARCH),
    FileSpec("regional_identity_summary", "research/regional_identity_summary.json", kind="json", builds=RESEARCH),
    FileSpec("structures", "structures_regional.gpkg", "structures_regional_4326"),
    FileSpec("camau_contacts", "detections_ml.gpkg", "detections_verified_4326"),
    FileSpec("camau_window", "detections_baseline.gpkg", "processing_window_4326"),
    FileSpec("camau_about", "detections_baseline.gpkg", "about"),
    FileSpec("regional_cnn", "ml/regional_cnn.parquet", kind="parquet"),
    FileSpec("weather", "weather_context.parquet", kind="parquet"),
    FileSpec("optical", "optical_check.gpkg", "optical_check_4326"),
    FileSpec("chips", "cache/chips", kind="dir", pattern="*.webp"),
    # vessels (3.2)
    FileSpec("ais_vessels", "ais_live.gpkg", "vessels_latest_4326"),
    FileSpec("ais_tracks", "ais_live.gpkg", "tracks_4326"),
    FileSpec("ais_about", "ais_live.gpkg", "about"),
    FileSpec("ais_summary", "ais_live_summary.json", kind="json"),
    FileSpec("ais_positions", "cache/ais/aisstream/positions", kind="dir", pattern="*/[0-9][0-9].parquet"),
    FileSpec("ais_static", "cache/ais/aisstream/static", kind="dir", pattern="*.parquet"),
    FileSpec("gfw_vessels", "research/gfw_vessels.parquet", kind="parquet", builds=RESEARCH),
    FileSpec("gfw_events_vessels", "research/gfw_events_vessels.parquet", kind="parquet", builds=RESEARCH),
    FileSpec("gfw_presence_passes", "research/gfw_presence_passes.parquet", kind="parquet", builds=RESEARCH),
    # lights (3.3)
    FileSpec("lights", "viirs_lights.gpkg", "viirs_lights_4326"),
    FileSpec("sites", "viirs_lights.gpkg", "viirs_sites_4326"),
    FileSpec("viirs_nights", "viirs_lights.gpkg", "viirs_nights"),
    # events (3.4)
    FileSpec("events_open", "events_open.gpkg", "events_4326", builds=OPEN),
    FileSpec("gfw_gaps", "research/gfw_events_gaps.parquet", kind="parquet", builds=RESEARCH),
    FileSpec("gfw_encounters", "research/gfw_events_encounters.parquet", kind="parquet", builds=RESEARCH),
    FileSpec("gfw_loitering", "research", kind="glob", pattern="gfw_events_loitering_part*of*.parquet", builds=RESEARCH),
    FileSpec("gfw_port_visits", "research", kind="glob", pattern="gfw_events_port_visits_part*of*.parquet", builds=RESEARCH),
    # leads (3.5)
    FileSpec("leads_open", "leads_open.gpkg", "leads_4326", builds=OPEN),
    FileSpec("leads_research", "research/leads_research.parquet", kind="parquet", builds=RESEARCH),
    FileSpec("leads_research_gpkg", "research/leads_research.gpkg", "leads_4326", builds=RESEARCH),
    FileSpec("decisions_open", "labels/lead_decisions.jsonl", kind="jsonl", builds=OPEN),
    FileSpec("decisions_research", "research/lead_decisions.jsonl", kind="jsonl", builds=RESEARCH),
    FileSpec("contact_labels", "labels/contact_labels.csv", kind="csv"),
    FileSpec("owner_labels", "labels/owner_2026-10.csv", kind="csv"),
    # passes (3.6)
    FileSpec("pass_plan", "s1_next_passes.json", kind="json", count_key="pass_groups"),
    FileSpec("pass_plan_layer", "ais_live.gpkg", "s1_next_passes_4326"),
    # cells (3.7)
    FileSpec("cells_static", "ocean_static_cells.parquet", kind="parquet"),
    FileSpec("cells_daily", "ocean_daily_cells.parquet", kind="parquet"),
    FileSpec("cells_pass", "ocean_radar_pass_cells.parquet", kind="parquet"),
    FileSpec("object_context", "ocean_context_objects.parquet", kind="parquet"),
    FileSpec("expected_activity", "expected_activity.parquet", kind="parquet"),  # scripts/34_expected_activity.py (R2-T6)
    FileSpec("radar_vs_gfw", "research/radar_vs_gfw.parquet", kind="parquet", builds=RESEARCH),
    FileSpec("ocean_static_summary", "ocean_static_summary.json", kind="json"),
    # geo and rasters
    FileSpec("aoi", "aoi.gpkg", "aoi_4326"),
    FileSpec("land", "raw/natural_earth/ne_10m_land.geojson", kind="geojson"),
    FileSpec("eez", "eez_marineregions.gpkg", "eez_4326"),
    FileSpec("eez_boundaries", "eez_marineregions.gpkg", "eez_boundaries_4326"),
    FileSpec("depth_contours", "ocean_context.gpkg", "depth_contours_4326"),
    FileSpec("ports", "ocean_context.gpkg", "ports_4326"),
    FileSpec("fronts", "ocean_fronts.gpkg", "fronts_4326"),
    FileSpec("footprints", "s1_footprints.gpkg", "s1_footprints_4326"),
    FileSpec("rasters", "outputs/small", kind="glob", pattern="*_4326.tif"),
    FileSpec("rasters_research", "research", kind="glob", pattern="gfw_*_4326.tif", builds=RESEARCH),
]


def _iso_mtime(ns: int | None) -> str | None:
    if ns is None:
        return None
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Catalog:
    def __init__(self, settings: Settings, specs: list[FileSpec] | None = None):
        self.settings = settings
        self.build = settings.build
        self.data_dir = settings.data_dir
        self._research = (self.data_dir / "research").resolve()
        self.specs: dict[str, FileSpec] = {}
        self.opened: list[str] = []  # every path this catalog opened (the tests audit it)
        self._rows: dict[tuple, int | None] = {}
        self._lock = threading.Lock()
        for s in specs if specs is not None else SPECS:
            if self.build in s.builds:
                self.guard(self.data_dir / s.rel)
                self.specs[s.key] = s

    # ------------------------------------------------------------ guard
    def guard(self, path) -> Path:
        """The resolved path, or ResearchPathError when the open build asks for anything under data/research/."""
        p = Path(path)
        p = (p if p.is_absolute() else self.data_dir / p).resolve()
        if self.build == "open" and (p == self._research or self._research in p.parents):
            raise ResearchPathError(f"open build refuses {p}: paths under data/research/ are research only (contract D2)")
        return p

    # ------------------------------------------------------------ paths and status
    def spec(self, key: str) -> FileSpec | None:
        return self.specs.get(key)

    def path(self, key: str) -> Path | None:
        s = self.specs.get(key)
        return None if s is None else self.guard(self.data_dir / s.rel)

    def paths(self, key: str) -> list[Path]:
        """Matching files of a glob or dir spec (sorted), or the one file of any other spec when it exists."""
        s = self.specs.get(key)
        if s is None:
            return []
        base = self.guard(self.data_dir / s.rel)
        if s.kind in ("glob", "dir"):
            if not base.is_dir():
                return []
            return sorted(self.guard(p) for p in base.glob(s.pattern or "*") if p.is_file())
        return [base] if base.exists() else []

    def exists(self, key: str) -> bool:
        s = self.specs.get(key)
        if s is None:
            return False
        if s.kind in ("glob", "dir"):
            return bool(self.paths(key))
        p = self.path(key)
        if not p.exists():
            return False
        if s.layer and s.kind == "gpkg":
            return s.layer in self._layers(p)
        return True

    def _layers(self, p: Path) -> set[str]:
        key = ("layers", str(p), p.stat().st_mtime_ns)
        if key not in self._rows:
            try:
                self._rows[key] = {str(n) for n, _ in pyogrio.list_layers(p)}
            except Exception:  # noqa: BLE001  (a corrupt file is reported missing, never an error)
                self._rows[key] = set()
        return self._rows[key]

    def mtime_ns(self, key: str) -> int | None:
        ps = self.paths(key)
        if not ps:
            return None
        if self.specs[key].kind in ("glob", "dir"):
            return max(p.stat().st_mtime_ns for p in ps) + len(ps)  # new or removed files change it too
        return ps[0].stat().st_mtime_ns

    def signature(self) -> dict[str, int | None]:
        return {k: self.mtime_ns(k) for k in self.specs}

    def rows(self, key: str) -> int | None:
        s = self.specs[key]
        if not self.exists(key):
            return None
        if s.kind in ("glob", "dir"):
            ps = self.paths(key)
            if s.kind == "glob" and ps and ps[0].suffix == ".parquet":
                return sum(pq.read_metadata(p).num_rows for p in ps)
            return len(ps)
        p = self.path(key)
        ck = (key, p.stat().st_mtime_ns)
        if ck in self._rows:
            return self._rows[ck]
        n = None
        try:
            if s.kind == "gpkg":
                n = int(pyogrio.read_info(p, layer=s.layer)["features"])
            elif s.kind == "parquet":
                n = pq.read_metadata(p).num_rows
            elif s.kind == "jsonl":
                n = sum(1 for line in p.open(encoding="utf-8") if line.strip())
            elif s.kind == "csv":
                n = max(0, sum(1 for line in p.open(encoding="utf-8") if line.strip()) - 1)
            elif s.kind == "json" and s.count_key:
                n = len(json.loads(p.read_text(encoding="utf-8")).get(s.count_key) or [])
        except Exception:  # noqa: BLE001
            n = None
        self._rows[ck] = n
        return n

    def entries(self) -> list[dict]:
        out = []
        for k, s in self.specs.items():
            ok = self.exists(k)
            out.append({"key": k, "path": "data/" + s.rel + (("/" + s.pattern) if s.pattern else ""), "layer": s.layer,
                        "status": "existing" if ok else "missing", "rows": self.rows(k) if ok else None,
                        "mtime": _iso_mtime(self.mtime_ns(k)) if ok else None})
        return out

    # ------------------------------------------------------------ readers (None when the file is missing)
    def _note(self, p: Path):
        with self._lock:
            self.opened.append(str(p))

    def read_gpkg(self, key: str, columns=None, geometry: bool = False, layer: str | None = None, where: str | None = None):
        s = self.specs.get(key)
        if s is None or not self.exists(key):
            return None
        p = self.path(key)
        self._note(p)
        df = pyogrio.read_dataframe(p, layer=layer or s.layer, columns=columns, read_geometry=geometry, use_arrow=True,
                                    where=where)
        return df

    def read_parquet(self, key: str, columns=None, path: Path | None = None) -> pd.DataFrame | None:
        if path is None:
            if not self.exists(key):
                return None
            path = self.path(key)
        path = self.guard(path)
        self._note(path)
        if columns is not None:
            have = set(pq.read_schema(path).names)
            columns = [c for c in columns if c in have]
        return pq.read_table(path, columns=columns).to_pandas()

    def read_arrow(self, key: str, columns=None, path: Path | None = None):
        """A parquet file as a pyarrow Table (for concatenating large parts before one conversion)."""
        path = self.guard(path if path is not None else self.path(key))
        if not path.exists():
            return None
        self._note(path)
        if columns is not None:
            have = set(pq.read_schema(path).names)
            columns = [c for c in columns if c in have]
        return pq.read_table(path, columns=columns)

    def read_glob_parquet(self, key: str, columns=None) -> pd.DataFrame | None:
        ps = self.paths(key)
        if not ps:
            return None
        return pd.concat([self.read_parquet(key, columns, path=p) for p in ps], ignore_index=True)

    def read_json(self, key: str):
        if not self.exists(key):
            return None
        p = self.path(key)
        self._note(p)
        return json.loads(p.read_text(encoding="utf-8"))

    def read_vector_file(self, key: str, bbox=None):
        """A GeoJSON (or any OGR file) spec as a GeoDataFrame, optionally clipped to a bbox."""
        if not self.exists(key):
            return None
        p = self.path(key)
        self._note(p)
        return pyogrio.read_dataframe(p, bbox=bbox, use_arrow=True)
