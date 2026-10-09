"""Offline tests of the GFW identity rules on small synthetic grids (no network, no token)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from darkvessel.ais import gfw as G
from darkvessel.ais import gfw_identity as I

FIX = Path(__file__).parent / "fixtures"
T0 = pd.Timestamp("2026-09-20T10:48:31Z")


def contacts(points, ts=T0, lengths=None):
    lon, lat = zip(*points)
    return pd.DataFrame({"lon": list(lon), "lat": list(lat), "ts": pd.to_datetime([ts] * len(points), utc=True),
                         "length_est_m": lengths if lengths is not None else [40.0] * len(points)})


def sar(cells, matched, vessel_ids=None, ts=T0):
    lon, lat = zip(*cells)
    vids = vessel_ids or [f"v{i}" if m else None for i, m in enumerate(matched)]
    return pd.DataFrame({"lon": list(lon), "lat": list(lat), "ts": pd.to_datetime([ts] * len(cells), utc=True), "matched": matched,
                         "vessel_id": vids})


def test_cell_ij_and_block_join_cover_the_nine_neighbours():
    # GFW's grid: cell k is centred on k * res and spans [(k - 0.5) res, (k + 0.5) res)
    cx, cy = I.cell_ij([109.53, 109.5349, 109.5351, 109.5249], [21.31, 21.3051, 21.3149, 21.2949], 0.01)
    assert cx.tolist() == [10953, 10953, 10954, 10952] and cy.tolist() == [2131, 2131, 2131, 2129]
    a = pd.DataFrame({"lon": [109.5331], "lat": [21.3129]})   # inside the cell centred on 109.53, 21.31
    b = pd.DataFrame({"lon": [109.53, 109.54, 109.52, 109.55, 109.53], "lat": [21.31, 21.32, 21.30, 21.31, 21.34]})
    p = I.block_join(a, b, 0.01)
    assert sorted(p.ib.tolist()) == [0, 1, 2]        # same cell, NE neighbour, SW neighbour; two and three cells away excluded
    assert p.dist_m.min() < 500 and p.dist_m.max() < 2200
    assert I.block_join(a, b.iloc[0:0], 0.01).empty
    # a GFW LOW cell centred on 108.8 holds a contact at 108.76, not one at 108.84 (the old floor put both in [108.8, 108.9))
    assert I.cell_ij([108.8, 108.76, 108.84], [11.2, 11.2, 11.2], 0.1)[0].tolist() == [1088, 1088, 1088]
    assert I.cell_ij([108.86], [11.2], 0.1)[0].tolist() == [1089]


def test_assign_one_to_one_is_greedy_cheapest_first_and_counts_rivals():
    pairs = pd.DataFrame({"ia": [0, 0, 1, 2], "ib": [0, 1, 0, 1], "cost": [0.5, 0.9, 0.3, 0.1]})
    ch = I.assign_one_to_one(pairs).set_index("ia")
    assert ch.loc[2, "ib"] == 1 and ch.loc[1, "ib"] == 0 and 0 not in ch.index   # 0 lost both its candidates
    assert ch.loc[1, "n_cand"] == 1 and ch.loc[1, "n_rivals"] == 2
    assert ch.loc[2, "n_cand"] == 1 and ch.loc[2, "n_rivals"] == 2


def test_pair_sar_matches_in_block_within_time_and_resolves_many_to_one():
    # contact A sits in the cell of matched detection v0 (centre 109.53, 21.31); B is in a neighbour cell and farther; C is alone.
    c = contacts([(109.5299, 21.3101), (109.5420, 21.3220), (104.9, 7.9)], lengths=[40.0, 45.0, 30.0])
    s = sar([(109.53, 21.31), (104.12, 7.05)], [True, False])
    out = I.pair_sar(c, s)
    assert out.gfw_sar_pair.tolist() == ["matched", None, None]
    assert out.gfw_sar_n_cand.tolist() == [1, 1, 0]       # B had a candidate and lost it to A
    assert out.gfw_sar_n_rivals.iloc[0] == 2 and out.match_dist_m.iloc[0] < 20 and out.match_dt_s.iloc[0] == 0
    # 31 minutes apart is not the same pass
    late = sar([(109.53, 21.31)], [True], ts=T0 + pd.Timedelta(minutes=31))
    assert I.pair_sar(c, late).gfw_sar_pair.tolist() == [None, None, None]
    # a registry length that disagrees by a factor 4 makes the far contact the cheaper pairing
    c2 = contacts([(109.5299, 21.3101), (109.5420, 21.3220)], lengths=[200.0, 48.0])
    out2 = I.pair_sar(c2, sar([(109.53, 21.31)], [True]), vessel_length=pd.Series({"v0": 50.0}))
    assert out2.gfw_sar_pair.tolist() == [None, "matched"] and out2.length_ais_m.iloc[1] == 50.0


def test_sar_quality_rules():
    q = I.sar_quality([100, 100, 1500, 100, 100], [1, 2, 1, 1, 1], [1, 2, 1, 1, 1], [40, 40, 40, 40, 40], [np.nan, np.nan, np.nan, 90, 200])
    assert q.tolist() == ["high", "low", "medium", "medium", "low"]
    # a GEAR buoy identity is never better than low, whatever the geometry
    assert I.sar_quality([100], [1], [1], [40], [np.nan], gear=[True]).tolist() == ["low"]
    assert I.is_gear(["GEAR", "FISHING", None, "gear"], ["OTHER", "GEAR", None, None]).tolist() == [True, True, False, True]


def test_sar_detections_expands_cells_and_keeps_identity_only_for_single_matched():
    hourly = pd.DataFrame({"lon": [109.535, 109.335], "lat": [21.315, 20.225], "date": ["2026-09-20 10:00"] * 2, "detections": [1, 2],
                           "entryTimestamp": ["2026-09-20T10:48:31Z", "2026-09-20T10:48:31Z"], "matched": [True, True],
                           "vesselId": ["v1", "v2"], "mmsi": ["111", "222"], "shipName": ["ONE", ""], "callsign": [None, None],
                           "imo": ["", ""], "flag": ["ZZZ", "ZZZ"], "vesselType": ["FISHING", "CARGO"], "geartype": ["TRAWLERS", "CARGO"]})
    s = I.sar_detections(hourly)
    assert len(s) == 3 and s.n_in_cell.tolist() == [1, 2, 2]
    assert s.vessel_id.iloc[0] == "v1" and s.vessel_id.iloc[1:].isna().all() and s.ship_name.iloc[0] == "ONE" and pd.isna(s.imo.iloc[0])
    assert s.hour.tolist() == [10, 10, 10] and str(s.ts.iloc[0]) == "2026-09-20 10:48:31+00:00"
    assert not s.ambiguous_cell.any()
    assert I.sar_detections(pd.DataFrame()).empty
    # the DAILY report grouped by vessel id lists both vessels of the two-detection cell: identities recovered, flagged ambiguous
    by_vessel = pd.DataFrame({"lon": [109.335, 109.335, 109.535], "lat": [20.225, 20.225, 21.315], "date": ["2026-09-20"] * 3,
                              "detections": [1, 1, 1], "vesselId": ["v2b", "v2a", "v1"], "mmsi": ["223", "222", "111"],
                              "shipName": ["TWO-B", "TWO-A", "ONE"], "callsign": ["", "", ""], "imo": ["", "", ""], "flag": ["ZZZ"] * 3,
                              "vesselType": ["CARGO", "CARGO", "FISHING"], "geartype": ["CARGO", "CARGO", "TRAWLERS"]})
    s2 = I.sar_detections(hourly, by_vessel)
    assert s2.vessel_id.tolist() == ["v1", "v2a", "v2b"] and s2.ambiguous_cell.tolist() == [False, True, True]
    assert s2.mmsi.tolist() == ["111", "222", "223"]
    # the pair quality of an ambiguous-cell identity is low whatever the geometry
    assert I.sar_quality([10], [1], [1], [40], [np.nan], ambiguous_cell=[True]).tolist() == ["low"]
    # fewer vessels listed than detections in the cell: nothing recovered
    s3 = I.sar_detections(hourly, by_vessel.iloc[[0, 2]])
    assert s3.vessel_id.iloc[1:].isna().all() and not s3.ambiguous_cell.any()


def test_alignment_check_prefers_the_reading_that_puts_pairs_in_the_same_cell():
    rng = np.random.default_rng(1)
    lon = 109 + rng.uniform(0, 1, 300)
    lat = 20 + rng.uniform(0, 1, 300)
    c = contacts(list(zip(lon, lat)))
    # GFW centres at multiples of 0.01 (the documented reading): every contact lies within half a cell of its centre
    s = sar(list(zip(np.round(lon, 2), np.round(lat, 2))), [True] * 300)
    a = I.alignment_check(c, s)
    assert a["chosen"] == "value_is_centre" and a["value_is_centre"]["within_half_cell_share"] == 1.0 and a["shift_applied_deg"] == 0.0
    assert abs(a["value_is_centre"]["median_dlon_deg"]) <= 0.001 and a["value_is_centre"]["median_dist_m"] < a["corner_plus_half"]["median_dist_m"]
    # the same centres offset by half a cell (a corner reading): the other reading wins and the shift corrects it
    a2 = I.alignment_check(c, I.shift_cells(s, -0.005))
    assert a2["chosen"] == "corner_plus_half" and a2["shift_applied_deg"] == 0.005


def test_presence_rows_tracks_and_pairing():
    resp = json.loads((FIX / "gfw_presence.json").read_text())
    pres = I.presence_rows(G.report_to_frame(resp), "S1C_20260920T1048")
    assert len(pres) == 8 and pres.vessel_id.nunique() == 4         # the empty vessel id row is dropped
    assert pres.lon.iloc[0] == 108.33 and pres.mmsi.iloc[0] == "000000001"   # float noise snapped, value = cell centre
    assert str(pres.hour_ts.iloc[0]) == "2026-09-20 09:00:00+00:00"
    tr = I.vessel_tracks(pres)
    assert tr["v-pres-1"][3] == 0.0 and len(tr["v-pres-1"][0]) == 3        # stationary over three hours
    assert 4 < tr["v-pres-2"][3] < 5 and np.isnan(tr["v-gear-3"][3]) and tr["v-fast-4"][3] > 20
    # interpolation between the hourly points (each at the hour's midpoint), held at the ends
    x, y, dh, k = I.track_position(tr["v-pres-2"], np.array([pd.Timestamp("2026-09-20T10:48Z").timestamp(), pd.Timestamp("2026-09-20T13:00Z").timestamp()]))
    assert abs(x[0] - (110.21 + 0.04 * 18 / 60)) < 1e-9 and x[1] == 110.25 and dh.tolist() == [0, -2] and k.tolist() == [2, 2]
    # A in the cell of stationary v-pres-1 (medium; the GEAR buoy in the same cell is not a candidate); B 1.9 km from the
    # interpolated position of slow mover v-pres-2 (low); C 1 km from the interpolated position of fast mover v-fast-4
    # (rejected: over 10 km/h, its hourly cell says little about where it was); D alone
    c = contacts([(108.3301, 21.5099), (110.2399, 20.2701), (105.05, 10.0), (100.0, 5.0)])
    out = I.pair_presence(c, pres)
    assert out.pres_vessel_id.tolist() == ["v-pres-1", "v-pres-2", None, None]
    assert out.match_quality.tolist() == ["medium", "low", None, None]
    assert out.match_dt_s.tolist()[:2] == [0.0, 0.0] and out.pres_n_cells.tolist()[:2] == [3, 2]
    assert out.match_dist_m.iloc[0] < 200 and 1500 < out.match_dist_m.iloc[1] < 2300
    assert out.pres_speed_kmh.iloc[0] == 0.0 and 4 < out.pres_speed_kmh.iloc[1] < 5
    assert out.pres_n_cand.tolist() == [1, 1, 1, 0]                         # C had a candidate that the speed rule rejected
    # a vessel GFW's SAR matcher already placed is not a candidate
    out2 = I.pair_presence(c, pres, exclude_vessels={"v-pres-1"})
    assert out2.pres_vessel_id.tolist() == [None, "v-pres-2", None, None]
    # two contacts in one cell with one vessel: one gets it (low, rivals 2), the other keeps the candidate count
    c3 = contacts([(108.3301, 21.5099), (108.3302, 21.5098)])
    out3 = I.pair_presence(c3, pres)
    assert sorted(out3.pres_vessel_id.fillna("-").tolist()) == ["-", "v-pres-1"] and out3.pres_n_cand.tolist() == [1, 1]
    assert out3.match_quality.dropna().tolist() == ["low"]
    # a single-cell vessel (no speed proxy) is a candidate but is rejected: its one cell says little about where it was
    single = pres[pres.vessel_id == "v-gear-3"].assign(gfw_vessel_type="OTHER", gfw_geartype="OTHER")
    out4 = I.pair_presence(contacts([(108.3301, 21.5099)]), single)
    assert out4.pres_vessel_id.tolist() == [None] and out4.pres_n_cand.tolist() == [1]


def test_presence_calibration_measures_track_position_error_by_speed():
    resp = json.loads((FIX / "gfw_presence.json").read_text())
    pres = I.presence_rows(G.report_to_frame(resp), "p1")
    ref = contacts([(108.3301, 21.5099), (110.2399, 20.2701), (105.05, 10.0)]).assign(vessel_id=["v-pres-1", "v-pres-2", "v-fast-4"], pass_id="p1")
    cal = I.presence_calibration(ref, pres)
    assert cal["n_reference"] == 3 and cal["n_with_hour_h_cell"] == 3
    assert cal["speed_0_to_3_kmh"]["n"] == 1 and cal["speed_0_to_3_kmh"]["interp_within_1km"] == 1.0
    assert cal["speed_3_to_10_kmh"]["n"] == 1 and cal["speed_over_10_kmh"]["n"] == 1
    assert cal["all"]["hour_cell_median_km"] is not None and cal["rule_b_accepts"]["max_speed_kmh"] == 10.0
    # the same vessel id in two passes stays two tracks
    two = pd.concat([pres.assign(pass_id="p1"), pres.assign(pass_id="p2", hour_ts=pres.hour_ts + pd.Timedelta(days=2))], ignore_index=True)
    ref2 = pd.concat([ref, ref.assign(pass_id="p2", ts=ref.ts + pd.Timedelta(days=2))], ignore_index=True)
    assert I.presence_calibration(ref2, two)["n_reference"] == 6


def test_status_reach_and_block_hours():
    # LOW cells are centred on whole multiples of 0.1 (GFW convention): 108.3 spans 108.25 to 108.35
    low = pd.DataFrame({"lon": [108.3, 108.3, 108.4, 112.0], "lat": [21.5, 21.5, 21.6, 10.0],
                        "date": ["2026-09-20", "2026-09-21", "2026-09-20", "2026-09-20"], "hours": [2.0, 1.0, 0.5, 3.0]})
    c = pd.DataFrame({"lon": [108.34, 115.0, 112.04], "lat": [21.54, 15.0, 10.04], "date": ["2026-09-20", "2026-09-20", "2026-09-21"]})
    h_day, h_win = I.presence_block_hours(c, low)
    assert h_day.tolist() == [2.5, 0.0, 0.0] and h_win.tolist() == [3.5, 0.0, 3.0]
    status = I.ais_status([False, False, True], h_win)
    assert status.tolist() == ["unmatched", "no_coverage", "matched"]
    reach = I.ais_reach(c, low, n_days=4)
    assert reach.tolist() == [0.5, 0.0, 0.25]
    assert np.isnan(I.ais_reach(c, low, n_days=0)).all()


def test_nearest_presence_and_counts_within_10km():
    pres = pd.DataFrame({"pass_id": ["p"] * 3, "lon": [108.33, 108.34, 108.6], "lat": [21.51, 21.51, 21.51],
                         "hour_ts": pd.to_datetime(["2026-09-20T10:00Z", "2026-09-20T11:00Z", "2026-09-20T10:00Z"], utc=True),
                         "hours": [1, 1, 1], "vessel_id": ["a", "b", "c"], "mmsi": ["1", "2", "3"], "ship_name": ["A", "B", "C"],
                         "call_sign": [None] * 3, "imo": [None] * 3, "flag": [None] * 3, "gfw_vessel_type": [None] * 3, "gfw_geartype": [None] * 3})
    c = contacts([(108.331, 21.51), (108.60, 21.515)])
    near = I.nearest_presence(c, pres)
    assert near.nearest_ais_vessel_id.tolist() == ["a", "c"] and near.nearest_ais_dt_s.tolist() == [0.0, 0.0]
    assert near.n_ais_10km.tolist() == [1, 1]        # b is in the hour after, so it does not count for the pass hour
    assert near.nearest_ais_dist_m.iloc[0] < 200


def test_events_and_gaps_nearby():
    c = contacts([(110.0, 15.0), (112.0, 15.0)])
    ev = pd.DataFrame({"lon": [110.01, 110.01], "lat": [15.0, 15.0], "start": ["2026-09-19T00:00Z", "2026-09-10T00:00Z"],
                       "end": ["2026-09-20T05:00Z", "2026-09-11T00:00Z"]})
    assert I.events_nearby(c, ev, 10, 24).tolist() == [1, 0]
    gaps = pd.DataFrame({"event_id": ["g1"], "off_lon": [110.3], "off_lat": [15.0], "start": ["2026-09-20T00:00Z"],
                         "on_lon": [112.2], "on_lat": [15.0], "end": ["2026-09-23T00:00Z"]})
    n, km = I.gaps_nearby(c, gaps, 50, 24)
    assert n.tolist() == [1, 0] and 30 < km[0] < 35 and np.isnan(km[1])   # the on position is 3 days later: outside 24 h


def test_merge_identity_prefers_report_fields_and_adds_registry_length():
    rep = pd.DataFrame({"vessel_id": ["v1", "v2"], "mmsi": ["111", None], "ship_name": ["ONE", None], "call_sign": [None, None],
                        "imo": [None, None], "flag": ["ZZZ", None], "gfw_vessel_type": ["FISHING", None], "gfw_geartype": ["TRAWLERS", None]})
    ves = pd.DataFrame({"vessel_id": ["v1", "v2"], "ssvid": ["999", "222"], "shipname": ["WRONG", "TWO"], "callsign": ["CS1", None],
                        "imo": [None, "1234567"], "flag": ["AAA", "BBB"], "shiptype": ["CARGO", "PASSENGER"], "length_m": [42.5, np.nan]})
    m = I.merge_identity(rep, ves).set_index("vessel_id")
    assert m.loc["v1", "mmsi"] == "111" and m.loc["v1", "vessel_name"] == "ONE" and m.loc["v1", "call_sign"] == "CS1"
    assert m.loc["v1", "length_ais_m"] == 42.5 and m.loc["v1", "ship_type"] == "FISHING"
    assert m.loc["v2", "mmsi"] == "222" and m.loc["v2", "vessel_name"] == "TWO" and m.loc["v2", "imo"] == "1234567"
    assert m.loc["v2", "ship_type"] == "PASSENGER" and np.isnan(m.loc["v2", "length_ais_m"])
    assert m.identity_source.str.contains("vessels API").all()


def test_passes_window_and_summary():
    import geopandas as gpd
    from shapely.geometry import box
    scenes = gpd.GeoDataFrame({"scene_idx": [0, 1, 2], "mission": ["S1C", "S1C", "S1D"],
                               "start_utc": ["2026-09-20 10:48:16+00:00", "2026-09-20 10:48:45+00:00", "2026-09-20 11:34:35+00:00"]},
                              geometry=[box(108, 18, 110, 20), box(108, 20, 110, 22), box(98, 6, 101, 12)], crs="EPSG:4326")
    p = I.passes_from_scenes(scenes)
    assert p.pass_id.tolist() == ["S1C_20260920T1048", "S1D_20260920T1134"] and p.n_scenes.tolist() == [2, 1]
    assert I.pass_window(p.t0.iloc[0], p.t1.iloc[0]) == ("2026-09-20T09:00:00.000Z", "2026-09-20T12:00:00.000Z")
    det = pd.DataFrame({"scene_idx": [1, 2]})
    assert I.assign_pass(det, p).tolist() == ["S1C_20260920T1048", "S1D_20260920T1134"]
    df = pd.DataFrame({"det_id": ["a", "b", "c"], "ais_status": ["matched", "unmatched", "no_coverage"], "match_method": [I.MATCH_SAR, None, None],
                       "match_quality": ["high", None, None], "gfw_sar_pair": ["matched", "unmatched", None], "mission": ["S1C", "S1D", "S1D"],
                       "identity_kind": ["vessel", None, None], "pres_speed_kmh": [np.nan] * 3, "pres_n_cand": [0, 2, 0], "ais_presence_h_day": [5.0, 0.0, 0.0],
                       "confidence": ["high", "medium", "medium"], "length_est_m": [40.0, 20.0, 120.0], "length_ais_m": [45.0, np.nan, np.nan],
                       "lon": [108.5, 112.0, 100.0], "lat": [20.0, 20.0, 10.0], "cnn_score": [0.9, 0.1, np.nan], "cnn_vessel": [True, False, None],
                       "nearest_ais_mmsi": [None, "1", None], "nearest_ais_dist_m": [np.nan, 1200.0, np.nan], "n_ais_10km": [3, 1, 0],
                       "ais_reach": [1.0, 0.5, 0.0], "ais_presence_h_window": [10, 2, 0], "gfw_neural_type": [None, "Likely Fishing", None],
                       "n_gfw_gaps_50km_24h": [0, 1, 0], "n_gfw_encounters_10km_24h": [0, 0, 0], "n_gfw_loitering_10km_24h": [0, 2, 0]})
    s = I.summarize(df)
    assert s["by_status"] == {"matched": 1, "unmatched": 1, "no_coverage": 1}
    assert s["by_method_quality"] == {f"{I.MATCH_SAR}|high": 1} and s["by_mission"]["S1C"]["matched_share"] == 1.0
    assert s["by_region"]["Gulf of Tonkin"]["n"] == 1 and s["cnn"]["matched_share_cnn_vessel"] == 1.0
    assert s["length_check"]["n_matched_with_ais_length"] == 1 and s["evidence_unmatched"]["with_gap_event_50km_24h"] == 1
    assert s["length_check"]["by_ais_length_bin"] == {"25-50 m": {"n": 1, "median_radar_m": 40.0, "median_ais_m": 45.0}}   # bins align with the filtered rows
    assert s["matched_by_identity_kind"] == {"vessel": 1} and s["n_matched_gear_identity"] == 0
    assert s["evidence_unmatched"]["no_ais_presence_in_block_on_pass_day"] == 1 and s["evidence_unmatched"]["presence_candidates_rejected"] == 1
    about = I.about_rows({"sar": "public-global-sar-presence:v4.0", "presence": "public-global-presence:v4.0"},
                         {"sar": "2026-10-08", "presence": "2026-10-08 to 2026-10-09"}, ("2026-09-01", "2026-10-08"))
    assert about.licence.iloc[0] == "CC BY-NC 4.0" and "does not mean illegal" in about.caveat_full.iloc[0]
    assert "presence: 2026-10-08 to 2026-10-09" in about.accessed.iloc[0] and "accessed 2026-10-08 to 2026-10-09" in about.attribution.iloc[0]
    assert all(c not in about.to_string() for c in ("\u2013", "\u2014"))
