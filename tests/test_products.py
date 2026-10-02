"""Product layout: every vessel layer carries the caveat; full caveat lives in the about table."""

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely.geometry import box

from darkvessel.config import DARK_CAVEAT, DARK_CAVEAT_SHORT
from darkvessel.products import write_detection_products
from darkvessel.viz.demo import _dms


def test_lean_layout_and_caveats(tmp_path):
    dets = gpd.GeoDataFrame(
        {"det_id": ["a", "b"], "confidence": ["high", "low"], "row": [1.0, 2.0], "col": [3.0, 4.0],
         "scene_id": "S", "acq_utc": "2026-09-29T11:10:23+00:00", "platform": "SENTINEL-1D", "pfa": 1e-6,
         "caveat": DARK_CAVEAT},
        geometry=gpd.points_from_xy([105.0, 105.1], [8.5, 8.6]), crs="EPSG:4326")
    win = gpd.GeoDataFrame({"scene_id": ["S"], "acq_utc": ["t"], "rows": [10], "cols": [10]},
                           geometry=[box(105, 8, 106, 9)], crs="EPSG:4326")
    meta = {"scene_id": "S", "pfa": 1e-6, "caveat": DARK_CAVEAT}
    out = tmp_path / "d.gpkg"
    write_detection_products(dets, {}, win, meta, out, tmp_path / "p.gpkg")
    layers = {name for name, _ in pyogrio.list_layers(out)}
    assert {"detections_baseline_4326", "detections_baseline_utm48n", "processing_window_4326", "about"} <= layers
    d = gpd.read_file(out, layer="detections_baseline_utm48n")
    assert d.crs.to_epsg() == 32648
    assert (d.caveat == DARK_CAVEAT_SHORT).all()
    assert "platform" not in d.columns  # scene-level fields moved to the about table
    about = pyogrio.read_dataframe(out, layer="about")
    assert about.caveat_full.iloc[0] == DARK_CAVEAT
    assert pd.notna(about.data_credit.iloc[0])


def test_dms_format():
    assert _dms(8.5, "N", "S") == "8°30.000'N"
    assert _dms(-0.25, "N", "S") == "0°15.000'S"


def test_dual_crs_without_spatial_index(tmp_path):
    import sqlite3

    from darkvessel.io import write_dual_crs

    g = gpd.GeoDataFrame({"det_id": ["a", "b"], "caveat": DARK_CAVEAT_SHORT},
                         geometry=gpd.points_from_xy([110.0, 112.0], [10.0, 12.0]), crs="EPSG:4326")
    out = tmp_path / "r.gpkg"
    assert write_dual_crs(g, out, "x", utm_crs="EPSG:32649", spatial_index=False) == ["x_4326", "x_utm49n"]
    assert gpd.read_file(out, layer="x_utm49n").crs.to_epsg() == 32649
    with sqlite3.connect(out) as con:
        rtree = con.execute("SELECT name FROM sqlite_master WHERE name LIKE 'rtree_x_%'").fetchall()
    assert rtree == []
