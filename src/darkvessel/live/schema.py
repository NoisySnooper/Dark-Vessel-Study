"""Column contract of the live-pass products (docs/PROJECT_BOARD.md decision D1) and the file layout."""

from __future__ import annotations

import pandas as pd

from darkvessel.config import DATA_DIR

LIVE_DIR = DATA_DIR / "live"
LIVE_CACHE = DATA_DIR / "cache" / "live"
SCENE_CACHE = LIVE_CACHE / "scenes"       # <product_id>.json (status), .parquet (contacts), .ais_only.parquet, .objects.parquet, .tested.wkb
PRODUCT_CACHE = LIVE_CACHE / "products"   # <product_id>.json: productInfo footprint and the AOI test
PASSES_PATH = LIVE_CACHE / "passes.json"  # orbit_abs -> run_id, fixed at first sight
CYCLE_LOCK = LIVE_CACHE / "cycle.lock"    # fcntl lock held for the whole of one cycle (watch, --once and --scene)
WATCH_PID = LIVE_CACHE / "watch.pid"
WATCH_LOG = LIVE_CACHE / "watch.log"
COMBINED_GPKG = LIVE_DIR / "live_contacts.gpkg"
SUMMARY_JSON = LIVE_DIR / "live_summary.json"

AIS_SOURCE = "aisstream"
MATCH_METHOD = "track_interp_hungarian"

# D1: fixed names and meanings; every contacts layer carries all of them in this order.
D1_COLUMNS = [
    "det_id", "run_id", "mission", "acq_utc", "lon", "lat", "length_est_m", "confidence", "cnn_score", "cnn_vessel",
    "ais_status", "ais_source", "match_method", "match_dist_m", "match_dt_s", "match_quality",
    "mmsi", "imo", "vessel_name", "call_sign", "flag", "ship_type", "length_ais_m", "identity_source",
    "nearest_ais_mmsi", "nearest_ais_dist_m", "nearest_ais_dt_s", "n_ais_10km", "ais_reach",
    "research_only", "caveat",
]
AIS_STATUS_VALUES = ("matched", "unmatched", "no_coverage")

# Extra columns allowed by D1: detector evidence, rule outcomes and matching diagnostics.
EXTRA_COLUMNS = [
    "dark_lead", "scene_id", "pass_dir", "orbit_rel", "inc_angle_deg", "pol_class", "n_pixels", "scr_vv_db", "scr_vh_db",
    "low_reason", "persist_dates", "persist_dates_checked", "n_low_1km", "near_fixed_m",
    "match_gate_m", "ais_sog_kn", "length_ratio", "ais_class", "mmsi_mid", "ais_footprint_positions",
    "az_time_utc", "match_dist_uncorr_m", "az_shift_m", "velocity_source", "match_ambiguous", "ambiguous_mmsi", "match_alt_dist_m",
]

# GeoPackage field types the contacts layers must keep (checked by the tests on the written file).
CONTACT_FIELD_TYPES = {"mmsi": "int64", "imo": "int64", "nearest_ais_mmsi": "int64", "n_ais_10km": "int64",
                       "cnn_vessel": "bool", "research_only": "bool", "dark_lead": "bool", "cnn_score": "float64",
                       "match_ambiguous": "bool"}
CONTACT_DTYPES = {
    "det_id": object, "run_id": object, "mission": object, "acq_utc": object, "lon": float, "lat": float, "length_est_m": float,
    "confidence": object, "cnn_score": float, "cnn_vessel": "boolean", "ais_status": object, "ais_source": object, "match_method": object,
    "match_dist_m": float, "match_dt_s": float, "match_quality": object, "mmsi": "Int64", "imo": "Int64", "vessel_name": object,
    "call_sign": object, "flag": object, "ship_type": object, "length_ais_m": float, "identity_source": object, "nearest_ais_mmsi": "Int64",
    "nearest_ais_dist_m": float, "nearest_ais_dt_s": float, "n_ais_10km": "Int64", "ais_reach": float, "research_only": bool, "caveat": object,
    "dark_lead": bool, "scene_id": object, "pass_dir": object, "orbit_rel": "Int64", "inc_angle_deg": float, "pol_class": object,
    "n_pixels": "Int64", "scr_vv_db": float, "scr_vh_db": float, "low_reason": object, "persist_dates": "Int64", "persist_dates_checked": "Int64",
    "n_low_1km": "Int64", "near_fixed_m": float, "match_gate_m": float, "ais_sog_kn": float, "length_ratio": float, "ais_class": object,
    "mmsi_mid": "Int64", "ais_footprint_positions": "Int64", "az_time_utc": object, "match_dist_uncorr_m": float, "az_shift_m": float,
    "velocity_source": object, "match_ambiguous": bool, "ambiguous_mmsi": object, "match_alt_dist_m": float,
    "review_note": object,
}
# Hand-check note (live.review.REVIEW_NOTE_RULE), set when the products are rebuilt; always the last contacts column.
REVIEW_NOTE = "review_note"

