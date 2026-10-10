"""Synthetic product files for the backend tests: every file of app/CONTRACT.md section 3, a few rows each, in a temp
data dir. Open and research files are both written, so the open-build tests can prove the research files are never read.
"""

from __future__ import annotations

import json
from pathlib import Path

import darkvessel  # noqa: F401  (PROJ_DATA before pyogrio and rasterio)
import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box

from darkvessel.ais.gfw_identity import D1_COLUMNS as D1_RESEARCH, EVIDENCE_COLUMNS
from darkvessel.config import DARK_CAVEAT, DARK_CAVEAT_SHORT
from darkvessel.live.schema import D1_COLUMNS as D1_LIVE, EXTRA_COLUMNS

LIVE_PASS = "live_S1D_20261008T2258"
LIVE_SCENES = ["S1D_IW_GRDH_1SDV_20261008T230043_20261008T230108_004930_0094A6_B8C6",
               "S1D_IW_GRDH_1SDV_20261008T230108_20261008T230133_004930_0094A6_78A4"]
REG_SCENES = ["S1C_IW_GRDH_1SDV_20260920T104816_20260920T104845_009530_012F70_2DA4",
              "S1C_IW_GRDH_1SDV_20260920T104845_20260920T104910_009530_012F70_AAAA"]
IDS = {
    "live_matched": "S1D_20261008T230043_00001", "live_unmatched": "S1D_20261008T230043_00002",
    "live_nocov": "S1D_20261008T230108_00003", "live_fixed": "S1D_20261008T230108_00004",
    "reg": [f"S1C_20260920T104816_{i:05d}" for i in range(1, 5)] + ["S1C_20260920T104845_00005"],
    "struct": ["S1C_20260920T104816_00090", "S1C_20260920T104816_00091"],
    "camau": ["S1D_20260929T1110_0000", "S1D_20260929T1110_0001", "S1D_20260929T1110_0002"],
    "light": ["N21_20260910T183012_000001", "N21_20260910T183012_000002", "SPP_20260911T175500_000003"],
    "site": "site_0001", "vessel_mmsi": "574000001", "vessel_mmsi2": "412000002",
    "gfw_vessel": "aaaaaaaaa-1111-2222-3333-444444444444", "gfw_near": "bbbbbbbbb-1111-2222-3333-444444444444",
    "gfw_stub": "ccccccccc-1111-2222-3333-444444444444", "gap": "gap0001.1", "enc": "enc0001.1", "loit": "loi0001.1",
    "port": "prt0001.1", "lead_l1": "L1-S1D_20261008T230043_00002", "lead_l7": "L7-r62c24",
    "lead_r1": "L1-S1C_20260920T104816_00002", "cell": "r62c24", "plan_past": "S1D_R164_20261008T2300",
    "plan_up": "S1C_R120_20261011T2235", "chip": "S1D_20261008T230043_00001",
}
POS = {"live": (101.85, 12.62), "reg": (108.91, 17.88), "camau": (105.88, 9.25), "cell": (105.1, 8.6)}
DARK_ROW_CAVEAT = DARK_CAVEAT


def _gpkg(df, path: Path, layer: str, crs="EPSG:4326"):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(df, gpd.GeoDataFrame):
        df.set_crs(crs, allow_override=True).to_file(path, layer=layer, driver="GPKG", engine="pyogrio")
    else:
        pyogrio.write_dataframe(pd.DataFrame(df), path, layer=layer, driver="GPKG")


