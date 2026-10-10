"""Offline tests for the leads queue (darkvessel.leads): synthetic frames only, no real data, no network.

Covers the caveat (equal to app/CONTRACT.md 1.1 and to darkvessel.config.PRODUCT_CAVEAT once it exists), every L1
gate condition, the per-part weather gate and the weather-unknown rule (board D4.5), factor points, sum and clip, the
bands, registry source keys, the L7 scoring and its ceiling below L1, the persistence pairing, the next-look lookup
(past passes skipped), lead_id stability across reruns, the open-build guard against data/research/, the caveat on
every lead and evidence row, the contract 3.5 field names, the VIIRS file choice, the L7 selection and evidence cap,
a GeoPackage round trip (atomic write, constant columns as defaults, generated time, no GFW text in the open files,
the open build's two-file layout of board D6.3 and byte-identical reruns of every file), the script rule that only a
run of both builds redraws the two-panel figure, the gate waterfall counts, the gate re-checked on the output rows (no
matched, no_coverage, fixed, low or ambiguous contact becomes a lead), the queue order of model v1 (every L1 lead with
known weather before every L7 lead), the codes of board D5.1 against the frontend's text.ts, and a research input
signature that ignores live passes.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import darkvessel  # noqa: F401  (PROJ_DATA before rasterio and pyogrio)
from darkvessel import config
from darkvessel.leads import PRIORITY_MODEL_ID, PRODUCT_CAVEAT, RESEARCH_LINE, caveat_for
from darkvessel.leads import build as B
from darkvessel.leads import evidence as E
from darkvessel.leads import priority as P
from darkvessel.leads import rules as R
from darkvessel.leads.guard import OpenBuildGuardError, checked_path, is_research_path, read_parquet

REPO = Path(__file__).resolve().parents[1]
T0 = pd.Timestamp("2026-09-25T22:30:00Z")

CONTRACT_35_FIELDS = ["lead_id", "lead_type", "title", "state", "reason", "priority", "priority_band", "factors",
                      "priority_model_id", "calibrated", "primary_type", "primary_id", "evidence", "lon", "lat", "time_utc",
                      "region_box", "next_look_utc", "lawful_explanations", "change_indicators", "history", "research_only",
                      "caveat", "src", "prov"]


def contacts(n_extra: int = 0) -> pd.DataFrame:
    """One passing L1 candidate ('ok') plus one row per failing condition, in the Gulf of Thailand reporting box."""
    base = {"run_id": "live_S1D_20260925T2230", "pass_id": "live_S1D_20260925T2230", "mission": "S1D", "acq_utc": T0.isoformat(),
            "lon": 102.5, "lat": 9.5, "length_est_m": 38.0, "confidence": "high", "cnn_score": 0.8, "ais_status": "unmatched",
            "ais_source": "aisstream", "nearest_ais_mmsi": 574001234, "nearest_ais_dist_m": 5200.0, "nearest_ais_dt_s": 120.0,
            "n_ais_10km": 2, "ais_reach": 0.75, "pol_class": "VV+VH", "n_low_1km": 0, "near_fixed_m": np.nan, "low_reason": None}
    rows = [
        {"det_id": "ok", **base},
        {"det_id": "matched", **base, "ais_status": "matched"},
        {"det_id": "no_coverage", **base, "ais_status": "no_coverage"},
        {"det_id": "low_class", **base, "confidence": "low"},
        {"det_id": "fixed_class", **base, "confidence": "fixed"},
        {"det_id": "weak_cnn", **base, "cnn_score": 0.49},
        {"det_id": "no_cnn", **base, "cnn_score": np.nan},
        {"det_id": "one_channel", **base, "pol_class": "VH only", "confidence": "medium"},
        {"det_id": "clutter", **base, "n_low_1km": 5},
        {"det_id": "near_fixed", **base, "near_fixed_m": 100.0},
        {"det_id": "windy", **base},
        {"det_id": "convective", **base},
        {"det_id": "weather_unknown", **base},
        {"det_id": "channel_unknown", **base, "pol_class": None, "confidence": "high"},
    ]
    for i in range(n_extra):
        rows.append({"det_id": f"x{i:03d}", **base, "lon": 102.6 + i * 0.01})
    return pd.DataFrame(rows)


def weather() -> pd.DataFrame:
    rows = [{"det_id": "ok", "wind_ms": 4.0, "ctt_k": np.nan, "deep_convection": False},
            {"det_id": "windy", "wind_ms": 13.0, "ctt_k": np.nan, "deep_convection": False},
            {"det_id": "convective", "wind_ms": 3.0, "ctt_k": 205.0, "deep_convection": True},
            {"det_id": "channel_unknown", "wind_ms": 5.0, "ctt_k": np.nan, "deep_convection": False}]
    for d in ("matched", "no_coverage", "low_class", "fixed_class", "weak_cnn", "no_cnn", "one_channel", "clutter", "near_fixed"):
        rows.append({"det_id": d, "wind_ms": 4.0, "ctt_k": np.nan, "deep_convection": False})
    return pd.DataFrame(rows)


def plan(groups=(("P1", "2026-09-26T10:00:00Z", [102.0, 9.0, 103.0, 10.0], "esa_plan"),
               ("P2", "2026-09-28T10:00:00Z", [102.0, 9.0, 103.0, 10.0], "repeat_cycle"),
               ("P0", "2026-09-20T10:00:00Z", [102.0, 9.0, 103.0, 10.0], "esa_plan"))) -> dict:
    return {"passes": [{"pass_group": g, "start_utc": t, "footprint_bbox": bb, "source": s, "status": "upcoming"} for g, t, bb, s in groups]}


# ---------------------------------------------------------------------------------------------------------------------
# caveat

def test_caveat_equals_contract_and_config():
    text = (REPO / "app" / "CONTRACT.md").read_text()
    m = re.search(r'`PRODUCT_CAVEAT` = "(.*?)"\n', text)
    assert m, "contract 1.1 must define PRODUCT_CAVEAT"
    assert m.group(1) == PRODUCT_CAVEAT
    if hasattr(config, "PRODUCT_CAVEAT"):
        assert config.PRODUCT_CAVEAT == PRODUCT_CAVEAT
    assert caveat_for("open") == PRODUCT_CAVEAT
    assert caveat_for("research") == PRODUCT_CAVEAT + " " + RESEARCH_LINE
    assert "Powered by Global Fishing Watch." in RESEARCH_LINE
    for s in (PRODUCT_CAVEAT, RESEARCH_LINE, *R.LAWFUL_EXPLANATIONS["L1"].values(), *R.LAWFUL_EXPLANATIONS["L7"].values(),
              *R.CHANGE_INDICATORS["L1"].values(), *R.CHANGE_INDICATORS["L7"].values(), *R.RULE_TEXT.values()):
        assert "\u2013" not in s and "\u2014" not in s


# ---------------------------------------------------------------------------------------------------------------------
# L1 gate

def test_l1_gate_each_condition():
    c = E.join_weather(contacts(), weather())
    g = R.l1_gate(c).set_index(c.det_id)
    assert g.loc["ok", "l1"]
    assert g.loc["channel_unknown", "l1"]          # polarisation unknown is kept (class high reads as both channels)
    assert g.loc["weather_unknown", "l1"] and not g.loc["weather_unknown", "weather_known"]
    fails = {"matched": "unmatched", "no_coverage": "unmatched", "low_class": "vessel_class", "fixed_class": "vessel_class",
             "weak_cnn": "cnn", "no_cnn": "cnn", "one_channel": "both_channels", "clutter": "not_clutter",
             "near_fixed": "not_near_fixed", "windy": "weather", "convective": "weather"}
    for det, cond in fails.items():
        assert not g.loc[det, "l1"], det
        assert not g.loc[det, cond], (det, cond)
        others = [k for k in R.L1_CONDITIONS if k != cond]
        assert g.loc[det, others].all(), (det, "only one condition should fail")
    counts = R.l1_gate_counts(g)
    assert counts["pass_all"] == 3 and counts["pass_with_weather_unknown"] == 1 and counts["pass_strict_weather_known"] == 2
    assert counts["fail_by_condition_among_unmatched"]["weather"] == 2


def test_weather_gate_per_part():
    """Wind and deep convection are gated one by one: a known failure excludes the contact even when the other part
    is missing; a missing part with a passing known part keeps the lead as 'weather unknown'."""
    df = pd.DataFrame({
        "det_id": ["calm", "conv_only_true", "conv_only_false", "wind_only_high", "wind_only_low", "none", "conv_float"],
        "ais_status": "unmatched", "confidence": "high", "cnn_score": 0.9,
        "wind_ms": [4.0, np.nan, np.nan, 13.0, 5.0, np.nan, np.nan],
        "deep_convection": [False, True, False, None, None, None, 1.0],
    })
    g = R.l1_gate(df).set_index(df.det_id)
    assert g.l1.to_dict() == {"calm": True, "conv_only_true": False, "conv_only_false": True, "wind_only_high": False,
                              "wind_only_low": True, "none": True, "conv_float": False}
    assert g.weather_known.to_dict()["calm"] and not g.weather_known[["conv_only_false", "wind_only_low", "none"]].any()
    assert g.weather_missing.to_dict() == {"calm": "", "conv_only_true": "wind", "conv_only_false": "wind",
                                           "wind_only_high": "deep convection", "wind_only_low": "deep convection",
                                           "none": "wind and deep convection", "conv_float": "wind"}
    counts = R.l1_gate_counts(g)
    assert counts["pass_all"] == 4 and counts["pass_with_weather_unknown"] == 3
    assert counts["pass_weather_missing_by_part"] == {"wind": 1, "deep convection": 1, "wind and deep convection": 1}
    # the points: only 'calm' earns the 5 weather points; every kept unknown names its missing part
    df["both_channels"] = True
    for c in ("n_lights_2km_3h", "n_ais_events_2km_3h", "n_persist_72h", "n_ais_10km"):
        df[c] = 0
    df["ais_reach"], df["region_box"], df["ais_source"] = 0.0, "other", "gfw"
    pts, factors = P.l1_points(df, build="research")
    assert pts.pts_evidence_quality.tolist() == [16 + 5 + 5, 21, 21, 21, 21, 21, 21]   # cnn 0.9: 16; channels 5; weather 5 only when both known and calm
    by = dict(zip(df.det_id, factors))
    assert by["conv_only_false"][-1]["factor"] == "weather unknown" and by["conv_only_false"][-1]["value"].startswith("wind unknown")
    assert by["wind_only_low"][-1]["value"].startswith("deep convection unknown")
    assert all(f["factor"] != "weather unknown" for f in by["calm"])


def test_l1_gate_regional_class_encodes_channels():
    df = pd.DataFrame({"det_id": ["a", "b"], "ais_status": "unmatched", "confidence": ["high", "medium"], "cnn_score": 0.9})
    g = R.l1_gate(df)
    assert g.l1.tolist() == [True, False]
    assert R.both_channels(df).tolist() == [True, False]
    assert g.channels.tolist() == ["VV and VH (class high)", "one channel (class medium)"]


def test_weather_unknown_rule_in_factors():
    leads, ev, counts = B.build_l1(contacts(), "open", weather(), None, None, None, E.passes_frame(plan()))
    by = leads.set_index("det_id")
    f_unknown = json.loads(by.loc["weather_unknown", "factors"])
    assert f_unknown[-1]["factor"] == "weather unknown" and f_unknown[-1]["points"] == 0 and f_unknown[-1]["max_points"] == 0
    f_ok = json.loads(by.loc["ok", "factors"])
    assert all(f["factor"] != "weather unknown" for f in f_ok)
    # the known-calm contact earns the 5 weather points the unknown one does not
    assert by.loc["ok", "pts_evidence_quality"] - by.loc["weather_unknown", "pts_evidence_quality"] == P.L1_WEATHER_PTS
    assert counts["weather_unknown"] == 1
    assert any(e["role"] == "weather" for e in json.loads(by.loc["ok", "evidence"]))
    assert not any(e["role"] == "weather" for e in json.loads(by.loc["weather_unknown", "evidence"]))


# ---------------------------------------------------------------------------------------------------------------------
# priority

def test_factor_points_sum_clip_and_max():
    df = pd.DataFrame([
        {"cnn_score": 1.0, "both_channels": True, "weather_known": True, "wind_ms": 2.0, "deep_convection": False, "n_lights_2km_3h": 1,
         "n_ais_events_2km_3h": 1, "ais_reach": 1.0, "n_ais_10km": 10, "n_persist_72h": 1, "region_box": "Gulf of Thailand", "ais_source": "aisstream"},
        {"cnn_score": 0.5, "both_channels": None, "weather_known": False, "wind_ms": np.nan, "deep_convection": None, "n_lights_2km_3h": 0,
         "n_ais_events_2km_3h": 0, "ais_reach": 0.0, "n_ais_10km": 0, "n_persist_72h": 0, "region_box": "other", "ais_source": "gfw"},
        {"cnn_score": 0.75, "both_channels": True, "weather_known": True, "wind_ms": 5.0, "deep_convection": False, "n_lights_2km_3h": 0,
         "n_ais_events_2km_3h": 0, "ais_reach": 0.5, "n_ais_10km": 5, "n_persist_72h": 0, "region_box": "other", "ais_source": "aisstream"},
    ])
    pts, factors = P.l1_points(df, area_weights={"Gulf of Thailand": 10})
    pts["priority"] = P.total(pts)
    assert pts.priority.tolist()[0] == 100
    assert [f["points"] for f in factors[0]] == [30, 25, 20, 15, 10]
    assert pts.priority.tolist()[1] == 0
    assert pts.priority.tolist()[2] == 20 + 6 + 4 + 0 + 0   # cnn 10 + channels 5 + weather 5; reach 6 + density 4
    for fl, p in zip(factors, pts.priority):
        assert P.check_factors(fl, p)
        assert all(0 <= f["points"] <= f["max_points"] or f["max_points"] == 0 for f in fl)
    registry = set(re.findall(r"^\| `([a-z0-9_]+)` \|", (REPO / "app" / "CONTRACT.md").read_text(), flags=re.M))
    assert {"det_live", "det_regional", "viirs_dnb", "gfw_events", "aisstream", "analyst", "s1_grd"} <= registry
    l7_pts, l7_factors = P.l7_points(pd.DataFrame({"n_lights": [3], "n_nights": [2], "ais_reach_share": [0.0], "region_box": "other"}))
    for fl in factors + l7_factors:
        for f in fl:
            assert set(f["source"].split("; ")) <= registry, f["source"]
    assert factors[0][1]["source"] == "viirs_dnb" and factors[1][1]["source"] == "viirs_dnb; gfw_events"
    assert factors[0][3]["source"] == "det_live" and factors[1][3]["source"] == "det_regional"
    assert factors[0][1]["value"].endswith("(no event source in this build yet)") and factors[1][1]["value"].endswith("3 h: 0")
    big = pd.DataFrame({P.POINTS_COLUMNS[f]: [60] for f in P.FACTORS})
    assert P.total(big).tolist() == [100]
    neg = pd.DataFrame({P.POINTS_COLUMNS[f]: [-5] for f in P.FACTORS})
    assert P.total(neg).tolist() == [0]


def test_bands():
    assert P.band([0, 33, 34, 66, 67, 100]).tolist() == ["low", "low", "medium", "medium", "high", "high"]


def test_l7_points_within_spec_meaning_and_ceiling():
    df = pd.DataFrame({"n_lights": [1, 30, 200, 0], "n_nights": [1, 4, 9, 0], "ais_reach_share": [1.0, 0.5, 0.0, np.nan],
                       "region_box": "other"})
    pts, factors = P.l7_points(df)
    assert pts.pts_evidence_quality.tolist() == [1, 5, 5, 0]            # a sixth of the L1 scale
    assert pts.pts_corroboration.tolist() == [0, 0, 0, 0]               # radar did not look; lights not matched to AIS
    assert pts.pts_ais_reach.tolist() == [0, 0, 0, 0]                   # an L7 lead makes no AIS claim
    assert pts.pts_persistence.tolist() == [0, 3, 5, 0]                 # other nights: 0 for one night, 5 at 7 or more
    assert P.total(pts).tolist() == [1, 8, 10, 0]
    assert P.total(pts).max() <= P.L7_CEILING == 10 and set(P.band(P.total(pts))) == {"low"}
    for fl, p in zip(factors, P.total(pts)):
        assert P.check_factors(fl, p) and [f["factor"] for f in fl] == P.FACTORS
    # with an analyst area weight the cell can leave the low band; that is the analyst's call
    w, _ = P.l7_points(df.assign(region_box="Gulf of Thailand"), area_weights={"Gulf of Thailand": 10})
    assert P.total(w).max() == 20
    # no L7 lead outranks an open-build L1 lead with ordinary evidence: CNN 0.75, both channels, weather unknown, half AIS
    # reach, no AIS vessel within 10 km, no corroboration, no persistence (10 + 5 + 0 + 6 = 21 points)
    l1 = pd.DataFrame([{"cnn_score": 0.75, "both_channels": True, "wind_ms": np.nan, "deep_convection": None, "n_lights_2km_3h": 0,
                        "n_ais_events_2km_3h": 0, "ais_reach": 0.5, "n_ais_10km": 0, "n_persist_72h": 0, "region_box": "other",
                        "ais_source": "aisstream"}])
    l1_pts, _ = P.l1_points(l1)
    assert P.total(l1_pts).iloc[0] == 21 > P.total(pts).max() == P.L7_CEILING
    # owner P0, model v1: the weakest L1 lead with known calm weather (CNN at the 0.5 floor, both channels, AIS never heard
    # in the cell, no AIS vessel within 10 km) still reaches the L7 ceiling, and ties sort L1 first (lead_id)
    weakest = l1.assign(cnn_score=0.5, wind_ms=5.0, deep_convection=False, ais_reach=0.0)
    w_pts, _ = P.l1_points(weakest)
    assert P.total(w_pts).iloc[0] == P.L1_FLOOR_WEATHER_KNOWN == 10 >= P.L7_CEILING
    assert PRIORITY_MODEL_ID == "lead_priority_v1_20261010"


# ---------------------------------------------------------------------------------------------------------------------
# evidence joins

def test_persistence_pairing():
    leads = pd.DataFrame({"det_id": ["A"], "lon": [103.0], "lat": [9.0], "acq_utc": [T0.isoformat()], "pass_id": ["pA"]})
    km = 1 / 111.0
    pool = pd.DataFrame({
        "det_id": ["A", "B_other_pass_24h", "C_100h", "D_3km", "E_same_pass", "F_5min"],
        "lon": [103.0, 103.0 + km, 103.0 + km, 103.0 + 3 * km, 103.0 + km, 103.0 + km], "lat": 9.0,
        "acq_utc": [T0.isoformat(), (T0 + pd.Timedelta(hours=24)).isoformat(), (T0 + pd.Timedelta(hours=100)).isoformat(),
                    (T0 + pd.Timedelta(hours=24)).isoformat(), T0.isoformat(), (T0 + pd.Timedelta(minutes=5)).isoformat()],
        "pass_id": ["pA", "pB", "pC", "pB", "pA", "pA"],
    })
    assert E.persistence_pairs(leads, pool) == [["B_other_pass_24h"]]
    assert E.persistence_pairs(leads, pool.iloc[0:0]) == [[]]


def test_lights_and_events_within_window():
    c = pd.DataFrame({"det_id": ["A"], "lon": [103.0], "lat": [9.0], "acq_utc": [T0.isoformat()]})
    lights = pd.DataFrame({"light_id": ["near_in_time", "near_late", "far"], "lon": [103.0 + 0.5 / 111, 103.0 + 0.5 / 111, 103.1],
                           "lat": 9.0, "time_utc": [(T0 - pd.Timedelta(hours=2)).isoformat(), (T0 + pd.Timedelta(hours=4)).isoformat(), T0.isoformat()]})
    assert E.lights_near(c, lights) == [["near_in_time"]]
    events = pd.DataFrame({"event_id": ["loiter_spanning", "encounter_old"], "lon": [103.0, 103.0], "lat": [9.0, 9.0],
                           "start": [(T0 - pd.Timedelta(hours=10)).isoformat(), (T0 - pd.Timedelta(hours=30)).isoformat()],
                           "end": [(T0 - pd.Timedelta(hours=1)).isoformat(), (T0 - pd.Timedelta(hours=20)).isoformat()]})
    assert E.events_near(c, events) == [["loiter_spanning"]]


def test_next_look_lookup():
    passes = E.passes_frame(plan())
    assert len(passes) == 3 and passes.pass_group.tolist() == ["P0", "P1", "P2"]   # sorted by start
    when, grp, src = E.next_look([102.5, 102.5, 110.0], [9.5, 9.5, 9.5],
                                 [T0, pd.Timestamp("2026-09-27T00:00:00Z"), T0], passes)
    assert when == ["2026-09-26T10:00:00Z", "2026-09-28T10:00:00Z", None]
    assert grp == ["P1", "P2", None] and src == ["esa_plan", "repeat_cycle", None]
    e_when, e_grp, e_src = E.next_look([], [], [], passes)
    assert e_when == [] and e_grp == [] and e_src == []
    none_when, _, _ = E.next_look([102.5], [9.5], [T0], E.passes_frame(None))
    assert none_when == [None]
    # a plan generated after the lead: passes before the plan time are already past and are skipped
    w2, g2, _ = E.next_look([102.5], [9.5], [T0], passes, not_before="2026-09-27T00:00:00Z")
    assert w2 == ["2026-09-28T10:00:00Z"] and g2 == ["P2"]
    # a pass the plan marks past is skipped even without a plan time
    p = plan()
    p["passes"][0]["status"] = "past"
    w3, g3, _ = E.next_look([102.5], [9.5], [T0], E.passes_frame(p))
    assert g3 == ["P2"]
    w4, _, _ = E.next_look([102.5], [9.5], [T0], passes, not_before="2026-10-01T00:00:00Z")
    assert w4 == [None]


def test_cell_ids_and_regions():
    ids, row, col = E.cell_ids([99.125, 121.0], [23.875, 9.0])
    assert ids[0] == "r0c0" and ids[1] == f"r{row[1]}c{col[1]}"
    assert E.regions([102.5, 150.0], [9.5, 0.0]).tolist() == ["Gulf of Thailand", "other"]


# ---------------------------------------------------------------------------------------------------------------------
# build, ids, caveat, contract fields

def test_lead_id_stability_and_contract_fields():
    kw = dict(weather=weather(), static=None, lights=None, events=None, passes=E.passes_frame(plan()))
    a, ev_a, _ = B.build_l1(contacts(5), "open", **kw)
    b, ev_b, _ = B.build_l1(contacts(5), "open", **kw)
    fa, fb = B.finalize([a], "open"), B.finalize([b], "open")
    pd.testing.assert_frame_equal(fa, fb)
    assert fa.lead_id.tolist() == sorted(fa.lead_id.tolist(), key=lambda s: (-int(fa.set_index("lead_id").priority[s]), s))
    assert set(fa.lead_id) == {"L1-ok", "L1-channel_unknown", "L1-weather_unknown"} | {f"L1-x{i:03d}" for i in range(5)}
    for f in CONTRACT_35_FIELDS:
        assert f in fa.columns, f
    assert (fa.state == "new").all() and fa.reason.isna().all() and (fa.history == "[]").all()
    assert (fa.priority_model_id == PRIORITY_MODEL_ID).all() and not fa.calibrated.any()
    assert (fa.primary_type == "contact").all() and (fa.primary_id == fa.det_id).all()
    for s, p in zip(fa.factors, fa.priority):
        assert P.check_factors(json.loads(s), p)
    assert fa.next_look_utc.notna().all() and (fa.next_look_pass == "P1").all()
    assert fa.set_index("lead_id").loc["L1-ok", "title"] == "Unmatched radar contact in AIS reach, 38 m, Gulf of Thailand"
    assert json.loads(fa.lawful_explanations.iloc[0]) == list(R.LAWFUL_EXPLANATIONS["L1"])
    assert json.loads(fa.change_indicators.iloc[0]) == list(R.CHANGE_INDICATORS["L1"])
    assert "no_carriage_requirement" in json.loads(fa.lawful_explanations.iloc[0])
    assert fa.set_index("lead_id").loc["L1-ok", "nearest_ais_key"] == "mmsi:574001234"
    assert not any(c.startswith("gfw_") for c in fa.columns)


def test_caveat_on_every_row_both_builds():
    for build in ("open", "research"):
        c = contacts(3)
        if build == "research":
            c["ais_source"] = "gfw"
            c["nearest_ais_vessel_id"] = "abc123"
        leads, ev, _ = B.build_l1(c, build, weather(), None, None, None, E.passes_frame(plan()))
        lights = synthetic_lights()
        l7, ev7, _ = B.build_l7(lights, None, build, None, E.passes_frame(plan()), (None, None, None), (None, None, None))
        df = B.finalize([leads, l7], build)
        evf = B.evidence_frame(ev + ev7, build)
        assert len(df) and len(evf)
        assert (df.caveat == caveat_for(build)).all() and (evf.caveat == caveat_for(build)).all()
        assert df.caveat.str.startswith(PRODUCT_CAVEAT).all()
        assert (df.research_only == (build == "research")).all() and (evf.research_only == (build == "research")).all()
        if build == "research":
            assert df.caveat.str.endswith(RESEARCH_LINE).all()
            assert df.set_index("lead_id").loc["L1-ok", "nearest_ais_key"] == "gfw:abc123"
            assert all(col in df.columns for col in B.RESEARCH_COLUMNS)
        else:
            assert not any(col in df.columns for col in B.RESEARCH_COLUMNS)
        about = B.about_frame({"build": build, "counts": {"leads": len(df), "by_type": {}, "by_band": {}}, "inputs": [], "inputs_signature": "x",
                               "notes": {}, "since": None, "until": None}, "2026-10-09T00:00:00Z")
        assert about.caveat.iloc[0] == caveat_for(build)
        if build == "research":
            assert about.licence.iloc[0] == "CC BY-NC 4.0" and "Global Fishing Watch" in about.attribution.iloc[0]


def synthetic_lights() -> pd.DataFrame:
    rows = []
    t = pd.Timestamp("2026-09-10T18:00:00Z")
    for i in range(6):   # one cell, 6 clear never-imaged lights on 3 nights
        rows.append({"light_id": f"SPP_2026091{i % 3}T180000_{i:06d}", "lon": 109.12 + i * 0.002, "lat": 7.12, "time_utc": (t + pd.Timedelta(days=i % 3)).isoformat(),
                     "night": f"2026-09-1{i % 3}", "quality": "clear", "s1_passes_90d": 0, "satlas_infra_m": 5000.0, "radiance_nw": 10.0 + i})
    rows.append({"light_id": "cloudy", "lon": 109.12, "lat": 7.12, "time_utc": t.isoformat(), "night": "2026-09-10", "quality": "under_cloud",
                 "s1_passes_90d": 0, "satlas_infra_m": 5000.0, "radiance_nw": 10.0})
    rows.append({"light_id": "imaged", "lon": 109.12, "lat": 7.12, "time_utc": t.isoformat(), "night": "2026-09-10", "quality": "clear",
                 "s1_passes_90d": 3, "satlas_infra_m": 5000.0, "radiance_nw": 10.0})
    rows.append({"light_id": "platform", "lon": 109.12, "lat": 7.12, "time_utc": t.isoformat(), "night": "2026-09-10", "quality": "clear",
                 "s1_passes_90d": 0, "satlas_infra_m": 300.0, "radiance_nw": 10.0})
    rows.append({"light_id": "other_cell", "lon": 112.6, "lat": 10.6, "time_utc": t.isoformat(), "night": "2026-09-10", "quality": "clear",
                 "s1_passes_90d": 0, "satlas_infra_m": np.nan, "radiance_nw": 3.0})
    return pd.DataFrame(rows)


def test_l7_selection_and_leads():
    lights = synthetic_lights()
    sel, counts = R.l7_select(lights)
    assert counts == {"lights": 10, "clear_lit_vessel_candidates": 9, "clear_never_imaged_90d": 8, "excluded_near_satlas_1km": 1, "kept": 7}
    sel2, counts2 = R.l7_select(lights.assign(**{"class": ["lit_vessel_candidate"] * 9 + ["persistent_light"]}))
    assert counts2["clear_lit_vessel_candidates"] == 8 and "other_cell" not in set(sel2.light_id)
    sites = pd.DataFrame({"site_id": ["S1"], "lon": [109.12], "lat": [7.12]})
    l7, ev, c7 = B.build_l7(lights, sites, "open", None, E.passes_frame(plan()), (None, None, None), (None, None, None))
    assert len(l7) == 2 and c7["leads"] == 2 and c7["lights_in_leads"] == 7
    big = l7.set_index("lead_id").iloc[[0]] if l7.iloc[0].n_lights == 6 else l7.set_index("lead_id").iloc[[1]]
    row = big.iloc[0]
    assert row.n_lights == 6 and row.n_nights == 3 and json.loads(row.nights) == ["2026-09-10", "2026-09-11", "2026-09-12"]
    assert row.n_lights_at_sites >= 1 and row.primary_type == "cell" and row.primary_id.startswith("r")
    assert "coverage lead, not a vessel lead" in row.title
    assert row.time_utc == "2026-09-12T18:00:00Z"
    f = json.loads(row.factors)
    assert [x["factor"] for x in f] == P.FACTORS and P.check_factors(f, row.priority)
    assert any(e["role"] == "recurring_site" for e in json.loads(row.evidence))
    assert json.loads(row.lawful_explanations) == list(R.LAWFUL_EXPLANATIONS["L7"])
    assert pd.isna(row.next_look_utc)   # the synthetic plan covers the Gulf of Thailand box only


def test_l7_evidence_cap_brightest_first():
    t = pd.Timestamp("2026-09-10T18:00:00Z")
    lights = pd.DataFrame([{"light_id": f"L{i:03d}", "lon": 109.12 + i * 0.001, "lat": 7.12, "time_utc": t.isoformat(),
                            "night": f"2026-09-{10 + i % 8:02d}", "quality": "clear", "s1_passes_90d": 0, "satlas_infra_m": np.nan,
                            "radiance_nw": float(i)} for i in range(25)])
    l7, ev, c7 = B.build_l7(lights, None, "open", None, E.passes_frame(None), (None, None, None), (None, None, None))
    row = l7.iloc[0]
    assert row.n_lights == 25 and row.n_nights == 8
    lids = [e["id"] for e in json.loads(row.evidence) if e["role"] == "light"]
    assert lids == [f"L{i:03d}" for i in range(24, 4, -1)]           # the 20 brightest, brightest first
    assert c7["light_evidence_rows"] == R.L7_EVIDENCE_LIGHTS_MAX and row.priority == 5 + 5   # 5 x ln 26 / ln 31 = 4.7; 8 nights


def test_load_lights_prefers_every_night_file(tmp_path):
    import geopandas as gpd

    def gdf(df):
        return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs="EPSG:4326")

    every = synthetic_lights().assign(**{"class": ["lit_vessel_candidate"] * 9 + ["persistent_light"]})
    lean = synthetic_lights().iloc[:3].assign(**{"class": "lit_vessel_candidate"})
    sites = pd.DataFrame({"site_id": ["S1"], "lon": [109.12], "lat": [7.12]})
    p_all, p_lean = tmp_path / "viirs_lights_all.gpkg", tmp_path / "viirs_lights.gpkg"
    gdf(every).to_file(p_all, layer="viirs_lights_4326", driver="GPKG", engine="pyogrio")
    gdf(lean).to_file(p_lean, layer="viirs_lights_4326", driver="GPKG", engine="pyogrio")
    gdf(sites).to_file(p_lean, layer="viirs_sites_4326", driver="GPKG", engine="pyogrio")
    lights, s, note = B.load_lights("open", {"viirs_lights_all": [p_all], "viirs_lights": [p_lean]})
    assert len(lights) == 9 and set(lights["class"]) == {"lit_vessel_candidate"} and "viirs_lights_all.gpkg" in note
    assert s.site_id.tolist() == ["S1"]
    lights2, s2, note2 = B.load_lights("open", {"viirs_lights_all": [tmp_path / "missing.gpkg"], "viirs_lights": [p_lean]})
    assert len(lights2) == 3 and "viirs_lights.gpkg" in note2 and len(s2) == 1


# ---------------------------------------------------------------------------------------------------------------------
# guard

def test_open_build_guard():
    for p in ("data/research/regional_identity.parquet", REPO / "data" / "research" / "x.gpkg", "/elsewhere/data/research/y.parquet"):
        assert is_research_path(p)
        with pytest.raises(OpenBuildGuardError):
            checked_path(p, "open")
        checked_path(p, "research")
    assert not is_research_path("data/live/live_contacts.gpkg")
    with pytest.raises(OpenBuildGuardError):
        read_parquet(REPO / "data" / "research" / "regional_identity.parquet", "open")
    with pytest.raises(ValueError):
        checked_path("data/x.gpkg", "commercial")
    paths = B.input_paths("open")
    assert not any(is_research_path(p) for lst in paths.values() for p in lst)
    assert "regional_identity" not in paths and "gfw_events_loitering" not in paths
    assert "regional_identity" in B.input_paths("research")
    with pytest.raises(OpenBuildGuardError):
        B.load_events("open", {"gfw_events_loitering": [REPO / "data" / "research" / "gfw_events_loitering.parquet"]}) or B.checked_path(
            REPO / "data" / "research" / "gfw_events_loitering.parquet", "open")


# ---------------------------------------------------------------------------------------------------------------------
# outputs

def test_gpkg_round_trip(tmp_path, monkeypatch):
    import pyogrio

    outputs = {"open": {"gpkg": tmp_path / "leads_open.gpkg", "detail": tmp_path / "leads_open_detail.gpkg",
                        "summary": tmp_path / "leads_open_summary.json", "parquet": None},
               "research": {"gpkg": tmp_path / "leads_research.gpkg", "detail": None, "summary": tmp_path / "leads_research_summary.json",
                            "parquet": tmp_path / "leads_research.parquet"}}
    monkeypatch.setattr(B, "OUTPUTS", outputs)
    for build in ("open", "research"):
        c = contacts(2)
        if build == "research":
            c["ais_source"] = "gfw"
        leads, ev, c1 = B.build_l1(c, build, weather(), None, None, None, E.passes_frame(plan()))
        l7, ev7, c7 = B.build_l7(synthetic_lights(), None, build, None, E.passes_frame(plan()), (None, None, None), (None, None, None))
        df = B.finalize([leads, l7], build)
        res = {"build": build, "leads": df, "evidence": B.evidence_frame(ev + ev7, build), "inputs": [{"path": "x", "mtime_utc": "t", "size": 1}],
               "inputs_signature": "sig1", "notes": {}, "plan": {}, "since": None, "until": None, "seconds": 0.1,
               "counts": {"leads": len(df), "by_type": {}, "by_band": {}, "by_type_band": {}, "by_region_box": {}, "by_type_region_box": {},
                          "evidence_rows": len(ev) + len(ev7), "L1": c1, "L7": c7, "with_next_look": 0, "factor_distributions": {}, "corroboration_window": {}}}
        w1 = B.write_outputs(res, log=lambda *a: None, now="2026-10-01T00:00:00Z")
        gpkg = outputs[build]["gpkg"]
        detail = outputs[build]["detail"] or gpkg              # open: two files (board D6.3); research: one file
        assert w1["generated_utc"] == "2026-10-01T00:00:00Z"
        assert not list(tmp_path.glob(".*writing*"))          # written under a temporary name, then moved into place
        layers = [l[0] for l in pyogrio.list_layers(gpkg)]
        if build == "open":
            assert layers == ["leads_4326", "about"]
            assert [l[0] for l in pyogrio.list_layers(detail)] == ["leads_utm49n", "lead_evidence", "about"]
            assert set(w1["paths"]) == {"gpkg", "detail", "summary"}
        else:
            assert layers == ["leads_4326", "leads_utm49n", "lead_evidence", "about"]
        assert pyogrio.read_info(gpkg, layer="leads_4326")["crs"] == "EPSG:4326"
        assert pyogrio.read_info(detail, layer="leads_utm49n")["crs"] == "EPSG:32649"
        back = pyogrio.read_dataframe(gpkg, layer="leads_4326")
        assert len(back) == len(df) and (back.caveat == caveat_for(build)).all()
        assert (back.research_only == (build == "research")).all()
        utm = pyogrio.read_dataframe(detail, layer="leads_utm49n")
        assert utm.lead_id.tolist() == back.lead_id.tolist() and (utm.caveat == caveat_for(build)).all()
        assert list(utm.columns) == list(back.columns)          # the UTM layer keeps every column too (project rule 4)
        evb = pyogrio.read_dataframe(detail, layer="lead_evidence")
        assert len(evb) == len(ev) + len(ev7) and (evb.caveat == caveat_for(build)).all()
        assert evb.research_only.dtype == bool and (evb.research_only == (build == "research")).all()
        for f in {gpkg, detail}:
            with sqlite3.connect(f) as con:                     # the caveat is a column default, stored once per table
                tables = [r[0] for r in con.execute("SELECT table_name FROM gpkg_contents")]
                for t in tables:
                    if t.startswith("lead"):
                        assert {r[1]: r[4] for r in con.execute(f'PRAGMA table_info("{t}")')}["caveat"] is not None, t
                if "lead_evidence" in tables:
                    dflt = {r[1]: r[4] for r in con.execute('PRAGMA table_info("lead_evidence")')}
                    assert dflt["caveat"] == "'" + caveat_for(build).replace("'", "''") + "'"
                assert con.execute("PRAGMA page_size").fetchone()[0] == B.GPKG_PAGE_SIZE
            ab = pyogrio.read_dataframe(f, layer="about")
            assert json.loads(ab.file_layout.iloc[0]) == B.file_layout(build) and ab.generated_utc.iloc[0] == w1["generated_utc"]
        for f in CONTRACT_35_FIELDS:
            assert f in back.columns
        about = pyogrio.read_dataframe(gpkg, layer="about")
        assert about.priority_model_id.iloc[0] == PRIORITY_MODEL_ID and about.caveat.iloc[0] == caveat_for(build)
        assert json.loads(about.lawful_explanations.iloc[0])["L1"]["no_carriage_requirement"].startswith("No carriage requirement")
        assert json.loads(about.weights.iloc[0])["max_points"] == P.MAX_POINTS
        # a rerun with the same inputs keeps the generated time and the bytes of every file
        first = {k: v.read_bytes() for k, v in w1["paths"].items()}
        w2 = B.write_outputs(res, log=lambda *a: None, now="2026-10-02T00:00:00Z")
        assert w2["generated_utc"] == w1["generated_utc"] == "2026-10-01T00:00:00Z"
        assert {k: v.read_bytes() for k, v in w2["paths"].items()} == first
        # a changed input signature moves the generated time
        res2 = {**res, "inputs_signature": "sig2"}
        w3 = B.write_outputs(res2, log=lambda *a: None, now="2026-10-03T00:00:00Z")
        assert w3["generated_utc"] == "2026-10-03T00:00:00Z"
        assert pyogrio.read_dataframe(gpkg, layer="about").generated_utc.iloc[0] == "2026-10-03T00:00:00Z"
        if build == "open":   # no research-only source name may reach an open file (contract section 2)
            blob = gpkg.read_bytes() + detail.read_bytes() + outputs[build]["summary"].read_bytes()
            for word in (b"GFW", b"gfw", b"Global Fishing Watch", b"CC BY-NC"):
                assert word not in blob, word
        summary = json.loads(outputs[build]["summary"].read_text())
        assert summary["caveat"] == caveat_for(build) and summary["calibrated"] is False
        assert summary["file_layout"] == B.file_layout(build)
        assert {B._rel(f): f.stat().st_size for f in {gpkg, detail}} == {k: v for k, v in summary["output_bytes"].items() if k.endswith(".gpkg")}
        if build == "research":
            import pyarrow.parquet as pq

            meta = pq.read_metadata(outputs[build]["parquet"]).metadata
            assert meta[b"licence"] == b"CC BY-NC 4.0" and meta[b"research_only"] == b"true" and b"attribution" in meta
            assert meta[b"caveat"].decode() == caveat_for("research")
            twin = pd.read_parquet(outputs[build]["parquet"])
            assert len(twin) == len(df) and twin.research_only.all()


# ---------------------------------------------------------------------------------------------------------------------
# round 3: live ambiguity flag and live weather sidecars

def test_l1_gate_excludes_ambiguous_live_contact():
    c = contacts()
    c = pd.concat([c, c[c.det_id == "ok"].assign(det_id="ambiguous")], ignore_index=True)
    c["match_ambiguous"] = c.det_id == "ambiguous"
    w = pd.concat([weather(), weather()[weather().det_id == "ok"].assign(det_id="ambiguous")], ignore_index=True)
    g = R.l1_gate(E.join_weather(c, w)).set_index(c.det_id)
    assert g.loc["ok", "l1"] and not g.loc["ambiguous", "l1"] and not g.loc["ambiguous", "not_ambiguous"]
    counts = R.l1_gate_counts(R.l1_gate(E.join_weather(c, w)))
    assert counts["fail_by_condition_among_unmatched"]["not_ambiguous"] == 1
    # inputs without the flag (the September run) keep the earlier count layout
    counts_old = R.l1_gate_counts(R.l1_gate(E.join_weather(contacts(), weather())))
    assert "not_ambiguous" not in counts_old["fail_by_condition_among_unmatched"]
    assert "\u2013" not in R.L1_AMBIGUITY_TEXT and "\u2014" not in R.L1_AMBIGUITY_TEXT   # no en or em dash


def test_live_weather_sidecars_join(tmp_path, monkeypatch):
    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setattr(B, "LIVE_DIR", live)
    pd.DataFrame({"det_id": ["ok", "weather_unknown"], "run_id": "live_S1D_20260925T2230", "wind_ms": [4.0, np.nan], "ctt_k": [np.nan, np.nan],
                  "deep_convection": pd.array([False, None], dtype="boolean")}).to_parquet(live / "live_S1D_20260925T2230_weather.parquet")
    paths = B.input_paths("open")
    assert [p.name for p in paths["live_weather"]] == ["live_S1D_20260925T2230_weather.parquet"]
    assert "live_weather" not in B.input_paths("research")
    w, note = B.load_weather("open", {**paths, "weather": [tmp_path / "missing.parquet"]})
    assert len(w) == 2 and "wind known 1" in note
    c = E.join_weather(contacts(), w)
    g = R.l1_gate(c).set_index(c.det_id)
    assert g.loc["ok", "l1"] and g.loc["ok", "weather_known"]                 # calm and clear from the sidecar
    assert g.loc["weather_unknown", "l1"] and g.loc["weather_unknown", "weather_missing"] == "wind and deep convection"


def test_script_draws_figure_only_for_both_builds(monkeypatch):
    """The committed figure has an open and a research panel; a single-build run (make leads) must not redraw it."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("leads_script", REPO / "scripts" / "33_leads.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    counts = {"leads": 0, "by_type": {}, "by_band": {}, "evidence_rows": 0, "L1": {}, "corroboration_window": {}}
    drawn, written = [], []
    monkeypatch.setattr(mod.B, "assemble", lambda b, **kw: {"build": b, "counts": counts})
    monkeypatch.setattr(mod.B, "write_outputs", lambda res, **kw: written.append(res["build"]))
    monkeypatch.setattr(mod.B, "figure", lambda results, **kw: drawn.append(sorted(results)))
    for argv, want_written, want_drawn in ((["--build", "open"], ["open"], []), (["--build", "research"], ["research"], []),
                                           (["--build", "both"], ["open", "research"], [["open", "research"]]),
                                           (["--build", "both", "--dry-run"], [], [])):
        drawn.clear()
        written.clear()
        monkeypatch.setattr(sys, "argv", ["33_leads.py", *argv])
        mod.main()
        assert written == want_written and drawn == want_drawn, argv


