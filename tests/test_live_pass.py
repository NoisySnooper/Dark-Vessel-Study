"""Offline tests for the live-pass pipeline (darkvessel.live): synthetic scene and AIS only, no network, no real data.

Covers the status rules (matched, unmatched, no_coverage, dark_lead), the ship-station MMSI filter, identity and MID
lookup, the speed-aware gate and the match_quality rule, retried tile reads and retried detection, checkpoint skipping,
the cycle lock, the no-op rerun, mirror candidate selection with a fake listing, and the D1 output schema with its
GeoPackage field types.
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
    assert u.dark_lead.all() and not m.dark_lead.any() and counts["n_dark_leads"] == len(u)
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
