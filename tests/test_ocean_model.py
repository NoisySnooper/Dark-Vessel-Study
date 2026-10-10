"""Expected-activity model on synthetic cell-nights with a known rate (no network, no project data)."""

import numpy as np
import pandas as pd
import pytest

from darkvessel.ocean import model as m


@pytest.fixture(scope="module")
def cells():
    return m.simulate_cells(n_cells=250, n_nights=16, seed=1)


def test_weighted_rate_deviance_equals_count_deviance_with_offset():
    rng = np.random.default_rng(0)
    expo = rng.uniform(1, 500, 1000)
    rate = np.exp(rng.normal(-5, 0.5, 1000))
    count = rng.poisson(expo * rate)
    mu_rate = rate * np.exp(rng.normal(0, 0.3, 1000))
    lhs = m.rate_deviance(count / expo, mu_rate, expo).sum()        # what the trees minimise on rates
    rhs = m.poisson_deviance(count, expo * mu_rate).sum()           # Poisson count model with log-exposure offset
    assert lhs == pytest.approx(rhs, rel=1e-9)
    # zero counts are finite and the deviance is zero at the truth
    assert np.isfinite(m.poisson_deviance([0, 0], [1.0, 2.0])).all()
    assert m.poisson_deviance([3.0, 0.0], [3.0, 1e-12]).sum() == pytest.approx(0.0, abs=1e-9)


def test_d2_is_zero_for_the_baseline_and_one_for_the_truth():
    count = np.array([0, 2, 5, 1, 0, 3.0])
    base = np.full(6, count.mean())
    assert m.d2_score(count, base, base) == pytest.approx(0.0)
    assert m.d2_score(count, np.maximum(count, 1e-12), base) == pytest.approx(1.0, abs=1e-9)


def test_region_lookup_and_feature_groups():
    r = m.region_of([107.0, 114.0, 101.0, 107.5, 114.0, 105.0, 120.0], [20.0, 20.0, 10.0, 9.0, 10.0, 0.0, 10.0])
    assert list(r) == ["Gulf of Tonkin", "North shelf", "Gulf of Thailand", "South Vietnam shelf", "Central sea",
                       "Southern sea", "other"]
    g = m.group_features(["lon", "lat", "region", "depth_mean_m", "dist_coast_km", "sst_c", "front_dist_km", "chl_mg_m3",
                          "wind_ms", "wave_hs_m", "moon_illum_pct", "ssh_m", "current_speed_ms", "inc_angle_mean_deg",
                          "time_of_day", "night", "count"])
    assert g["static"] == ["depth_mean_m", "dist_coast_km"]
    assert g["sst"] == ["sst_c", "front_dist_km"] and g["chl"] == ["chl_mg_m3"]
    assert g["weather"] == ["wind_ms", "wave_hs_m"] and g["moon"] == ["moon_illum_pct"]
    assert g["dynamics"] == ["ssh_m", "current_speed_ms"]
    assert g["radar_geometry"] == ["inc_angle_mean_deg", "time_of_day"]
    assert "night" not in sum(g.values(), []) and "count" not in sum(g.values(), [])


def test_week_folds_are_blocks_of_seven_nights():
    nights = pd.date_range("2026-09-05", "2026-10-01")
    f = m.week_folds(nights)
    assert list(pd.unique(f)) == ["w1", "w2", "w3", "w4"]
    assert (f[:7] == "w1").all() and (f[7:14] == "w2").all() and (f[-6:] == "w4").all()


