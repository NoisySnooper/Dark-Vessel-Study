"""Ocean context at objects (board D5.3) and expected activity per cell (board D5.4), contract 1.3.0.

Object context comes from `data/ocean_context_objects.parquet` (scripts/25_object_context.py), keyed by
(`object_type`, `object_id`): `radar` and `radar_detail` rows belong to contacts, `viirs` rows to lights. Each field is
returned as `{value, unit, time, src}`: `time` and `src` come from the table's `*_time` and `*_source` / `*_dataset`
columns where it has them, otherwise `src` is the registry key of the producing layer and `time` is null (static
layers). Expected activity comes from `data/expected_activity.parquet` (scripts/34_expected_activity.py): the tested rows
of a cell, newest first, per target.
"""

from __future__ import annotations

import threading

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from ..records import iso_series, iso_z, prov_time

PRESENCE_UNIT = "presence as published, not a count"  # board D4.3: World Bank/IMF shipping density, presence only
# name: (unit, time column, source column or None, registry key used when the table names no source, decimals)
CONTEXT_FIELDS = {
    "depth_m": ("m", None, None, "gebco_2026", 1),
    "dist_coast_km": ("km", None, None, "natural_earth", 2),
    "dist_port_km": ("km", None, None, "wpi", 2),
    "ship_presence_all": (PRESENCE_UNIT, None, None, "worldbank_density", None),
    "ship_presence_commercial": (PRESENCE_UNIT, None, None, "worldbank_density", None),
    "ship_presence_fishing": (PRESENCE_UNIT, None, None, "worldbank_density", None),
    "ship_presence_oilgas": (PRESENCE_UNIT, None, None, "worldbank_density", None),
    "ship_presence_passenger": (PRESENCE_UNIT, None, None, "worldbank_density", None),
    "ship_presence_leisure": (PRESENCE_UNIT, None, None, "worldbank_density", None),
    "sst_c": ("degC", "sst_time", "sst_source", "mur_sst", 3),
    "sst_grad": ("degC/km", "sst_time", "sst_source", "mur_sst", 5),
    "dist_front_km": ("km", "sst_time", "sst_source", "mur_sst", 2),
    "chl_log10": ("log10 mg m-3", "chl_time", "chl_dataset", "chl_dineof", 3),
    "current_speed_ms": ("m/s", "current_time", None, "rtofs", 3),
    "mld_m": ("m", "current_time", None, "rtofs", 2),
    "wave_hs_m": ("m", "wave_time", None, "gfs_wave", 2),
}
TIME_COLS = ["sst_time", "chl_time", "current_time", "wave_time"]
SRC_COLS = ["sst_source", "chl_dataset"]
CONTEXT_COLUMNS = ["object_type", "object_id", "time_utc", "cell_id", "region", *CONTEXT_FIELDS, *TIME_COLS, *SRC_COLS, "caveat"]
CONTACT_TYPES = {"camau": ("radar_detail",)}  # contact _source to object types; every other source: radar
LIGHT_TYPES = ("viirs",)
EXPECTED_ROW = ["unit_id", "night", "time_start_utc", "time_end_utc", "tested", "observed", "expected", "z", "q_bh", "flag",
                "flag_robust", "calm", "exposure_km2"]  # board D5.4, in its order
EXPECTED_COLUMNS = ["target", "row", "col", *EXPECTED_ROW]
EXPECTED_ROUND = {"expected": 4, "z": 3, "exposure_km2": 2}


def _null(x) -> bool:
    return x is None or x is pd.NA or x is pd.NaT or (isinstance(x, float) and np.isnan(x))


def time_text(v) -> str | None:
    """A valid time as the table wrote it, normalised to ISO 8601 UTC with Z when it is a date-time; dates stay dates
    (chlorophyll is daily). Text that is neither is None (`records.prov_time`)."""
    if _null(v):
        return None
    if isinstance(v, (pd.Timestamp, np.datetime64)):
        return iso_z(v)
    return prov_time(v)


def _plain(a: np.ndarray, decimals: int | None) -> list:
    """Numeric or boolean values as JSON-ready Python values (NaN to None, rounded)."""
    if a.dtype == bool:
        return [bool(x) for x in a]
    if a.dtype == object:  # nullable booleans read as object (None, NaN or pd.NA for a missing value)
        return [None if _null(x) else bool(x) for x in a]
    f = a.astype(float)
    if decimals is not None:
        f = np.round(f, decimals)
    return [None if not np.isfinite(x) else float(x) for x in f]


