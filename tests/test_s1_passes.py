"""Offline tests for Sentinel-1 pass prediction (darkvessel.ais.s1_passes): repeat cycle, acquisition-plan KML parsing,
newest-plan-wins selection, plan links on the ESA page, pass grouping, AOI part names and atomic NaN-free JSON.

No network: KMLs and the plan page are small synthetic strings in the ESA layout.
"""

from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

import darkvessel  # noqa: F401  (sets PROJ_DATA before rasterio/pyogrio)
from darkvessel.ais import s1_passes


def _scenes():
    rows = []
    base = pd.Timestamp("2026-09-02 10:00:00", tz="UTC")
    for cyc in range(3):  # S1D relative orbit 55, ascending, three cycles 12 days apart, two scenes per pass
        t = base + pd.Timedelta(days=12 * cyc)
        for i in range(2):
            rows.append({"product_id": f"S1D_{cyc}_{i}", "mission": "S1D", "orbit_rel": 55, "pass_dir": "ASCENDING",
                         "start_utc": t + pd.Timedelta(seconds=25 * i), "stop_utc": t + pd.Timedelta(seconds=25 * i + 25)})
    # S1C relative orbit 69 with a drifted pass (300 s off) in the middle
    base_c = pd.Timestamp("2026-09-03 22:00:00", tz="UTC")
    for cyc, off in enumerate([0, 300, 0]):
        t = base_c + pd.Timedelta(days=12 * cyc, seconds=off)
        rows.append({"product_id": f"S1C_{cyc}", "mission": "S1C", "orbit_rel": 69, "pass_dir": "DESCENDING",
                     "start_utc": t, "stop_utc": t + pd.Timedelta(seconds=25)})
    # a stale orbit last seen long ago: must not be predicted
    rows.append({"product_id": "S1C_old", "mission": "S1C", "orbit_rel": 3, "pass_dir": "DESCENDING",
                 "start_utc": pd.Timestamp("2026-06-01 22:30:00", tz="UTC"), "stop_utc": pd.Timestamp("2026-06-01 22:30:25", tz="UTC")})
    return pd.DataFrame(rows)


def test_passes_and_repeat_check():
    passes = s1_passes.passes_from_scenes(_scenes())
    assert len(passes) == 7
    p55 = passes[(passes.mission == "S1D") & (passes.orbit_rel == 55)]
    assert len(p55) == 3 and (p55.n_scenes == 2).all()
    chk = s1_passes.repeat_check(passes, tol_s=120)
    assert chk["pairs"] == 4 and chk["share_within_tolerance"] == 0.5 and chk["max_abs_drift_s"] == 300
    assert any("rel 69" in o for o in chk["outliers"])


def test_predict_passes_window():
    passes = s1_passes.passes_from_scenes(_scenes())
    now = pd.Timestamp("2026-10-08 00:00:00", tz="UTC")  # last S1D pass 2026-09-26 10:00 -> next 2026-10-08 10:00
    pred = s1_passes.predict_passes(passes, now, horizon_h=72)
    assert list(pred.mission) == ["S1D", "S1C"]
    d = pred[pred.mission == "S1D"].iloc[0]
    assert d.start_utc == pd.Timestamp("2026-10-08 10:00:00", tz="UTC") and d.cycles_ahead == 1 and d.n_scenes == 2
    assert d.stop_utc == pd.Timestamp("2026-10-08 10:00:50", tz="UTC") and d.product_ids == ["S1D_2_0", "S1D_2_1"]
    c = pred[pred.mission == "S1C"].iloc[0]
    assert c.start_utc == pd.Timestamp("2026-10-09 22:00:00", tz="UTC") and c.source == "repeat_cycle"
    assert not (pred.orbit_rel == 3).any()  # stale orbit skipped
    # a window that starts after the next repeat gets the one after
    pred2 = s1_passes.predict_passes(passes, pd.Timestamp("2026-10-09 00:00:00", tz="UTC"), horizon_h=24 * 12)
    assert pred2[pred2.mission == "S1D"].start_utc.iloc[0] == pd.Timestamp("2026-10-20 10:00:00", tz="UTC")
    assert pred2[pred2.mission == "S1D"].cycles_ahead.iloc[0] == 2


