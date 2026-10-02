"""Scene search, footprint, geocoding and calibration helpers (offline)."""

import datetime as dt
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box, shape

from darkvessel.io import write_dual_crs
from darkvessel.s1 import footprints as fp
from darkvessel.s1.aws import in_utc_windows, parse_product_id
from darkvessel.s1.grd import CalibrationLUT, Geocoder, NoiseLUT

FIX = Path(__file__).parent / "fixtures"
PID = "S1D_IW_GRDH_1SDV_20260929T111023_20260929T111053_004792_008FC8_A5CA"


def test_parse_product_id():
    m = parse_product_id(PID)
    assert m["mission"] == "S1D" and m["mode"] == "IW" and m["pol"] == "DV"
    assert m["start"] == dt.datetime(2026, 9, 29, 11, 10, 23)
    assert m["orbit"] == 4792
    with pytest.raises(ValueError):
        parse_product_id("S2A_MSIL1C_20260929T031021_N0511_R075_T48PVS")


def test_utc_windows_including_midnight_wrap():
    w = [(dt.time(10, 30), dt.time(11, 35)), (dt.time(23, 30), dt.time(0, 30))]
    assert in_utc_windows(dt.datetime(2026, 9, 29, 11, 10), w)
    assert in_utc_windows(dt.time(0, 10), w)
    assert not in_utc_windows(dt.time(12, 0), w)


def _record_from_fixture():
    info = json.loads((FIX / "productInfo_S1D_20260929T111023.json").read_text())
    rec = parse_product_id(info["id"])
    rec.update({"pass_dir": "ASCENDING", "orbit_rel": 26, "platform": "SENTINEL-1D", "pols": ["VV", "VH"],
                "path": info["path"], "geometry": shape(info["footprint"]), "source": "aws:sentinel-s1-l1c"})
    return rec


def test_records_to_gdf_from_real_product_info():
    gdf = fp.records_to_gdf([_record_from_fixture()])
    assert gdf.crs.to_epsg() == 4326
    assert gdf.geometry.iloc[0].is_valid
    row = gdf.iloc[0]
    assert row.mission == "S1D" and row.pols == "VV+VH" and row.pass_dir == "ASCENDING"
    # the real footprint covers Ca Mau's east coast
    assert gdf.geometry.iloc[0].contains(shape({"type": "Point", "coordinates": [105.5, 8.8]}))


def test_empty_records_give_empty_gdf():
    gdf = fp.records_to_gdf([])
    assert gdf.empty and gdf.crs.to_epsg() == 4326


def test_aoi_overlap_full_partial_disjoint():
    recs = []
    for i, geom in enumerate([box(100, 5, 110, 12), box(105, 7.5, 107, 9.8), box(120, 20, 121, 21)]):
        r = _record_from_fixture()
        r["geometry"] = geom
        r["product_id"] = f"fake{i}"
        recs.append(r)
    gdf = fp.add_aoi_overlap(fp.records_to_gdf(recs), box(103.5, 7.5, 106.0, 9.8))
    cov = gdf.aoi_coverage.tolist()
    assert cov[0] == pytest.approx(1.0, abs=1e-3)
    assert 0.3 < cov[1] < 0.5
    assert cov[2] == 0.0


def test_summarize_counts():
    a, b = _record_from_fixture(), _record_from_fixture()
    b["pass_dir"], b["start"] = "DESCENDING", dt.datetime(2026, 9, 25, 22, 45)
    s = fp.summarize(fp.records_to_gdf([a, b]))
    assert s["count"] == 2 and s["by_pass"] == {"ASCENDING": 1, "DESCENDING": 1}
    assert s["unique_dates"] == ["2026-09-25", "2026-09-29"]


def test_stac_items_mapping_normalises_case():
    geom = {"type": "Polygon", "coordinates": [[[104, 8], [106, 8], [106, 10], [104, 10], [104, 8]]]}
    pc_style = {"id": PID, "geometry": geom, "links": [], "properties": {
        "platform": "SENTINEL-1D", "sat:orbit_state": "ascending", "sat:relative_orbit": 26,
        "sar:polarizations": ["VV", "VH"], "sar:instrument_mode": "IW"}}
    cdse_style = {"id": PID + ".SAFE", "geometry": geom, "links": [], "properties": {
        "platform": "sentinel-1d", "sat:orbit_state": "ASCENDING", "sat:relative_orbit": 26,
        "sar:polarizations": ["vv", "vh"]}}
    for it in (pc_style, cdse_style):
        rec = fp.stac_items_to_records([it], "test")[0]
        assert rec["platform"] == "SENTINEL-1D" and rec["pass_dir"] == "ASCENDING"
        assert rec["pols"] == ["VV", "VH"] and rec["product_id"] == PID


