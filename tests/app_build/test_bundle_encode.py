"""Encoders of the single-file bundle (contract 6.2) round-trip through the Python decoder that mirrors columns.ts."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO / "app" / "build", _REPO / "app" / "backend", _REPO / "tests" / "app"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))  # appended: tests/app's conftest keeps the name `conftest`

import json

import numpy as np
import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from scs_bundle import encode as E


def rt(spec, n, ctx=None):
    return E.decode_column(json.loads(json.dumps(spec)), n, ctx)


@pytest.mark.parametrize("vals,s,t", [
    ([0, 1, 254, None], 1, "u8"),
    ([0, 255, None], 1, "u16"),
    ([0, 65534], 1, "u16"),
    ([-1, 300, None], 1, "i16"),
    ([0, 65535], 1, "u32"),
    ([-40000, 5], 1, "i32"),
    ([99.18291, 122.26101, None], 100000, "u32"),
    ([-3.18785, 23.73402], 100000, "i32"),
    ([20.0, 3727.2, None], 10, "u16"),
])
def test_narrowest_integer(vals, s, t):
    spec = E.num(vals, s, allow_dict=False)
    assert spec["t"] == t
    out = rt(spec, len(vals))
    for a, b in zip(vals, out):
        assert (a is None and b is None) or abs(a - b) <= E.tolerance(spec)


def test_scaled_values_decode_to_the_decimal():
    vals = [109.37482, 13.3152, 0.98, 1234.5]
    for v, s in zip(vals, [100000, 100000, 250, 10]):
        out = rt(E.num([v, None], s, allow_dict=False), 2)
        assert out[1] is None and abs(out[0] - v) <= 0.5 / s


def test_const_and_all_null():
    assert E.num([5, 5, 5])["t"] == "const"
    assert rt(E.num([5, 5]), 2) == [5, 5]
    assert E.num([None, None]) == {"t": "const", "v": None}
    assert E.num([5, None])["t"] != "const"  # a null breaks const


def test_numeric_dict_is_exact_and_chosen_when_smaller():
    vals = [-3600.0, 0.0, 3600.0, None] * 50
    spec = E.num(vals, 1)
    assert spec["t"] == "dict8" and spec["na"] == 255
    assert rt(spec, len(vals)) == [None if v is None else int(v) for v in vals]
    reach = [round(k / 34, 3) for k in range(35)] * 10
    spec = E.num(reach, 1000)
    assert spec["t"] == "dict8"
    assert rt(spec, len(reach)) == [E.plain(v) for v in reach]


def test_scale_must_be_integer():
    with pytest.raises(E.EncodeError):
        E.num([1.5, 2.5], 0.1, allow_dict=False)


def test_bool_dict_time():
    b = E.bool8([True, False, None, 1, 0])
    assert b["t"] == "bool8" and b["na"] == 255
    assert rt(b, 5) == [True, False, None, True, False]
    d = E.dict_col(["high", None, "fixed", "high"], ["high", "medium", "fixed", "low"])
    assert d["t"] == "dict8" and d["dict"][:4] == ["high", "medium", "fixed", "low"]
    assert rt(d, 4) == ["high", None, "fixed", "high"]
    big = E.dict_col([f"v{i}" for i in range(300)])
    assert big["t"] == "dict16" and rt(big, 300)[299] == "v299"
    t = E.time_col(["2026-09-20T10:48:16Z", None, "2026-10-08T23:00:43+00:00"])
    assert t["t"] == "time" and t["e"] == "2026-09-20T00:00:00Z" and t["na"] == E.TIME_NA
    assert rt(t, 3) == ["2026-09-20T10:48:16Z", None, "2026-10-08T23:00:43Z"]


def test_time_or_dict_decodes_to_the_same_text():
    vals = ["2026-10-11T22:35:08Z"] * 300 + ["2026-10-12T11:05:00Z", None]
    spec = E.time_or_dict(vals)
    assert spec["t"] == "dict8"
    assert rt(spec, len(vals)) == vals
    many = [f"2026-09-{1 + i % 28:02d}T{i % 24:02d}:{i % 60:02d}:00Z" for i in range(400)]
    spec = E.time_or_dict(many)
    assert spec["t"] == "time"
    assert rt(spec, len(many)) == many


def test_detid_mixed_widths_and_fallback():
    ids = ["S1C_20260920T104816_00005", "S1D_20260929T224413_0012", "live_X_00001", "S1C_20260920T104816_00999"]
    spec = E.detid(ids)
    assert spec["t"] == "detid" and spec["w"] == [5, 4, 5]
    assert rt(spec, 4) == ids
    assert E.detid(["noindex"])["t"] == "str"


def test_lightid_rebuilt_from_satellite_and_time():
    ids = ["SPP_20260929T183012_000017", "N21_20261001T174500_123456"]
    sats = ["S-NPP", "NOAA-21"]
    times = ["2026-09-29T18:30:12Z", "2026-10-01T17:45:00Z"]
    part = E.part("lights", 2, {"light_id": E.lightid(ids, sats, times), "satellite": E.dict_col(sats),
                                "time_utc": E.time_col(times)})
    dec = E.decode_part(json.loads(json.dumps(part)))
    assert dec["light_id"] == ids
    assert E.lightid(["SPP_20260929T183012_000017"], ["NOAA-20"], times[:1])["t"] == "str"


def test_ref16():
    spec = E.ref16([0, None, 7, -1], "vessels")
    assert spec["to"] == "vessels" and spec["na"] == 65535
    assert rt(spec, 4) == [0, None, 7, None]
    with pytest.raises(E.EncodeError):
        E.ref16([70000], "vessels")


def test_geom_round_trip():
    poly = Polygon([(100, 10), (101, 10), (101, 11), (100, 10)], [[(100.2, 10.2), (100.4, 10.2), (100.3, 10.4)]])
    multi = MultiPolygon([poly, Polygon([(110, 5), (111, 5), (111, 6)])])
    g = json.loads(json.dumps(E.geom([poly, multi], "polygon", props={"name": E.strs(["a", "b"])})))
    feats = E.decode_geom(g)
    assert len(feats) == 2 and len(feats[0]) == 2 and len(feats[1]) == 3
    assert feats[0][0] == [(100.0, 10.0), (101.0, 10.0), (101.0, 11.0)]  # closing vertex dropped
    assert E.decode_column(g["props"]["name"], 2) == ["a", "b"]
    lines = E.decode_geom(E.geom([LineString([(100.12345, 1.5), (100.5, 2)])], "line"))
    assert lines[0][0][0] == (100.1235, 1.5) or lines[0][0][0] == (100.1234, 1.5)
    pts = E.decode_geom(E.geom([Point(105.25, 9.5), Point(106, 10)], "point"))
    assert pts[1][0][0] == (106.0, 10.0)


def test_element_escapes_script_end():
    el = E.element("meta", {"x": "</script><!-- a"})
    body = el[el.index(">") + 1: el.rindex("</script>")]
    assert "</" not in body and "<!--" not in body
    assert json.loads(body) == {"x": "</script><!-- a"}
    assert el.startswith('<script type="application/json" id="scs-part-meta">')
    assert E.element_bytes("meta", {"x": "é"}) == len(E.element("meta", {"x": "é"}).encode("utf-8"))


def test_decoder_matches_typed_array_widths():
    # every typed column decodes exactly n values (the frontend views the buffer as the type in `t`)
    for t, vals in [("u8", [1, 2]), ("u16", [300, 2]), ("i16", [-300, 2]), ("u32", [70000, 2]), ("i32", [-70000, 2])]:
        spec = E.num(vals, 1, allow_dict=False, allow_const=False)
        assert spec["t"] == t
        raw = np.frombuffer(__import__("base64").b64decode(spec["b"]), dtype=E.INT_TYPES[t][0])
        assert len(raw) == 2
