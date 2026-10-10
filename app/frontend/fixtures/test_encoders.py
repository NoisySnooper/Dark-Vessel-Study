"""Offline round-trip tests of the contract 6.2 encoders in make_fixture.py (no data files, no network).

Usage: /home/user/.mamba/envs/darkvessel/bin/python -m pytest -q app/frontend/fixtures/test_encoders.py
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_fixture as mf  # noqa: E402

DTYPES = {"i32": "<i4", "u32": "<u4", "i16": "<i2", "u16": "<u2", "u8": "u1", "f32": "<f4", "bool8": "u1", "dict8": "u1",
          "dict16": "<u2", "time": "<u4", "ref16": "<u2"}


def decode(col):
    """Decode a column as the frontend does (src/adapters/columns.ts)."""
    t = col["t"]
    if t == "const":
        return col["v"]
    if t == "str":
        return col["v"]
    arr = np.frombuffer(base64.b64decode(col["b"]), dtype=DTYPES[t])
    na = col.get("na")
    s = col.get("s", 1)
    if t in ("dict8", "dict16"):
        return [None if v == na else col["dict"][v] for v in arr]
    if t == "bool8":
        return [None if v == na else bool(v) for v in arr]
    if t == "time":
        import pandas as pd
        e = pd.Timestamp(col["e"])
        return [None if v == na else (e + pd.Timedelta(seconds=int(v))).strftime("%Y-%m-%dT%H:%M:%SZ") for v in arr]
    if t == "f32":
        return [None if np.isnan(v) else float(v) for v in arr]
    if t == "ref16":
        return [None if v == na else int(v) for v in arr]
    return [None if (na is not None and v == na) else v / s for v in arr]


def test_num_picks_narrowest_type_and_round_trips():
    c = mf.col_num([0, 1.5, 25.4, None], 10)
    assert c["t"] == "u8" and c["na"] == 255  # 254 scaled still fits u8
    assert decode(c) == [0.0, 1.5, 25.4, None]
    c = mf.col_num([0, 1.5, 30.0, None], 10)
    assert c["t"] == "u16" and c["na"] == 65535
    assert decode(c) == [0.0, 1.5, 30.0, None]
    c = mf.col_num([0, 200, None], 1)
    assert c["t"] == "u8" and decode(c) == [0, 200, None]
    c = mf.col_num([-12.3, 30.0], 10)
    assert c["t"] == "i16" and decode(c) == [-12.3, 30.0]
    c = mf.col_num([176391.1], 1)
    assert c["t"] == "u32" and decode(c) == [176391.0]
    c = mf.col_num([-3.2, 123.45678], 100000)
    assert c["t"] == "i32" and decode(c) == pytest.approx([-3.2, 123.45678])
    c = mf.col_num([1.5, None], prefer="f32")
    assert c["t"] == "f32" and decode(c) == [1.5, None]


def test_dict_bool_time_str_const():
    c = mf.col_dict(["high", None, "fixed"], ["high", "medium", "fixed", "low"])
    assert c["t"] == "dict8" and decode(c) == ["high", None, "fixed"]
    big = [f"v{i}" for i in range(300)]
    c = mf.col_dict(big)
    assert c["t"] == "dict16" and decode(c) == big
    c = mf.col_bool([True, False, None])
    assert decode(c) == [True, False, None]
    c = mf.col_time(["2026-10-08T23:00:43+00:00", None, "2026-09-01T00:00:00Z"])
    assert decode(c) == ["2026-10-08T23:00:43Z", None, "2026-09-01T00:00:00Z"]
    assert mf.col_str(["a", None, float("nan"), ""])["v"] == ["a", None, None, None]
    assert mf.col_const(False)["v"] is False


def test_detid_and_lightid_rebuild():
    ids = ["S1D_20261008T230043_00005", "S1C_20260920T104816_00051", "S1D_20260929T1110_0002"]
    c = mf.col_detid(ids)
    p = np.frombuffer(base64.b64decode(c["p"]), dtype="<u2")
    q = np.frombuffer(base64.b64decode(c["q"]), dtype="<u4")
    rebuilt = [c["pfx"][pi] + "_" + str(qi).zfill(c["w"][pi]) for pi, qi in zip(p, q)]
    assert rebuilt == ids
    lids = ["SPP_20260906T170505_009581", "N20_20260907T180000_000001"]
    c = mf.col_lightid(lids, ["S-NPP", "NOAA-20"], ["2026-09-06T17:05:05Z", "2026-09-07T18:00:00Z"])
    q = np.frombuffer(base64.b64decode(c["q"]), dtype="<u4")
    assert list(q) == [9581, 1]
    with pytest.raises(AssertionError):
        mf.col_lightid(["SPP_20260906T170505_009581"], ["NOAA-20"], ["2026-09-06T17:05:05Z"])


def test_ref16_and_geom_encoding():
    c = mf.col_ref16([3, -1, None, 0])
    assert decode(c) == [3, None, None, 0]
    import geopandas as gpd
    from shapely.geometry import LineString, Polygon, box
    outer = box(100, 5, 101, 6)
    hole = Polygon([(100.2, 5.2), (100.4, 5.2), (100.4, 5.4), (100.2, 5.4)])
    poly = Polygon(outer.exterior.coords, [hole.exterior.coords])
    g = mf.geom_part(gpd.GeoDataFrame(geometry=[poly, box(102, 7, 103, 8)], crs="EPSG:4326"), "polygon")
    xy = np.frombuffer(base64.b64decode(g["xy"]), dtype="<i4")
    ring = np.frombuffer(base64.b64decode(g["ring"]), dtype="<u4")
    feat = np.frombuffer(base64.b64decode(g["feat"]), dtype="<u4")
    assert g["kind"] == "polygon" and g["n"] == 2
    assert list(feat) == [0, 2]  # feature 0 has the outer ring and the hole, feature 1 one ring
    assert list(ring) == [0, 4, 8]  # closing vertices dropped: 4 + 4 + 4
    assert xy[0] == 1010000 and xy[1] == 50000  # box(100, 5, 101, 6) starts at (101, 5) in shapely order
    lines = mf.geom_part(gpd.GeoDataFrame(geometry=[LineString([(100, 5), (101, 6), (102, 7)])], crs="EPSG:4326"), "line")
    assert list(np.frombuffer(base64.b64decode(lines["ring"]), dtype="<u4")) == [0]
    assert len(np.frombuffer(base64.b64decode(lines["xy"]), dtype="<i4")) == 6


def test_region_box_and_cell_id():
    assert mf.region_box(102.0, 10.0) == "Gulf of Thailand"
    assert mf.region_box(120.0, 0.0) == "other"
    assert mf.cell_id(99.0, 24.0) == "r0c0"
    assert mf.cell_id(104.9, 8.6) == "r61c23"