# ---------------------------------------------------------------------------------------------------------------------
# round 3 second dispatch (R3-T8): gate waterfall, gate invariant on output rows, queue order, codes in every output

def _contacts_with_ambiguous() -> pd.DataFrame:
    c = contacts()
    c = pd.concat([c, c[c.det_id == "ok"].assign(det_id="ambiguous")], ignore_index=True)
    c["match_ambiguous"] = c.det_id == "ambiguous"
    return c


def _weather_with_ambiguous() -> pd.DataFrame:
    w = weather()
    return pd.concat([w, w[w.det_id == "ok"].assign(det_id="ambiguous")], ignore_index=True)


def test_l1_gate_waterfall_counts():
    """remaining_after_condition applies the conditions one after another; a reviewer can rebuild it from the contacts
    file with plain filters, and its last entry is pass_all."""
    c = E.join_weather(_contacts_with_ambiguous(), _weather_with_ambiguous())
    counts = R.l1_gate_counts(R.l1_gate(c))
    rem = counts["remaining_after_condition"]
    assert list(rem) == R.L1_CONDITIONS[1:]
    vals = [counts["unmatched"], *rem.values()]
    assert all(a >= b for a, b in zip(vals, vals[1:])) and rem["weather"] == counts["pass_all"] == 3
    um = c.ais_status == "unmatched"
    assert counts["unmatched"] == int(um.sum()) and rem["not_ambiguous"] == int((um & ~c.match_ambiguous).sum())
    # without the ambiguity flag (the September run) the waterfall skips that step
    old = R.l1_gate_counts(R.l1_gate(E.join_weather(contacts(), weather())))
    assert "not_ambiguous" not in old["remaining_after_condition"] and list(old["remaining_after_condition"])[-1] == "weather"


