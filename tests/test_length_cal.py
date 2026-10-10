"""Radar length calibration (darkvessel.detect.length_cal): fit, grouped CV, intervals, application, null reasons, and a
guard that the open calibration holds no Global Fishing Watch (GFW) derived number."""

import json
import math
import warnings

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from darkvessel.config import DATA_DIR
from darkvessel.detect import length_cal as LC

LIVE_FILES = sorted((DATA_DIR / "live").glob("live_S1*.gpkg"))
OPEN_JSON = DATA_DIR / "length_calibration.json"
OPEN_PAIRS = DATA_DIR / "length_calibration_open_pairs.parquet"
RES_JSON = DATA_DIR / "research" / "length_calibration_research.json"
RES_PAIRS = DATA_DIR / "research" / "length_calibration_research_pairs.parquet"


def synthetic(n=600, groups=40, factor=1.7, sd=0.3, slope=1.0, seed=0):
    rng = np.random.default_rng(seed)
    hull = np.exp(rng.uniform(math.log(25), math.log(350), n))
    g = rng.integers(0, groups, n)
    shift = rng.normal(0, 0.05, groups)[g]  # scene-level effect, so groups matter
    radar = factor * hull ** slope * np.exp(rng.normal(0, sd, n) + shift)
    return radar, hull, g


# ----------------------------------------------------------------------------------------------- fitting
def test_ratio_and_loglinear_recover_a_constant_factor():
    x, y, _ = synthetic()
    r = LC.fit_ratio(x, y)
    assert r["kind"] == "ratio" and r["slope"] == 1.0
    assert abs(r["intercept"] + math.log(1.7)) < 0.05
    m = LC.fit_loglinear(x, y)
    # hull on radar: the slope is attenuated by the radar noise, var(log hull) / (var(log hull) + sd^2)
    v = math.log(350 / 25) ** 2 / 12
    assert m["slope"] == pytest.approx(v / (v + 0.3 ** 2), abs=0.06)
    assert np.median(np.exp(LC.predict_log(m, x)) / y) == pytest.approx(1.0, abs=0.05)


def test_lad_fit_resists_gross_outliers():
    x, y, _ = synthetic(n=400, sd=0.1)
    bad = np.arange(len(x)) % 6 == 0  # one pair in six is a wrong object, 4 times too long
    xb = np.where(bad, x * 4, x)
    m = LC.fit_loglinear(xb, y)
    ratio_clean = np.exp(LC.predict_log(m, x[~bad])) / y[~bad]
    assert np.median(ratio_clean) == pytest.approx(1.0, abs=0.06)
    ols = np.polyfit(np.log(xb), np.log(y), 1)  # least squares moves much further
    assert abs(np.median(np.exp(np.polyval(ols, np.log(x[~bad]))) / y[~bad]) - 1) > abs(np.median(ratio_clean) - 1)


def test_loglinear_recovers_a_covariate_and_records_its_range():
    rng = np.random.default_rng(1)
    hull = np.exp(rng.uniform(math.log(30), math.log(300), 500))
    scr = rng.uniform(10, 35, 500)
    radar = 1.5 * hull * np.exp(0.02 * (scr - 20) + rng.normal(0, 0.1, 500))
    Z = pd.DataFrame({"scr_max_db": scr})
    m = LC.fit_loglinear(radar, hull, Z, ["scr_max_db"])
    c = m["covariates"][0]
    assert c["name"] == "scr_max_db" and c["coef"] == pytest.approx(-0.02, abs=0.006)
    assert c["range"][0] == pytest.approx(scr.min()) and c["range"][1] == pytest.approx(scr.max())


def test_lad_dual_matches_the_primal_objective():
    rng = np.random.default_rng(5)
    for n in (12, 90, 400):
        X = np.column_stack([np.ones(n), rng.normal(size=n), rng.uniform(0, 5, n)])
        y = X @ np.array([1.0, 2.0, -0.5]) + rng.standard_t(2, n)
        b_dual, b_primal = LC._lad(X, y), LC._lad_primal(X, y)
        assert np.abs(y - X @ b_dual).sum() == pytest.approx(np.abs(y - X @ b_primal).sum(), rel=1e-7)


def test_isotonic_is_non_decreasing():
    x, y, _ = synthetic(n=300)
    m = LC.fit_isotonic(x, y)
    assert np.all(np.diff(m["knots_log_y"]) >= -1e-12)
    p = LC.predict_log(m, np.array([30.0, 60.0, 120.0, 240.0, np.nan]))
    assert np.all(np.diff(p[:4]) >= 0) and np.isnan(p[4])


