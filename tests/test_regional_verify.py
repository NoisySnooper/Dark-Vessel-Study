"""Offline tests for darkvessel.ml.regional_verify: tile grouping, checkpoints, reuse of scores, output schema."""

import numpy as np
import pandas as pd
import pytest

import darkvessel  # noqa: F401 (PROJ_DATA before geopandas)
from darkvessel.ml import regional_verify as rv
from darkvessel.ml.chips import CHIP_HALF, chips_db

SHAPE = (3000, 2600)


class FakeReader:
    """Synthetic VV/VH sigma0 scene; counts reads and records windows."""

    def __init__(self, seed=0, shape=SHAPE):
        rng = np.random.default_rng(seed)
        self.shape = shape
        self.vv = rng.uniform(0.001, 0.05, shape).astype(np.float32)
        self.vh = rng.uniform(0.0002, 0.01, shape).astype(np.float32)
        self.vv[:, :40] = np.nan  # no-data stripe at the swath edge
        self.vh[:, :40] = np.nan
        self.windows = []

    def read(self, win):
        r0, r1, c0, c1 = win
        self.windows.append(win)
        return {"VV": self.vv[r0:r1, c0:c1], "VH": self.vh[r0:r1, c0:c1]}

    def close(self):
        pass


def fake_score(chips):
    """Deterministic score from the chip centre, so a chip cut from any window gives the same value."""
    c = np.nan_to_num(chips.astype(np.float32)[:, 0, CHIP_HALF - 2:CHIP_HALF + 2, CHIP_HALF - 2:CHIP_HALF + 2],
                      nan=-30.0)  # missing pixels filled dark, as the model does
    return 1.0 / (1.0 + np.exp(-(c.mean(axis=(1, 2)) + 16.0)))


def objects(n=60, scene="S1D_IW_GRDH_1SDV_20260925T111023_20260925T111053_004750_008AAA_AAAA", seed=1, shape=SHAPE):
    rng = np.random.default_rng(seed)
    rows = rng.uniform(0, shape[0] - 1, n)
    cols = rng.uniform(0, shape[1] - 1, n)
    rows[:3] = [5.0, 1023.6, 2999.0]  # image edge, tile edge, last row
    cols[:3] = [5.0, 1024.2, 2599.0]
    return pd.DataFrame({"det_id": [f"S1D_20260925T111023_{i:05d}" for i in range(n)], "scene_id": scene,
                         "mission": "S1D", "confidence": rng.choice(["high", "medium", "fixed"], n),
                         "row": rows, "col": cols, "lon": 105 + cols / 1e4, "lat": 8 + rows / 1e4,
                         "length_est_m": rng.uniform(10, 300, n)})


