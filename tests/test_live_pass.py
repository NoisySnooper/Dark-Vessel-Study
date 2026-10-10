"""Offline tests for the live-pass pipeline (darkvessel.live): synthetic scene and AIS only, no network, no real data.

Covers the status rules (matched, unmatched, no_coverage, dark_lead), the ship-station MMSI filter, identity and MID
lookup, the speed-aware gate and the match_quality rules, retried tile reads and retried detection, checkpoint skipping,
the cycle lock, the no-op rerun, mirror candidate selection with a fake listing, and the D1 output schema with its
GeoPackage field types. Round 3: dense-traffic pairing (azimuth shift, assignment, ambiguity), the pairing rules set by
the Pearl River hand check (length, fixed contacts, oversized returns), the tested-sea polygon, the review tables and
review_note, rematch, weather sidecars.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

import darkvessel  # noqa: F401  (PROJ_DATA before rasterio/pyogrio)
from darkvessel.ais.match import MatchConfig, gate_metres, match_detections, match_quality, predict_positions
from darkvessel.ais.synthetic import synthetic_scene
from darkvessel.config import CRS_UTM_REGIONAL, DARK_CAVEAT
from darkvessel.live import identity as ident
from darkvessel.live import matching, mid, outputs, rules, scene as scene_mod, watch
from darkvessel.live.schema import (AIS_ONLY_COLUMNS, AIS_ONLY_FIELD_TYPES, AIS_STATUS_VALUES, CONTACT_FIELD_TYPES, D1_COLUMNS,
                                    SCENE_COLUMNS)

T = pd.Timestamp("2026-10-08T22:59:30Z")
BBOX = (105.0, 8.3, 105.6, 8.9)
SCENE = {"scene_time": T, "run_id": "live_S1D_20261008T2258", "footprint": box(*BBOX).buffer(0.05), "product_id": "SYN_PRODUCT",
         "mission": "S1D", "acq_utc": T.isoformat(), "pass_dir": "DESCENDING", "orbit_rel": 164}


def _grid():
    from rasterio.transform import from_origin

    return from_origin(99.0, 24.0, 0.25, 0.25), (112, 96)  # 0.25 degree cells over the AOI bounds


def _static(mmsis, lengths=None, names=None):
    n = len(mmsis)
    return pd.DataFrame({"mmsi": np.asarray(mmsis, dtype="int64"), "imo": pd.array([9000000 + i for i in range(n)], dtype="Int64"),
                         "name": names or [f"VESSEL {m}" for m in mmsis], "callsign": [f"XV{i:03d}" for i in range(n)],
                         "ship_type": pd.array([30] * n, dtype="Int16"), "ship_type_label": ["fishing"] * n,
                         "length_m": lengths if lengths is not None else [25.0] * n, "width_m": [6.0] * n,
                         "destination": [None] * n, "eta": [None] * n, "static_seen_utc": [T] * n})


def _synthetic():
    dets, ais, truth, t0 = synthetic_scene(n_vessels=60, seed=3, sar_time=str(T), bbox=BBOX)
    ais = ais.assign(ais_class="A", ship_name=None, heading=np.nan, nav_status=0, msg_type="PositionReport", msg_id=1)
    objects = pd.DataFrame({"det_id": dets.det_id.values, "confidence": "high", "length_est_m": dets.length_est_m.values,
                            "lon": dets.geometry.x.values, "lat": dets.geometry.y.values, "scene_id": "SYN", "mission": "S1D",
                            "acq_utc": T.isoformat(), "row": 100.0, "col": 100.0, "inc_angle_deg": 35.0, "pol_class": "VV+VH",
                            "n_pixels": 6, "scr_vv_db": 12.0, "scr_vh_db": 9.0, "low_reason": "", "persist_dates": 0,
                            "persist_dates_checked": 0, "n_low_1km": 0, "near_fixed_m": np.nan, "cnn_score": 0.8, "cnn_vessel": True})
    return objects, ais, truth


def _scene_record(objects, counts, **over):
    rec = {"product_id": "SYN_PRODUCT", "run_id": "live_S1D_20261008T2258", "mission": "S1D", "start_utc": T.isoformat(), "stop_utc": T.isoformat(),
           "scene_time_utc": T.isoformat(), "orbit_abs": 4928, "orbit_rel": 164, "pass_dir": "DESCENDING", "aoi_overlap_km2": 4000.0,
           "tested_km2": 3500.0, "blocks_processed": 4, "runtime_s": 1.0, "detect_attempts": 1, "io_retries": 0, "n_objects": len(objects),
           "n_high": len(objects), "n_medium": 0, "n_fixed": 0, "n_low": 0, "n_cnn_scored": len(objects), "n_cnn_vessel": len(objects),
           "ais_recorded_hours": 1, "processed_utc": T.isoformat(), "status": "done", "footprint_wkt": box(*BBOX).wkt, **counts}
    rec.update(over)
    return rec


# ----------------------------------------------------------------------------------------------- MID and identity
def test_mid_lookup():
    assert mid.flag_from_mmsi(574001234).startswith("Viet Nam")
    assert "Hong Kong" in mid.flag_from_mmsi(477123456)
    assert mid.flag_from_mmsi(412000001).startswith("China")
    assert mid.flag_from_mmsi(2573000) is None        # coast station (00 prefix)
    assert mid.flag_from_mmsi(111234567) is None      # SAR aircraft
    assert mid.flag_from_mmsi(None) is None and mid.flag_from_mmsi(float("nan")) is None
    assert len(mid.MID) >= 280 and all(201 <= k <= 775 for k in mid.MID)


def test_identity_table_and_attach():
    st = _static([574001001, 477002002], lengths=[32.0, 180.0])
    pos = pd.DataFrame({"mmsi": [574001001, 525003003], "timestamp": [T, T], "ais_class": ["A", "B"], "ship_name": ["FROM POS", "KM MAJU"]})
    tab = ident.identity_table(st, pos)
    assert set(tab.mmsi) == {574001001, 477002002, 525003003}
    r = tab.set_index("mmsi")
    assert r.loc[574001001, "vessel_name"] == "VESSEL 574001001" and r.loc[574001001, "call_sign"] == "XV000"
    assert r.loc[574001001, "length_ais_m"] == 32.0 and r.loc[574001001, "ship_type"] == "fishing"
    assert r.loc[574001001, "flag"].startswith("Viet Nam") and r.loc[574001001, "mmsi_mid"] == 574
    assert r.loc[525003003, "vessel_name"] == "KM MAJU" and r.loc[525003003, "identity_source"] == ident.IDENTITY_POSITION
    assert r.loc[525003003, "flag"].startswith("Indonesia")
    det = pd.DataFrame({"det_id": ["a", "b", "c"], "mmsi": pd.array([477002002, None, 567009999], dtype="Int64")})
    out = ident.attach_identity(det, tab)
    o = out.set_index("det_id")
    assert o.loc["a", "imo"] == 9000001 and o.loc["a", "flag"].startswith("China") and o.loc["a", "length_ais_m"] == 180.0
    assert pd.isna(o.loc["b", "flag"]) and pd.isna(o.loc["b", "identity_source"])
    assert o.loc["c", "flag"] == "Thailand" and o.loc["c", "identity_source"] == ident.IDENTITY_POSITION  # MMSI never heard statically


def test_ship_station_filter():
    ais = pd.DataFrame({"mmsi": [574001001, 2573000, 111234567, 992571234, 982570001, 775999999, 201000000, 5, 776000000],
                        "lon": 105.0, "lat": 8.5, "timestamp": T})
    kept = rules.ship_stations(ais)
    assert kept.mmsi.tolist() == [574001001, 775999999, 201000000]   # coast station, SAR aircraft, AtoN, child craft, malformed dropped
    assert rules.ship_stations(ais.iloc[:0]).empty
    assert "201000000 to 775999999" in rules.MMSI_FILTER_TEXT


# ----------------------------------------------------------------------------------------------- gate and quality
def test_speed_aware_gate():
    pred = pd.DataFrame({"sog_kn": [0.0, 10.0, 20.0, np.nan]})
    g = gate_metres(pred, rules.LIVE_MATCH)
    assert g[0] == 500.0
    assert g[1] == pytest.approx(500 + 125 * 10 * 0.514444)
    assert g[2] == pytest.approx(min(2000.0, 500 + 125 * 20 * 0.514444))
    assert g[3] == 2000.0
    assert (gate_metres(pred, MatchConfig(max_dist_m=1000.0)) == 1000.0).all()  # fixed gate when base_gate_m is None


def test_match_quality_rule():
    q = match_quality([100, 800, 1500, np.nan, 100], [60, 600, 60, 60, 60], [30, 30, 30, 30, 200], [25, 25, 25, 25, 25])
    assert list(q) == ["high", "medium", "low", None, "low"]  # the last: length ratio 8 fails both bands
    assert match_quality([100], [60], [np.nan], [25])[0] == "high"  # unknown radar length does not demote


def test_gate_prevents_stopped_vessel_far_match():
    import geopandas as gpd

    det = gpd.GeoDataFrame({"det_id": ["a"], "confidence": ["high"], "length_est_m": [30.0]},
                           geometry=gpd.points_from_xy([105.0], [8.5]), crs="EPSG:4326")
    ais = pd.DataFrame([(1, T, 105.011, 8.5, 0.0, 0.0, 30.0)], columns=["mmsi", "timestamp", "lon", "lat", "sog_kn", "cog_deg", "length_m"])
    out, _ = match_detections(det, ais, T, rules.LIVE_MATCH)      # 1.2 km away but stopped: outside the 500 m gate
    assert out.ais_status.iloc[0] == "unmatched"
    ais.loc[0, "sog_kn"] = 15.0                                   # moving at 15 kn: gate 500 + 125 x 7.7 = 1464 m
    out, _ = match_detections(det, ais, T, rules.LIVE_MATCH)
    assert out.ais_status.iloc[0] == "matched" and out.match_gate_m.iloc[0] > 1200


# ----------------------------------------------------------------------------------------------- status rules
def test_status_rules_matched_unmatched_no_coverage():
    transform, shape = _grid()
    det = pd.DataFrame({"det_id": ["m", "u_cell", "u_near", "n", "f"], "ais_status": ["matched", "unmatched", "unmatched", "unmatched", "unmatched"],
                        "confidence": ["high", "high", "medium", "high", "fixed"],
                        "lon": [105.0, 105.2, 105.0, 112.0, 105.1], "lat": [8.5, 8.6, 8.9, 15.0, 8.55]})
    # AIS heard at (105.0, 8.5): same 0.25 cell as u_cell (105.0-105.25, 8.5-8.75); u_near is 44 km north in another cell,
    # but a second report at (105.0, 8.75) is 17 km from it; nothing within 20 km of n; f is a fixed structure in the heard cell
    ais_window = pd.DataFrame({"mmsi": [1, 1, 2], "timestamp": [T, T + pd.Timedelta("5min"), T], "lon": [105.0, 105.0, 105.0], "lat": [8.5, 8.75, 8.5]})
    pred_ll = pd.DataFrame({"mmsi": [1], "lon": [105.0], "lat": [8.5], "dt_s": [30.0]})
    positions_all = ais_window.assign(timestamp=[T, T, T - pd.Timedelta("3h")])
    out = rules.assign_status(det, ais_window, pred_ll, positions_all, transform, shape)
    s = out.set_index("det_id")
    assert s.loc["m", "ais_status"] == "matched"
    assert s.loc["u_cell", "ais_status"] == "unmatched"
    assert s.loc["u_near", "ais_status"] == "unmatched"
    assert s.loc["n", "ais_status"] == "no_coverage"
    assert s.loc["f", "ais_status"] == "unmatched"
    assert set(out.ais_status) <= set(AIS_STATUS_VALUES)
    assert s.dark_lead.to_dict() == {"m": False, "u_cell": True, "u_near": True, "n": False, "f": False}  # fixed is never a lead
    assert s.loc["n", "nearest_ais_mmsi"] == 1 and s.loc["n", "nearest_ais_dist_m"] > 500_000
    assert s.loc["m", "n_ais_10km"] == 2 and s.loc["n", "n_ais_10km"] == 0
    # reach: cell of (105.0, 8.5) heard in 2 of 2 recorded hours; the cell of n never
    assert s.loc["m", "ais_reach"] == 1.0 and s.loc["n", "ais_reach"] == 0.0
    assert (out.ais_recorded_hours == 2).all()


def test_empty_ais_window_gives_no_coverage():
    transform, shape = _grid()
    det = pd.DataFrame({"det_id": ["a"], "ais_status": ["unmatched"], "lon": [105.0], "lat": [8.5]})
    empty = pd.DataFrame(columns=["mmsi", "timestamp", "lon", "lat"])
    out = rules.assign_status(det, empty, pd.DataFrame(), empty, transform, shape)
    assert out.ais_status.iloc[0] == "no_coverage" and pd.isna(out.nearest_ais_mmsi.iloc[0]) and out.n_ais_10km.iloc[0] == 0
    assert not out.dark_lead.iloc[0]


# ----------------------------------------------------------------------------------------------- end to end on synthetic data
def test_match_scene_synthetic_schema_and_identity():
    objects, ais, truth = _synthetic()
    heard = truth[truth.has_ais].mmsi.astype("int64")
    static = _static(heard.tolist(), lengths=truth[truth.has_ais].length_m.tolist())
    # a SAR aircraft and a coast station right on top of contacts must be ignored by every rule
    junk = pd.DataFrame({"mmsi": [111574001, 2574001], "timestamp": [T, T], "lon": objects.lon.iloc[:2].values, "lat": objects.lat.iloc[:2].values,
                         "sog_kn": [0.0, 0.0], "cog_deg": [0.0, 0.0], "ais_class": ["A", "A"], "ship_name": [None, None]})
    ais_in = pd.concat([ais, junk.reindex(columns=ais.columns)], ignore_index=True)
    contacts, ais_only, counts = matching.match_scene(objects, SCENE, ais_in, static, ais_in, _grid(), box(*BBOX).buffer(1.0), log=lambda m: None)
    assert list(contacts.columns[:len(D1_COLUMNS)]) == D1_COLUMNS
    assert set(contacts.ais_status) <= set(AIS_STATUS_VALUES)
    assert (contacts.caveat == DARK_CAVEAT).all() and (contacts.research_only == False).all()  # noqa: E712
    assert (contacts.ais_source == "aisstream").all()
    m = contacts[contacts.ais_status == "matched"]
    assert len(m) >= 10
    assert m.mmsi.notna().all() and m.vessel_name.notna().all() and m.flag.str.startswith("Viet Nam").all()
    assert m.match_method.eq("track_interp_hungarian").all() and m.match_quality.isin(["high", "medium", "low"]).all()
    assert m.match_dist_m.notna().all() and m.match_dt_s.notna().all() and m.length_ais_m.notna().all()
    assert not m.mmsi.isin([111574001, 2574001]).any() and not contacts.nearest_ais_mmsi.isin([111574001, 2574001]).any()
    t = truth.set_index("det_id")
    assert (m.mmsi.astype("int64").values == t.loc[m.det_id, "mmsi"].values).mean() >= 0.95
    u = contacts[contacts.ais_status != "matched"]
    assert u.mmsi.isna().all() and u.match_method.isna().all() and u.vessel_name.isna().all()
    assert (u.ais_status == "unmatched").all()  # AIS was heard all over the synthetic box: nothing is no_coverage
    assert (u.dark_lead == ~u.match_ambiguous).all() and not m.dark_lead.any() and counts["n_dark_leads"] == int(u.dark_lead.sum())
    assert counts["n_ambiguous"] == int(contacts.match_ambiguous.sum()) and not m.match_ambiguous.any()
    assert contacts.nearest_ais_mmsi.notna().all() and (contacts.n_ais_10km >= 1).all()
    assert (contacts.ais_footprint_positions == counts["ais_footprint_positions"]).all() and counts["ais_footprint_positions"] == len(ais)
    assert counts["ais_aoi_positions"] == len(ais) and counts["ais_footprint_mmsi"] == ais.mmsi.nunique() == counts["ais_near_footprint_mmsi"]
    assert list(ais_only.columns) == AIS_ONLY_COLUMNS
    assert len(ais_only) >= 1 and (ais_only.ais_status == "ais_only").all() and ais_only.vessel_name.notna().all()
    assert counts["n_contacts"] == len(contacts) and counts["n_matched"] == len(m)


def test_outputs_roundtrip_schema_and_field_types(tmp_path):
    import pyogrio

    objects, ais, truth = _synthetic()
    static = _static(truth[truth.has_ais].mmsi.astype("int64").tolist())
    contacts, ais_only, counts = matching.match_scene(objects, SCENE, ais, static, ais, _grid(), box(*BBOX).buffer(1.0), log=lambda m: None)
    rec = _scene_record(objects, counts)
    outputs.rebuild([rec], contacts, ais_only, "verifier_v0_test", 0.6318, 1, T.isoformat(), out_dir=tmp_path, log=lambda m: None)
    gp = tmp_path / "live_S1D_20261008T2258.gpkg"
    layers = {name for name, _ in pyogrio.list_layers(gp)}
    assert {"contacts_4326", "contacts_utm49n", "ais_only_4326", "ais_only_utm49n", "scenes_4326", "scenes_utm49n", "about"} <= layers
    c = pyogrio.read_dataframe(gp, layer="contacts_utm49n")
    assert c.crs.to_string() == CRS_UTM_REGIONAL and len(c) == len(contacts)
    assert all(col in c.columns for col in D1_COLUMNS)
    assert set(c.ais_status) <= set(AIS_STATUS_VALUES) and (c.caveat == DARK_CAVEAT).all()
    for layer in ("contacts_4326", "contacts_utm49n"):
        info = pyogrio.read_info(gp, layer=layer)
        types = dict(zip(info["fields"], info["dtypes"]))
        for col, want in CONTACT_FIELD_TYPES.items():
            assert types[col] == want, (layer, col, types[col])   # integer and boolean fields, not REAL or TEXT
    info = pyogrio.read_info(gp, layer="ais_only_4326")
    types = dict(zip(info["fields"], info["dtypes"]))
    for col, want in AIS_ONLY_FIELD_TYPES.items():
        assert types[col] == want, (col, types[col])
    sc = pyogrio.read_dataframe(gp, layer="scenes_4326")
    assert list(sc.columns[:-1]) == SCENE_COLUMNS and (sc.caveat == DARK_CAVEAT).all()
    assert sc.ais_aoi_positions.iloc[0] == len(ais) and sc.ais_footprint_positions.iloc[0] == len(ais)
    about = pyogrio.read_dataframe(gp, layer="about").iloc[0]
    for key, needle in (("distance_gate", "500 m"), ("ais_window", "30 min"), ("match_quality_rule", "high ="), ("no_coverage_rule", "20 km"),
                        ("caveat", "does not mean illegal"), ("source_mid_table", "itu.int"), ("ais_mmsi_filter", "201000000"),
                        ("dark_lead_rule", "never leads"), ("reads", "retried"), ("threads", "I/O threads")):
        assert needle in about[key]
    assert (tmp_path / "live_contacts.gpkg").exists()
    s = json.loads((tmp_path / "live_summary.json").read_text())
    p = s["passes"]["live_S1D_20261008T2258"]
    assert p["contacts"] == len(contacts) and sum(p["ais_status"].values()) == len(contacts)
    assert p["dark_leads"] == int(contacts.dark_lead.sum()) and p["ais_status_by_class"]["high"]["matched"] == p["ais_status"]["matched"]
    assert p["ais_window"]["footprint_positions_sum_over_scenes"] == len(ais) and p["ais_window"]["aoi_mmsi_max_per_scene"] == ais.mmsi.nunique()
    assert p["ais_only"] == len(ais_only) and "by_length_bin" in p and s["totals"]["contacts"] == len(contacts)


def test_empty_ais_only_layer_keeps_field_types(tmp_path):
    import pyogrio

    objects, ais, truth = _synthetic()
    contacts, _, counts = matching.match_scene(objects, SCENE, ais, _static([]), ais, _grid(), box(*BBOX).buffer(1.0), log=lambda m: None)
    outputs.rebuild([_scene_record(objects, counts)], contacts, pd.DataFrame(), None, None, 1, T.isoformat(), out_dir=tmp_path, log=lambda m: None)
    info = pyogrio.read_info(tmp_path / "live_S1D_20261008T2258.gpkg", layer="ais_only_4326")
    types = dict(zip(info["fields"], info["dtypes"]))
    assert list(info["fields"]) == AIS_ONLY_COLUMNS and info["features"] == 0
    for col, want in AIS_ONLY_FIELD_TYPES.items():
        assert types[col] == want, (col, types[col])
    assert types["lon"] == "float64" and types["vessel_name"] == "object"


# ----------------------------------------------------------------------------------------------- retried reads and detection
class _FlakyDS:
    """Fake rasterio dataset: the first `fail` reads raise RasterioIOError, then reads return a constant tile."""

    def __init__(self, fail: int, value: int):
        self.fail, self.value, self.reads, self.closed = fail, value, 0, False

    def read(self, band, window=None, **kw):
        self.reads += 1
        if self.reads <= self.fail:
            from rasterio.errors import RasterioIOError

            raise RasterioIOError("Read failed. See previous exception for details.")
        return np.full((int(window.height), int(window.width)), self.value, np.uint16)

    def close(self):
        self.closed = True


def test_live_scene_retries_tile_reads(monkeypatch):
    import threading

    from rasterio.windows import Window

    opened, cleared, threads = [], [], []
    s = scene_mod.LiveGRDScene("GRD/2026/10/8/IW/DV/S1D_IW_GRDH_1SDV_20261008T230043_20261008T230108_004930_0094A6_B8C6",
                               io_threads=1, retries=3, backoff=(0.0,), log=lambda m: None)   # one fetch thread: one dataset handle

    def fake_open(pol):
        ds = _FlakyDS(fail=1 if not opened else 0, value=7)   # the first connection is bad once, later ones are good
        opened.append(ds)
        threads.append(threading.current_thread().name)
        return ds

    monkeypatch.setattr(s, "_open", fake_open)
    monkeypatch.setattr(scene_mod, "_clear_curl_cache", lambda url: cleared.append(url))
    dn = s.read_dn("VV", Window(0, 0, 1500, 1200))           # 4 tiles; the first read fails, 3 succeed, 1 is re-read
    assert dn.shape == (1200, 1500) and (dn == 7).all()
    assert s.io_retries == 1 and len(opened) == 2 and threads[0] != threads[1]   # the retry ran in a renewed pool (new thread)
    assert cleared == [s.href("VV")]                        # GDAL's negative cache for the file was dropped before the retry
    # a read that never succeeds raises after `retries` attempts, each retry on a fresh thread
    monkeypatch.setattr(s, "_open", lambda pol: _FlakyDS(fail=99, value=0))
    with pytest.raises(Exception):
        s.read_tile("VH", Window(0, 0, 10, 10))
    assert s.io_retries == 3 and cleared[1:] == [s.href("VH")] * 2
    with pytest.raises(Exception):
        s.read_dn("VH", Window(0, 0, 10, 10))
    assert s.io_retries == 5


def test_http_retry_resets_session_and_gives_up():
    calls, resets = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("404 Client Error: Not Found")
        return "ok"

    assert scene_mod.http_retry(flaky, attempts=4, waits=(0.0,), reset=lambda: resets.append(1), log=lambda m: None) == "ok"
    assert len(calls) == 3 and len(resets) == 2
    calls.clear()
    with pytest.raises(RuntimeError):
        scene_mod.http_retry(flaky, attempts=2, waits=(0.0,), reset=lambda: resets.append(1), log=lambda m: None)
    assert len(calls) == 2


def _ctx(tmp_path, **kw):
    kw.setdefault("rebuild_tested", False)  # never read the mirror in a test
    kw.setdefault("do_weather", False)      # nor GFS or Himawari
    ctx = watch.Context(workers=1, do_cnn=False, do_persistence=False, out_dir=tmp_path / "live", lock_path=tmp_path / "cycle.lock", **kw)
    g = box(*BBOX).buffer(1.0)
    ctx._aoi, ctx._grid, ctx._model = (g, g.buffer(-0.05)), _grid(), (None, None, None)
    return ctx


def test_detect_with_retry(tmp_path, monkeypatch):
    calls = []

    def flaky(path, aoi, inner, **kw):
        calls.append(path)
        if len(calls) < 3:
            raise RuntimeError("TIFFFillTile: Read error, got 0 bytes")
        return pd.DataFrame({"det_id": []}), {"tested_km2": 1.0}

    rec = {"product_id": "P", "path": "GRD/x/P"}
    objects, stats, used = watch.detect_with_retry(rec, _ctx(tmp_path), attempts=3, waits=(0.0,), process=flaky, log=lambda m: None)
    assert used == 3 and len(calls) == 3 and stats["tested_km2"] == 1.0
    calls.clear()
    with pytest.raises(RuntimeError):
        watch.detect_with_retry(rec, _ctx(tmp_path), attempts=2, waits=(0.0,), process=flaky, log=lambda m: None)
    assert len(calls) == 2


class _FakeAIS:
    """Stand-in for darkvessel.ais.aisstream inside watch: a fixed AIS frame and static table."""

    def __init__(self, positions, static):
        self.positions, self.static = positions, static

    def load_positions(self, root=None, start=None, end=None):
        p = self.positions
        if start is not None:
            p = p[p.timestamp >= start]
        if end is not None:
            p = p[p.timestamp <= end]
        return p.reset_index(drop=True)

    def load_static(self, root=None):
        return self.static

    def latest_static(self, static):
        return static

    def recorded_hours(self, positions):
        return pd.DatetimeIndex(sorted(positions.timestamp.dt.floor("h").unique())) if len(positions) else pd.DatetimeIndex([], tz="UTC")


def _mirror_rec(pid="S1D_IW_GRDH_1SDV_20261008T225918_20261008T225943_004928_009496_AAAA"):
    return {"product_id": pid, "path": f"GRD/2026/10/8/IW/DV/{pid}", "mission": "S1D", "start": "2026-10-08T22:59:18+00:00",
            "stop": "2026-10-08T22:59:43+00:00", "orbit": 4928, "footprint_wkt": box(*BBOX).buffer(0.05).wkt, "intersects_aoi": True,
            "aoi_overlap_km2": 4000.0, "pass_dir": "DESCENDING", "orbit_rel": 164}


def test_process_record_error_attempts_and_done(tmp_path, monkeypatch):
    objects, ais, truth = _synthetic()
    monkeypatch.setattr(watch, "aisstream", _FakeAIS(ais, _static(truth[truth.has_ais].mmsi.astype("int64").tolist())))
    monkeypatch.setattr(watch, "DETECT_WAITS_S", (0.0,))
    monkeypatch.setattr(watch, "PASSES_PATH", tmp_path / "passes.json")
    ctx, ck = _ctx(tmp_path), watch.Checkpoint(tmp_path / "scenes")
    rec = _mirror_rec()

    def failing(path, aoi, inner, **kw):
        raise RuntimeError("Read failed")

    st = watch.process_record(rec, ctx, ck, [], process=failing, log=lambda m: None)
    assert st["status"] == "error" and st["attempts_total"] == 3 and not ck.done(rec["product_id"])
    st = watch.process_record(rec, ctx, ck, [], process=failing, log=lambda m: None)
    assert st["attempts_total"] == 6                                        # attempts accumulate across cycles

    def working(path, aoi, inner, **kw):
        return objects.copy(), {"tested_km2": 3500.0, "blocks_processed": 4, "orbit_rel": 164, "pass_dir": "DESCENDING", "io_retries": 2,
                                "n_candidates_pre_rules": len(objects), "n_cnn_scored": 0, "cnn_model_id": None, "cnn_threshold": None, "runtime_s": 1.0}

    st = watch.process_record(rec, ctx, ck, [], process=working, log=lambda m: None)
    assert st["status"] == "done" and ck.done(rec["product_id"]) and st["detect_attempts"] == 1 and st["attempts_total"] == 7 and st["io_retries"] == 2
    assert st["n_contacts"] == len(objects) and st["n_matched"] >= 10 and st["ais_footprint_positions"] == len(ais) and st["ais_aoi_positions"] == len(ais)
    assert st["run_id"].startswith("live_S1D_20261008T2259")
    recs, contacts, ais_only = ck.load_all()
    assert len(recs) == 1 and list(contacts.columns[:len(D1_COLUMNS)]) == D1_COLUMNS and len(contacts) == len(objects)
    # no AIS in the window: skipped for good
    empty = _FakeAIS(ais.iloc[:0], _static([]))
    monkeypatch.setattr(watch, "aisstream", empty)
    st = watch.process_record(_mirror_rec("S1D_IW_GRDH_1SDV_20261008T230500_20261008T230525_004928_009496_BBBB"), ctx, ck, [], log=lambda m: None)
    assert st["status"] == "skipped_no_ais" and ck.done("S1D_IW_GRDH_1SDV_20261008T230500_20261008T230525_004928_009496_BBBB")


# ----------------------------------------------------------------------------------------------- checkpoints, lock, no-op rerun
def test_checkpoint_skip_and_resume(tmp_path):
    ck = watch.Checkpoint(tmp_path / "scenes")
    assert not ck.done("X")
    ck.mark("X", "error", error="boom", attempts_total=3)
    assert not ck.done("X") and ck.attempts("X") == 3   # errors are retried
    ck.mark("X", "skipped_no_ais")
    assert ck.done("X")                           # a final skip is never redone
    ck.mark("Y", "done", run_id="live_S1D_20261008T2258", n_contacts=2)
    ck.write_tables("Y", pd.DataFrame({"det_id": ["a", "b"], "run_id": "live_S1D_20261008T2258"}), pd.DataFrame())
    recs, contacts, ais_only = ck.load_all()
    assert [r["product_id"] for r in recs] == ["Y"] and len(contacts) == 2 and ais_only.empty
    assert not list((tmp_path / "scenes").glob("*.tmp"))


def test_cycle_rerun_is_noop_and_lock_is_respected(tmp_path, monkeypatch):
    objects, ais, truth = _synthetic()
    static = _static(truth[truth.has_ais].mmsi.astype("int64").tolist())
    contacts, ais_only, counts = matching.match_scene(objects, SCENE, ais, static, ais, _grid(), box(*BBOX).buffer(1.0), log=lambda m: None)
    ck = watch.Checkpoint(tmp_path / "scenes")
    pid = "SYN_PRODUCT"
    ck.mark(pid, "done", **{k: v for k, v in _scene_record(objects, counts).items() if k not in ("product_id", "status")})
    ck.write_tables(pid, contacts, ais_only)
    ctx = _ctx(tmp_path)
    asked = []

    def candidates(aoi, since, now, log):
        asked.append(now)
        return [dict(_mirror_rec(pid), start=T.isoformat(), stop=T.isoformat())]

    since = pd.Timestamp("2026-10-08T14:30Z")
    r1 = watch.cycle(ctx, ck, since, candidates=candidates, log=lambda m: None)
    assert r1 == {"candidates": 1, "new": 0, "processed": 0} and ctx.summary_path.exists()
    files = sorted(ctx.out_dir.glob("*"))
    assert {f.name for f in files} >= {"live_S1D_20261008T2258.gpkg", "live_contacts.gpkg", "live_summary.json"}
    before = {f.name: (f.stat().st_mtime_ns, f.stat().st_size) for f in files}
    r2 = watch.cycle(ctx, ck, since, candidates=candidates, log=lambda m: None)
    assert r2["processed"] == 0 and len(asked) == 2
    after = {f.name: (f.stat().st_mtime_ns, f.stat().st_size) for f in sorted(ctx.out_dir.glob("*"))}
    assert after == before                                   # nothing processed: the products are not rewritten
    assert not list(ctx.out_dir.glob("*.tmp*"))
    # the lock: while another process (here: another open file description) holds it, a non-blocking cycle does nothing
    with watch.CycleLock(ctx.lock_path):
        r3 = watch.cycle(ctx, ck, since, candidates=candidates, blocking=False, log=lambda m: None)
    assert r3["locked"] is True and len(asked) == 2
    r4 = watch.cycle(ctx, ck, since, candidates=candidates, blocking=False, log=lambda m: None)   # released: runs again
    assert "locked" not in r4 and len(asked) == 3
    assert str(os.getpid()) in ctx.lock_path.read_text()


# ----------------------------------------------------------------------------------------------- candidates, pass naming
def test_mirror_candidates_with_fake_listing(tmp_path):
    aoi = box(100.0, 5.0, 120.0, 22.0)
    since = pd.Timestamp("2026-10-08T14:30Z")
    now = pd.Timestamp("2026-10-09T02:00Z")
    ids = ["S1D_IW_GRDH_1SDV_20261008T225900_20261008T225925_004928_009496_AAAA",   # in window, in AOI
           "S1D_IW_GRDH_1SDV_20261008T110000_20261008T110025_004920_009400_BBBB",   # before since
           "S1C_IW_GRDH_1SDV_20261008T150000_20261008T150025_009790_013800_CCCC",   # outside the time-of-day windows
           "S1D_IW_GRDH_1SDV_20261009T014000_20261009T014025_004930_009500_DDDD",   # too recent (AIS window not complete)
           "S1D_IW_GRDH_1SDV_20261008T230500_20261008T230525_004928_009496_EEEE"]   # in window, outside the AOI
    listing = lambda day: [f"GRD/{day.year}/{day.month}/{day.day}/IW/DV/{i}" for i in ids] if day.day == 8 else []  # noqa: E731
    fetched = []

    def record(path, aoi_geom):
        pid = path.rsplit("/", 1)[-1]
        fetched.append(pid)
        inside = pid.endswith("AAAA")
        return {"product_id": pid, "path": path, "mission": pid[:3], "start": "2026-10-08T22:59:00+00:00", "stop": "2026-10-08T22:59:25+00:00",
                "orbit": 4928, "footprint_wkt": "POLYGON EMPTY", "intersects_aoi": inside, "aoi_overlap_km2": 5000.0 if inside else 0.0}

    out = watch.mirror_candidates(aoi, since, now, list_day=listing, record=record, cached=lambda: [], log=lambda m: None)
    assert [r["product_id"][-4:] for r in out] == ["AAAA"]
    assert set(p[-4:] for p in fetched) == {"AAAA", "EEEE"}   # only time-filtered products cost a productInfo fetch
    # the listing fails every time: the cached records in range are still candidates (an errored scene is retried)
    cached = [record(f"GRD/2026/10/8/IW/DV/{i}", aoi) for i in ids]
    calls = []

    def broken(day):
        calls.append(day)
        raise ConnectionError("IncompleteRead")

    out = watch.mirror_candidates(aoi, since, now, list_day=broken, record=record, cached=lambda: cached, waits=(), log=lambda m: None)
    assert [r["product_id"][-4:] for r in out] == ["AAAA"] and len(calls) == 2   # one attempt per day, no sleep with waits=()


def test_run_id_from_plan_and_fallback(tmp_path):
    plan = [{"start_utc": pd.Timestamp("2026-10-08T22:58:28Z"), "stop_utc": pd.Timestamp("2026-10-08T23:04:55Z"), "mission": "S1D"}]
    rec = {"mission": "S1D", "orbit": 4928, "start": "2026-10-08T23:01:10+00:00"}
    pp = tmp_path / "passes.json"
    assert watch.run_id_for(rec, plan, pp) == "live_S1D_20261008T2258"
    assert watch.run_id_for(rec, [], pp) == "live_S1D_20261008T2258"          # stored: stable even if the plan vanishes
    rec2 = {"mission": "S1C", "orbit": 9800, "start": "2026-10-09T22:50:47+00:00"}
    assert watch.run_id_for(rec2, plan, pp) == "live_S1C_20261009T2250"       # no plan segment: first scene's minute
    assert json.loads(pp.read_text()) == {"S1D_4928": "live_S1D_20261008T2258", "S1C_9800": "live_S1C_20261009T2250"}


def test_predict_positions_carries_sog_and_reports():
    ais = pd.DataFrame([(7, T - pd.Timedelta("2min"), 105.0, 8.5, 6.0, 90.0, 20.0, "A"), (7, T + pd.Timedelta("3min"), 105.01, 8.5, 6.0, 90.0, 20.0, "A")],
                       columns=["mmsi", "timestamp", "lon", "lat", "sog_kn", "cog_deg", "length_m", "ais_class"])
    p = predict_positions(ais, T, rules.LIVE_MATCH)
    assert p.method.iloc[0] == "interp" and p.n_reports.iloc[0] == 2 and p.sog_kn.iloc[0] == 6.0 and p.ais_class.iloc[0] == "A"


# ----------------------------------------------------------------------------------------------- dense traffic (round 3)
TP = pd.Timestamp("2026-10-10T10:33:02Z")       # middle of a slice
AZ = np.array([np.sin(np.radians(-11.0)), np.cos(np.radians(-11.0))])   # satellite motion on the ground (ascending)
RG = np.array([AZ[1], -AZ[0]])                  # right-looking: away from the ground track
INC, SLANT, VSAT = 35.0, 870_000.0, 7598.0
ORIGIN = (114.00, 22.15)                         # off Hong Kong, UTM 49N


def _utm():
    from pyproj import Transformer

    return (Transformer.from_crs("EPSG:4326", CRS_UTM_REGIONAL, always_xy=True).transform,
            Transformer.from_crs(CRS_UTM_REGIONAL, "EPSG:4326", always_xy=True).transform)


def _radar_image(x, y, vx, vy):
    """Where Sentinel-1 images a target at (x, y) moving at (vx, vy) m/s: the azimuth shift -(R/V) v_r along AZ."""
    v_r = (vx * RG[0] + vy * RG[1]) * np.sin(np.radians(INC))
    s = -(SLANT / VSAT) * v_r
    return x + s * AZ[0], y + s * AZ[1]


def _dense_anchorage(seed=7):
    """60 anchored vessels at 300 m spacing, 20 moving at 5 to 20 kn, 30 contacts with 50 to 150 m position noise and
    the azimuth shift of moving targets, plus one explicit trap (a 20 kn ship imaged 300 m from an undetected anchored
    ship) and dark boats. The anchorage lies 68 km along track from the slice middle (contact times about +10 s)."""
    rng = np.random.default_rng(seed)
    fwd, inv = _utm()
    ox, oy = fwd(*ORIGIN)
    ox, oy = ox + 68_000 * AZ[0], oy + 68_000 * AZ[1]
    vessels = []
    k = 0
    for r in range(6):
        for c in range(10):
            vessels.append({"mmsi": 477000000 + k, "x": ox + c * 300.0, "y": oy + r * 300.0, "sog": rng.uniform(0, 0.3),
                            "cog": rng.uniform(0, 360), "kind": "anchored"})
            k += 1
    for j in range(20):
        vessels.append({"mmsi": 563000000 + j, "x": ox + rng.uniform(-1500, 4200), "y": oy + rng.uniform(-1500, 3000),
                        "sog": rng.uniform(5, 20), "cog": rng.uniform(0, 360), "kind": "moving"})
    # the trap, 4 km east: ship M at 20 kn straight along the range direction, anchored ship A 300 m from M's image
    tx, ty = ox + 7000.0, oy
    cog_m = float(np.degrees(np.arctan2(RG[0], RG[1])))
    vxm, vym = 20 * 0.514444 * RG[0], 20 * 0.514444 * RG[1]
    ix, iy = _radar_image(tx, ty, vxm, vym)
    vessels.append({"mmsi": 412000001, "x": tx, "y": ty, "sog": 20.0, "cog": cog_m, "kind": "trap_moving"})
    vessels.append({"mmsi": 412000002, "x": ix + 300 * RG[0], "y": iy + 300 * RG[1], "sog": 0.0, "cog": 0.0, "kind": "trap_anchored"})
    v = pd.DataFrame(vessels)
    v["vx"] = v.sog * 0.514444 * np.sin(np.radians(v.cog))
    v["vy"] = v.sog * 0.514444 * np.cos(np.radians(v.cog))
    # AIS reports: anchored every 180 s with 10 m jitter, moving every 30 s, from -30 to +30 min
    rows = []
    for _, s in v.iterrows():
        step = 180 if s.kind in ("anchored", "trap_anchored") else 30
        for t in np.arange(-1800 + rng.uniform(0, step), 1800, step):
            lo, la = inv(s.x + s.vx * t + rng.normal(0, 10), s.y + s.vy * t + rng.normal(0, 10))
            rows.append({"mmsi": int(s.mmsi), "timestamp": TP + pd.Timedelta(seconds=float(t)), "lon": lo, "lat": la,
                         "sog_kn": round(float(s.sog), 1), "cog_deg": round(float(s.cog), 1), "ais_class": "A", "ship_name": None,
                         "length_m": 120.0})
    ais = pd.DataFrame(rows)
    # radar contacts: 20 anchored, 8 moving, the trap ship, 2 dark boats far from everything, 2 at grid-square centres
    det_anch = rng.choice(np.where(v.kind == "anchored")[0], 20, replace=False)
    det_mov = rng.choice(np.where(v.kind == "moving")[0], 8, replace=False)
    det_rows = []
    t_c = 68_000 / 6800.0                        # contact time offset from the slice middle, s
    for i in list(det_anch) + list(det_mov) + [int(np.where(v.kind == "trap_moving")[0][0])]:
        s = v.iloc[i]
        x, y = s.x + s.vx * t_c, s.y + s.vy * t_c
        x, y = _radar_image(x, y, s.vx, s.vy)
        ang, d = rng.uniform(0, 2 * np.pi), (40.0 if s.kind == "trap_moving" else rng.uniform(50, 150))
        det_rows.append({"x": x + d * np.cos(ang), "y": y + d * np.sin(ang), "truth": int(s.mmsi), "kind": s.kind})
    undetected = sorted(set(np.where(v.kind == "anchored")[0]) - set(det_anch))
    centres = []
    for r in range(5):
        for c in range(9):
            corner = [r * 10 + c, r * 10 + c + 1, (r + 1) * 10 + c, (r + 1) * 10 + c + 1]
            if all(q in undetected for q in corner):
                centres.append((ox + c * 300 + 150, oy + r * 300 + 150))
    assert len(centres) >= 2, "seed gives too few empty grid squares"
    for x, y in centres[:2]:
        det_rows.append({"x": x, "y": y, "truth": None, "kind": "dark_centre"})
    for x, y in ((ox - 6000, oy - 6000), (ox + 3000, oy + 6000)):
        det_rows.append({"x": x, "y": y, "truth": None, "kind": "dark_far"})
    d = pd.DataFrame(det_rows)
    lo, la = inv(d.x.to_numpy(), d.y.to_numpy())
    n = len(d)
    objects = pd.DataFrame({"det_id": [f"DEN_{i:03d}" for i in range(n)], "confidence": "high", "length_est_m": 150.0, "lon": lo, "lat": la,
                            "scene_id": "SYN_DENSE", "mission": "S1D", "acq_utc": TP.isoformat(), "row": 5000.0, "col": 5000.0,
                            "inc_angle_deg": INC, "pol_class": "VV+VH", "n_pixels": 30, "scr_vv_db": 15.0, "scr_vh_db": 10.0,
                            "low_reason": "", "persist_dates": 0, "persist_dates_checked": 0, "n_low_1km": 0, "near_fixed_m": np.nan,
                            "cnn_score": 0.9, "cnn_vessel": True,
                            "az_time_utc": (TP + pd.Timedelta(seconds=t_c)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                            "slant_range_m": SLANT, "az_e": AZ[0], "az_n": AZ[1], "rg_e": RG[0], "rg_n": RG[1]})
    return objects, ais, d.assign(det_id=objects.det_id), v


def _dense_scene(objects):
    from shapely.geometry import box as _box

    lo, la = objects.lon.to_numpy(), objects.lat.to_numpy()
    fp = _box(lo.min() - 0.1, la.min() - 0.1, lo.max() + 0.1, la.max() + 0.1)
    return {"scene_time": TP, "run_id": "live_S1D_20261010T1032", "footprint": fp, "product_id": "SYN_DENSE", "mission": "S1D",
            "acq_utc": TP.isoformat(), "pass_dir": "ASCENDING", "orbit_rel": 11, "sat_speed_ms": VSAT}


def test_dense_anchorage_correct_or_conservative():
    from darkvessel.live import assign as asg

    objects, ais, truth, vessels = _dense_anchorage()
    static = _static(vessels.mmsi.astype("int64").tolist(), lengths=[120.0] * len(vessels))
    scene = _dense_scene(objects)
    transform, shape = _grid()
    contacts, ais_only, counts = matching.match_scene(objects, scene, ais, static, ais, (transform, shape), scene["footprint"], log=lambda m: None)
    t = truth.set_index("det_id")
    c = contacts.set_index("det_id")
    m = c[c.ais_status == "matched"]
    wrong = m[m.mmsi.astype("int64").to_numpy() != t.loc[m.index, "truth"].fillna(-1).astype("int64").to_numpy()]
    assert len(wrong) == 0, f"wrong identities: {wrong[['mmsi']].join(t[['truth', 'kind']]).to_dict('index')}"
    ais_det = t[t.truth.notna()]
    assert len(m) >= 0.5 * len(ais_det)          # 29 contacts carry AIS; 150 m noise in a 300 m grid is often truly ambiguous
    amb = c[c.match_ambiguous & c.index.isin(ais_det.index)]
    for i, r in amb.iterrows():                  # conservative, not wrong: the right vessel is always among the candidates
        assert str(int(t.loc[i, "truth"])) in str(r.ambiguous_mmsi).split(";")
    mov = t[t.kind == "moving"].index
    assert (c.loc[mov, "ais_status"] == "matched").sum() >= 6               # azimuth-shifted ships are found
    assert c.loc[t[t.kind == "trap_moving"].index[0], "ais_status"] == "matched"
    assert c.loc[t[t.kind == "trap_moving"].index[0], "mmsi"] == 412000001
    for i in t[t.kind == "dark_far"].index:                               # nothing within any gate: a dark lead
        assert c.loc[i, "ais_status"] == "unmatched" and c.loc[i, "dark_lead"] and not c.loc[i, "match_ambiguous"]
    for i in t[t.kind == "dark_centre"].index:                            # 212 m from four undetected ships: never named
        assert c.loc[i, "ais_status"] != "matched" and c.loc[i, "match_ambiguous"] and not c.loc[i, "dark_lead"]
        assert len(str(c.loc[i, "ambiguous_mmsi"]).split(";")) >= 2
    # moving matches carry the shift, and the corrected distance is the smaller one
    mm = m[m.index.isin(mov)]
    big = mm[mm.az_shift_m.abs() > 150]
    assert len(big) >= 2 and (big.match_dist_m < big.match_dist_uncorr_m).all()
    assert counts["azimuth_correction"] and counts["azimuth_check"]["vessels"] >= 5
    ac = counts["azimuth_check"]
    assert ac["corrected_median_nearest_m"] < ac["uncorrected_median_nearest_m"] < ac["sign_flipped_median_nearest_m"]
    # paired reading: per vessel, the corrected position is the one with the nearest contact more often than either other
    assert ac["vessels_compared"] >= 5 and ac["closest_corrected"] > max(ac["closest_uncorrected"], ac["closest_sign_flipped"])
    assert ac["closest_uncorrected"] + ac["closest_corrected"] + ac["closest_sign_flipped"] == ac["vessels_compared"]
    # AIS-only: the undetected anchored ships; the ones a dark centre boat competed for are marked
    assert ais_only.ambiguous_det_id.notna().sum() >= 2 and ais_only.mmsi.isin(vessels[vessels.kind == "anchored"].mmsi).sum() >= 30
    # the old matcher (scene time, no shift, most pairs) names the trap contact after the anchored ship
    import geopandas as gpd

    det = gpd.GeoDataFrame(objects.copy(), geometry=gpd.points_from_xy(objects.lon, objects.lat), crs="EPSG:4326")
    old, _ = match_detections(det, matching.ais_with_static(ais, static), TP, rules.LIVE_MATCH)
    trap = old.set_index("det_id").loc[t[t.kind == "trap_moving"].index[0]]
    assert trap.ais_status == "matched" and int(trap.mmsi) == 412000002
    # without the correction the same scene loses or confuses ships under way, never more right than with it
    c2, _, _ = matching.match_scene(objects, scene, ais, static, ais, (transform, shape), scene["footprint"], log=lambda m: None, correct=False)
    m2 = c2.set_index("det_id")
    m2 = m2[m2.ais_status == "matched"]
    right2 = (m2.mmsi.astype("int64").to_numpy() == t.loc[m2.index, "truth"].fillna(-1).astype("int64").to_numpy()).sum()
    assert right2 < len(m)
    assert asg.AMBIG_MARGIN_M == 100.0 and asg.AMBIG_RATIO == 1.5


def test_rafted_vessels_are_ambiguous_and_chain_is_not_forced():
    from darkvessel.live import assign as asg

    # two ships rafted 40 m apart (bunkering), one radar return between them: no identity, not a lead
    fwd, inv = _utm()
    x0, y0 = fwd(*ORIGIN)
    lo, la = inv(np.array([x0, x0 + 40.0]), np.array([y0, y0]))
    ais = pd.DataFrame({"mmsi": [413000001, 413000002] * 2, "timestamp": [TP - pd.Timedelta("1min")] * 2 + [TP + pd.Timedelta("2min")] * 2,
                        "lon": np.r_[lo, lo], "lat": np.r_[la, la], "sog_kn": 0.0, "cog_deg": 0.0, "ais_class": "A", "ship_name": None})
    clo, cla = inv(x0 + 25.0, y0 + 10.0)
    contacts = pd.DataFrame({"det_id": ["r1"], "confidence": ["high"], "length_est_m": [200.0], "lon": [clo], "lat": [cla]})
    out, ao, diag = asg.assign(contacts, ais, TP, rules.LIVE_MATCH)
    assert out.ais_status.iloc[0] == "unmatched" and out.match_ambiguous.iloc[0]
    assert set(out.ambiguous_mmsi.iloc[0].split(";")) == {"413000001", "413000002"} and not out.dark_candidate.iloc[0]
    # both rafted vessels are held back (not counted as radar misses), not only the one the assignment tried
    assert diag["ambiguous_pairs"] == 1 and ao.ambiguous_det_id.notna().sum() == 2 and set(ao.ambiguous_det_id) == {"r1"}
    # chain: c1 is 50 m from v1 and 480 m from v2, c2 is 450 m from v1 only. Most pairs would give c1-v2 and c2-v1;
    # the cost with unpaired vessels pays the gate keeps c1-v1, and c2 stays unmatched
    D = np.array([[50.0, 480.0], [450.0, 1e9]])
    feas = D <= 500.0
    pairs = asg._assign_component(D, feas, np.array([500.0, 500.0]))
    assert pairs == [(0, 0)]


def test_gear_beacons_never_named_but_count_as_coverage():
    objects, ais, truth = _synthetic()
    heard = truth[truth.has_ais].mmsi.astype("int64").tolist()
    names = [f"VESSEL {m}" for m in heard]
    names[0] = "NET-82542-84%"                                   # a net beacon sitting on a ship-station MMSI
    static = _static(heard, lengths=truth[truth.has_ais].length_m.tolist(), names=names)
    contacts, ais_only, counts = matching.match_scene(objects, SCENE, ais, static, ais, _grid(), box(*BBOX).buffer(1.0), log=lambda m: None)
    assert counts["ais_gear_beacons_excluded"] == 1
    assert not contacts.mmsi.isin([heard[0]]).any() and not contacts.nearest_ais_mmsi.isin([heard[0]]).any()
    assert not ais_only.mmsi.isin([heard[0]]).any()
    assert matching.gear_mmsi(ais.assign(ship_name=None), static) == {heard[0]}


def _annotation_xml():
    """A minimal Sentinel-1 annotation: 2 x 3 geolocation grid, ascending, right-looking, two orbit vectors."""
    pts = []
    for line, t in ((0, "2026-10-10T10:32:47.500000"), (1000, "2026-10-10T10:32:49.000000")):
        for pixel, srt, inc in ((0, 5.33e-3, 30.8), (10000, 5.80e-3, 38.0), (20000, 6.40e-3, 45.9)):
            k = 1.0 / np.cos(np.radians(20.6))              # degrees of longitude per degree of latitude, in metres
            lat = 20.6 + line * 0.0001 * np.cos(np.radians(-11)) + pixel * 0.00002 * np.sin(np.radians(11))
            lon = 112.4 + k * (line * 0.0001 * np.sin(np.radians(-11)) + pixel * 0.00002 * np.cos(np.radians(11)))
            pts.append(f"<geolocationGridPoint><azimuthTime>{t}</azimuthTime><slantRangeTime>{srt}</slantRangeTime><line>{line}</line>"
                       f"<pixel>{pixel}</pixel><latitude>{lat}</latitude><longitude>{lon}</longitude><height>0</height>"
                       f"<incidenceAngle>{inc}</incidenceAngle><elevationAngle>27</elevationAngle></geolocationGridPoint>")
    orbit = "".join(f"<orbit><time>2026-10-10T10:32:{s}.0</time><frame>Earth Fixed</frame><position><x>0</x><y>0</y><z>0</z></position>"
                    f"<velocity><x>2194.6</x><y>-1345.5</y><z>7148.4</z></velocity></orbit>" for s in (40, 50))
    return (f"<product><generalAnnotation><productInformation><pass>Ascending</pass><platformHeading>-1.228e+01</platformHeading>"
            f"</productInformation><orbitList count='2'>{orbit}</orbitList></generalAnnotation><imageAnnotation><imageInformation>"
            f"<productFirstLineUtcTime>2026-10-10T10:32:47.500000</productFirstLineUtcTime><azimuthTimeInterval>1.5e-03</azimuthTimeInterval>"
            f"</imageInformation></imageAnnotation><geolocationGrid><geolocationGridPointList count='6'>{''.join(pts)}"
            f"</geolocationGridPointList></geolocationGrid></product>").encode()


def test_sar_geometry_from_annotation():
    from darkvessel.s1.grd import Geocoder

    xml = _annotation_xml()
    geo, info = scene_mod.sar_geometry(xml, [500.0, 0.0], [10000.0, 0.0], Geocoder.from_annotation(xml))
    assert list(geo.columns) == scene_mod.GEOMETRY_COLUMNS
    assert geo.az_time_utc.iloc[0].startswith("2026-10-10T10:32:48.25") and geo.az_time_utc.iloc[1].startswith("2026-10-10T10:32:47.5")
    assert geo.slant_range_m.iloc[1] == pytest.approx(5.33e-3 * 299_792_458.0 / 2, rel=1e-6)
    heading = np.degrees(np.arctan2(geo.az_e, geo.az_n))
    look = np.degrees(np.arctan2(geo.rg_e, geo.rg_n))
    assert np.allclose(heading, -11.0, atol=1.0) and np.allclose(look, 79.0, atol=1.0)   # right-looking: track + 90
    assert info["platform_heading_deg"] == pytest.approx(-12.28) and info["sat_speed_ms"] == pytest.approx(7597.6, abs=1.0)
    assert info["pass"] == "Ascending" and info["line_interval_s"] == 1.5e-3


# ----------------------------------------------------------------------------------------------- live weather join
def _fake_gfs(fail_first: int = 1):
    """GFS fetch stand-in: the first `fail_first` cycles answer 404, then a global 0.25 degree grid of 6 m/s with 14 m/s
    east of 114.0E."""
    import requests
    from rasterio.transform import from_origin

    calls = []

    def fetch(cycle, fh, cache_dir):
        calls.append((cycle, fh))
        if len(calls) <= fail_first:
            resp = requests.Response()
            resp.status_code, resp.url = 404, f"https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{cycle:%Y%m%d}/{cycle:%H}/x.idx"
            raise requests.HTTPError("404 Client Error", response=resp)
        spd = np.full((721, 1440), 6.0, np.float32)
        spd[:, int((114.0 + 0.125) / 0.25):int(120 / 0.25)] = 14.0
        return spd, from_origin(-0.125, 90.125, 0.25, 0.25), "fake"

    return fetch, calls


def test_weather_wind_falls_back_to_previous_cycle():
    from darkvessel.live import weather as lw_

    fetch, calls = _fake_gfs(fail_first=1)
    t = pd.Timestamp("2026-10-10T10:33:00Z").to_pydatetime()
    wind, cycle, fh, src = lw_.wind_at(t, np.array([113.5, 114.5]), np.array([22.0, 22.0]), cache_dir=None, fetch=fetch)
    assert [c[1] for c in calls] == [5, 11]                     # 06Z f005 missing, then 00Z f011
    assert f"{cycle:%H}" == "00" and fh == 11 and np.allclose(wind, [6.0, 14.0]) and src.startswith("GFS 2026-10-10 00Z f011")
    fetch, calls = _fake_gfs(fail_first=9)
    wind, cycle, fh, src = lw_.wind_at(t, np.array([113.5]), np.array([22.0]), cache_dir=None, fetch=fetch)
    assert np.isnan(wind).all() and cycle is None and src.startswith("unknown:") and "HTTP 404" in src and src.count("|") == 2


def test_weather_cloud_unknown_kept_apart_from_clear():
    from darkvessel.live import weather as lw_

    t = pd.Timestamp("2026-10-10T10:33:00Z").to_pydatetime()
    lon, lat = np.array([113.5, 114.5]), np.array([22.0, 22.0])
    plon, plat = np.meshgrid(np.arange(113.0, 115.0, 0.02), np.arange(21.5, 22.5, 0.02))
    ctt = np.where(plon > 114.0, 210.0, np.nan).astype(np.float32)        # a deep convective cell east of 114E, clear west
    c, deep, key, src = lw_.cloud_at(t, lon, lat, key_fn=lambda t: "AHI-L2-FLDK-Clouds/x.nc", window_fn=lambda k, bb: None,
                                     ctt_fn=lambda k, w: (plon, plat, ctt))
    assert np.isnan(c[0]) and c[1] == 210.0 and deep == [False, True] and key.endswith("x.nc")
    c, deep, key, src = lw_.cloud_at(t, lon, lat, key_fn=lambda t: None)
    assert np.isnan(c).all() and deep == [None, None] and key is None and src.startswith("unknown:")


def test_weather_sidecars_written_and_retried(tmp_path):
    from darkvessel.live import weather as lw_

    T1 = pd.Timestamp.now(tz="UTC").floor("min") - pd.Timedelta("5h")    # the retry window counts from the scene time
    contacts = pd.DataFrame({"det_id": ["a", "b", "c"], "run_id": "live_S1D_20261010T1032", "scene_id": ["S1", "S1", "S2"],
                             "lon": [113.5, 114.5, 114.2], "lat": [22.0, 22.0, 22.5]})
    recs = [{"run_id": "live_S1D_20261010T1032", "product_id": "S1", "scene_time_utc": T1.isoformat(), "status": "done"},
            {"run_id": "live_S1D_20261010T1032", "product_id": "S2", "scene_time_utc": (T1 + pd.Timedelta("25s")).isoformat(), "status": "done"}]
    state = {"cloud_ok": False}

    def scene_fn(cs, t, run_id, scene_id):
        fetch, _ = _fake_gfs(fail_first=0)
        cloud = (lambda *a, **k: (np.full(len(cs), np.nan), [False] * len(cs), "k.nc", "Himawari-9 k.nc")) if state["cloud_ok"] else \
                (lambda *a, **k: (np.full(len(cs), np.nan), [None] * len(cs), None, "unknown: HTTPError HTTP 503 https://noaa-himawari9.s3.amazonaws.com/x"))
        return lw_.scene_weather(cs, t, run_id, scene_id, cache_dir=None, wind_fn=lambda t, lo, la, cd: lw_.wind_at(t, lo, la, cd, fetch=fetch),
                                 cloud_fn=cloud)

    out = lw_.update_sidecars(recs, contacts, tmp_path, now=T1 + pd.Timedelta("5h"), log=lambda m: None, scene_fn=scene_fn)
    assert out == {"live_S1D_20261010T1032": "written with unknown parts"}
    df = pd.read_parquet(tmp_path / "live_S1D_20261010T1032_weather.parquet")
    assert list(df.columns) == lw_.WEATHER_COLUMNS and len(df) == 3 and df.wind_ms.notna().all() and df.deep_convection.isna().all()
    assert set(df.scene_id) == {"S1", "S2"} and df.cloud_source.str.contains("HTTP 503").all()
    # too soon to retry: kept; after RETRY_MINUTES: retried and complete
    state["cloud_ok"] = True
    out = lw_.update_sidecars(recs, contacts, tmp_path, now=pd.Timestamp.now(tz="UTC"), log=lambda m: None, scene_fn=scene_fn)
    assert out == {"live_S1D_20261010T1032": "unknown parts kept"}
    later = pd.Timestamp.now(tz="UTC") + pd.Timedelta(minutes=lw_.RETRY_MINUTES + 1)
    out = lw_.update_sidecars(recs, contacts, tmp_path, now=later, log=lambda m: None, scene_fn=scene_fn)
    assert out == {"live_S1D_20261010T1032": "complete"}
    df = pd.read_parquet(tmp_path / "live_S1D_20261010T1032_weather.parquet")
    assert (df.deep_convection == False).all() and lw_.complete(df)  # noqa: E712
    # complete and unchanged contacts: not fetched again
    out = lw_.update_sidecars(recs, contacts, tmp_path, now=later, log=lambda m: None, scene_fn=lambda *a: 1 / 0)
    assert out == {"live_S1D_20261010T1032": "complete"}


def test_review_and_figures_on_synthetic_pass(tmp_path):
    import geopandas as gpd

    from darkvessel.live import figures, review

    objects, ais, truth, vessels = _dense_anchorage()
    static = _static(vessels.mmsi.astype("int64").tolist(), lengths=[120.0] * len(vessels))
    scene = _dense_scene(objects)
    contacts, ais_only, _ = matching.match_scene(objects, scene, ais, static, ais, _grid(), scene["footprint"], log=lambda m: None)
    me = review.matched_evidence(contacts, ais, static)
    assert len(me) == (contacts.ais_status == "matched").sum() and me.check.eq("consistent").mean() >= 0.8
    assert me.before_dt_s.le(0).all() and me.after_dt_s.gt(0).all() and not me.gear_beacon_like.any()
    ue = review.unmatched_evidence(contacts, ais, ais_only, TP)
    assert len(ue) == (contacts.ais_status == "unmatched").sum() and ue.why_unmatched.str.startswith("ambiguous").sum() == contacts.match_ambiguous.sum()
    scenes = gpd.GeoDataFrame({"product_id": ["SYN_DENSE"]}, geometry=[scene["footprint"]], crs="EPSG:4326")
    lo, la = objects.lon, objects.lat
    mp = figures.pass_map(contacts, scenes, ais, tmp_path / "map.png", "synthetic", zoom=(lo.min() - 0.02, la.min() - 0.02, lo.max() + 0.02, la.max() + 0.02))
    pp = figures.match_panels(contacts, tmp_path / "matches.png", "synthetic")
    assert mp.stat().st_size > 20_000 and pp.stat().st_size > 20_000


def test_rematch_from_stored_objects(tmp_path, monkeypatch):
    """--rematch redoes pairing, status and identity from <id>.objects.parquet without detection, with the AIS of now."""
    objects, ais, truth = _synthetic()
    heard = truth[truth.has_ais].mmsi.astype("int64").tolist()
    monkeypatch.setattr(watch, "aisstream", _FakeAIS(ais.iloc[:0], _static([])))
    monkeypatch.setattr(watch, "PASSES_PATH", tmp_path / "passes.json")
    ctx, ck = _ctx(tmp_path, do_weather=False), watch.Checkpoint(tmp_path / "scenes")
    rec = _mirror_rec()

    def working(path, aoi, inner, **kw):
        return objects.copy(), {"tested_km2": 3500.0, "blocks_processed": 4, "orbit_rel": 164, "pass_dir": "DESCENDING", "io_retries": 0,
                                "n_candidates_pre_rules": len(objects), "n_cnn_scored": 0, "runtime_s": 1.0, "sat_speed_ms": 7597.0}

    st = watch.process_record(rec, ctx, ck, [], force=True, process=working, log=lambda m: None)
    assert st["status"] == "done" and st["n_matched"] == 0 and st["n_no_coverage"] == len(objects) and st["sat_speed_ms"] == 7597.0
    assert ck.objects(rec["product_id"]) is not None and len(ck.objects(rec["product_id"])) == len(objects)
    # the AIS of that window arrives later (a late flush, or a recorder caught up): rematch, no detection call
    monkeypatch.setattr(watch, "aisstream", _FakeAIS(ais, _static(heard)))
    n = watch.rematch(ctx, ck, pd.Timestamp("2026-10-08T00:00Z"), log=lambda m: None)
    st = ck.status(rec["product_id"])
    assert n == 1 and st["n_matched"] >= 10 and st["rematched_utc"] and st["attempts_total"] == 1
    recs, contacts, _ = ck.load_all()
    assert (contacts.ais_status == "matched").sum() == st["n_matched"] and contacts.vessel_name.notna().sum() >= 10
    assert (ctx.out_dir / "live_contacts.gpkg").exists()


# ----------------------------------------------------------------------------------------------- Pearl River hand-check rules
def _pair_frames(contacts, vessels, minutes=2.0):
    """Contacts (lon, lat from metre offsets around ORIGIN) and AIS reports either side of TP for stationary or moving
    vessels (x, y in metres, sog kn, cog deg, length m). No imaging geometry: no azimuth shift."""
    fwd, inv = _utm()
    x0, y0 = fwd(*ORIGIN)
    c = pd.DataFrame(contacts)
    c["lon"], c["lat"] = inv(x0 + c.pop("x").to_numpy(float), y0 + c.pop("y").to_numpy(float))
    rows = []
    for v in vessels:
        vx, vy = v["sog"] * 0.514444 * np.sin(np.radians(v["cog"])), v["sog"] * 0.514444 * np.cos(np.radians(v["cog"]))
        for dt in (-minutes * 60, minutes * 60):
            lo, la = inv(x0 + v["x"] + vx * dt, y0 + v["y"] + vy * dt)
            rows.append({"mmsi": v["mmsi"], "timestamp": TP + pd.Timedelta(seconds=dt), "lon": lo, "lat": la, "sog_kn": v["sog"],
                         "cog_deg": v["cog"], "ais_class": "A", "ship_name": None, "length_m": v.get("length", np.nan)})
    return c, pd.DataFrame(rows)


def test_pairing_rules_length_and_fixed_contacts():
    from darkvessel.live import assign as asg

    # a 290 m tanker 150 m from a 41 m return and 350 m from a 330 m return: the short return cannot be the tanker
    c, ais = _pair_frames([{"det_id": "short", "confidence": "medium", "length_est_m": 41.0, "x": 150.0, "y": 0.0},
                           {"det_id": "long", "confidence": "high", "length_est_m": 330.0, "x": -350.0, "y": 0.0}],
                          [{"mmsi": 477584200, "x": 0.0, "y": 0.0, "sog": 0.0, "cog": 0.0, "length": 290.0}])
    out, ao, _ = asg.assign(c, ais, TP, rules.LIVE_MATCH)
    o = out.set_index("det_id")
    assert o.loc["long", "ais_status"] == "matched" and o.loc["long", "mmsi"] == 477584200
    assert o.loc["short", "ais_status"] == "unmatched" and not o.loc["short", "match_ambiguous"]
    # alone, the short return stays unpaired: the vessel is AIS-only, not named on a return a seventh of its length
    out, ao, _ = asg.assign(c[c.det_id == "short"], ais, TP, rules.LIVE_MATCH)
    assert out.ais_status.iloc[0] == "unmatched" and len(ao) == 1
    # a fixed contact (a structure on earlier passes) 50 m from a vessel under way at 8 kn is not that vessel ...
    c, ais = _pair_frames([{"det_id": "fx", "confidence": "fixed", "length_est_m": 32.0, "x": 50.0, "y": 0.0}],
                          [{"mmsi": 412536055, "x": 0.0, "y": 0.0, "sog": 8.2, "cog": 90.0}], minutes=0.5)
    out, ao, _ = asg.assign(c, ais, TP, rules.LIVE_MATCH)
    assert out.ais_status.iloc[0] == "unmatched" and ao.mmsi.tolist() == [412536055]
    # ... but a vessel at anchor on a fixed contact is (a ship moored at the same berth on every pass)
    c, ais = _pair_frames([{"det_id": "fx", "confidence": "fixed", "length_est_m": 106.0, "x": 50.0, "y": 0.0}],
                          [{"mmsi": 412402680, "x": 0.0, "y": 0.0, "sog": 0.0, "cog": 0.0, "length": 84.0}])
    out, _, _ = asg.assign(c, ais, TP, rules.LIVE_MATCH)
    assert out.ais_status.iloc[0] == "matched" and out.mmsi.iloc[0] == 412402680
    assert rules.MIN_LENGTH_RATIO == 0.25 and rules.FIXED_MAX_SOG_KN == 2.0


def test_oversized_return_takes_its_large_ship_and_duplicates_are_left_out():
    from darkvessel.live import assign as asg

    # PANCON BRIDGE case: a 172 m ship whose bright return the detector dropped as oversized (459 m) lies 40 m from its
    # expected position; a faint 60 m return 300 m away must not inherit the name
    c, ais = _pair_frames([{"det_id": "faint", "confidence": "medium", "length_est_m": 60.0, "x": 300.0, "y": 0.0},
                           {"det_id": "ovs", "confidence": "low", "low_reason": "oversized", "length_est_m": 459.0, "x": 0.0, "y": 40.0}],
                          [{"mmsi": 441420000, "x": 0.0, "y": 0.0, "sog": 0.0, "cog": 0.0, "length": 172.0}])
    contacts, extra = c[c.det_id == "faint"], c[c.det_id == "ovs"]
    out, ao, diag = asg.assign(contacts, ais, TP, rules.LIVE_MATCH)          # without the oversized return: a false name
    assert out.ais_status.iloc[0] == "matched"
    out, ao, diag = asg.assign(contacts, ais, TP, rules.LIVE_MATCH, extra_returns=extra)
    assert out.ais_status.iloc[0] == "unmatched" and out.dark_candidate.iloc[0] and len(out) == 1
    assert ao.mmsi.tolist() == [441420000] and ao.oversized_det_id.tolist() == ["ovs"] and diag["pairs_with_oversized"] == 1
    # an oversized return within 150 m of a contact is that contact's other polarisation: left out of the pairing
    objs = pd.concat([c.assign(low_reason=c.get("low_reason")), c[c.det_id == "faint"].assign(det_id="dup", confidence="low",
                      low_reason="oversized", lat=c.lat.iloc[0] + 0.0004)], ignore_index=True)
    ov = matching.oversized_returns(objs, objs[objs.det_id == "faint"])
    assert ov.det_id.tolist() == ["ovs"] and rules.OVERSIZED_DUPLICATE_M == 150.0


def test_live_quality_rule_uses_distance_travelled():
    q = rules.live_match_quality(
        [79, 79, 442, 424, 239, 150, 99, np.nan, 100],
        [1183, 1183, 472, 268, 144, 60, 98, 60, 1200],
        [0.1, 10.0, 29.9, 4.0, 8.7, 0.0, 27.9, 0.0, np.nan],
        [279, 279, 110, 170, 61, 400, 54, 30, 100], [190, 190, 41, 269, 96, 100, 40, 25, 100])
    # anchored, old reports: high | the same at 10 kn (6 km of dead reckoning): low | 30 kn for 8 min: low |
    # 424 m: medium | 239 m: medium | ratio 4.0: medium | 28 kn for 98 s (1.4 km): medium | unmatched | speed unknown: time bands
    assert list(q) == ["high", "low", "low", "medium", "medium", "medium", "medium", None, "low"]
    assert rules.live_match_quality([100], [200], [np.nan], [30], [25])[0] == "high"
    assert "track_m" in rules.QUALITY_TEXT and "200 m" in rules.QUALITY_TEXT


def test_on_tested_sea_follows_the_detector_mask_and_recall_uses_it(tmp_path):
    objects, ais, truth = _synthetic()
    static = _static(truth[truth.has_ais].mmsi.astype("int64").tolist())
    west = box(BBOX[0] - 0.1, BBOX[1] - 0.1, (BBOX[0] + BBOX[2]) / 2, BBOX[3] + 0.1)   # only the west half was tested
    scene = dict(SCENE, tested=west)
    contacts, ais_only, counts = matching.match_scene(objects, scene, ais, static, ais, _grid(), box(*BBOX).buffer(1.0), log=lambda m: None)
    assert len(ais_only) >= 2 and counts["tested_area_source"] == "detector_mask"
    import shapely

    assert (ais_only.on_tested_sea.astype(bool).to_numpy() == shapely.contains_xy(west, ais_only.lon.to_numpy(), ais_only.lat.to_numpy())).all()
    assert counts["n_ais_only_on_tested_sea"] == int(ais_only.on_tested_sea.sum())
    # without the polygon the distance-to-coast layer stands in
    _, _, c2 = matching.match_scene(objects, SCENE, ais, static, ais, _grid(), box(*BBOX).buffer(1.0), log=lambda m: None)
    assert c2["tested_area_source"] == "dist_coast"
    p = outputs.pass_summary("live_S1D_20261008T2258", contacts, ais_only, pd.DataFrame([_scene_record(objects, counts)]))
    n_ais = sum(v["n_ais"] for v in p["recall_by_ais_length"].values())
    tested_ok = int((ais_only.on_tested_sea.astype(bool) & ais_only.ambiguous_det_id.isna()).sum())
    assert n_ais == int((contacts.ais_status == "matched").sum()) + tested_ok - int(pd.to_numeric(ais_only.length_ais_m, errors="coerce")[ais_only.on_tested_sea.astype(bool) & ais_only.ambiguous_det_id.isna()].isna().sum())
    assert p["ais_only_on_tested_sea_not_ambiguous"] == tested_ok and "on tested sea" in p["recall_note"]


def test_mask_polygon_maps_cells_to_lonlat():
    m = np.zeros((10, 12), bool)
    m[2:6, 3:9] = True                       # 4 x 6 cells of 16 px

    def lonlat(rows, cols):                  # 10 m pixels near the equator: 1e-4 degree per pixel, north up
        return 110.0 + np.asarray(cols) * 1e-4, 10.0 - np.asarray(rows) * 1e-4

    poly = scene_mod.mask_polygon(m, 16, lonlat)
    assert poly.area == pytest.approx((6 * 16e-4) * (4 * 16e-4), rel=1e-6)
    import shapely

    assert shapely.contains_xy(poly, 110.0 + 100 * 1e-4, 10.0 - 50 * 1e-4) and not shapely.contains_xy(poly, 110.0 + 10 * 1e-4, 10.0 - 10 * 1e-4)
    assert scene_mod.mask_polygon(np.zeros((3, 3), bool), 16, lonlat).is_empty


def test_review_tables_keep_grades_and_feed_review_note(tmp_path):
    import pyogrio

    from darkvessel.live import review

    objects, ais, truth = _synthetic()
    static = _static(truth[truth.has_ais].mmsi.astype("int64").tolist())
    contacts, ais_only, counts = matching.match_scene(objects, SCENE, ais, static, ais, _grid(), box(*BBOX).buffer(1.0), log=lambda m: None)
    m = contacts[contacts.ais_status == "matched"]
    me = review.matched_evidence(contacts, ais, static, scene_times={"SYN": T}, sat_speed={"SYN": 7597.0})
    assert len(me) == len(m) and me.pred_lon.notna().all() and (me.check_dist_m - me.match_dist_m).abs().max() < 1.0
    ue = review.unmatched_evidence(contacts, ais, ais_only, T, static_latest=static)
    ue["sample"] = review.pick_samples(ue, n_leads=3, n_ambiguous=2, n_no_coverage=1).to_numpy()
    assert ue["sample"].eq("top_cnn_dark_lead").sum() == 3
    # both tables carry the dark caveat and the board D4.7 label of the aisstream identities on every row
    for t in (me, ue):
        assert (t.caveat == DARK_CAVEAT).all() and (t.identity_label == "live AIS relayed by aisstream.io; terms UNVERIFIED").all()
    # the reviewer grades two matched rows and one lead; a rerun of --review keeps the grades
    me = review.keep_hand_check(me.assign(sample="all_matched"), None)
    me.loc[0, ["grade", "reason"]] = ["confirmed", "track through the return"]
    me.loc[1, ["grade", "reason"]] = ["doubtful", "a brighter return 300 m away"]
    ue = review.keep_hand_check(ue, None, key=("det_id",))
    lead = ue.index[ue["sample"] == "top_cnn_dark_lead"][0]
    ue.loc[lead, ["grade", "reason"]] = ["confirmed", "no AIS vessel within its gate"]
    out_dir = tmp_path / "live"
    out_dir.mkdir()
    mp, up = review.review_paths("live_S1D_20261008T2258", out_dir)
    me.to_csv(mp, index=False)
    ue.to_csv(up, index=False)
    again = review.keep_hand_check(review.matched_evidence(contacts, ais, static).assign(sample="all_matched"), review.read_review(mp))
    assert again.grade.notna().sum() == 2 and list(again.columns[-4:]) == review.HAND_COLUMNS
    assert list(again.columns[-6:-4]) == review.LABEL_COLUMNS and (review.read_review(up).caveat == DARK_CAVEAT).all()
    # a rematch that pairs a graded contact with another MMSI drops its note
    moved = contacts.copy()
    moved.loc[moved.det_id == me.det_id.iloc[1], "mmsi"] = 999999999
    notes = review.review_notes(moved.assign(run_id="live_S1D_20261008T2258"), out_dir)
    assert notes.notna().sum() == 2 and notes[moved.det_id == me.det_id.iloc[0]].iloc[0] == "confirmed: track through the return"
    # the products carry review_note as the last contacts column, null where nobody looked; the about layer has the rule
    outputs.rebuild([_scene_record(objects, counts)], contacts, ais_only, None, None, 1, T.isoformat(), out_dir=out_dir, log=lambda m: None)
    gp = out_dir / "live_S1D_20261008T2258.gpkg"
    c = pyogrio.read_dataframe(gp, layer="contacts_4326", read_geometry=False)
    assert list(c.columns[:len(D1_COLUMNS)]) == D1_COLUMNS and c.columns[-1] == "review_note"
    assert c.review_note.notna().sum() == 3 and c.review_note.dropna().str.match(r"^(confirmed|plausible|doubtful): ").all()
    info = pyogrio.read_info(gp, layer="contacts_4326")
    assert dict(zip(info["fields"], info["dtypes"]))["review_note"] == "object"
    assert "hand-checked" in pyogrio.read_dataframe(gp, layer="about").iloc[0]["review_note"]
    s = json.loads((out_dir / "live_summary.json").read_text())["passes"]["live_S1D_20261008T2258"]
    assert s["hand_check"]["matched"] == {"confirmed": 1, "plausible": 0, "doubtful": 1} and s["hand_check"]["unmatched"]["confirmed"] == 1
    # static identity counts rows whose identity comes from a static message, apart from rows that only have a name
    cm = c[c.ais_status == "matched"]
    assert s["matched_with_static_identity"] == int((cm.identity_source == ident.IDENTITY_STATIC).sum()) > 0
    assert s["matched_with_name"] == int(cm.vessel_name.notna().sum()) and "ambiguous contacts" in s["dark_leads_note"]


def test_rebuild_and_weather_modes_respect_the_lock(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("live_script", os.path.join(os.path.dirname(__file__), "..", "scripts", "30_live_pass.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    lock = tmp_path / "cycle.lock"
    calls = []
    monkeypatch.setattr(mod.lw, "rebuild_outputs", lambda *a, **k: calls.append("rebuild"))
    monkeypatch.setattr(mod.lw, "update_weather", lambda *a, **k: calls.append("weather"))
    real_ctx = mod.lw.Context
    monkeypatch.setattr(mod.lw, "Context", lambda **kw: real_ctx(lock_path=lock, out_dir=tmp_path / "live", **kw))
    for flag in ("--rebuild", "--weather"):
        monkeypatch.setattr("sys.argv", ["30_live_pass.py", flag, "--nice", "0"])
        with watch.CycleLock(lock):
            assert mod.main() == 2            # the watcher holds the lock: nothing written
        assert mod.main() == 0
    assert calls == ["rebuild", "weather"]