def test_select_model_keeps_ratio_for_a_pure_factor_and_takes_loglinear_for_a_slope():
    x, y, g = synthetic(n=500, sd=0.2)
    assert LC.select_model(x, y, g)["kind"] == "ratio"
    x2, y2, g2 = synthetic(n=500, sd=0.15, slope=0.6, factor=12.0)
    sel = LC.select_model(x2, y2, g2)
    assert sel["kind"] in ("loglinear", "isotonic") and sel["table"]


def test_select_model_keeps_a_covariate_pair_that_helps_only_jointly():
    # a and b each mostly carry a shared nuisance u; only a - b carries the effect, so neither alone earns 5 %
    rng = np.random.default_rng(3)
    n = 400
    hull = np.exp(rng.uniform(math.log(30), math.log(300), n))
    g = rng.integers(0, 20, n)
    u, w = rng.normal(0, 1.0, n), rng.normal(0, 0.25, n)
    Z = pd.DataFrame({"a": u + w, "b": u - w})
    radar = 1.7 * hull * np.exp(0.6 * (Z.a - Z.b).to_numpy() + rng.normal(0, 0.15, n))
    sel = LC.select_model(radar, hull, g, Z, candidates=["a", "b"])
    t = {LC.structure_key(r["kind"], r["covariates"]): r["male"] for r in sel["table"]}
    base = min(t["ratio"], t["loglinear"])
    assert min(t["loglinear+a"], t["loglinear+b"]) > base * 0.95  # a forward step would stop here
    assert sel["key"] == "loglinear+a+b" and sel["covariates"] == ["a", "b"]


def test_selection_stability_and_stable_structure():
    x, y, g = synthetic(n=240, groups=8, sd=0.2)
    st = LC.selection_stability(x, y, g, max_folds=10)
    assert st["runs"] == 8 and st["unit_left_out"] == "group"
    assert sum(st["shares"].values()) == pytest.approx(1.0, abs=0.01) and st["shares"].get("ratio", 0) >= 0.75
    assert np.isfinite(st["pred_log"]).all()  # every row predicted once, by a selection made without its group
    sel = LC.select_model(x, y, g)
    assert LC.stable_structure(sel, st)["key"] == "ratio"
    # a strong covariate is picked in every re-selection and kept
    rng = np.random.default_rng(2)
    hull = np.exp(rng.uniform(math.log(30), math.log(300), 240))
    scr = rng.uniform(10, 40, 240)
    gg = rng.integers(0, 6, 240)
    radar = 1.6 * hull * np.exp(0.04 * (scr - 25) + rng.normal(0, 0.1, 240))
    Z = pd.DataFrame({"scr_max_db": scr})
    sel = LC.select_model(radar, hull, gg, Z, candidates=["scr_max_db"])
    st = LC.selection_stability(radar, hull, gg, Z, candidates=["scr_max_db"])
    assert sel["key"] == "loglinear+scr_max_db" and st["shares"] == {"loglinear+scr_max_db": 1.0}
    assert LC.stable_structure(sel, st)["covariates"] == ["scr_max_db"]
    # a richer structure picked in under 75 % of the re-selections falls back to the ratio form
    shaky = {"kind": "loglinear", "covariates": ["fill_width_m"], "key": "loglinear+fill_width_m"}
    out = LC.stable_structure(shaky, {"shares": {"loglinear+fill_width_m": 0.6, "ratio": 0.4}})
    assert out["key"] == "ratio" and out["share_of_reselections"] == 0.6 and out["selected_key"] == shaky["key"]
    # more groups than max_drops: whole folds of groups are left out
    x, y, g = synthetic(n=300, groups=30, sd=0.2)
    st = LC.selection_stability(x, y, g, max_drops=5)
    assert st["runs"] == 5 and st["unit_left_out"].startswith("fold of groups")


# ----------------------------------------------------------------------------------------------- CV and intervals
def test_group_folds_never_split_a_group():
    g = np.repeat(np.arange(25), np.arange(1, 26))
    f = LC.group_folds(g, max_folds=10)
    assert len(np.unique(f)) == 10
    for k in np.unique(g):
        assert len(np.unique(f[g == k])) == 1
    assert len(np.unique(LC.group_folds(np.array(["a", "b", "b", "c"]), 10))) == 3  # one fold per group


def test_conformal_quantiles():
    lo, hi = LC.conformal_quantiles(np.arange(1, 20, dtype=float), 0.8)  # n = 19: ranks 2 and 18
    assert (lo, hi) == (2.0, 18.0)
    assert all(math.isnan(v) for v in LC.conformal_quantiles([0.1, 0.2, 0.3], 0.8))