def test_empirical_bayes_shrinks_thin_cells_more_than_thick_ones():
    rng = np.random.default_rng(3)
    n = 400
    true = rng.gamma(4.0, 0.5e-3, n)                       # cell rates around 2 per 1,000 km2, real spread
    expo = np.where(np.arange(n) % 2 == 0, 20.0, 5000.0)    # thin and thick cells alternate
    count = rng.poisson(true * expo)
    est, prior = m.eb_shrink(count, expo, np.full(n, "A"))
    region_rate = count.sum() / expo.sum()
    raw = count / expo
    thin, thick = np.arange(n) % 2 == 0, np.arange(n) % 2 == 1
    pull_thin = np.abs(est[thin] - region_rate).mean() / max(np.abs(raw[thin] - region_rate).mean(), 1e-12)
    pull_thick = np.abs(est[thick] - region_rate).mean() / max(np.abs(raw[thick] - region_rate).mean(), 1e-12)
    assert pull_thin < 0.6 < pull_thick                    # thin cells move most of the way, thick cells stay put
    assert prior.prior_var.iloc[0] > 0
    # the shrunk estimates are closer to the truth than the raw rates
    assert np.mean((est - true) ** 2) < np.mean((raw - true) ** 2)
    # a cell with no exposure gets the group rate
    est2, _ = m.eb_shrink([0, 10, 0], [0, 1000, 500], ["B", "B", "B"])
    assert est2[0] == pytest.approx(10 / 1500)


def test_trees_recover_the_known_rate_and_beat_the_baselines(cells):
    fit = cells[cells.exposure_km2 >= m.EXPOSURE_MIN_KM2].reset_index(drop=True)
    feats = ["lon", "lat", "region", "depth_mean_m", "wind_ms", "moon_illum_pct", "noise_0", "noise_1"]
    folds = m.week_folds(fit.night)
    full = m.cross_validate(fit, lambda: m.RateTrees(feats), folds)
    clim = m.cross_validate(fit, lambda: m.Climatology(), folds)
    glob = m.cross_validate(fit, lambda: m.GlobalRate(), folds)
    assert glob.d2 == pytest.approx(0.0, abs=0.05)
    assert full.d2 > clim.d2 > 0.1
    # the out-of-fold rate tracks the true rate (log scale, exposure weighted)
    ok = np.isfinite(full.oof_rate)
    corr = np.corrcoef(np.log(full.oof_rate[ok]), np.log(fit.true_rate[ok]))[0, 1]
    assert corr > 0.85
    # D2 cannot be better than the truth's own D2 by much (the truth is the ceiling up to noise)
    base = m.GlobalRate().fit(fit).rate_ * fit.exposure_km2
    truth_d2 = m.d2_score(fit["count"], fit.true_rate * fit.exposure_km2, base)
    assert full.d2 < truth_d2 + 0.05
    assert set(full.folds.fold) == {"w1", "w2", "w3"} and (full.folds.d2 > 0).all()


def test_predictions_are_rates_not_counts(cells):
    fit = cells[cells.exposure_km2 >= m.EXPOSURE_MIN_KM2].reset_index(drop=True)
    model = m.RateTrees(["lon", "lat", "depth_mean_m", "wind_ms"]).fit(fit)
    rate = model.predict_rate(fit)
    assert rate.min() >= 0
    # doubling the exposure doubles the expected count, not the rate
    twice = fit.assign(exposure_km2=fit.exposure_km2 * 2)
    assert np.allclose(m.predict_count(model, twice), 2 * m.predict_count(model, fit))
    assert np.allclose(model.predict_rate(twice), rate)
    # the fitted total is close to the observed total (Poisson trees are nearly calibrated in total)
    assert m.predict_count(model, fit).sum() == pytest.approx(fit["count"].sum(), rel=0.1)


def test_unseen_region_is_treated_as_missing_not_an_error(cells):
    fit = cells[cells.exposure_km2 >= m.EXPOSURE_MIN_KM2].reset_index(drop=True)
    folds = m.region_folds(fit.region)
    res = m.cross_validate(fit, lambda: m.RateTrees(["lon", "lat", "region", "depth_mean_m", "wind_ms"]), folds)
    assert np.isfinite(res.oof_rate).all()
    assert len(res.folds) == fit.region.nunique()
    assert set(res.d2_by_region) <= set(fit.region.unique())