class ContextData:
    """Object context rows and expected-activity rows, with the indexes the records need."""

    def __init__(self, objects: pd.DataFrame | None, caveat: str | None, expected: pd.DataFrame | None,
                 model_id: str | None, ea_caveat: str | None):
        self.objects = objects
        self.caveat = caveat
        self.expected = expected
        self.model_id = model_id
        self.ea_caveat = ea_caveat
        self.targets = [] if expected is None else sorted(expected["target"].astype(str).unique())
        self._lock = threading.Lock()
        self._index: dict[str, tuple[pd.Index, np.ndarray]] | None = None  # built on first use
        self._ea_index: dict | None = None

    @property
    def index(self) -> dict[str, tuple[pd.Index, np.ndarray]]:
        """object_type to (object ids, their row positions), built once on first use."""
        if self._index is None:
            with self._lock:
                if self._index is None:
                    out = {}
                    if self.objects is not None and len(self.objects):
                        ids = self.objects["object_id"].to_numpy()
                        for t, pos in self.objects.groupby("object_type", observed=True, sort=False).indices.items():
                            out[str(t)] = (pd.Index(ids[pos]), np.asarray(pos))
                    self._index = out
        return self._index

    @property
    def ea_index(self) -> dict:
        """(row, col) to the cell's expected-activity row positions, built once on first use."""
        if self._ea_index is None:
            with self._lock:
                if self._ea_index is None:
                    e = self.expected
                    self._ea_index = {} if e is None or not len(e) else \
                        {(int(k[0]), int(k[1])): v for k, v in e.groupby(["row", "col"], sort=False).indices.items()}
        return self._ea_index

    # ------------------------------------------------------------ object context (D5.3)
    def positions(self, object_types, ids) -> np.ndarray:
        """Row positions in `objects` for each id (-1 when no row), trying the object types in order."""
        ids = pd.Index([str(i) for i in ids])
        out = np.full(len(ids), -1, dtype=np.int64)
        index = self.index
        for t in object_types:
            ix = index.get(t)
            if ix is None:
                continue
            todo = out < 0
            if not todo.any():
                break
            ix, base = ix
            hit = ix.get_indexer(ids[todo])
            pos = np.where(hit >= 0, base[np.maximum(hit, 0)], -1)
            out[np.flatnonzero(todo)] = pos
        return out

    def records(self, object_types, ids) -> list[dict | None]:
        """`object_context` for each id: the D5.3 shape, or None when the table has no row for it."""
        pos = self.positions(object_types, ids)
        out: list[dict | None] = [None] * len(pos)
        have = np.flatnonzero(pos >= 0)
        if not len(have):
            return out
        sub = self.objects.iloc[pos[have]]
        times = {c: [time_text(x) for x in sub[c].tolist()] for c in TIME_COLS if c in sub}
        srcs = {c: [None if _null(x) else str(x) for x in sub[c].tolist()] for c in SRC_COLS if c in sub}
        vals = {f: _plain(sub[f].to_numpy(), spec[4]) for f, spec in CONTEXT_FIELDS.items() if f in sub}
        t_utc = iso_series(sub["time_utc"]).tolist() if "time_utc" in sub else [None] * len(sub)
        cells = sub["cell_id"].tolist() if "cell_id" in sub else [None] * len(sub)
        regions = sub["region"].astype(object).tolist() if "region" in sub else [None] * len(sub)
        cav = sub["caveat"].astype(object).tolist() if "caveat" in sub else [None] * len(sub)
        for j, i in enumerate(have):
            fields = {}
            for f, (unit, tcol, scol, key, _) in CONTEXT_FIELDS.items():
                if f not in vals:
                    continue
                src = (srcs.get(scol) or [None] * len(sub))[j] if scol else None
                fields[f] = {"value": vals[f][j], "unit": unit, "time": times[tcol][j] if tcol in times else None,
                             "src": src or key}
            out[i] = {"time_utc": t_utc[j], "cell_id": cells[j], "region": None if regions[j] is None else str(regions[j]),
                      "fields": fields, "caveat": self._caveat(cav[j])}
        return out

    def _caveat(self, row_caveat) -> str:
        """OCEAN_CAVEAT, followed by the table's own object sentence when the table's caveat extends OCEAN_CAVEAT."""
        from darkvessel.ocean.grid import OCEAN_CAVEAT

        c = row_caveat if isinstance(row_caveat, str) and row_caveat else self.caveat
        return c if isinstance(c, str) and c.startswith(OCEAN_CAVEAT) else OCEAN_CAVEAT

    # ------------------------------------------------------------ expected activity (D5.4)
    def expected_for(self, row: int, col: int) -> dict | None:
        """The D5.4 block of one cell: tested rows per target, newest first; None when the cell has none."""
        idx = self.ea_index.get((int(row), int(col)))
        if idx is None or not len(idx):
            return None
        sub = self.expected.iloc[idx]
        targets: dict[str, list] = {t: [] for t in self.targets}
        cols = {c: sub[c].tolist() for c in EXPECTED_COLUMNS if c in sub and c not in ("row", "col")}
        for j in range(len(sub)):
            r = {c: cols[c][j] if c in cols else None for c in EXPECTED_ROW}
            for c in ("observed", "expected", "z", "q_bh", "exposure_km2"):
                v = r.get(c)
                r[c] = None if _null(v) or not np.isfinite(float(v)) else round(float(v), EXPECTED_ROUND.get(c, 8))
            r["tested"] = bool(r["tested"])
            r["calm"] = None if _null(r.get("calm")) else bool(r["calm"])
            r["flag"] = None if _null(r.get("flag")) else str(r["flag"])
            r["flag_robust"] = None if _null(r.get("flag_robust")) else str(r["flag_robust"])
            r["unit_id"] = str(r["unit_id"])
            r["night"] = str(r["night"])
            targets.setdefault(str(cols["target"][j]), []).append(r)
        return {"model_id": self.model_id, "caveat": self.ea_caveat or "", "targets": targets}