def test_nested_cv_interval_coverage_is_near_nominal_on_synthetic_pairs():
    covs = []
    for seed in range(3):
        x, y, g = synthetic(n=700, groups=40, seed=seed)
        before, after = LC.cv_report("loglinear", x, y, g)
        covs.append(after["coverage"])
        assert after["n_folds"] == 10
        assert before["median_ratio"] == pytest.approx(1.7, rel=0.08)
        assert after["median_ratio"] == pytest.approx(1.0, abs=0.06)
        assert after["mae_m"] < before["mae_m"] / 2
    assert 0.74 <= np.mean(covs) <= 0.86


def test_metrics_shares():
    m = LC.metrics([100, 100, 100, 100], [100, 140, 190, 250], [90, 0, 0, 260], [110, 1e9, 1e9, 300])
    assert m["within_1_5"] == 0.5 and m["within_2"] == 0.75 and m["median_ratio"] == pytest.approx(1.65)
    assert m["coverage"] == 0.75 and m["mae_m"] == pytest.approx(70.0)


# ----------------------------------------------------------------------------------------------- application
def make_cal(with_covariate=False):
    model = {"kind": "ratio", "intercept": math.log(1 / 1.6), "slope": 1.0, "covariates": [],
             "interval": {"level": 0.8, "q_lo": math.log(0.5), "q_hi": math.log(1.5)}, "valid_length_est_m": [30.0, 450.0],
             "n_pairs": 50}
    fb = None
    if with_covariate:
        fb = model
        model = {"kind": "loglinear", "intercept": math.log(1 / 1.6), "slope": 1.0,
                 "covariates": [{"name": "scr_max_db", "centre": 20.0, "coef": -0.01, "range": [10.0, 40.0]}],
                 "interval": {"level": 0.8, "q_lo": math.log(0.6), "q_hi": math.log(1.4)}, "valid_length_est_m": [30.0, 450.0],
                 "n_pairs": 50}
    return LC.make_calibration("open", model, fb, contains_gfw_data=False)


def test_apply_frame_values_and_null_reasons():
    cal = make_cal()
    df = pd.DataFrame({"length_est_m": [160.0, 26.0, 500.0, np.nan, 45.0, 35.0, 20.0, 24.1]})
    o = LC.apply_frame(df, cal)
    assert list(o.columns) == LC.OUT_COLUMNS
    assert o.length_cal_m[0] == pytest.approx(100.0) and o.length_cal_lo_m[0] == pytest.approx(50.0)
    assert o.length_cal_hi_m[0] == pytest.approx(150.0)
    assert list(o.length_cal_reason) == [None, "below_range", "above_range", "missing_length", None, "below_min_claim",
                                         "pixel_floor", "pixel_floor"]
    assert o.length_cal_reason.dtype == object
    assert o.length_cal_m[4] == pytest.approx(45 / 1.6, abs=0.05)  # 28.1 m; 35 m reads 21.9 m, under the claim floor
    assert o.length_cal_m[1:4].isna().all() and o.length_cal_lo_m[1:4].isna().all() and o.length_cal_m[5:].isna().all()
    assert (o.length_cal_id == cal["calibration_id"]).all()
    assert set(o.length_cal_reason.dropna()) <= set(LC.REASONS)


def test_fallback_when_covariate_missing_or_out_of_range():
    cal = make_cal(with_covariate=True)
    df = pd.DataFrame({"length_est_m": [160.0, 160.0, 160.0], "scr_vv_db": [30.0, np.nan, 55.0], "scr_vh_db": [np.nan] * 3})
    o = LC.apply_frame(df, cal)
    assert o.length_cal_m[0] == pytest.approx(100 * math.exp(-0.1), abs=0.1)  # model: scr 30 is 10 dB over the centre
    assert o.length_cal_m[1] == pytest.approx(100.0) and o.length_cal_m[2] == pytest.approx(100.0)  # fallback
    one = LC.length_cal_m(160.0, {"scr_vv_db": np.nan}, cal)
    assert one["model"] == "fallback" and one["length_cal_m"] == pytest.approx(100.0) and one["reason"] is None
    one = LC.length_cal_m(160.0, {"scr_vv_db": 30.0}, cal)
    assert one["model"] == "model" and one["lo_m"] < one["length_cal_m"] < one["hi_m"] and one["level"] == 0.8