def test_calibration_deciles_conserve_count_and_exposure(cells):
    fit = cells[cells.exposure_km2 >= m.EXPOSURE_MIN_KM2].reset_index(drop=True)
    cal = m.calibration_deciles(fit["count"], fit.exposure_km2, fit.true_rate)
    assert len(cal) == 10
    assert cal["count"].sum() == fit["count"].sum()
    assert cal.exposure_km2.sum() == pytest.approx(fit.exposure_km2.sum())
    assert cal.predicted_per_1000km2.is_monotonic_increasing
    # the truth is calibrated: observed within 25 % of predicted in the well-filled deciles
    rel = (cal.observed_per_1000km2 / cal.predicted_per_1000km2 - 1).abs()
    assert (rel[cal["count"] > 50] < 0.25).all()


def test_permutation_importance_ranks_real_features_above_noise(cells):
    fit = cells[cells.exposure_km2 >= m.EXPOSURE_MIN_KM2].reset_index(drop=True)
    feats = ["lon", "lat", "depth_mean_m", "wind_ms", "noise_0", "noise_1"]
    groups = {"static": ["depth_mean_m"], "weather": ["wind_ms"], "noise": ["noise_0", "noise_1"]}
    imp = m.permutation_importance_groups(fit, lambda: m.RateTrees(feats), m.week_folds(fit.night), groups, n_repeats=2)
    imp = imp.set_index("group")
    assert imp.loc["static", "deviance_rise"] > imp.loc["noise", "deviance_rise"]
    assert imp.loc["weather", "deviance_rise"] > imp.loc["noise", "deviance_rise"]
    assert abs(imp.loc["noise", "share_of_deviance"]) < 0.05


def test_anomaly_flags_follow_poisson_tails():
    t = m.anomaly_table([0, 20, 5, 5, 50], [5.0, 5.0, 5.0, np.nan, 50.0], control="none")
    assert t.flag.tolist() == ["low", "high", "none", "none", "none"]
    assert t.z.iloc[1] == pytest.approx(15 / np.sqrt(5))
    assert t.p_high.iloc[1] < 0.01 and t.p_low.iloc[0] == pytest.approx(np.exp(-5))
    assert t.p_two_sided.iloc[0] == pytest.approx(2 * np.exp(-5)) and t.p_two_sided.iloc[2] == 1.0
    assert np.isnan(t.z.iloc[3]) and not t.tested.iloc[3]
    # the uncontrolled flag rate on true Poisson draws is about the nominal level on each side
    rng = np.random.default_rng(0)
    mu = rng.uniform(2, 40, 20000)
    f = m.anomaly_table(rng.poisson(mu), mu, control="none").flag
    assert 0.002 < (f == "high").mean() < 0.012 and 0.002 < (f == "low").mean() < 0.012
    with pytest.raises(ValueError):
        m.anomaly_table([1], [1.0], control="bonferroni")


def test_bh_adjust_matches_hand_computation():
    q = m.bh_adjust([0.01, 0.04, 0.03, 0.005, np.nan])
    assert q[:4].tolist() == pytest.approx([0.02, 0.04, 0.04, 0.02]) and np.isnan(q[4])
    assert np.isnan(m.bh_adjust([np.nan, np.nan])).all()
    assert m.bh_adjust([0.5]).tolist() == [0.5]


def test_bh_control_keeps_null_data_quiet_and_finds_planted_anomalies():
    rng = np.random.default_rng(4)
    mu = rng.uniform(2, 40, 20000)
    obs = rng.poisson(mu).astype(float)
    t = m.anomaly_table(obs, mu)                                       # default: BH over all rows
    raw = m.anomaly_table(obs, mu, control="none")
    assert (raw.flag != "none").sum() > 100                            # thousands of tests at 0.01 flag hundreds of rows
    assert (t.flag != "none").sum() <= 2                               # BH at 0.01: almost surely none on null data
    planted = np.arange(0, 20000, 1000)
    obs[planted] = np.round(mu[planted] * 4 + 30)
    windy = np.zeros(20000, bool)
    windy[planted[:5]] = True                                          # five of them in rough weather: not in the family
    t2 = m.anomaly_table(obs, mu, eligible=~windy)
    hit = t2.flag.to_numpy() == "high"
    assert hit[planted[5:]].all() and not hit[planted[:5]].any()
    assert not t2.tested.to_numpy()[planted[:5]].any() and np.isnan(t2.q_bh.to_numpy()[planted[:5]]).all()
    assert hit.sum() <= 15 + 2