def test_build_l1_never_makes_a_lead_of_matched_no_coverage_fixed_or_ambiguous():
    leads, ev, counts = B.build_l1(_contacts_with_ambiguous(), "open", _weather_with_ambiguous(), None, None, None, E.passes_frame(plan()))
    ids = set(leads.det_id)
    assert ids == {"ok", "channel_unknown", "weather_unknown"}
    assert not ids & {"matched", "no_coverage", "fixed_class", "low_class", "ambiguous"}
    assert counts["leads_by_ais_status"] == {"unmatched": 3} and counts["leads_ambiguous"] == 0
    assert set(counts["leads_by_confidence"]) <= set(R.L1_CLASSES) and sum(counts["leads_by_pass"].values()) == 3
    # the invariant check fires if a forbidden row ever reaches the output (here: a gate that lets everything through)
    import unittest.mock as um

    real_gate = R.l1_gate
    allow_all = lambda df: real_gate(df).assign(l1=True)   # noqa: E731
    with um.patch.object(B.R, "l1_gate", allow_all), pytest.raises(AssertionError, match="break the gate"):
        B.build_l1(_contacts_with_ambiguous(), "open", _weather_with_ambiguous(), None, None, None, E.passes_frame(plan()))


def test_queue_order_puts_l1_before_l7():
    """Owner P0: with model v1 every L1 lead with known calm weather sorts before every L7 lead; the summary says so."""
    l1, _, _ = B.build_l1(contacts(), "open", weather(), None, None, None, E.passes_frame(plan()))
    t = pd.Timestamp("2026-09-10T18:00:00Z")
    many = pd.DataFrame([{"light_id": f"L{i:03d}", "lon": 109.12 + i * 0.001, "lat": 7.12, "time_utc": t.isoformat(),
                          "night": f"2026-09-{1 + i % 20:02d}", "quality": "clear", "s1_passes_90d": 0, "satlas_infra_m": np.nan,
                          "radiance_nw": float(i)} for i in range(60)])
    l7, _, _ = B.build_l7(many, None, "open", None, E.passes_frame(None), (None, None, None), (None, None, None))
    assert l7.priority.max() == P.L7_CEILING
    known = l1[l1.weather_known]
    weakest = known.iloc[[0]].assign(lead_id="L1-weakest", priority=P.L1_FLOOR_WEATHER_KNOWN)   # ties sort L1 first
    df = B.finalize([pd.concat([known, weakest], ignore_index=True), l7], "open")
    q = B.queue_order(df)
    assert q["every_l1_before_every_l7"] and q["l1_below_l7_max"] == 0 and q["l1_at_l7_max"] == 1
    top = B.top_of_queue(df)
    assert top["top_100_by_type_band"] == {f"L1_{b}": int(n) for b, n in df[df.lead_type == "L1"].priority_band.value_counts().items()} | {"L7_low": 1}
    # an L1 lead with weather unknown can score under the ceiling; the summary reports it rather than hiding it
    low = known.iloc[[0]].assign(lead_id="L1-low", priority=5)
    q2 = B.queue_order(B.finalize([pd.concat([known, low], ignore_index=True), l7], "open"))
    assert not q2["every_l1_before_every_l7"] and q2["l1_below_l7_max"] == 1