def test_length_cal_m_out_of_range_returns_null_with_reason():
    cal = make_cal()
    r = LC.length_cal_m(600.0, None, cal)
    assert r["length_cal_m"] is None and r["lo_m"] is None and r["reason"] == "above_range"
    assert r["reason_text"] == LC.REASONS["above_range"] and r["valid_range_m"] == [30.0, 450.0]
    r = LC.length_cal_m(None, None, cal)
    assert r["reason"] == "missing_length"
    wide = LC.make_calibration("open", {**cal["model"], "valid_length_est_m": [10.0, 450.0]})
    r = LC.length_cal_m(20.0, None, wide)  # inside the fitted range, but a 2-pixel object
    assert r["reason"] == "pixel_floor" and r["length_cal_m"] is None
    r = LC.length_cal_m(100.0, None, None)
    assert r["reason"] == "no_calibration" and r["length_cal_m"] is None


def test_short_return_is_null():
    # slope 0.5: a 40 m return maps to 4 x sqrt(40) = 25.3 m (shorter, kept); a 10 m return to 12.6 m (longer: null)
    m = {"kind": "loglinear", "intercept": math.log(4.0), "slope": 0.5, "covariates": [],
         "interval": {"level": 0.8, "q_lo": -0.3, "q_hi": 0.3}, "valid_length_est_m": [5.0, 450.0], "n_pairs": 9}
    cal = LC.make_calibration("research", m)
    cal["min_claim_m"] = 0.0
    o = LC.apply_frame(pd.DataFrame({"length_est_m": [40.0, 30.0, 400.0], "n_pixels": [5, 4, 300]}), cal)
    assert o.length_cal_reason[0] is None and o.length_cal_m[0] == pytest.approx(4 * math.sqrt(40), abs=0.05)
    m2 = {**m, "intercept": math.log(8.0)}  # 8 x sqrt(30) = 43.8 m > 30 m
    o = LC.apply_frame(pd.DataFrame({"length_est_m": [30.0, 400.0]}), LC.make_calibration("research", m2))
    assert list(o.length_cal_reason) == ["short_return", None]


def test_derive_covariates():
    df = pd.DataFrame({"scr_vv_db": [20.0, np.nan, 12.0], "scr_vh_db": [25.0, 15.0, np.nan], "n_pixels": [50, 4, 10],
                       "length_est_m": [100.0, 40.0, np.nan], "pol_class": ["VV+VH", "VH only", None],
                       "mission": ["S1D", "S1C", None]})
    z = LC.derive_covariates(df)
    assert list(z.scr_max_db) == [25.0, 15.0, 12.0]
    assert z.fill_width_m[0] == pytest.approx(50.0) and z.fill_width_m[1] == pytest.approx(10.0) and np.isnan(z.fill_width_m[2])
    assert z.dual_pol[0] == 1 and z.dual_pol[1] == 0 and np.isnan(z.dual_pol[2])
    assert z.mission_s1d[0] == 1 and z.mission_s1d[1] == 0 and np.isnan(z.mission_s1d[2])


def test_build_model_and_round_trip(tmp_path):
    x, y, g = synthetic(n=300, groups=12)
    m = LC.build_model("ratio", x, y, g)
    assert "grouped" in m["interval"]["source"] and m["interval"]["q_lo"] < 0 < m["interval"]["q_hi"]
    assert m["valid_length_est_m"] == [pytest.approx(x.min()), pytest.approx(x.max())]
    one = LC.build_model("ratio", x[:30], y[:30], np.zeros(30))
    assert "leave-one-pair-out" in one["interval"]["source"]
    cal = LC.make_calibration("open", m)
    p = tmp_path / "cal.json"
    p.write_text(json.dumps(cal))
    assert LC.load_calibration(p)["calibration_id"] == cal["calibration_id"]
    p.write_text(json.dumps({**cal, "schema": "other"}))
    with pytest.raises(ValueError):
        LC.load_calibration(p)


# ----------------------------------------------------------------------------------------------- open-build guard
GFW_MARKERS = ("global fishing watch", "globalfishingwatch", "cc by-nc", "gfw_registry", "regional_identity",
               "gfw_vessel", "registryinfo")