def _kml(placemarks: list[tuple[str, str, int, tuple]]) -> str:
    pms = []
    for begin, end, rel, (w, s, e, n) in placemarks:
        pms.append(f"""<Placemark><name>{rel}-IW</name><TimeSpan><begin>{begin}</begin><end>{end}</end></TimeSpan>
<ExtendedData><Data name="Mode"><value>IW</value></Data><Data name="OrbitAbsolute"><value>{12000 + rel}</value></Data>
<Data name="OrbitRelative"><value>{rel}</value></Data><Data name="Polarisation"><value>DV</value></Data></ExtendedData>
<Polygon><outerBoundaryIs><LinearRing><coordinates>{w},{s},0 {e},{s},0 {e},{n},0 {w},{n},0 {w},{s},0</coordinates></LinearRing></outerBoundaryIs></Polygon>
</Placemark>""")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>S1D_MP_USER</name>'
            + "".join(pms) + "</Document></kml>")


def test_parse_acquisition_kml(tmp_path):
    path = tmp_path / "plan.kml"
    path.write_text(_kml([("2026-10-08T22:40:00", "2026-10-08T22:41:30", 62, (108.0, 10.0, 110.5, 15.0))]))
    g = s1_passes.parse_acquisition_kml(path)
    assert len(g) == 1 and g.iloc[0]["mode"] == "IW" and int(g.iloc[0]["orbitrelative"]) == 62
    assert g.iloc[0].begin_utc == pd.Timestamp("2026-10-08 22:40:00", tz="UTC") and g.geometry.iloc[0].bounds == (108.0, 10.0, 110.5, 15.0)
    hits = s1_passes.plan_passes_over(g, box(100, 0, 120, 20), pd.Timestamp("2026-10-08", tz="UTC"), pd.Timestamp("2026-10-11", tz="UTC"), "S1D")
    assert len(hits) == 1 and hits.iloc[0].source == "esa_plan" and hits.iloc[0].mission == "S1D"
    none = s1_passes.plan_passes_over(g, box(0, 0, 10, 10), pd.Timestamp("2026-10-08", tz="UTC"), pd.Timestamp("2026-10-11", tz="UTC"), "S1D")
    assert len(none) == 0


def test_plan_file_window():
    m, a, b = s1_passes.plan_file_window("s1d_mp_user_20261008t182319_20261015t200700.kml")
    assert m == "S1D" and a == pd.Timestamp("2026-10-08 18:23:19", tz="UTC") and b == pd.Timestamp("2026-10-15 20:07:00", tz="UTC")
    assert s1_passes.plan_file_window("https://x/documents/d/sentinel/s1c_mp_user_20261005t174134_20261027t194000")[0] == "S1C"
    assert s1_passes.plan_file_window("Sentinel-1B_MP_20220114T160000_20220203T180000.kml") is None


def test_newest_plan_wins_inside_its_window(tmp_path):
    old = tmp_path / "s1d_mp_user_20261007t000000_20261027t000000.kml"
    new = tmp_path / "s1d_mp_user_20261008t000000_20261015t000000.kml"
    # the old plan has segments on 10-09 (inside the new window, dropped there) and 10-20 (outside: kept)
    old.write_text(_kml([("2026-10-09T11:00:00", "2026-10-09T11:05:00", 171, (100, 0, 105, 10)),
                         ("2026-10-20T11:00:00", "2026-10-20T11:05:00", 171, (100, 0, 105, 10))]))
    # the new plan moved the 10-09 segment by 20 min
    new.write_text(_kml([("2026-10-09T11:20:00", "2026-10-09T11:25:00", 171, (100, 0, 105, 10))]))
    sel = s1_passes.select_plan_segments([(old.name, s1_passes.parse_acquisition_kml(old)),
                                          (new.name, s1_passes.parse_acquisition_kml(new))])
    assert list(sel.begin_utc.dt.strftime("%m-%d %H:%M")) == ["10-09 11:20", "10-20 11:00"]
    assert list(sel.plan_file) == [new.name, old.name] and set(sel.mission) == {"S1D"}
    assert s1_passes.select_plan_segments([]).empty