TEXT_TS = REPO / "app" / "frontend" / "src" / "app" / "text.ts"


def _ts_keys(name: str) -> set[str]:
    src = TEXT_TS.read_text(encoding="utf-8")
    m = re.search(rf"export const {name}: Record<string, string> = \{{(.*?)\n\}};", src, re.S)
    assert m, name
    return {a or b for a, b in re.findall(r'^\s+(?:"([^"]+)"|([a-z0-9_]+)):', m.group(1), re.M)}


def test_rows_carry_codes_with_display_strings():
    """Board D5.1: factor, lawful_explanations and change_indicators carry codes, not sentences, in every output row of
    both builds, and every code has a display string in the frontend's text.ts (read here, never edited)."""
    factor_codes, lawful_codes, change_codes = set(), set(), set()
    for build in ("open", "research"):
        c = contacts(2)
        if build == "research":
            c["ais_source"] = "gfw"
        l1, _, _ = B.build_l1(c, build, weather(), None, None, None, E.passes_frame(plan()))
        l7, _, _ = B.build_l7(synthetic_lights(), None, build, None, E.passes_frame(plan()), (None, None, None), (None, None, None))
        df = B.finalize([l1, l7], build)
        for _, r in df.iterrows():
            factor_codes |= {f["factor"] for f in json.loads(r.factors)}
            lawful = json.loads(r.lawful_explanations)
            change = json.loads(r.change_indicators)
            assert lawful == list(R.LAWFUL_EXPLANATIONS[r.lead_type]) and change == list(R.CHANGE_INDICATORS[r.lead_type])
            lawful_codes |= set(lawful)
            change_codes |= set(change)
    assert factor_codes == set(P.FACTORS) | {"weather unknown"}
    for code in lawful_codes | change_codes | set(P.FACTORS):
        assert re.fullmatch(r"[a-z0-9_]+", code), code          # a code, not a sentence
    assert not factor_codes - _ts_keys("FACTOR_LABEL"), factor_codes - _ts_keys("FACTOR_LABEL")
    assert not lawful_codes - _ts_keys("LAWFUL_TEXT"), lawful_codes - _ts_keys("LAWFUL_TEXT")
    assert not change_codes - _ts_keys("CHANGE_TEXT"), change_codes - _ts_keys("CHANGE_TEXT")


