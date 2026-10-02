"""Offline tests for the ML stage: Web Mercator decoding, candidate labelling, chips, metrics."""

import numpy as np
import pandas as pd
import pytest

from darkvessel.ml import chips as ch
from darkvessel.ml.evaluate import pr_curve, recall_by_length, system_metrics, wilson
from darkvessel.ml.labels import (M_PER_PX, length_bin, lonlat_to_webmerc_pixel, product_path,
                                  webmerc_pixel_to_lonlat)
from darkvessel.ml.model import VerifierCNN, normalise_chips, predict_proba


def test_webmerc_pixel_matches_ai2_readme_example():
    # README example image row: column 55407, row 2287033 is the top-left of an image whose
    # bounds are Min lon -175.2443, Max lat -16.0822 (image 1 of metadata.sqlite3).
    lon, lat = webmerc_pixel_to_lonlat(55407, 2287033)
    assert lon == pytest.approx(-175.2443, abs=1e-3)
    assert lat == pytest.approx(-16.0822, abs=1e-3)
    # world corners
    lon0, lat0 = webmerc_pixel_to_lonlat(0, 0)
    assert lon0 == pytest.approx(-180.0) and lat0 == pytest.approx(85.0511, abs=1e-3)
    assert M_PER_PX == pytest.approx(9.5546, abs=1e-3)


def test_webmerc_round_trip():
    cols = np.array([0.0, 1e6, 3316376.0, 4194303.0])
    rows = np.array([10.0, 2e6, 2046567.0, 4194000.0])
    lon, lat = webmerc_pixel_to_lonlat(cols, rows)
    c2, r2 = lonlat_to_webmerc_pixel(lon, lat)
    assert np.allclose(c2, cols, atol=1e-6) and np.allclose(r2, rows, atol=1e-6)


def test_product_path_has_no_zero_padding():
    pid = "S1A_IW_GRDH_1SDV_20220328T111731_20220328T111756_042520_05125B_ECDC"
    assert product_path(pid + ".SAFE") == "GRD/2022/3/28/IW/DV/" + pid
    assert product_path("S1B_IW_GRDH_1SDV_20211110T215142_20211110T215211_029531_03864B_5A52") == \
        "GRD/2021/11/10/IW/DV/S1B_IW_GRDH_1SDV_20211110T215142_20211110T215211_029531_03864B_5A52"


def test_length_bins():
    b = length_bin([5, 15, 24.9, 25, 99, 100, 350, np.nan])
    assert list(b.astype(str)) == ["0-15 m", "15-25 m", "15-25 m", "25-50 m", "50-100 m", "100+ m", "100+ m", "nan"]


def test_candidate_class_radius_logic():
    d = np.array([0.0, 50.0, 50.1, 150.0, 150.1, 200.0, 200.0])
    L = np.array([np.nan, np.nan, np.nan, np.nan, np.nan, 300.0, 100.0])
    out = ch.candidate_class(d, L)
    assert list(out) == ["vessel", "vessel", "ambiguous", "ambiguous", "clutter", "ambiguous", "clutter"]


def test_match_points_distances_in_metres():
    # label at (lon 104, lat 1); candidates 30 m east and ~500 m north
    lab_lon, lab_lat = np.array([104.0]), np.array([1.0])
    k = np.cos(np.radians(1.0))
    cand_lon = np.array([104.0 + 30 / (111320.0 * k), 104.0])
    cand_lat = np.array([1.0, 1.0 + 500 / 110540.0])
    cd, ci, ld, li = ch.match_points(cand_lon, cand_lat, lab_lon, lab_lat)
    assert cd[0] == pytest.approx(30.0, abs=0.5) and cd[1] == pytest.approx(500.0, abs=1.0)
    assert ld[0] == pytest.approx(30.0, abs=0.5) and li[0] == 0
    assert list(ch.candidate_class(cd)) == ["vessel", "clutter"]
    # empty sets
    cd, ci, ld, li = ch.match_points([], [], lab_lon, lab_lat)
    assert len(cd) == 0 and np.isinf(ld).all() and (li == -1).all()


def test_extract_chip_shapes_and_edges():
    arr = np.arange(100 * 120, dtype=np.float32).reshape(100, 120)
    c = ch.extract_chip(arr, row=50 + 1000, col=60 + 2000, row_off=1000, col_off=2000, half=32)
    assert c.shape == (64, 64) and not np.isnan(c).any()
    assert c[32, 32] == arr[50, 60]
    # near the corner: outside pixels are NaN but the shape holds
    c = ch.extract_chip(arr, row=1000 + 5, col=2000 + 3, row_off=1000, col_off=2000, half=32)
    assert c.shape == (64, 64)
    assert np.isnan(c[:27, :]).all() and np.isnan(c[:, :29]).all()
    assert c[32, 32] == arr[5, 3]
    sigma = {"VV": np.full((100, 120), 0.01, np.float32), "VH": np.full((100, 120), 0.001, np.float32)}
    stack = ch.chips_db(sigma, rows=[1050.0, 1010.2], cols=[2060.0, 2100.7], row_off=1000, col_off=2000)
    assert stack.shape == (2, 2, 64, 64) and stack.dtype == np.float16
    assert float(stack[0, 0, 32, 32]) == pytest.approx(-20.0, abs=0.05)
    assert float(stack[0, 1, 32, 32]) == pytest.approx(-30.0, abs=0.05)