def test_plan_links_and_files_needed():
    html = """<a href="/documents/d/sentinel/s1d_mp_user_20261002t181009_20261022t201800">a</a>
<a href="/documents/d/sentinel/s1d_mp_user_20261007t190903_20261027t210500">b</a>
<a href="https://documents/d/sentinel/s1d_mp_user_20261008t182319_20261015t200700">c</a>
<a href="/documents/d/sentinel/s1c_mp_user_20261008t180959_20261030t202000?download=true">d</a>
<a href="/documents/d/sentinel/s1a_mp_user_20260625t173940_20260630t194000">old S1A</a>
<a href="/web/sentinel/other">not a plan</a>"""
    links = s1_passes.plan_links(html)
    assert len(links) == 5 and all(u.startswith("https://sentinels.copernicus.eu/documents/d/sentinel/") for u in links)
    assert not any("?" in u for u in links)
    need = s1_passes.files_needed(links, pd.Timestamp("2026-10-08 23:00", tz="UTC"), pd.Timestamp("2026-10-20 23:00", tz="UTC"))
    names = [u.rsplit("/", 1)[1] for u in need]
    # S1D: the 10-08 file ends 10-15, so the 10-07 file is needed for the tail; the 10-02 file is not
    assert names == ["s1c_mp_user_20261008t180959_20261030t202000", "s1d_mp_user_20261008t182319_20261015t200700",
                     "s1d_mp_user_20261007t190903_20261027t210500"]
    # a window that starts before every S1D file but the oldest needs the oldest too
    need2 = s1_passes.files_needed(links, pd.Timestamp("2026-10-05", tz="UTC"), pd.Timestamp("2026-10-20", tz="UTC"), missions=("S1D",))
    assert len(need2) == 3 and need2[-1].endswith("20261002t181009_20261022t201800")


def test_group_passes_one_satellite_one_pass():
    df = pd.DataFrame({
        "mission": ["S1D", "S1D", "S1D", "S1D", "S1C"],
        "orbit_rel": [69, 70, 69, 25, 69],
        "source": ["esa_plan", "repeat_cycle", "esa_plan", "esa_plan", "esa_plan"],
        "start_utc": ["2026-10-14T11:31:06Z", "2026-10-14T11:34:35Z", "2026-10-14T10:00:24Z", "2026-10-11T11:05:46Z",
                      "2026-10-14T11:32:00Z"],
        "stop_utc": ["2026-10-14T11:43:42Z", "2026-10-14T11:36:15Z", "2026-10-14T10:05:27Z", "2026-10-11T11:07:30Z",
                     "2026-10-14T11:33:00Z"]})
    g = s1_passes.group_passes(df)
    assert g[0] == g[1] == "S1D_R069_20261014T1131"  # relative orbit changes inside the take: still one pass
    assert g[2] == "S1D_R069_20261014T1000" and g[2] != g[0]  # one orbit (about 100 min) earlier: another pass
    assert g[4].startswith("S1C_") and g[4] != g[0]  # another satellite at the same time is another pass
    assert g.nunique() == 4


def test_area_names_and_json_clean(tmp_path):
    parts = gpd.GeoDataFrame({"name": ["Gulf of Thailand", "South China Sea"]},
                             geometry=[box(99, 6, 105, 14), box(105, 0, 120, 23)], crs="EPSG:4326")
    assert s1_passes.area_names(box(103, 8, 108, 9), parts) == ["South China Sea", "Gulf of Thailand"]  # larger first
    assert s1_passes.area_names(box(104.999, 8, 105.0005, 8.001), parts) == []  # slivers under 100 km2 are ignored
    obj = {"a": np.nan, "b": [1.5, float("inf"), pd.NA, pd.NaT], "c": np.int64(3), "d": np.bool_(True),
           "t": pd.Timestamp("2026-10-08 22:58:28", tz="UTC")}
    path = tmp_path / "passes.json"
    s1_passes.write_json_atomic(path, obj)
    back = json.loads(path.read_text())
    assert back == {"a": None, "b": [1.5, None, None, None], "c": 3, "d": True, "t": "2026-10-08T22:58:28Z"}
    assert "NaN" not in path.read_text() and not list(tmp_path.glob(".passes.json.tmp*"))


def test_write_json_atomic_never_leaves_half_a_file(tmp_path, monkeypatch):
    path = tmp_path / "s1_next_passes.json"
    s1_passes.write_json_atomic(path, {"n_passes": 1})

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(s1_passes.os, "replace", boom)
    with pytest.raises(OSError):
        s1_passes.write_json_atomic(path, {"n_passes": 2})
    assert json.loads(path.read_text()) == {"n_passes": 1}  # readers still see the old, complete file
    assert not list(tmp_path.glob(".s1_next_passes.json.tmp*"))  # and the temp file is cleaned up