def _floats(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _floats(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _floats(v)
    elif isinstance(obj, float):
        yield obj


@pytest.mark.skipif(not OPEN_JSON.exists() or not OPEN_PAIRS.exists(), reason="open calibration not built")
def test_open_calibration_holds_no_gfw_derived_values():
    cal = LC.load_calibration(OPEN_JSON)
    text = OPEN_JSON.read_text().lower()
    assert cal["build"] == "open" and cal["contains_gfw_data"] is False
    for k in GFW_MARKERS:
        assert k not in text, k
    pairs = pd.read_parquet(OPEN_PAIRS)
    assert set(pairs.source) == {"live_aisstream"}
    assert not [c for c in pairs.columns if c.startswith("gfw")]
    md = {k.decode(): v.decode() for k, v in pq.read_schema(OPEN_PAIRS).metadata.items() if k != b"pandas"}
    assert md["use"] == "open build" and not any(m in json.dumps(md).lower() for m in GFW_MARKERS)
    # the applied model is reproduced from the open (aisstream) pairs alone
    used = pairs[pairs.used]
    assert len(used) == cal["model"]["n_pairs"] == cal["pairs"]["used"]
    m = cal["model"]
    refit = LC.build_model(m["kind"], used.length_est_m, used.hull_length_m, used.scene_id)
    assert refit["intercept"] == pytest.approx(m["intercept"], abs=1e-9)
    assert refit["slope"] == pytest.approx(m["slope"], abs=1e-9)
    assert refit["interval"]["q_lo"] == pytest.approx(m["interval"]["q_lo"], abs=1e-9)
    assert refit["interval"]["q_hi"] == pytest.approx(m["interval"]["q_hi"], abs=1e-9)
    assert cal["fallback"] is None or cal["fallback"]["n_pairs"] == len(used)
    # no coefficient or CV number of the research calibration appears in the open file
    if RES_JSON.exists():
        res = json.loads(RES_JSON.read_text())
        secret = {round(v, 9) for part in ("model", "fallback", "grouped_cv_leave_one_pass_out", "gfw_pairs_only",
                                            "open_model_on_gfw_pairs", "diagnosis") for v in _floats(res.get(part) or {})
                  if abs(v) > 1e-6 and len(f"{abs(v):.10g}".replace(".", "").strip("0")) >= 5}
        found = {round(v, 9) for v in _floats(cal)} & secret
        assert not found, found


@pytest.mark.skipif(not OPEN_PAIRS.exists() or not LIVE_FILES, reason="open calibration or live files absent")
def test_open_pairs_follow_the_current_live_review():
    """The calibration is stale when a live pair was regraded (doubtful or no longer doubtful) or unmatched after the
    script ran: rerun scripts/35_length_calibration.py. New matched pairs only warn (a new pass needs a rerun too)."""
    import pyogrio

    cur = pd.concat([pyogrio.read_dataframe(f, layer="contacts_4326", read_geometry=False) for f in LIVE_FILES],
                    ignore_index=True).drop_duplicates("det_id").set_index("det_id")
    pairs = pd.read_parquet(OPEN_PAIRS)
    note = (cur.review_note if "review_note" in cur else pd.Series(None, index=cur.index, dtype=object)).reindex(pairs.det_id)
    doubtful_now = note.fillna("").astype(str).str.lower().str.startswith("doubtful").to_numpy()
    used, excl = pairs.used.to_numpy(bool), pairs.exclusion.astype(object).to_numpy()
    stale = pairs.det_id[(used & doubtful_now) | ((excl == "review_doubtful") & ~doubtful_now)].tolist()
    assert not stale, f"live review changed for {stale}: rerun scripts/35_length_calibration.py"
    status = cur.ais_status.reindex(pairs.det_id).to_numpy(object)
    gone = pairs.det_id[status != "matched"].tolist()
    assert not gone, f"no longer matched in the live files: {gone}: rerun scripts/35_length_calibration.py"
    new = (set(cur.index[(cur.ais_status == "matched") & (pd.to_numeric(cur.length_ais_m, errors="coerce") > 0)])
           - set(pairs.det_id))
    if new:
        warnings.warn(f"{len(new)} matched live pairs are not in the open calibration: rerun scripts/35_length_calibration.py")


@pytest.mark.skipif(not RES_JSON.exists() or not RES_PAIRS.exists(), reason="research calibration not built")
def test_research_files_carry_licence_and_attribution():
    res = json.loads(RES_JSON.read_text())
    assert res["build"] == "research" and res["research_only"] is True and res["contains_gfw_data"] is True
    assert res["licence"] == "CC BY-NC 4.0" and "Global Fishing Watch" in res["attribution"] and "noncommercial" in res["use"]
    assert "public-global-vessel-identity" in res["attribution"]  # the registry lengths are the target of the fit
    assert "not mean illegal" in res["caveat"]
    md = {k.decode(): v.decode() for k, v in pq.read_schema(RES_PAIRS).metadata.items() if k != b"pandas"}
    for k in ("use", "licence", "licence_url", "terms_url", "attribution", "caveat", "dark_caveat"):
        assert md.get(k), k
    assert md["licence"] == "CC BY-NC 4.0" and "public-global-vessel-identity" in md["attribution"]
    rp = pd.read_parquet(RES_PAIRS)
    assert rp.loc[rp.source == "gfw_registry", "research_only"].all()
