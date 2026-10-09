"""Offline tests of the GFW model-comparison functions on synthetic inputs (no network, no token)."""

import numpy as np
import pandas as pd
import pytest

from darkvessel.ais import gfw_compare as C


def test_nearest_m_known_distances():
    # one degree of latitude at the equator is 111.19 km on a sphere of radius 6,371,008.8 m
    d, i = C.nearest_m([0.0, 10.0], [0.0, 0.0], [0.0, 10.0], [1.0, 0.0])
    assert abs(d[0] - 111_195) < 50
    assert d[1] < 1e-6 and i[1] == 1
    # empty reference set
    d, i = C.nearest_m([0.0], [0.0], [], [])
    assert np.isinf(d[0]) and i[0] == -1
    # haversine agrees with the chord conversion
    h = C.haversine_m(0.0, 0.0, 0.0, 1.0)
    assert abs(h - 111_195) < 50


def test_wilson_interval():
    p, lo, hi = C.wilson(50, 100)
    assert p == 0.5 and 0.40 < lo < 0.41 and 0.59 < hi < 0.60
    assert C.wilson(0, 0) == (None, None, None)
    p, lo, hi = C.wilson(0, 10)
    assert p == 0.0 and lo == 0.0 and 0.27 < hi < 0.29


def test_agreement_by_region_both_directions():
    a = pd.DataFrame({"lon": [106.0, 106.1, 112.0], "lat": [20.0, 20.0, 20.0]})
    # b: one point 300 m east of a[0], one 800 m north of a[1], one far away in the north shelf box
    b = pd.DataFrame({"lon": [106.0 + 300 / (111_195 * np.cos(np.radians(20))), 106.1, 115.0],
                      "lat": [20.0, 20.0 + 800 / 111_195, 21.0]})
    res = C.agreement_by_region(a, b, radii_m=(500.0, 1000.0), name_a="ours", name_b="gfw")
    tonkin = res["Gulf of Tonkin"]
    assert tonkin["ours_near_gfw"]["n"] == 2
    assert tonkin["ours_near_gfw"]["within_500m"]["k"] == 1
    assert tonkin["ours_near_gfw"]["within_1000m"]["k"] == 2
    assert tonkin["gfw_near_ours"]["n"] == 2 and tonkin["gfw_near_ours"]["within_1000m"]["k"] == 2
    north = res["North shelf"]
    assert north["gfw_near_ours"]["n"] == 1 and north["gfw_near_ours"]["within_1000m"]["k"] == 0
    assert north["ours_near_gfw"]["n"] == 1  # a[2] at 112E 20N is in the north shelf box, no gfw point near
    assert res["All"]["ours_near_gfw"]["n"] == 3 and res["All"]["gfw_near_ours"]["within_1000m"]["share"] == round(2 / 3, 4)


def test_structures_from_detections_aggregates_per_structure():
    det = pd.DataFrame({
        "detection_id": ["a", "b", "c", "d"],
        "detection_date": ["2024-01-01", "2024-07-01", "2025-01-01", "2026-03-01"],
        "structure_id": ["1", "1", "1", "2"],
        "lon": [105.0, 105.001, 105.002, 110.0], "lat": [10.0, 10.0, 10.0, 12.0],
        "structure_start_date": ["2024-01-01"] * 3 + ["2026-03-01"],
        "structure_end_date": ["2025-01-01"] * 3 + [None],
        "label": ["oil", "oil", "wind", "unknown"], "label_confidence": ["low", "medium", "high", "low"],
    })
    s = C.structures_from_detections(det).set_index("structure_id")
    assert len(s) == 2
    assert s.loc["1", "label"] == "oil" and s.loc["1", "n_detections"] == 3 and s.loc["1", "label_confidence"] == "high"
    assert abs(s.loc["1", "lon"] - 105.001) < 1e-9
    assert bool(s.loc["2", "ongoing"]) is True and bool(s.loc["1", "ongoing"]) is False
    assert 11.5 < s.loc["1", "months_seen"] < 12.5


def test_assign_gfw_type_single_mixed_none_and_date():
    cands = pd.DataFrame({"det_id": ["c1", "c2", "c3", "c4"],
                          "lon": [105.005, 106.005, 107.0, 105.005], "lat": [10.005, 10.005, 10.0, 10.005],
                          "date": ["2026-09-20"] * 3 + ["2026-09-21"]})
    cells = pd.DataFrame({"lon": [105.005, 106.005, 106.015], "lat": [10.005, 10.005, 10.005],
                          "date": ["2026-09-20"] * 3, "detections": [1, 1, 2],
                          "neural_vessel_type": ["Likely Fishing", "Likely non-fishing", "Unknown"]})
    out = C.assign_gfw_type(cands, cells, max_dist_m=1500.0).set_index("det_id")
    assert out.loc["c1", "gfw_type"] == "Likely Fishing" and bool(out.loc["c1", "gfw_single"]) is True
    assert out.loc["c2", "gfw_type"] == "mixed" and out.loc["c2", "gfw_n_cells"] == 2 and out.loc["c2", "gfw_n_detections"] == 3
    assert out.loc["c3", "gfw_type"] is None and out.loc["c3", "gfw_n_cells"] == 0
    assert out.loc["c4", "gfw_type"] is None  # same place, other date
    assert out.loc["c1", "gfw_nearest_m"] < 1.0
    shares = C.type_shares(out.reset_index())
    assert shares.loc[0, "n"] == 2 and shares.loc[0, "share_Likely Fishing"] == 0.5 and shares.loc[0, "n_mixed"] == 1