def _pq(df, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(df).to_parquet(path, index=False)


def _pts(rows, lon="lon", lat="lat"):
    df = pd.DataFrame(rows)
    return gpd.GeoDataFrame(df, geometry=[Point(x, y) for x, y in zip(df[lon], df[lat])], crs="EPSG:4326")


def _tif(path: Path, arr, west, north, res, nodata=-9999.0, tags=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(arr, dtype="float32")
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(west, north, res, res), nodata=nodata) as ds:
        ds.write(arr, 1)
        if tags:
            ds.update_tags(**tags)


def live_contacts():
    lon, lat = POS["live"]
    base = {c: None for c in D1_LIVE + EXTRA_COLUMNS}
    rows = []
    for k, status, conf in (("live_matched", "matched", "high"), ("live_unmatched", "unmatched", "medium"),
                            ("live_nocov", "no_coverage", "high"), ("live_fixed", "no_coverage", "fixed")):
        r = dict(base)
        i = len(rows)
        r.update({"det_id": IDS[k], "run_id": LIVE_PASS, "mission": "S1D", "acq_utc": "2026-10-08T23:00:43+00:00",
                  "lon": lon + 0.05 * i, "lat": lat + 0.05 * i, "length_est_m": 40.0 + i, "confidence": conf,
                  "cnn_score": 0.9 - 0.1 * i, "cnn_vessel": i < 2, "ais_status": status, "ais_source": "aisstream",
                  "nearest_ais_mmsi": 574000001, "nearest_ais_dist_m": 900.0 + i, "nearest_ais_dt_s": 30.0,
                  "n_ais_10km": 2, "ais_reach": 0.5, "research_only": False, "caveat": DARK_ROW_CAVEAT, "dark_lead": status == "unmatched",
                  "scene_id": LIVE_SCENES[0 if i < 2 else 1], "pass_dir": "DESCENDING", "orbit_rel": 164,
                  "inc_angle_deg": 35.0, "pol_class": "VV+VH", "n_pixels": 5, "scr_vv_db": 8.0, "scr_vh_db": 7.0,
                  "low_reason": "", "persist_dates": 0, "persist_dates_checked": 2, "n_low_1km": 0, "near_fixed_m": 5000.0,
                  "ais_footprint_positions": 12, "pred_method": None, "cnn_chip_valid_frac": 1.0, "ais_recorded_hours": 10,
                  "row": 13672.3 + i, "col": 15189.4 + i})
        if status == "matched":
            r.update({"match_method": "track_interp_hungarian", "match_dist_m": 120.0, "match_dt_s": 15.0, "match_quality": "high",
                      "mmsi": 574000001, "imo": 9123456, "vessel_name": "TEST VESSEL", "call_sign": "XVAB", "flag": "Viet Nam (Socialist Republic of)",
                      "ship_type": "cargo", "length_ais_m": 52.0, "identity_source": "aisstream static message",
                      "match_gate_m": 1500.0, "ais_sog_kn": 8.0, "length_ratio": 0.8, "ais_class": "A", "mmsi_mid": 574})
        rows.append(r)
    df = pd.DataFrame(rows)[D1_LIVE + EXTRA_COLUMNS + ["pred_method", "cnn_chip_valid_frac", "ais_recorded_hours", "row", "col"]]
    df = df.loc[:, ~df.columns.duplicated()]
    for c in ("mmsi", "imo"):
        df[c] = pd.to_numeric(df[c]).astype(float)
    return _pts(df)


def scenes_gdf(ids, run_ids, mission, starts, extra=None):
    rows = []
    for i, (pid, rid, st) in enumerate(zip(ids, run_ids, starts)):
        r = {"product_id": pid, "run_id": rid, "mission": mission, "start_utc": st, "stop_utc": st, "orbit_rel": 164,
             "pass_dir": "DESCENDING", "aoi_overlap_km2": 1000.0, "tested_km2": 900.0}
        r.update((extra or {}))
        r["_g"] = box(100 + i, 11, 103 + i, 14)
        rows.append(r)
    df = pd.DataFrame(rows)
    return gpd.GeoDataFrame(df.drop(columns="_g"), geometry=df["_g"], crs="EPSG:4326")


def write_all(d: Path) -> Path:
    d = Path(d)
    # ---------------------------------------------------------------- contacts
    live = live_contacts()
    p = d / "live" / "live_contacts.gpkg"
    _gpkg(live, p, "contacts_4326")
    sc = scenes_gdf(LIVE_SCENES, [LIVE_PASS] * 2, "S1D", ["2026-10-08T23:00:43Z", "2026-10-08T23:01:08Z"],
                    {"ais_aoi_positions": 8812, "ais_aoi_mmsi": 995, "ais_footprint_positions": 0, "ais_footprint_mmsi": 0,
                     "ais_near_footprint_mmsi": 0, "n_contacts": 2, "caveat": DARK_ROW_CAVEAT})
    sc.loc[1, "ais_aoi_positions"] = 9074
    _gpkg(sc, p, "scenes_4326")
    _gpkg(pd.DataFrame([{"product": "live", "cnn_model_id": "verifier_v0_356af0ca", "cnn_threshold": "0.631783",
                         "dark_lead_rule": "unmatched high or medium", "no_coverage_rule": "nothing heard within 20 km",
                         "caveat": DARK_ROW_CAVEAT}]), p, "about")
    (d / "live" / "live_summary.json").write_text(json.dumps({"generated_utc": "2026-10-09T07:37:06Z", "passes": {LIVE_PASS: {"scenes": 2}}}))

    lon, lat = POS["reg"]
    reg = _pts([{"det_id": i, "scene_idx": 0 if k < 4 else 1, "mission": "S1C", "acq_utc": "2026-09-20T10:48:16+00:00" if k < 4 else "2026-09-20T10:48:45+00:00",
                 "confidence": "high" if k % 2 == 0 else "medium", "lat": lat + 0.01 * k, "lon": lon + 0.01 * k,
                 "length_est_m": 20.0 + 10 * k, "scr_vv_db": 9.0, "scr_vh_db": 9.9, "inc_angle_deg": 31.2, "persist_dates": 0,
                 "persist_dates_checked": 0, "ais_status": "not_checked", "caveat": DARK_CAVEAT_SHORT} for k, i in enumerate(IDS["reg"])])
    p = d / "detections_regional.gpkg"
    _gpkg(reg, p, "detections_regional_4326")
    rs = scenes_gdf(REG_SCENES, ["regional_2026-09"] * 2, "S1C", ["2026-09-20T10:48:16Z", "2026-09-20T10:48:45Z"])
    rs = rs.drop(columns=["run_id", "stop_utc", "aoi_overlap_km2"])
    rs.insert(0, "scene_idx", [0, 1])
    _gpkg(rs, p, "scenes_processed_4326")
    st = _pts([{"det_id": i, "scene_idx": 0, "mission": "S1C", "acq_utc": "2026-09-20T10:48:16+00:00", "confidence": "fixed",
                "lat": lat - 0.1, "lon": lon - 0.1 - 0.01 * k, "length_est_m": 60.0, "scr_vv_db": 20.0, "scr_vh_db": 18.0,
                "inc_angle_deg": 31.0, "persist_dates": 3, "persist_dates_checked": 3, "ais_status": "not_checked",
                "caveat": DARK_CAVEAT_SHORT} for k, i in enumerate(IDS["struct"])])
    _gpkg(st, d / "structures_regional.gpkg", "structures_regional_4326")
    lon, lat = POS["camau"]
    cm = _pts([{"det_id": i, "row": 13803.1 + k, "col": 15233.5, "detected_vv": True, "detected_vh": k > 0, "n_pixels": 6,
                "length_est_m": 30.0, "width_est_m": 20.0, "scr_vv_db": 11.0, "scr_vh_db": 5.0, "pol_class": "VV only",
                "lon": lon + 0.01 * k, "lat": lat, "inc_angle_deg": 40.6, "scene_id": "S1D_IW_GRDH_1SDV_20260929T111023_20260929T111053_004792_008FC8_A5CA",
                "acq_utc": "2026-09-29T11:10:23+00:00", "ais_status": "not_checked: no AIS source connected",
                "confidence": ["low", "medium", "high"][k], "low_reason": "weak_vv_only" if k == 0 else "", "persist_dates": 2,
                "persist_dates_checked": 2, "caveat": DARK_CAVEAT_SHORT, "cnn_score": 0.4 + 0.2 * k, "cnn_vessel": k == 2,
                "cnn_threshold": 0.631783, "cnn_model_id": "verifier_v0_356af0ca", "cnn_chip_valid_frac": 1.0,
                "cnn_training_data": "AI2 labels"} for k, i in enumerate(IDS["camau"])])
    _gpkg(cm, d / "detections_ml.gpkg", "detections_verified_4326")
    _gpkg(gpd.GeoDataFrame({"scene_id": ["x"], "caveat": [DARK_CAVEAT_SHORT]}, geometry=[box(104, 8, 106, 10)], crs="EPSG:4326"),
          d / "detections_baseline.gpkg", "processing_window_4326")
    cnn_ids = IDS["reg"] + IDS["struct"]
    _pq({"det_id": cnn_ids, "scene_id": [REG_SCENES[0]] * len(cnn_ids), "mission": "S1C", "confidence": "high",
         "cnn_score": np.linspace(0.3, 0.95, len(cnn_ids)), "cnn_vessel": np.linspace(0.3, 0.95, len(cnn_ids)) >= 0.631783,
         "cnn_threshold": 0.631783, "cnn_model_id": "verifier_v0_356af0ca", "cnn_score_source": "regional",
         "cnn_chip_valid_frac": 1.0, "chip_valid_frac_full": 1.0, "bg_vv_db": -20.0, "bg_vh_db": -27.0, "caveat": DARK_CAVEAT},
        d / "ml" / "regional_cnn.parquet")
    all_ids = cnn_ids + [live["det_id"].iloc[i] for i in range(4)] + IDS["camau"]
    _pq({"det_id": all_ids, "wind_ms": 5.0, "ctt_k": 280.0, "himawari_start": "2026-09-20T10:40:00Z", "deep_convection": False},
        d / "weather_context.parquet")
    _gpkg(_pts([{"det_id": IDS["reg"][0], "group": "a", "confidence": "high", "acq_utc": "2026-09-20T10:48:16Z", "lat": 17.88,
                 "lon": 108.91, "length_est_m": 20.0, "optical_object": True, "optical_kind": "vessel", "s2_item": "S2A_x",
                 "s2_datetime": "2026-09-20T03:00:00Z", "satlas_m": 5000.0, "caveat": DARK_CAVEAT_SHORT}]), d / "optical_check.gpkg", "optical_check_4326")
    chips = d / "cache" / "chips"
    chips.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    Image.new("L", (130, 64), 128).save(chips / f"{IDS['chip']}.webp", "WEBP")

    # ---------------------------------------------------------------- vessels and AIS
    p = d / "ais_live.gpkg"
    _gpkg(_pts([{"mmsi": int(IDS["vessel_mmsi"]), "mid": 574, "name": "TEST VESSEL", "callsign": "XVAB", "imo": "9123456", "ais_class": "A",
                 "ship_type": 70, "ship_type_label": "cargo", "length_m": 52.0, "width_m": 9.0, "destination": "VUNG TAU", "eta": "10-09 06:00",
                 "last_seen_utc": "2026-10-08T23:10:00Z", "first_seen_utc": "2026-10-08T14:10:00Z", "n_positions": 3, "sog_kn": 8.0,
                 "cog_deg": 90.0, "heading": 91.0, "nav_status": 0, "nav_status_label": "under way using engine", "msg_type": "PositionReport",
                 "in_aoi": True, "ever_in_aoi": True, "static_seen_utc": "2026-10-08T14:20:00Z", "gear_beacon_like": False,
                 "lon": 101.86, "lat": 12.63, "ais_note": "aisstream relay; terms UNVERIFIED"},
                {"mmsi": int(IDS["vessel_mmsi2"]), "mid": 412, "name": "SECOND", "callsign": "BXYZ", "imo": None, "ais_class": "B",
                 "ship_type": 30, "ship_type_label": "fishing", "length_m": 14.0, "width_m": 4.0, "destination": None, "eta": None,
                 "last_seen_utc": "2026-10-08T15:00:00Z", "first_seen_utc": "2026-10-08T14:30:00Z", "n_positions": 1, "sog_kn": 2.0,
                 "cog_deg": 10.0, "heading": None, "nav_status": 7, "nav_status_label": "engaged in fishing", "msg_type": "StandardClassBPositionReport",
                 "in_aoi": False, "ever_in_aoi": True, "static_seen_utc": None, "gear_beacon_like": False, "lon": 114.0, "lat": 22.0,
                 "ais_note": "aisstream relay; terms UNVERIFIED"}]), p, "vessels_latest_4326")
    plan = gpd.GeoDataFrame({"pass_group": [IDS["plan_past"], IDS["plan_past"], IDS["plan_up"]],
                             "product_ids": [None, ";".join(LIVE_SCENES), None], "status": ["past", "past", "upcoming"],
                             "source": ["esa_plan", "repeat_cycle", "esa_plan"]},
                            geometry=[box(100, 11, 103, 14), box(100.5, 11, 103.5, 14), box(110, 10, 113, 13)], crs="EPSG:4326")
    _gpkg(plan, p, "s1_next_passes_4326")
    (d / "ais_live_summary.json").write_text(json.dumps({"positions": 3, "mmsi_count": 2, "period_start_utc": "2026-10-08T14:10:00+00:00",
                                                         "period_end_utc": "2026-10-08T23:10:00+00:00", "hours_recorded": 2,
                                                         "recording_gaps_over_10_min": [{"from_utc": "2026-10-08T15:19:52Z", "to_utc": "2026-10-08T22:38:51Z", "minutes": 439.0}]}))
    pos = d / "cache" / "ais" / "aisstream" / "positions" / "20261008"
    for hour, ts in (("14", ["2026-10-08T14:10:00Z", "2026-10-08T14:50:00Z"]), ("23", ["2026-10-08T23:10:00Z"])):
        _pq({"mmsi": [int(IDS["vessel_mmsi"])] * len(ts), "timestamp": pd.to_datetime(ts, utc=True), "lon": [101.5, 101.6, 101.86][: len(ts)],
             "lat": [12.5, 12.55, 12.63][: len(ts)], "sog_kn": 8.0, "cog_deg": 90.0, "heading": 90.0, "nav_status": 0,
             "msg_type": "PositionReport", "msg_id": 1, "ais_class": "A", "ship_name": None}, pos / f"{hour}.parquet")

    # ---------------------------------------------------------------- lights
    p = d / "viirs_lights.gpkg"
    lights = _pts([{"light_id": IDS["light"][k], "satellite": ["NOAA-21", "NOAA-21", "S-NPP"][k],
                    "time_utc": ["2026-09-10T18:30:12Z", "2026-09-10T18:30:12Z", "2026-09-11T17:55:00Z"][k],
                    "night": ["2026-09-10", "2026-09-10", "2026-09-11"][k], "lat": 8.6 + 0.01 * k, "lon": 105.1 + 0.01 * k,
                    "radiance_nw": 50.0 + k, "spike_nw": 10.0, "isolation": 3.0, "quality": "clear" if k < 2 else "under_cloud",
                    "class": "lit_vessel_candidate", "nights_seen_500m": 1, "clear_nights_cell": 5, "moon_illum_pct": 20.0,
                    "satlas_infra_m": 9000.0, "s1_passes_90d": 0, "caveat": DARK_CAVEAT_SHORT} for k in range(3)])
    _gpkg(lights, p, "viirs_lights_4326")
    _gpkg(_pts([{"site_id": IDS["site"], "lat": 8.6, "lon": 105.1001, "n_lights": 4, "nights": 3, "radiance_med_nw": 40.0,
                 "radiance_max_nw": 90.0, "satlas_infra_m": 600.0, "nights_seen_max": 3, "s1_passes_90d": 0,
                 "likely": "platform or turbine (Satlas point within 1 km)", "caveat": DARK_CAVEAT_SHORT}]), p, "viirs_sites_4326")
    _gpkg(pd.DataFrame([{"night": "2026-09-10", "moon_illum_pct_median": 20.0, "lit_candidates_clear": 2, "lit_candidates": 2, "in_lean_file": True},
                        {"night": "2026-09-11", "moon_illum_pct_median": 30.0, "lit_candidates_clear": 0, "lit_candidates": 1, "in_lean_file": True}]),
          p, "viirs_nights")

    # ---------------------------------------------------------------- leads (open)
    caveat = "fixture caveat"
    l1 = {"lead_id": IDS["lead_l1"], "lead_type": "L1", "title": "Unmatched radar contact in AIS reach, 41 m, Gulf of Thailand",
          "state": "new", "reason": None, "priority": 55, "priority_band": "medium", "pts_evidence_quality": 25, "pts_corroboration": 0,
          "pts_ais_reach": 20, "pts_persistence": 10, "pts_area_weight": 0,
          "factors": json.dumps([{"factor": "evidence_quality", "value": "cnn 0.8", "points": 25, "max_points": 30, "source": "cnn_v0"}]),
          "priority_model_id": "lead_priority_v0_20261009", "calibrated": False, "primary_type": "contact", "primary_id": IDS["live_unmatched"],
          "evidence": json.dumps([{"type": "contact", "id": IDS["live_unmatched"], "role": "primary"},
                                  {"type": "vessel", "id": "mmsi:" + IDS["vessel_mmsi"], "role": "nearest_ais"},
                                  {"type": "pass", "id": LIVE_PASS, "role": "pass"}, {"type": "cell", "id": "r45c11", "role": "cell"}]),
          "n_evidence": 4, "lon": 101.9, "lat": 12.67, "time_utc": "2026-10-08T23:00:43Z", "region_box": "Gulf of Thailand",
          "next_look_utc": "2026-10-20T23:00:00Z", "next_look_pass": "S1D_R164_20261020T2300", "next_look_source": "repeat_cycle",
          "lawful_explanations": json.dumps(["no_carriage_requirement", "vms_fleet"]), "change_indicators": json.dumps(["late_ais_match"]),
          "history": "[]", "research_only": False, "caveat": caveat, "src": "app", "prov": json.dumps({"cnn_score": "cnn_v0"}),
          "det_id": IDS["live_unmatched"], "run_id": LIVE_PASS, "pass_id": LIVE_PASS, "mission": "S1D", "acq_utc": "2026-10-08T23:00:43Z",
          "confidence": "medium", "cnn_score": 0.8, "length_est_m": 41.0, "ais_status": "unmatched", "ais_source": "aisstream",
          "cell_id": "r45c11", "nights": None}
    l7 = {**{k: None for k in l1}, "lead_id": IDS["lead_l7"], "lead_type": "L7", "title": "Lit activity where radar does not look, South Vietnam shelf",
          "state": "new", "priority": 70, "priority_band": "high", "pts_evidence_quality": 30, "pts_corroboration": 25,
          "pts_ais_reach": 0, "pts_persistence": 15, "pts_area_weight": 0, "factors": "[]", "priority_model_id": "lead_priority_v0_20261009",
          "calibrated": False, "primary_type": "cell", "primary_id": IDS["cell"],
          "evidence": json.dumps([{"type": "cell", "id": IDS["cell"], "role": "primary"}, {"type": "light", "id": IDS["light"][0], "role": "light"},
                                  {"type": "site", "id": IDS["site"], "role": "recurring_site"}]),
          "n_evidence": 3, "lon": 105.1, "lat": 8.6, "time_utc": "2026-09-11T17:55:00Z", "region_box": "South Vietnam shelf",
          "lawful_explanations": json.dumps(["lawful_fishing_lights"]), "change_indicators": json.dumps(["radar_acquisition"]),
          "history": "[]", "research_only": False, "caveat": caveat, "src": "app", "prov": "{}", "cell_id": IDS["cell"],
          "nights": json.dumps(["2026-09-10", "2026-09-11"])}
    leads = _pts([l1, l7])
    p = d / "leads_open.gpkg"
    _gpkg(leads, p, "leads_4326")
    _gpkg(pd.DataFrame([{"product": "leads", "build": "open", "priority_model_id": "lead_priority_v0_20261009", "caveat": caveat}]), p, "about")

    # ---------------------------------------------------------------- passes
    (d / "s1_next_passes.json").write_text(json.dumps({
        "generated_utc": "2026-10-09T13:34:20Z", "passes": [{}, {}],
        "pass_groups": [
            {"pass_group": IDS["plan_past"], "start_utc": "2026-10-08T23:00:40Z", "stop_utc": "2026-10-08T23:03:00Z", "mission": "S1D",
             "relative_orbit": 164, "pass_dir": "DESCENDING", "sources": ["esa_plan", "repeat_cycle"], "status": "past",
             "aoi_overlap_km2": 50000.0, "aoi_parts": ["Gulf of Thailand"], "aoi_overlap_bbox": [100, 11, 103, 14], "ais_heard_share": 0.0, "rows": 2},
            {"pass_group": IDS["plan_up"], "start_utc": "2026-10-11T22:35:08Z", "stop_utc": "2026-10-11T22:38:00Z", "mission": "S1C",
             "relative_orbit": 120, "pass_dir": "DESCENDING", "sources": ["esa_plan"], "status": "upcoming", "aoi_overlap_km2": 80000.0,
             "aoi_parts": ["South China Sea"], "ais_heard_share": 0.01, "rows": 1}]}))

    # ---------------------------------------------------------------- cells
    cells = []
    for (r, c) in ((62, 24), (45, 11), (24, 39)):
        lon_c, lat_c = 99.0 + (c + 0.5) * 0.25, 24.0 - (r + 0.5) * 0.25
        cells.append({"row": r, "col": c, "lon": lon_c, "lat": lat_c, "region": "South Vietnam shelf" if r == 62 else "other",
                      "aoi_centre": True, "aoi_share": 1.0, "n_sea": 625, "sea_share": 1.0, "sea_area_km2": 760.0,
                      "depth_mean_m": 40.0, "depth_median_m": 40.0, "depth_min_m": 30.0, "depth_max_m": 50.0, "depth_std_m": 5.0,
                      "share_shallower_50m": 0.9, "share_shallower_200m": 1.0, "share_shelf_break_150_250m": 0.0,
                      "slope_mean_m_per_km": 0.2, "dist_coast_km": 60.0, "dist_coast_min_km": 50.0, "dist_port_km": 120.0,
                      "dist_port_min_km": 100.0, "ship_presence_share_all": 0.8, "ship_presence_share_fishing": 0.5,
                      "ship_presence_share_commercial": 0.4, "ship_presence_share_oilgas": 0.0, "ship_presence_share_passenger": 0.0,
                      "ship_presence_share_leisure": 0.0, "marineregions_overlap_share": 0.0, "marineregions_mrgid": 8484,
                      "marineregions_geoname": "Fixture EEZ", "marineregions_pol_type": "200NM", "marineregions_share": 1.0,
                      "marineregions_n": 1})
    _pq(cells, d / "ocean_static_cells.parquet")
    _pq([{"night": n, "row": 62, "col": 24, "lon": 105.125, "lat": 8.375, "region": "South Vietnam shelf", "is_viirs_night": True,
          "is_s1_date": False, "sst_mean_c": 29.0 + k, "sst_sd_c": 0.1, "sst_grad_mean": 0.01, "front_share": 0.0, "dist_front_km": 30.0,
          "chl_log10_mean": -0.5, "chl_valid_share": 0.9, "ssh_m": 0.5, "ssh_anom_m": 0.0, "ssh_grad": 0.0, "current_speed_ms": 0.2,
          "mld_m": 20.0, "sbl_m": 10.0, "wave_hs_m": 1.0, "wind_ms": 5.0, "moon_illum_pct": 20.0, "sst_source": "mur", "sst_date": n,
          "chl_dataset": "x", "chl_date": n, "chl_obs_dataset": "x", "rtofs_valid_utc": n + "T00:00:00Z", "wave_valid_utc": n + "T18:00:00Z",
          "wind_valid_utc": n + "T18:00:00Z", "caveat": "ocean"} for k, n in enumerate(["2026-09-10", "2026-09-11"])],
        d / "ocean_daily_cells.parquet")
    _pq([{"scene_id": REG_SCENES[0], "mission": "S1C", "acq_utc": "2026-09-20T10:48:16Z", "utc_date": "2026-09-20", "row": 62, "col": 24,
          "lon": 105.125, "lat": 8.375, "region": "South Vietnam shelf", "wave_hs_m": 1.2, "wave_valid_utc": "x", "wind_ms": 6.0,
          "wind_valid_utc": "x", "sst_mean_c": 29.5, "sst_sd_c": 0.1, "sst_grad_mean": 0.01, "front_share": 0.0, "dist_front_km": 20.0,
          "chl_log10_mean": -0.4, "chl_valid_share": 1.0, "ssh_m": 0.5, "ssh_anom_m": 0.0, "ssh_grad": 0.0, "current_speed_ms": 0.3,
          "mld_m": 22.0, "sst_source": "mur", "chl_dataset": "x", "rtofs_valid_utc": "x", "caveat": "ocean"}], d / "ocean_radar_pass_cells.parquet")
    (d / "ocean_static_summary.json").write_text(json.dumps({"eez_statement": "as published"}))

    # ---------------------------------------------------------------- geo and rasters
    aoi = gpd.GeoDataFrame({"aoi_id": ["south_china_sea"], "label": ["AOI"]}, geometry=[box(99.2, -3.2, 122.2, 23.7)], crs="EPSG:4326")
    _gpkg(aoi, d / "aoi.gpkg", "aoi_4326")
    p = d / "eez_marineregions.gpkg"
    _gpkg(gpd.GeoDataFrame({"mrgid": [8484], "geoname": ["Fixture EEZ"], "pol_type": ["200NM"]}, geometry=[box(104, 7, 110, 12)], crs="EPSG:4326"), p, "eez_4326")
    _gpkg(gpd.GeoDataFrame({"line_id": [1], "line_type": ["Unsettled (maritime)"]}, geometry=[LineString([(104, 7), (110, 12)])], crs="EPSG:4326"), p, "eez_boundaries_4326")
    p = d / "ocean_context.gpkg"
    _gpkg(gpd.GeoDataFrame({"depth_m": [200], "length_km": [100.0], "source": ["GEBCO"], "licence": ["PD"]}, geometry=[LineString([(105, 8), (106, 9)])], crs="EPSG:4326"), p, "depth_contours_4326")
    _gpkg(_pts([{"name": "Port A", "country": "VN", "lon": 106.7, "lat": 10.3, "major": True}]), p, "ports_4326")
    _gpkg(gpd.GeoDataFrame({"date": ["2026-09-29"], "grad_c_per_km": [0.05], "length_km": [10.0], "caveat": ["x"]}, geometry=[LineString([(110, 10), (110.5, 10.5)])], crs="EPSG:4326"), d / "ocean_fronts.gpkg", "fronts_4326")
    _gpkg(gpd.GeoDataFrame({"product_id": REG_SCENES, "start_utc": ["2026-09-20T10:48:16Z", "2026-09-20T10:48:45Z"]}, geometry=[box(108, 17, 110, 19), box(108, 15, 110, 17)], crs="EPSG:4326"), d / "s1_footprints.gpkg", "s1_footprints_4326")
    land = d / "raw" / "natural_earth" / "ne_10m_land.geojson"
    land.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame({"featurecla": ["Land"]}, geometry=[box(102, 10, 107, 22)], crs="EPSG:4326").to_file(land, driver="GeoJSON")
    small = d / "outputs" / "small"
    grid = np.full((109, 94), -9999.0)
    grid[62, 24] = 0.25
    _tif(small / "ais_reach_share_4326.tif", grid, 99.0, 24.0, 0.25, tags={"units": "share of recorded hours", "period": "2026-10-08"})
    look = np.full((541, 463), 255.0)
    look[310:316, 117:123] = 40.0
    _tif(small / "s1_look_prob_7d_4326.tif", look, 99.15, 23.8, 0.05, nodata=255.0, tags={"units": "percent"})
    ship = np.zeros((50, 50))
    ship[10:20, 10:20] = 1e6
    _tif(small / "ship_density_all_4326.tif", ship, 104.0, 10.0, 0.01, tags={"units": "values as published", "use": "presence only: value > 0"})
    _tif(small / "depth_m_4326.tif", np.full((50, 50), 40.0), 104.0, 10.0, 0.01, tags={"units": "m", "licence": "GEBCO public domain"})

    # ---------------------------------------------------------------- research build
    r = d / "research"
    lon, lat = POS["reg"]
    rows = []
    for k, det in enumerate(IDS["reg"]):
        row = {c: None for c in D1_RESEARCH + EVIDENCE_COLUMNS}
        status = ["matched", "unmatched", "no_coverage", "unmatched", "matched"][k]
        row.update({"det_id": det, "run_id": "regional_2026-09", "mission": "S1C",
                    "acq_utc": "2026-09-20T10:48:16+00:00" if k < 4 else "2026-09-20T10:48:45+00:00", "lon": lon + 0.01 * k,
                    "lat": lat + 0.01 * k, "length_est_m": 20.0 + 10 * k, "confidence": "high" if k % 2 == 0 else "medium",
                    "cnn_score": None if k == 3 else 0.7, "cnn_vessel": None if k == 3 else True, "ais_status": status,
                    "ais_source": "gfw", "research_only": True, "caveat": DARK_CAVEAT, "pass_id": "S1C_20260920T1048",
                    "nearest_ais_vessel_id": IDS["gfw_near"] if k != 3 else IDS["gfw_stub"], "nearest_ais_mmsi": "412000009",
                    "nearest_ais_name": "NEAR ONE", "nearest_ais_dist_m": 2000.0, "n_ais_10km": 3, "ais_reach": 0.7,
                    "identity_kind": "vessel" if status == "matched" else None, "gfw_sar_pair": "matched" if status == "matched" else "unmatched",
                    "n_gfw_encounters_10km_24h": 1})
        if status == "matched":
            row.update({"match_method": "gfw_sar_cell_hour", "match_dist_m": 300.0, "match_dt_s": 600.0, "match_quality": "high",
                        "mmsi": IDS["vessel_mmsi"], "vessel_name": "TEST VESSEL", "call_sign": "XVAB", "flag": "VNM", "ship_type": "CARGO",
                        "identity_source": "GFW", "gfw_vessel_id": IDS["gfw_vessel"]})
        rows.append(row)
    ri = pd.DataFrame(rows)[D1_RESEARCH + EVIDENCE_COLUMNS]
    ri["research_only"] = ri["research_only"].astype(bool)
    _pq(ri, r / "regional_identity.parquet")
    (r / "regional_identity_summary.json").write_text(json.dumps({"cnn_source": "data/ml/regional_cnn.parquet"}))
    gv = {"vessel_id": [IDS["gfw_vessel"], IDS["gfw_near"]], "mmsi": [IDS["vessel_mmsi"], "412000009"], "vessel_name": ["TEST VESSEL", "NEAR ONE"],
          "call_sign": ["XVAB", None], "imo": ["9123456", None], "flag": ["VNM", "CHN"], "ship_type": ["CARGO", "FISHING"],
          "gfw_geartype": ["CARGO", "FISHING"], "length_ais_m": [52.0, None], "identity_source": ["GFW", "GFW"], "geartype": ["CARGO", "TRAWLERS"],
          "shiptype": ["CARGO", "FISHING"], "length_m": [50.0, None], "tonnage_gt": [500.0, None], "registry_sources": ["IMO", None],
          "registry_records": [1.0, 0.0], "ais_messages": [100.0, 50.0], "ais_positions": [80.0, 40.0], "transmission_from": ["2025-01-01T00:00:00Z"] * 2,
          "transmission_to": ["2026-10-06T00:00:00Z"] * 2, "dataset_version": ["v4.0"] * 2, "identity_kind": ["vessel", "vessel"],
          "use": ["research build only"] * 2, "licence": ["https://creativecommons.org/licenses/by-nc/4.0/"] * 2}
    _pq(gv, r / "gfw_vessels.parquet")
    _pq({"vessel_id": ["ddddddddd-1111-2222-3333-444444444444"], "ssvid": ["413000001"], "shipname": ["EVENT SHIP"], "flag": ["CHN"],
         "callsign": [None], "imo": [None], "geartype": ["CARGO"], "shiptype": ["CARGO"], "length_m": [None], "tonnage_gt": [None],
         "registry_sources": [None], "registry_records": [0.0], "ais_messages": [10.0], "ais_positions": [5.0],
         "transmission_from": ["2025-01-01T00:00:00Z"], "transmission_to": ["2026-10-01T00:00:00Z"], "dataset_version": ["v4.0"],
         "use": ["research"], "licence": ["cc"]}, r / "gfw_events_vessels.parquet")
    _pq({"pass_id": ["S1C_20260920T1048"] * 2, "lon": [108.9, 108.95], "lat": [17.9, 17.95], "hour_ts": pd.to_datetime(["2026-09-20T10:00Z", "2026-09-20T11:00Z"], utc=True),
         "hours": [1.0, 1.0], "vessel_id": [IDS["gfw_vessel"]] * 2, "mmsi": [IDS["vessel_mmsi"]] * 2, "ship_name": ["TEST VESSEL"] * 2,
         "call_sign": [None] * 2, "imo": [None] * 2, "flag": ["VNM"] * 2, "gfw_vessel_type": ["CARGO"] * 2, "gfw_geartype": ["CARGO"] * 2,
         "use": ["r"] * 2, "licence": ["cc"] * 2, "caveat": [DARK_CAVEAT] * 2}, r / "gfw_presence_passes.parquet")
    common = {"vessel_id": IDS["gfw_vessel"], "vessel_name": "TEST VESSEL", "ssvid": IDS["vessel_mmsi"], "flag": "VNM",
              "vessel_type": "carrier", "start_dist_shore_km": 100.0, "end_dist_shore_km": 110.0, "start_dist_port_km": 200.0,
              "eez": "8484", "use": "research", "overlaps_window_only": False, "starts_in_window": True}
    _pq([{**common, "event_id": IDS["gap"], "type": "gap", "start": "2026-09-19T00:00:00Z", "end": "2026-09-20T12:00:00Z", "lat": 17.0, "lon": 109.0,
          "gap_intentional_disabling": False, "gap_duration_h": 36.0, "gap_distance_km": 50.0, "gap_implied_speed_kn": 1.0,
          "gap_positions_12h_before_sat": 5, "gap_positions_per_day_sat_reception": 10.0, "off_lat": 17.0, "off_lon": 109.0,
          "on_lat": 17.3, "on_lon": 109.3, "duration_h": 36.0}], r / "gfw_events_gaps.parquet")
    _pq([{**common, "event_id": IDS["enc"], "type": "encounter", "start": "2026-09-20T09:00:00Z", "end": "2026-09-20T11:00:00Z", "lat": 17.9, "lon": 108.92,
          "encounter_type": "carrier-fishing", "encounter_vessel_id": IDS["gfw_near"], "encounter_vessel_name": "NEAR ONE",
          "encounter_ssvid": "412000009", "encounter_flag": "CHN", "encounter_vessel_type": "fishing",
          "encounter_median_distance_km": 0.1, "encounter_median_speed_kn": 1.0, "duration_h": 2.0}], r / "gfw_events_encounters.parquet")
    _pq([{**common, "event_id": IDS["loit"], "type": "loitering", "start": "2026-09-21T00:00:00Z", "end": "2026-09-21T05:00:00Z", "lat": 18.0, "lon": 109.0,
          "loitering_total_time_h": 5.0, "loitering_total_distance_km": 3.0, "loitering_avg_speed_kn": 0.5,
          "loitering_avg_dist_shore_km": 90.0, "duration_h": 5.0}], r / "gfw_events_loitering_part1of1.parquet")
    _pq([{**common, "event_id": IDS["port"], "type": "port_visit", "start": "2026-09-22T00:00:00Z", "end": "2026-09-23T00:00:00Z", "lat": 20.0, "lon": 110.0,
          "port_visit_id": "pv1", "port_visit_confidence": 4, "port_visit_duration_h": 24.0, "port_name": "Haikou", "port_flag": "CHN",
          "port_id": "chn-haikou", "duration_h": 24.0}], r / "gfw_events_port_visits_part1of1.parquet")
    rl1 = {**l1, "lead_id": IDS["lead_r1"], "primary_id": IDS["reg"][1], "det_id": IDS["reg"][1], "run_id": "regional_2026-09",
           "pass_id": "S1C_20260920T1048", "research_only": True, "ais_source": "gfw",
           "evidence": json.dumps([{"type": "contact", "id": IDS["reg"][1], "role": "primary"},
                                   {"type": "vessel", "id": "gfw:" + IDS["gfw_near"], "role": "nearest_ais"},
                                   {"type": "event", "id": IDS["enc"], "role": "ais_behaviour"}]),
           "prov": json.dumps({"ais_reach": "gfw_4wings"}), "n_gfw_encounters_10km_24h": 1}
    rl7 = {**l7, "research_only": True, "n_gfw_encounters_10km_24h": None}
    _pq(pd.DataFrame([rl1, rl7]), r / "leads_research.parquet")
    _pq({"date": ["2026-09-20"], "hour": [10], "cx": [0], "cy": [0], "n_ours": [3], "n_gfw": [2], "n_gfw_matched": [1], "n_gfw_unmatched": [1],
         "n_pair": [1], "lon": [108.92], "lat": [17.9], "res_deg": [0.1], "use": ["r"], "licence": ["cc"]}, r / "radar_vs_gfw.parquet")
    _tif(r / "gfw_ais_presence_hours_4326.tif", np.ones((10, 10)), 108.0, 19.0, 0.1, tags={"units": "hours", "licence": "CC BY-NC 4.0"})
    return d