# AIS vessels inside the tested area that no contact matched (the recall evidence for paper 2).
AIS_ONLY_COLUMNS = [
    "mmsi", "run_id", "scene_id", "mission", "acq_utc", "lon", "lat", "pred_method", "pred_dt_s", "n_reports",
    "sog_kn", "ais_class", "vessel_name", "call_sign", "imo", "flag", "ship_type", "length_ais_m", "identity_source",
    "on_tested_sea", "dist_coast_km", "nearest_object_m", "nearest_object_class", "ambiguous_det_id", "oversized_det_id", "ais_status",
    "research_only", "caveat",
]
AIS_ONLY_DTYPES = {
    "mmsi": "Int64", "run_id": object, "scene_id": object, "mission": object, "acq_utc": object, "lon": float, "lat": float,
    "pred_method": object, "pred_dt_s": float, "n_reports": "Int64", "sog_kn": float, "ais_class": object, "vessel_name": object,
    "call_sign": object, "imo": "Int64", "flag": object, "ship_type": object, "length_ais_m": float, "identity_source": object,
    "on_tested_sea": "boolean", "dist_coast_km": float, "nearest_object_m": float, "nearest_object_class": object,
    "ambiguous_det_id": object, "oversized_det_id": object, "ais_status": object, "research_only": bool, "caveat": object,
}
AIS_ONLY_FIELD_TYPES = {"mmsi": "int64", "imo": "int64", "n_reports": "int64", "on_tested_sea": "bool", "research_only": "bool"}

# One row per processed scene. AIS counts: *_aoi_* = heard anywhere in the AOI during the window (the feed was up),
# *_footprint_* = heard inside the scene footprint, near_footprint = within 0.3 degree of it (what the matcher sees).
SCENE_COLUMNS = [
    "product_id", "run_id", "mission", "start_utc", "stop_utc", "scene_time_utc", "orbit_abs", "orbit_rel", "pass_dir",
    "aoi_overlap_km2", "tested_km2", "blocks_processed", "runtime_s", "detect_attempts", "io_retries",
    "n_objects", "n_high", "n_medium", "n_fixed", "n_low",
    "n_contacts", "n_matched", "n_unmatched", "n_no_coverage", "n_dark_leads", "n_ambiguous", "n_ais_only", "n_ais_only_ambiguous",
    "n_ais_only_on_tested_sea", "n_ais_only_oversized", "tested_area_source", "n_cnn_scored", "n_cnn_vessel",
    "ais_aoi_positions", "ais_aoi_mmsi", "ais_footprint_positions", "ais_footprint_mmsi", "ais_near_footprint_mmsi",
    "ais_gear_beacons_excluded", "azimuth_correction", "platform_heading_deg", "sat_speed_ms",
    "ais_recorded_hours", "processed_utc", "status", "caveat",
]


def empty_ais_only() -> pd.DataFrame:
    """A typed, empty AIS-only frame so an empty layer still gets integer and boolean fields."""
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in AIS_ONLY_DTYPES.items()})


def empty_contacts() -> pd.DataFrame:
    """A typed, empty contacts frame (D1 columns then the extras) for a pass with no contact at all."""
    return pd.DataFrame({c: pd.Series(dtype=CONTACT_DTYPES.get(c, object)) for c in D1_COLUMNS + EXTRA_COLUMNS + [REVIEW_NOTE]})
