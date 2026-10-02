"""CA-CFAR tests on synthetic gamma clutter (offline)."""

import numpy as np
import pytest

from darkvessel.detect.cfar import ca_cfar, ca_cfar_tiled, estimate_enl, extract_detections, gamma_alpha


def clutter(shape=(600, 600), enl=4.0, mean=0.01, seed=0):
    rng = np.random.default_rng(seed)
    return rng.gamma(enl, mean / enl, shape)


def test_gamma_alpha_matches_exponential_closed_form():
    # L = 1 is exponential clutter: P(I > a*mu) = exp(-a)  ->  a = -ln(PFA)
    assert gamma_alpha(1e-6, 1.0) == pytest.approx(-np.log(1e-6), rel=1e-9)
    # more looks -> less spread -> lower multiplier for the same PFA
    assert gamma_alpha(1e-6, 4.4) < gamma_alpha(1e-6, 1.0)
    assert gamma_alpha(1e-8, 4.4) > gamma_alpha(1e-6, 4.4)


def test_estimate_enl_recovers_true_looks():
    img = clutter((400, 400), enl=5.0)
    est = estimate_enl(img, np.ones_like(img, bool), win=15)
    assert est == pytest.approx(5.0, rel=0.25)


def test_detects_injected_targets():
    img = clutter()
    targets = [(100, 100), (300, 450), (520, 80)]
    for r, c in targets:
        img[r - 1 : r + 2, c - 1 : c + 2] = 0.01 * 10 ** (20 / 10)  # 20 dB above mean clutter
    res = ca_cfar(img, np.ones_like(img, bool), guard=21, background=41, pfa=1e-6, enl=4.0)
    for r, c in targets:
        assert res["detect"][r, c]


def test_false_alarm_rate_close_to_design():
    img = clutter((800, 800), enl=4.0, seed=1)
    pfa = 1e-3
    res = ca_cfar(img, np.ones_like(img, bool), guard=11, background=41, pfa=pfa, enl=4.0)
    rate = res["detect"][res["testable"]].mean()
    # estimated mean from ~1,500 cells adds a little spread; stay within a factor of 2
    assert pfa / 2 < rate < pfa * 2


def test_masked_pixels_never_detected_and_do_not_bias_background():
    img = clutter()
    valid = np.ones_like(img, bool)
    img[:, :200] = 5.0  # very bright "land"
    valid[:, :200] = False
    img[300, 230] = 0.01 * 10 ** 2  # target 30 px from the mask edge, 20 dB above sea
    res = ca_cfar(img, valid, guard=21, background=61, pfa=1e-6, enl=4.0)
    assert not res["detect"][:, :200].any()
    assert res["detect"][300, 230]


def test_tiled_equals_untiled():
    img = clutter((700, 650), seed=3)
    img[350, 320] = 1.0
    valid = np.ones_like(img, bool)
    valid[600:, 500:] = False
    kw = dict(guard=21, background=41, pfa=1e-5, enl=4.0)
    a = ca_cfar(img, valid, **kw)
    b = ca_cfar_tiled(img, valid, tile=256, **kw)
    assert np.array_equal(a["detect"], b["detect"])


def test_bad_windows_rejected():
    img = clutter((50, 50))
    with pytest.raises(ValueError):
        ca_cfar(img, np.ones_like(img, bool), guard=20, background=41)
    with pytest.raises(ValueError):
        ca_cfar(img, np.ones_like(img, bool), guard=41, background=41)


def test_extract_detections_measures_length_and_merges_fragments():
    img = clutter((300, 300), seed=4)
    det = np.zeros_like(img, bool)
    # a 20-pixel-long ship along the rows, with one-pixel gap (fragmented return)
    det[150, 100:110] = True
    det[150, 111:121] = True
    img[det] = 1.0
    bg = np.full_like(img, 0.01)
    df = extract_detections(det, img, bg, min_pixels=2, merge_px=1, row_off=1000, col_off=2000)
    assert len(df) == 1
    assert df.length_est_m.iloc[0] == pytest.approx(200, rel=0.2)
    assert df.row.iloc[0] == pytest.approx(1150, abs=0.5)
    assert df.peak_to_bg_db.iloc[0] == pytest.approx(20, abs=0.1)


def test_clutter_zone_flags_candidates_among_weak_returns():
    import pandas as pd

    from darkvessel.detect.postprocess import clutter_zone

    rng = np.random.default_rng(1)
    # Scene A: a candidate inside a field of 20 weak returns within ~500 m, and a lone candidate 20 km away
    lon = list(110.0 + rng.uniform(-0.004, 0.004, 20)) + [110.0, 110.18]
    lat = list(10.0 + rng.uniform(-0.004, 0.004, 20)) + [10.0, 10.0]
    conf = ["low"] * 20 + ["medium", "high"]
    df = pd.DataFrame({"lon": lon, "lat": lat, "confidence": conf, "scene_id": "A"})
    # Scene B: same position as the field, other scene: must not borrow scene A's weak returns
    df = pd.concat([df, pd.DataFrame({"lon": [110.0], "lat": [10.0], "confidence": ["high"], "scene_id": ["B"]})],
                   ignore_index=True)
    flag, n_low = clutter_zone(df, radius_m=1000.0, min_low=5)
    assert flag.tolist() == [False] * 20 + [True, False, False]
    assert n_low[20] == 20 and n_low[21] == 0 and n_low[22] == 0


def test_near_fixed_flags_candidates_next_to_structures_only_in_the_same_scene():
    import pandas as pd

    from darkvessel.detect.postprocess import near_fixed

    # Scene A: two turbines 1 km apart, a candidate 100 m from one, a candidate 2 km away;
    # scene B: a candidate at the same spot as the first one, but scene B has no structures.
    m = 1 / 111_320  # degrees per metre near the equator
    df = pd.DataFrame({
        "lon": [110.0, 110.0 + 1000 * m, 110.0 + 100 * m, 110.0 + 2000 * m, 110.0 + 100 * m],
        "lat": [1.0] * 5,
        "confidence": ["fixed", "fixed", "medium", "high", "high"],
        "scene_id": ["A", "A", "A", "A", "B"],
    })
    flag, dist = near_fixed(df, radius_m=250.0)
    assert flag.tolist() == [False, False, True, False, False]
    assert abs(dist[0] - 1000) < 5 and abs(dist[2] - 100) < 5 and abs(dist[3] - 1000) < 5
    assert np.isinf(dist[4])