def test_length_bins_and_type_shares_by_bin():
    typed = pd.DataFrame({"gfw_type": ["Likely Fishing", "Likely Fishing", "Unknown", None, "Likely non-fishing"],
                          "length_est_m": [20, 24, 60, 30, 150]})
    typed["bin"] = C.length_bin(typed.length_est_m)
    assert list(typed.bin.astype(str)) == ["0-25 m", "0-25 m", "50-100 m", "25-50 m", "100 m and longer"]
    t = C.type_shares(typed, by="bin").set_index("bin")
    assert t.loc["0-25 m", "n"] == 2 and t.loc["0-25 m", "share_Likely Fishing"] == 1.0
    assert "25-50 m" not in t.index  # the only 25-50 m candidate has no GFW type


def test_night_of_local_evening_date():
    nights = C.night_of(["2026-09-22T22:50:00Z", "2026-09-22T10:00:00Z", "2026-09-22T17:30:00Z"])
    # 05:50 local on the 23rd belongs to the night of the 22nd; 17:00 local on the 22nd to the night of the 22nd;
    # 00:30 local on the 23rd to the night of the 22nd
    assert list(nights) == ["2026-09-22", "2026-09-22", "2026-09-22"]


def test_lit_colocation_same_night_within_radius():
    cands = pd.DataFrame({"lon": [105.0, 105.0, 106.0], "lat": [10.0, 10.0, 10.0],
                          "night": ["2026-09-20", "2026-09-21", "2026-09-20"]})
    lights = pd.DataFrame({"lon": [105.0 + 500 / (111_195 * np.cos(np.radians(10)))], "lat": [10.0], "night": ["2026-09-20"]})
    lit = C.lit_colocation(cands, lights, radius_m=1000.0)
    assert list(lit) == [True, False, False]


def test_rank_compare_correlation_and_quadrants():
    lon, lat = np.meshgrid(np.linspace(105.6, 109.9, 9), np.linspace(17.1, 22.4, 9))
    a = np.arange(81, dtype=float).reshape(9, 9)
    valid = np.ones_like(a, bool)
    res = C.rank_compare(a, a.copy(), valid, lon, lat, regions={"Gulf of Tonkin": C.REGIONS["Gulf of Tonkin"]})
    assert res["spearman"]["All"]["rho"] == 1.0 and res["spearman"]["Gulf of Tonkin"]["n"] == 81
    assert np.nanmax(np.abs(res["rank_diff"])) < 1e-12
    assert (res["quadrant"] == 0).all()
    anti = C.rank_compare(a, -a, valid, lon, lat, regions={})
    assert anti["spearman"]["All"]["rho"] == -1.0
    assert anti["counts"]["All"]["a_high_b_low"] == 27 and anti["counts"]["All"]["a_low_b_high"] == 27
    assert anti["rank_diff"][valid].max() > 0.9 and anti["rank_diff"][valid].min() < -0.9
    # invalid cells are excluded and marked -1
    valid[0, :] = False
    part = C.rank_compare(a, a, valid, lon, lat, regions={})
    assert (part["quadrant"][0, :] == -1).all() and part["spearman"]["All"]["n"] == 72
    assert C.spearman([1, 1, 1], [1, 2, 3])["rho"] is None


def test_gap_proximity_counts_space_and_time():
    gaps = pd.DataFrame({"event_id": ["g1"], "off_lon": [110.0], "off_lat": [15.0], "start": ["2026-09-20T00:00:00Z"],
                         "on_lon": [110.5], "on_lat": [15.0], "end": ["2026-09-21T00:00:00Z"]})
    cands = pd.DataFrame({"lon": [110.0, 110.0, 110.5, 110.25, 111.5],
                          "lat": [15.0, 15.0, 15.0, 15.0, 15.0],
                          "ts": ["2026-09-20T03:00:00Z", "2026-09-22T00:00:00Z", "2026-09-20T22:00:00Z",
                                 "2026-09-20T12:00:00Z", "2026-09-20T12:00:00Z"]})
    p = C.gap_proximity(cands, gaps, max_km=10.0, max_h=6.0).iloc[0]
    assert p.n_off == 1 and p.n_on == 1 and p.duration_h == 24.0
    assert p.n_between == 3  # the two end points at the right times plus the midpoint; the one 1.5 degrees east is outside
    assert p.nearest_off_km == 0.0
    empty = C.gap_proximity(cands.iloc[:0], gaps, 10.0, 6.0).iloc[0]
    assert empty.n_off == 0 and empty.nearest_off_km is None


def test_bulk_report_body_defaults():
    body = C.bulk_report_body({"type": "Polygon", "coordinates": []})
    assert body["dataset"] == "public-fixed-infrastructure-data:latest" and body["format"] == "CSV"
    assert body["filters"] and "structure_start_date" in body["filters"][0]


def test_in_box_half_open():
    assert C.in_box([105.5, 110.0], [17.0, 22.5], C.REGIONS["Gulf of Tonkin"]).tolist() == [True, False]