def test_write_dual_crs(tmp_path):
    gdf = fp.records_to_gdf([_record_from_fixture()])
    layers = write_dual_crs(gdf, tmp_path / "f.gpkg", "s1_footprints")
    assert layers == ["s1_footprints_4326", "s1_footprints_utm48n"]
    assert gpd.read_file(tmp_path / "f.gpkg", layer=layers[1]).crs.to_epsg() == 32648


ANNOT = """<product><geolocationGrid><geolocationGridPointList>
{pts}</geolocationGridPointList></geolocationGrid></product>"""


def _annotation():
    pts = []
    for line in (0, 1000):
        for pixel in (0, 1000, 2000):
            lat = 8.0 + line * 1e-4
            lon = 105.0 + pixel * 1e-4
            pts.append(f"<geolocationGridPoint><line>{line}</line><pixel>{pixel}</pixel><latitude>{lat}</latitude>"
                       f"<longitude>{lon}</longitude><incidenceAngle>{30 + pixel / 200}</incidenceAngle>"
                       f"</geolocationGridPoint>")
    return ANNOT.format(pts="".join(pts))


def test_geocoder_roundtrip_and_incidence():
    g = Geocoder.from_annotation(_annotation())
    lon, lat = g.lonlat([500], [1500])
    assert lon[0] == pytest.approx(105.15) and lat[0] == pytest.approx(8.05)
    r, c = g.rowcol(np.array([105.15]), np.array([8.05]))
    assert r[0] == pytest.approx(500, abs=1e-6) and c[0] == pytest.approx(1500, abs=1e-6)
    assert g.incidence([0], [1000])[0] == pytest.approx(35.0)


def test_calibration_and_noise_interpolation():
    cal = CalibrationLUT.from_xml(
        "<c><calibrationVectorList>"
        "<calibrationVector><line>0</line><pixel>0 100</pixel><sigmaNought>100 200</sigmaNought></calibrationVector>"
        "<calibrationVector><line>10</line><pixel>0 100</pixel><sigmaNought>300 400</sigmaNought></calibrationVector>"
        "</calibrationVectorList></c>")
    a = cal.grid(np.array([0, 5, 10]), np.array([0, 50, 100]))
    assert a[1, 1] == pytest.approx(250)
    noise = NoiseLUT.from_xml(
        "<n><noiseRangeVectorList><noiseRangeVector><line>0</line><pixel>0 100</pixel><noiseRangeLut>10 20</noiseRangeLut>"
        "</noiseRangeVector><noiseRangeVector><line>10</line><pixel>0 100</pixel><noiseRangeLut>10 20</noiseRangeLut>"
        "</noiseRangeVector></noiseRangeVectorList><noiseAzimuthVectorList><noiseAzimuthVector><swath>IW1</swath>"
        "<firstAzimuthLine>0</firstAzimuthLine><firstRangeSample>0</firstRangeSample><lastAzimuthLine>10</lastAzimuthLine>"
        "<lastRangeSample>100</lastRangeSample><line>0 10</line><noiseAzimuthLut>1 2</noiseAzimuthLut>"
        "</noiseAzimuthVector></noiseAzimuthVectorList></n>")
    n = noise.grid(np.array([0, 10]), np.array([0, 100]))
    assert n[0, 0] == pytest.approx(10) and n[1, 1] == pytest.approx(40)


def test_get_retries_transient_errors(monkeypatch):
    import requests

    from darkvessel.s1 import aws

    calls = {"n": 0}

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

    class FlakySession:
        def get(self, url, timeout=60, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.exceptions.ProxyError("Unable to connect to proxy")
            return Resp()

    monkeypatch.setattr(aws, "_session", lambda: FlakySession())
    monkeypatch.setattr(aws.time, "sleep", lambda s: None)
    assert isinstance(aws._get("https://example.invalid/x"), Resp)
    assert calls["n"] == 3
