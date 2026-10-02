"""AIS matching tests on synthetic data (no real AIS used)."""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest

from darkvessel.ais.match import MatchConfig, match_detections, predict_positions, recall_by_length
from darkvessel.ais.synthetic import synthetic_scene
from darkvessel.config import DARK_CAVEAT

T = pd.Timestamp("2026-09-29T11:10:30Z")


def _ais(rows):
    return pd.DataFrame(rows, columns=["mmsi", "timestamp", "lon", "lat", "sog_kn", "cog_deg", "length_m"])


def test_interpolates_between_bracketing_reports():
    ais = _ais([(1, T - pd.Timedelta("5min"), 105.0, 8.5, 5, 90, 20),
                (1, T + pd.Timedelta("5min"), 105.02, 8.5, 5, 90, 20)])
    p = predict_positions(ais, T).to_crs("EPSG:4326")
    assert p.method.iloc[0] == "interp"
    assert p.geometry.x.iloc[0] == pytest.approx(105.01, abs=1e-4)


def test_dead_reckons_from_single_report():
    ais = _ais([(2, T - pd.Timedelta("300s"), 105.0, 8.5, 10, 0, 30)])  # 10 kn due north for 5 min
    p = predict_positions(ais, T)
    start = gpd.GeoSeries(gpd.points_from_xy([105.0], [8.5]), crs="EPSG:4326").to_crs(p.crs)
    moved = p.geometry.y.iloc[0] - start.y.iloc[0]
    assert p.method.iloc[0] == "extrap"
    assert moved == pytest.approx(10 * 0.514444 * 300, rel=1e-6)


def test_stale_reports_are_dropped():
    ais = _ais([(3, T - pd.Timedelta("2h"), 105.0, 8.5, 5, 0, 20)])
    assert predict_positions(ais, T, MatchConfig(max_extrap_s=600)).empty


def test_gate_and_one_to_one():
    det = gpd.GeoDataFrame({"det_id": ["a", "b", "c"], "confidence": ["high"] * 3},
                           geometry=gpd.points_from_xy([105.0, 105.001, 105.2], [8.5, 8.5, 8.5]), crs="EPSG:4326")
    ais = _ais([(10, T, 105.0, 8.5, 0, 0, 25)])
    out, ais_only = match_detections(det, ais, T)
    assert (out.ais_status == "matched").sum() == 1          # one AIS vessel, one match
    assert out.set_index("det_id").loc["a", "ais_status"] == "matched"  # nearest wins
    assert out.set_index("det_id").loc["c", "ais_status"] == "unmatched"  # 22 km away: outside gate
    assert ais_only.empty
    assert (out.caveat == DARK_CAVEAT).all()


def test_synthetic_scene_end_to_end():
    dets, ais, truth, t0 = synthetic_scene(n_vessels=80, seed=7)
    out, ais_only = match_detections(dets, ais, t0, MatchConfig(max_dist_m=1200))
    t = truth.set_index("det_id")
    matched = out[out.ais_status == "matched"]
    # matched pairs must be the right vessel
    correct = (matched.mmsi.astype(int).values == t.loc[matched.det_id, "mmsi"].values).mean()
    assert correct >= 0.95
    # detected vessels without AIS must never be matched
    no_ais_dets = t[(~t.has_ais) & t.index.notna()].index
    assert not out.set_index("det_id").loc[no_ais_dets, "ais_status"].eq("matched").any()
    assert out.dark_candidate.sum() >= len(no_ais_dets)
    # recall by length rises with length (logistic detection model in the simulator)
    rec = recall_by_length(out.assign(ais_length_m=out.get("ais_length_m")), ais_only, bins=(0, 15, 40, 400))
    assert rec.recall.iloc[-1] > rec.recall.iloc[0]
    assert np.isclose(rec.n_ais.sum(), len(matched) + len(ais_only))