# ----------------------------------------------------------------------------- grouping
def test_tile_groups_one_window_per_tile_covering_every_chip():
    obj = objects(200)
    groups = rv.tile_groups(obj.row, obj.col, SHAPE)
    tiles = {(int(r // 1024), int(c // 1024)) for r, c in zip(obj.row, obj.col)}
    assert len(groups) == len(tiles)
    seen = np.concatenate([sel for sel, _ in groups])
    assert sorted(seen) == list(range(len(obj)))  # every object exactly once
    keys = [(int(obj.row[sel[0]] // 1024), int(obj.col[sel[0]] // 1024)) for sel, _ in groups]
    assert keys == sorted(keys)  # row-major tile order
    for sel, (r0, r1, c0, c1) in groups:
        assert len({(int(obj.row[i] // 1024), int(obj.col[i] // 1024)) for i in sel}) == 1  # one tile per group
        assert 0 <= r0 < r1 <= SHAPE[0] and 0 <= c0 < c1 <= SHAPE[1]
        for i in sel:
            r, c = int(round(obj.row[i])), int(round(obj.col[i]))
            assert max(0, r - CHIP_HALF) >= r0 and min(SHAPE[0], r + CHIP_HALF) <= r1
            assert max(0, c - CHIP_HALF) >= c0 and min(SHAPE[1], c + CHIP_HALF) <= c1


def test_tile_groups_empty():
    assert rv.tile_groups([], [], SHAPE) == []


def test_chips_do_not_depend_on_grouping():
    """A chip read through its tile window equals the chip cut from the whole image."""
    obj = objects(40)
    reader = FakeReader()
    whole = chips_db({"VV": reader.vv, "VH": reader.vh}, obj.row, obj.col, 0, 0)
    got = np.full_like(whole, np.nan)
    for sel, ch in rv.iter_group_chips(reader, rv.tile_groups(obj.row, obj.col, SHAPE), obj.row, obj.col):
        got[sel] = ch
    np.testing.assert_array_equal(np.isnan(got), np.isnan(whole))
    np.testing.assert_array_equal(np.nan_to_num(got), np.nan_to_num(whole))


def test_chip_features():
    chips = np.full((2, 2, 64, 64), -20.0, np.float16)
    chips[:, :, 28:36, 28:36] = 5.0  # bright centre does not move the background
    chips[:, 1] = -27.0
    chips[1, :, :, :32] = np.nan  # half the chip off the swath
    f = rv.chip_features(chips)
    assert f["bg_vv_db"][0] == pytest.approx(-20.0) and f["bg_vh_db"][0] == pytest.approx(-27.0)
    assert f["cnn_chip_valid_frac"][0] == 1.0 and f["chip_valid_frac_full"][0] == 1.0
    assert f["cnn_chip_valid_frac"][1] == pytest.approx(0.5) and f["chip_valid_frac_full"][1] == pytest.approx(0.5)


# ----------------------------------------------------------------------------- reuse
def test_align_prior_exact_match_only():
    obj = objects(5)
    prior = obj[["det_id", "scene_id", "row", "col", "lon", "lat"]].copy()
    prior["cnn_score"] = [0.9, 0.1, 0.5, 0.7, 0.2]
    prior["cnn_vessel"] = prior.cnn_score >= 0.632
    prior["bg_vv_db"], prior["bg_vh_db"] = -20.0, -27.0
    prior.loc[1, "col"] += 0.5  # moved: not the same detection
    prior = pd.concat([prior[prior.index != 3], prior[prior.index == 4]])  # 3 missing, 4 duplicated
    a = rv.align_prior(obj, prior)
    assert list(a.prior_status) == ["match", "position_differs", "match", "none", "none"]
    assert a.prior_score.iloc[0] == 0.9 and np.isnan(a.prior_score.iloc[1]) and np.isnan(a.prior_score.iloc[4])
    assert a.prior_vessel.tolist() == [True, False, False, False, False]
    assert rv.align_prior(obj, None).prior_score.isna().all()


def test_verification_mask_fixed_and_order_free():
    ids = np.array([f"d{i:04d}" for i in range(1000)], dtype=object)
    reused = np.arange(1000) % 3 != 0
    m = rv.verification_mask(ids, reused, n=50)
    assert m.sum() == 50 and not (m & ~reused).any()
    perm = np.random.default_rng(5).permutation(1000)
    m2 = rv.verification_mask(ids[perm], reused[perm], n=50)
    assert set(ids[m]) == set(ids[perm][m2])
    assert rv.verification_mask(ids[:10], reused[:10], n=50).sum() == reused[:10].sum()


def test_score_scene_reuses_prior_and_rescores_sample():
    obj = objects(60)
    reader = FakeReader()
    prior = np.full(len(obj), np.nan)
    prior[:30] = 0.25  # reused
    verify = np.zeros(len(obj), bool)
    verify[:5] = True
    calls = []

    def score(ch):
        calls.append(len(ch))
        return fake_score(ch)

    df, st = rv.score_scene(obj, prior, verify, reader, score, batch=8)
    assert st == {"n": 60, "scored": 35, "reused": 30, "reads": len(reader.windows)}
    assert sum(calls) == 35  # reused rows outside the sample never reach the model
    assert (df.cnn_score.iloc[:30] == np.float32(0.25)).all()
    assert (df.cnn_score_source.iloc[:30] == rv.SOURCE_REUSED).all()
    assert (df.cnn_score_source.iloc[30:] == rv.SOURCE_SCORED).all()
    assert df.cnn_rescore_check.notna().sum() == 5 and df.cnn_rescore_check.iloc[:5].notna().all()
    whole = chips_db({"VV": reader.vv, "VH": reader.vh}, obj.row, obj.col, 0, 0)
    np.testing.assert_allclose(df.cnn_score.iloc[30:], fake_score(whole[30:]), rtol=1e-6)
    np.testing.assert_allclose(df.cnn_rescore_check.iloc[:5], fake_score(whole[:5]), rtol=1e-6)
    inner = (obj.col > 80) & (obj.col < SHAPE[1] - 40) & (obj.row > 40) & (obj.row < SHAPE[0] - 40)
    assert df.bg_vv_db[inner].notna().all() and (df.cnn_chip_valid_frac[inner] == 1).all()
    assert df.cnn_chip_valid_frac.iloc[2] == pytest.approx(25 / 64)  # last pixel: 5 x 5 of the 8 x 8 centre inside
    assert np.isnan(df.bg_vv_db.iloc[0])  # chip wholly on the no-data stripe has no background
    assert df.chip_valid_frac_full.iloc[0] < 1  # object at column 5 sits on the no-data stripe


# ----------------------------------------------------------------------------- checkpoints
def run(tmp_path, obj, model_id="verifier_v0_aaaa", calls=None):
    calls = [] if calls is None else calls

    def factory(sid):
        calls.append(sid)
        return FakeReader(seed=len(sid))

    prior = rv.align_prior(obj, None)
    return rv.run_phase(obj, prior, np.zeros(len(obj), bool), cache_dir=tmp_path, phase="main", model_id=model_id,
                        score_fn=fake_score, reader_factory=factory, pause_s=0, log=lambda m: None), calls


def two_scenes():
    a = objects(20, scene="S1C_IW_GRDH_1SDV_20260920T104816_20260920T104845_009530_012F70_2DA4", seed=2)
    b = objects(30, scene="S1D_IW_GRDH_1SDV_20260925T111023_20260925T111053_004750_008AAA_AAAA", seed=3)
    b["det_id"] = b.det_id.str.replace("S1D_", "S1Dx_")
    return pd.concat([a, b], ignore_index=True)


def test_checkpoint_skip_and_rerun_rules(tmp_path):
    obj = two_scenes()
    st, calls = run(tmp_path, obj)
    assert len(calls) == 2 and st["checkpointed_before"] == 0 and not st["failed"]
    paths = sorted((tmp_path / "main").glob("*.parquet"))
    assert [p.stem for p in paths] == sorted(obj.scene_id.unique())
    st, calls = run(tmp_path, obj)
    assert calls == [] and st["checkpointed_before"] == 2  # a rerun skips done scenes
    st, calls = run(tmp_path, obj, model_id="verifier_v0_bbbb")
    assert len(calls) == 2  # another model: redo
    obj2 = obj.iloc[1:]  # det_id set of the first scene changed
    st, calls = run(tmp_path, obj2, model_id="verifier_v0_bbbb")
    assert calls == [obj.scene_id.iloc[0]]
    paths[1].write_bytes(b"not a parquet")
    st, calls = run(tmp_path, obj2, model_id="verifier_v0_bbbb")
    assert calls == [paths[1].stem]
    assert not list((tmp_path / "main").glob(".*.tmp"))  # atomic writes leave no temporaries
    ck = rv.load_checkpoints(tmp_path, "main")
    assert list(ck.columns) == rv.CHECKPOINT_COLS and len(ck) == len(obj2)


def test_failed_scene_is_reported_and_not_checkpointed(tmp_path):
    obj = two_scenes()

    def factory(sid):
        if sid.startswith("S1C"):
            raise OSError("proxy dropped")
        return FakeReader()

    st = rv.run_phase(obj, rv.align_prior(obj, None), np.zeros(len(obj), bool), cache_dir=tmp_path, phase="main",
                      model_id="m", score_fn=fake_score, reader_factory=factory, pause_s=0, log=lambda m: None)
    assert st["failed"] == [obj.scene_id.iloc[0]]  # tried twice, reported once
    assert [p.stem for p in (tmp_path / "main").glob("*.parquet")] == [obj.scene_id.iloc[-1]]


# ----------------------------------------------------------------------------- outputs
def test_score_table_schema_and_threshold(tmp_path):
    obj = two_scenes()
    run(tmp_path, obj)
    ck = rv.load_checkpoints(tmp_path, "main")
    t = rv.score_table(ck, obj, threshold=0.5)
    assert list(t.columns) == rv.SCORE_COLS and len(t) == len(obj)
    assert t.det_id.is_unique and t.cnn_score.dtype == np.float32 and t.bg_vv_db.dtype == np.float32
    assert (t.cnn_vessel == (t.cnn_score.astype(float) >= 0.5)).all()
    assert (t.cnn_threshold == np.float32(0.5)).all() and (t.cnn_model_id == "verifier_v0_aaaa").all()
    assert t[["scene_id", "det_id"]].equals(t[["scene_id", "det_id"]].sort_values(["scene_id", "det_id"]))


def test_verified_layer_keeps_unscored_contacts_null():
    import geopandas as gpd
    from shapely.geometry import Point

    lean = gpd.GeoDataFrame({"det_id": ["a", "b", "c"], "scene_idx": 3, "mission": "S1D",
                             "acq_utc": "2026-09-25T11:10:23+00:00", "confidence": ["high", "medium", "high"],
                             "lat": [8.0, 9.0, 10.0], "lon": [105.0, 106.0, 107.0], "length_est_m": [20.0, 50.0, 100.0],
                             "scr_vv_db": 1.0, "persist_dates": 0, "ais_status": "not_checked", "caveat": "Dark = ...",
                             "cnn_score": 0.5},  # a stale score column in the input is replaced, not duplicated
                            geometry=[Point(105, 8), Point(106, 9), Point(107, 10)], crs=4326)
    scores = pd.DataFrame({"det_id": ["a", "c", "z"], "cnn_score": np.float32([0.912345, 0.1, 0.8]),
                           "cnn_vessel": [True, False, True]})  # "z" is not a contact and must not appear
    g = rv.verified_layer(lean, scores)
    assert list(g.columns) == ["det_id", "scene_idx", "mission", "confidence", "length_est_m", "scr_vv_db",
                               "persist_dates", *rv.CNN_GPKG_COLS, "geometry"] and len(g) == 3
    assert not set(rv.VERIFIED_DROP) & set(g.columns)
    assert g.cnn_score.iloc[0] == pytest.approx(0.9123) and np.isnan(g.cnn_score.iloc[1])
    assert g.cnn_vessel.iloc[0] and g.cnn_vessel.isna().iloc[1] and not g.cnn_vessel.iloc[2]


def test_summarise_wilson_and_groups():
    n = 400
    rng = np.random.default_rng(0)
    obj = pd.DataFrame({"det_id": [f"d{i}" for i in range(n)], "lon": rng.uniform(100, 120, n),
                        "lat": rng.uniform(0, 22, n), "length_est_m": rng.uniform(5, 400, n)})
    t = pd.DataFrame({"det_id": obj.det_id, "mission": rng.choice(["S1C", "S1D"], n),
                      "confidence": rng.choice(["high", "medium", "fixed"], n), "cnn_score": rng.random(n),
                      "bg_vv_db": -20.0, "bg_vh_db": -27.0})
    t["cnn_vessel"] = t.cnn_score >= 0.632
    weather = pd.DataFrame({"det_id": obj.det_id, "wind_ms": rng.uniform(0, 12, n),
                            "deep_convection": rng.random(n) < 0.1})
    s = rv.summarise(t, obj, shared_ids=obj.det_id[:100], weather=weather)
    for key in ("by_class", "contacts_by_mission", "by_mission_class", "contacts_by_length_bin", "by_length_bin_class",
                "contacts_by_region", "by_region_class", "inside_shared_cells", "outside_shared_cells",
                "contacts_by_wind_bin_class", "contacts_by_deep_convection_class"):
        assert s[key], key
        for r in s[key]:
            lo, hi = r["accept_ci95"]
            assert lo <= r["accept_share"] <= hi
    assert sum(r["n"] for r in s["by_class"]) == n
    assert sum(r["n"] for r in s["inside_shared_cells"]) == 100
    assert s["contacts_high_medium"]["n"] == int(t.confidence.isin(["high", "medium"]).sum())
    assert {r["length_bin"] for r in s["contacts_by_length_bin"]} <= set(rv.length_bin_labels())


def test_reuse_check():
    obj = objects(6)
    prior = obj[["det_id", "scene_id", "row", "col", "lon", "lat"]].assign(
        cnn_score=[0.9, 0.2, 0.7, 0.1, 0.5, 0.6], cnn_vessel=lambda d: d.cnn_score >= 0.632, bg_vv_db=-20.0,
        bg_vh_db=-27.0)
    pa = rv.align_prior(obj, prior)
    ck = pd.DataFrame({"det_id": obj.det_id, "cnn_score": pa.prior_score.astype(np.float32),
                       "cnn_score_source": rv.SOURCE_REUSED, "cnn_rescore_check": np.nan, "bg_vv_db": -20.0,
                       "bg_vh_db": -27.0})
    ck.loc[:2, "cnn_rescore_check"] = [0.90004, 0.2, 0.7]
    r = rv.reuse_check(ck, pa, obj, threshold=0.632)
    assert r["pass"] and r["rescored_sample"] == 3 and r["verdict_agree"] == 3 and r["bg_max_abs_diff_db"] == 0
    ck.loc[1, "cnn_rescore_check"] = 0.21
    assert not rv.reuse_check(ck, pa, obj, threshold=0.632)["pass"]


def test_region_and_length_bins():
    assert list(rv.region_of([106.0, 112.0, 100.0, 125.0], [20.0, 20.0, 10.0, 10.0])) == [
        "Gulf of Tonkin", "North shelf", "Gulf of Thailand", "other"]
    assert list(rv.length_bin([10, 25, 99.9, 150, 500, np.nan])) == [
        "0-25 m", "25-50 m", "50-100 m", "100-200 m", "200 m and longer", "unknown"]


def test_should_stop_returns_after_a_checkpointed_scene(tmp_path):
    obj = two_scenes()
    calls = []
    st = rv.run_phase(obj, rv.align_prior(obj, None), np.zeros(len(obj), bool), cache_dir=tmp_path, phase="main",
                      model_id="m", score_fn=fake_score, reader_factory=lambda sid: calls.append(sid) or FakeReader(),
                      pause_s=0, should_stop=lambda: True, log=lambda m: None)
    assert st["stopped"] and len(calls) == 1  # never stops before doing at least one scene
    assert len(list((tmp_path / "main").glob("*.parquet"))) == 1