def _meta(schema: pa.Schema, key: str) -> str | None:
    md = schema.metadata or {}
    v = md.get(key.encode())
    return v.decode("utf-8") if v is not None else None


def load(cat, settings) -> ContextData:
    from darkvessel.ocean.grid import OCEAN_CAVEAT

    objects, caveat = None, None
    if cat.exists("object_context"):
        tb = cat.read_arrow("object_context", CONTEXT_COLUMNS)
        objects = tb.to_pandas() if {"object_type", "object_id"} <= set(tb.column_names) else None
    if objects is not None and "caveat" in objects and len(objects):
        caveat = str(objects["caveat"].iloc[0])
    expected, model_id, ea_caveat = None, None, None
    if cat.exists("expected_activity"):
        path = cat.path("expected_activity")
        tb = cat.read_arrow("expected_activity", EXPECTED_COLUMNS)
        model_id, ea_caveat = _meta(tb.schema, "model_id"), _meta(tb.schema, "caveat")
        if not {"target", "row", "col", "night", "unit_id"} <= set(tb.column_names):
            tb = None  # not the scripts/34 layout: no rows to serve (the file still shows in /meta)
        elif "tested" in tb.column_names:
            tb = tb.filter(pc.equal(tb["tested"], True))
        expected = tb.to_pandas() if tb is not None else None
    if expected is not None:
        for c in ("time_start_utc", "time_end_utc"):
            if c in expected:
                expected[c] = iso_series(expected[c])
        # newest first within each cell and target: time_start_utc, then night, then unit_id (descending)
        keys = [(c, a) for c, a in (("row", True), ("col", True), ("target", True), ("time_start_utc", False),
                                    ("night", False), ("unit_id", False)) if c in expected]
        expected = expected.sort_values([c for c, _ in keys], ascending=[a for _, a in keys], na_position="last",
                                        kind="stable").reset_index(drop=True)
        if model_id is None or ea_caveat is None:  # older outputs: the JSON summary holds both
            js = path.with_suffix(".json")
            if js.exists():
                import json

                d = json.loads(cat.guard(js).read_text(encoding="utf-8"))
                model_id = model_id or d.get("model_id")
                ea_caveat = ea_caveat or d.get("caveat")
    if ea_caveat is not None and not ea_caveat.startswith(OCEAN_CAVEAT):
        ea_caveat = OCEAN_CAVEAT + " " + ea_caveat
    return ContextData(objects, caveat, expected, model_id, ea_caveat or (OCEAN_CAVEAT if expected is not None else None))