def test_research_signature_ignores_live_passes():
    """The research build does not read live passes, so a processed live pass must not move its inputs_signature (and
    with it generated_utc and the bytes of every research file)."""
    assert "live_contacts" in B.input_paths("open") and "live_weather" in B.input_paths("open")
    assert "live_contacts" not in B.input_paths("research") and "live_weather" not in B.input_paths("research")


def test_signature_moves_with_model(monkeypatch):
    """generated_utc follows inputs_signature, so a rule or weight change must move the signature (provenance); the same
    code and inputs give the same signature (byte-identical reruns)."""
    paths = {"x": [B.DATA_DIR / "no_such_input.parquet"]}
    rows, sig = B.inputs_manifest(paths)
    assert B.inputs_manifest(paths)[1] == sig and len(sig) == 16
    monkeypatch.setattr(P, "L1_DENSITY_SATURATION", P.L1_DENSITY_SATURATION + 1)
    assert B.inputs_manifest(paths)[1] != sig
    monkeypatch.undo()
    monkeypatch.setattr(B, "PRIORITY_MODEL_ID", "lead_priority_test")
    assert B.inputs_manifest(paths)[1] != sig
    monkeypatch.undo()
    assert B.inputs_manifest(paths, {"since": "2026-10-01"})[1] != sig