def test_connected_sea_uses_code_zero_or_edge():
    wc = np.full((20, 20), 10, np.uint8)        # land
    wc[2:6, 2:6] = 80                           # enclosed lake -> not sea
    wc[10:, :] = 80                             # water touching the edge -> sea
    sea = ch.connected_sea(wc)
    assert not sea[3, 3] and sea[15, 15] and not sea[0, 0]
    wc2 = np.full((20, 20), 10, np.uint8)
    wc2[5:9, 5:9] = 80
    wc2[6, 6] = 0                               # open-sea code inside -> sea
    assert ch.connected_sea(wc2)[5, 5]


def test_bbox_and_pad_window_with_synthetic_geocoder():
    from darkvessel.s1.grd import Geocoder
    lines = np.array([0.0, 1000.0, 2000.0])
    pixels = np.array([0.0, 1000.0, 2000.0])
    LON = 100.0 + np.tile(pixels, (3, 1)) / 1000.0 * 0.1       # lon grows with column
    LAT = 10.0 - np.tile(lines[:, None], (1, 3)) / 1000.0 * 0.1  # lat falls with row
    geo = Geocoder(lines, pixels, LAT, LON, np.zeros_like(LAT))
    win = ch.bbox_window(geo, (2001, 2001), 100.05, 9.85, 100.1, 9.9)
    assert win is not None
    assert win.col_off == 500 and win.row_off == 1000 and win.width == pytest.approx(501, abs=1) and win.height == pytest.approx(501, abs=1)
    assert ch.bbox_window(geo, (2001, 2001), 120, 50, 121, 51) is None
    padded = ch.pad_window(win, 81, (2001, 2001))
    assert padded.col_off == 419 and padded.row_off == 919
    assert padded.col_off + padded.width <= 2001 and padded.row_off + padded.height <= 2001


def test_wilson_interval():
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and 0.25 < hi < 0.35
    lo, hi = wilson(50, 100)
    assert lo == pytest.approx(0.404, abs=0.01) and hi == pytest.approx(0.596, abs=0.01)
    assert np.isnan(wilson(0, 0)[0])


def _toy_eval():
    labels = pd.DataFrame({"label_id": [1, 2, 3, 4], "cfar_detected": [True, True, True, False],
                           "cfar_detected_loose": [True, True, True, False], "length_m": [10.0, 30.0, 120.0, 60.0]})
    cands = pd.DataFrame({"cand_class": ["vessel", "vessel", "vessel", "clutter", "clutter", "ambiguous"],
                          "match_label_id": [1, 2, 3, -1, -1, 3]})
    scores = np.array([0.9, 0.8, 0.2, 0.7, 0.1, 0.6])
    return labels, cands, scores


def test_system_metrics_and_pr_curve():
    labels, cands, scores = _toy_eval()
    cfar_only = system_metrics(labels, cands, np.ones(len(cands), bool))
    assert cfar_only["precision"] == pytest.approx(3 / 5) and cfar_only["recall"] == pytest.approx(3 / 4)
    assert cfar_only["ambiguous_accepted"] == 1
    m = system_metrics(labels, cands, scores >= 0.5)
    assert m["tp_candidates"] == 2 and m["fp_candidates"] == 1 and m["labels_detected"] == 2
    assert m["precision"] == pytest.approx(2 / 3) and m["recall"] == pytest.approx(0.5)
    pr = pr_curve(labels, cands, scores, n_points=20)
    assert pr.recall.iloc[0] == pytest.approx(0.75)  # everything accepted = CFAR-only
    assert pr.recall.is_monotonic_decreasing


def test_recall_by_length_table():
    labels, cands, scores = _toy_eval()
    tab = recall_by_length(labels, cands, scores >= 0.5)
    tab = tab.set_index("length_bin")
    assert tab.loc["0-15 m", "n_labels"] == 1 and tab.loc["0-15 m", "cfar_recall"] == 1.0 and tab.loc["0-15 m", "cnn_recall"] == 1.0
    assert tab.loc["100+ m", "cnn_recall"] == 0.0 and tab.loc["100+ m", "cfar_recall"] == 1.0
    assert tab.loc["50-100 m", "cfar_recall"] == 0.0
    assert tab.loc["all", "n_labels"] == 4 and tab.loc["all", "cfar_detected"] == 3
    assert (tab.cfar_ci_lo <= tab.cfar_recall).all() and (tab.cfar_recall <= tab.cfar_ci_hi).all()


def test_model_forward_and_normalisation():
    model = VerifierCNN(width=8)
    x = torch._C._nn if False else None  # noqa: F841 (keep torch import local to the model module)
    chips = np.random.default_rng(0).normal(-15, 5, size=(5, 2, 64, 64)).astype(np.float16)
    chips[0, 0, :10, :10] = np.nan
    xn = normalise_chips(chips, [-15, -22], [5, 5])
    assert xn.dtype == np.float32 and np.isfinite(xn).all() and xn[0, 0, 0, 0] == pytest.approx(-2.0)
    meta = {"norm_mean": [-15, -22], "norm_std": [5, 5]}
    p = predict_proba(model, chips, meta, batch=2, tta=True)
    assert p.shape == (5,) and ((p >= 0) & (p <= 1)).all()