def test_compare_oof_and_the_gate():
    rng = np.random.default_rng(2)
    n = 6000
    expo = rng.uniform(50, 500, n)
    true = np.exp(rng.normal(-6, 0.8, n))
    count = rng.poisson(true * expo)
    folds = np.repeat(["w1", "w2", "w3"], n // 3)
    glob = np.full(n, count.sum() / expo.sum())
    pooled, per = m.compare_oof(count, expo, {"global_rate": glob, "climatology": glob * 1.0, "truth": true}, folds)
    truth_vs_clim = pooled[(pooled.model == "truth") & (pooled.baseline == "climatology")].iloc[0]
    assert truth_vs_clim.d2 > 0.2 and truth_vs_clim.folds_better == 3 and truth_vs_clim.folds == 3
    assert {"fold_d2_mean", "fold_d2_sd", "fold_d2_min", "fold_d2_max"} <= set(pooled.columns)
    assert len(per[(per.model == "truth") & (per.baseline == "global_rate")]) == 3
    clim_vs_glob = pooled[(pooled.model == "climatology") & (pooled.baseline == "global_rate")].iloc[0]
    assert clim_vs_glob.d2 == pytest.approx(0.0, abs=1e-12)
    folds_d2 = per[(per.model == "truth") & (per.baseline == "climatology")].d2.tolist()
    assert m.beats_baseline(truth_vs_clim.d2, folds_d2)
    assert not m.beats_baseline(0.0, [0.1, 0.1, 0.1])                 # pooled must be above 0
    assert not m.beats_baseline(0.05, [0.2, -0.1, -0.1])               # and more than half of the folds
    assert not m.beats_baseline(float("nan"), [0.2])


def test_no_anomalies_when_the_model_loses_to_the_climatology():
    """Cells with fixed, very different rates and features that are pure noise: the cell climatology wins out of
    sample, the gate fails, and nothing is flagged even where the Poisson tails would flag."""
    rng = np.random.default_rng(5)
    n_cells, n_nights = 200, 21
    cell_rate = np.exp(rng.normal(-6, 1.2, n_cells))
    rows = []
    for k in range(n_nights):
        expo = rng.uniform(100, 600, n_cells)
        rows.append(pd.DataFrame({"night": pd.Timestamp("2026-09-05") + pd.Timedelta(days=k), "row": np.arange(n_cells), "col": 0,
                                  "region": "Central sea", "exposure_km2": expo, "noise_0": rng.normal(size=n_cells),
                                  "noise_1": rng.normal(size=n_cells), "count": rng.poisson(cell_rate * expo)}))
    df = pd.concat(rows, ignore_index=True)
    df.loc[df.index[::97], "count"] += 40                               # planted excesses
    folds = m.week_folds(df.night)
    oof = {"global_rate": m.cross_validate(df, lambda: m.GlobalRate(), folds).oof_rate,
           "climatology": m.cross_validate(df, lambda: m.Climatology(), folds).oof_rate,
           "full_trees": m.cross_validate(df, lambda: m.RateTrees(["noise_0", "noise_1"]), folds).oof_rate}
    pooled, per = m.compare_oof(df["count"], df.exposure_km2, oof, folds)
    r = pooled[(pooled.model == "full_trees") & (pooled.baseline == "climatology")].iloc[0]
    fold_d2 = per[(per.model == "full_trees") & (per.baseline == "climatology")].d2
    assert r.d2 < 0
    gate = m.beats_baseline(r.d2, fold_d2)
    assert not gate
    mu = oof["full_trees"] * df.exposure_km2.to_numpy()
    t, s = m.gated_anomalies(df["count"], mu, gate)
    assert (t.flag == "none").all() and s["flagged_high"] == 0 and s["flagged_low"] == 0
    assert s["would_flag_high"] + s["would_flag_low"] > 0 and not s["gate_passed"]
    assert np.isfinite(t.p_two_sided).all()                             # p-values stay for inspection
    t2, s2 = m.gated_anomalies(df["count"], mu, True)
    assert s2["flagged_high"] == s["would_flag_high"]


def test_forbidden_features_are_refused():
    for bad in (["ship_density_all"], ["lon", "ship_density_fishing"], ["marineregions_mrgid"], ["eez_share"],
                ["ship_presence_all"], ["log_density"], ["in_shipping_lane"]):
        with pytest.raises(ValueError):
            m.check_features(bad)
        with pytest.raises(ValueError):
            m.RateTrees(bad)
    ok = ["lon", "share_shallower_200m", "ship_presence_share_fishing", "ship_presence_share_all", "wind_ms"]
    assert m.check_features(ok) == ok
    g = m.group_features(ok + ["marineregions_geoname", "ship_density_all"])
    assert g["static"] == ["share_shallower_200m", "ship_presence_share_fishing", "ship_presence_share_all"]
    assert "forbidden" not in g and "marineregions_geoname" not in sum(g.values(), [])


def test_overdispersion_and_negative_binomial_tail():
    rng = np.random.default_rng(6)
    mu = rng.uniform(1, 30, 40000)
    pois = m.overdispersion(rng.poisson(mu), mu)
    assert pois["pearson_dispersion"] == pytest.approx(1.0, abs=0.05) and pois["nb_alpha"] < 0.01
    alpha = 0.5
    nb = rng.negative_binomial(1 / alpha, (1 / alpha) / (1 / alpha + mu))
    est = m.overdispersion(nb, mu)
    assert est["nb_alpha"] == pytest.approx(alpha, rel=0.15) and est["pearson_dispersion"] > 3
    p_pois = m.nb_two_sided([40.0], [10.0], 0.0)
    p_nb = m.nb_two_sided([40.0], [10.0], alpha)
    assert p_pois[0] == pytest.approx(m.anomaly_table([40.0], [10.0]).p_two_sided.iloc[0])
    assert p_nb[0] > 100 * p_pois[0]                                     # overdispersion makes the excess far less surprising


def test_region_holdout_climatology_falls_back_to_the_global_rate(cells):
    fit = cells[cells.exposure_km2 >= m.EXPOSURE_MIN_KM2].reset_index(drop=True)
    folds = m.region_folds(fit.region)
    clim = m.cross_validate(fit, lambda: m.Climatology(), folds).oof_rate
    glob = m.cross_validate(fit, lambda: m.GlobalRate(), folds).oof_rate
    assert np.allclose(clim, glob)


def test_partial_dependence_of_wind_falls(cells):
    fit = cells[cells.exposure_km2 >= m.EXPOSURE_MIN_KM2].reset_index(drop=True)
    model = m.RateTrees(["lon", "lat", "depth_mean_m", "wind_ms", "moon_illum_pct"]).fit(fit)
    pd_wind = m.partial_dependence(model, fit, "wind_ms", n_grid=6)
    assert len(pd_wind) == 6 and pd_wind.value.is_monotonic_increasing
    assert pd_wind.rate_per_1000km2.iloc[0] > pd_wind.rate_per_1000km2.iloc[-1]   # true effect: -0.25 per m/s
    pd_depth = m.partial_dependence(model, fit, "depth_mean_m", n_grid=6)
    assert pd_depth.rate_per_1000km2.iloc[-1] > pd_depth.rate_per_1000km2.iloc[0]  # shallower water, more boats
