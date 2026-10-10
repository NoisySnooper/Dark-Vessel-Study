"""Products of the live-pass pipeline: one GeoPackage per pass, one combined, a summary JSON and the about layer.

Layers (EPSG:4326 and UTM 49N): contacts_*, ais_only_*, scenes_*; plus `about` (one row of text fields: caveat,
method, gates, status rules, sources, licences). Rebuilt from the per-scene checkpoints on every cycle, so a pass whose
scenes land one by one is complete as soon as the last one is processed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from darkvessel.config import CRS_GEO, CRS_UTM_REGIONAL, DARK_CAVEAT
from darkvessel.live import rules
from darkvessel.live.assign import AMBIGUITY_TEXT, ASSIGN_TEXT, AZIMUTH_TEXT, PAIRING_RULES_TEXT
from darkvessel.live.identity import IDENTITY_STATIC, IDENTITY_TEXT
from darkvessel.live.weather import WEATHER_TEXT
from darkvessel.live.scene import RULES_TEXT
from darkvessel.live.matching import TESTED_SEA_TEXT
from darkvessel.live.review import REVIEW_NOTE_RULE, review_notes
from darkvessel.live.schema import (CONTACT_DTYPES, COMBINED_GPKG, D1_COLUMNS, LIVE_DIR, REVIEW_NOTE, SCENE_COLUMNS, SUMMARY_JSON,
                                    empty_ais_only, empty_contacts)

LENGTH_BINS = (0, 15, 25, 50, 100, 200, 500)
CLASS_ORDER = ("high", "medium", "fixed")
STATUS_ORDER = ("matched", "unmatched", "no_coverage")
SOURCES = {
    "sentinel1_mirror": "https://sentinel-s1-l1c.s3.amazonaws.com (AWS Open Data mirror of Copernicus Sentinel-1 GRD, anonymous reads)",
    "sentinel1_terms": "Copernicus Sentinel Data Legal Notice, https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice "
                       "(fetched 2026-10-08): free, full and open access; credit 'Contains modified Copernicus Sentinel data 2026'",
    "sentinel1_orbit": "https://sentiwiki.copernicus.eu/web/s1-mission (fetched 2026-10-08): 693 km altitude, 12-day repeat",
    "azimuth_shift": "Raney 1971, Synthetic aperture imaging radar and moving targets, IEEE Trans. AES, doi:10.1109/TAES.1971.310292 "
                     "(DOI resolved 2026-10-08 and 2026-10-10 to IEEE Xplore document 4103740)",
    "scene_geometry": "Sentinel-1 product annotation (geolocation grid: azimuth time, slant range time, line, pixel, lat, lon, "
                      "incidence; Earth-fixed orbit state vectors), read from the mirror with the scene",
    "aisstream": "aisstream.io websocket feed, wss://stream.aisstream.io/v0/stream; docs https://aisstream.io/documentation "
                 "(fetched 2026-10-09: 3 subscribed connections per account, no SLA or uptime guarantee, no terms or licence text). "
                 "Reach is that of shore receivers: in this recording the feed is dense at Hong Kong, the Singapore and Bangka straits "
                 "and Manila Bay and nearly silent off Vietnam and over the open sea.",
    "aisstream_terms": "UNVERIFIED: aisstream.io publishes no terms of service or data licence (only a privacy policy); "
                       "redistribution and commercial use of the relayed AIS are not addressed. Research input until the operator "
                       "confirms in writing.",
    "mid_table": "ITU Table of Maritime Identification Digits, https://www.itu.int/en/ITU-R/terrestrial/fmd/Pages/mid.aspx (fetched 2026-10-08)",
    "cnn_training": "AI2 Skylight Sentinel-1 point labels (Apache-2.0), S1A/S1B 2020-2022; Contains modified Copernicus Sentinel data 2020-2022",
    "sea_mask": "ESA WorldCover 2021 v200 (CC BY 4.0), doi:10.5281/zenodo.7254221 (resolved 2026-10-10 to "
                "https://zenodo.org/records/7254221); Natural Earth 10 m marine areas and land (public domain)",
}


def about_row(run_id: str, scenes: pd.DataFrame, model_id: str | None, threshold: float | None, ais_hours: int,
              generated_utc: str) -> dict:
    return {
        "product": f"SCS Vessel Watch live pass {run_id}: Sentinel-1 radar contacts matched to live AIS (aisstream) with identity",
        "run_id": run_id, "generated_utc": generated_utc, "scenes": int(len(scenes)),
        "scene_ids": ";".join(scenes.product_id) if len(scenes) else "",
        "caveat": DARK_CAVEAT,
        "caveat_coverage": "Live AIS here comes from the shore receivers of aisstream.io; they hear class A a few tens of kilometres "
                           "offshore and class B less far. A contact in a cell the feed never hears is no_coverage, not dark.",
        "caveat_gap": "An AIS gap is not proof of intent: receivers lose class B first, messages collide in busy waters, many "
                      "vessels need not carry AIS, and the feed drops messages without notice.",
        "detector": RULES_TEXT["detector"], "confidence_classes": RULES_TEXT["confidence_classes"],
        "persistence": RULES_TEXT["persistence"], "clutter_zone": RULES_TEXT["clutter_zone"], "near_fixed": RULES_TEXT["near_fixed"],
        "cnn": RULES_TEXT["cnn"], "cnn_model_id": model_id or "not applied",
        "cnn_threshold": f"{threshold:.4f}" if threshold is not None else "not applied",
        "reads": RULES_TEXT["reads"],
        "contacts_layer": "high, medium and fixed objects; low objects (weak VV only, oversized, clutter zone, near fixed) are kept "
                          "only in the cache and counted in scenes_*.n_low. Fixed contacts are structures, never leads.",
        "dark_lead_rule": "dark_lead = true for an unmatched high or medium contact: a vessel the radar saw and the live feed did not "
                          "place although it was listening there. A lead for review, not evidence of wrongdoing. Fixed contacts, "
                          "ambiguous contacts (match_ambiguous) and no_coverage contacts are never leads.",
        "ais_source": "aisstream (open build); research_only = false on every row",
        "ais_mmsi_filter": rules.MMSI_FILTER_TEXT,
        "ais_window": rules.WINDOW_TEXT, "distance_gate": rules.GATE_TEXT, "match_method": "track_interp_hungarian: " + rules.WINDOW_TEXT,
        "azimuth_correction": AZIMUTH_TEXT, "assignment": ASSIGN_TEXT, "pairing_rules": PAIRING_RULES_TEXT, "ambiguity": AMBIGUITY_TEXT,
        "match_quality_rule": rules.QUALITY_TEXT, "ais_status_rule": rules.STATUS_TEXT, "evidence_columns": rules.EVIDENCE_TEXT,
        "no_coverage_rule": (f"no_coverage = not matched and no AIS position heard during the window in the contact's 0.25 degree "
                             f"cell or within {rules.NEAR_KM:.0f} km. It never means dark."),
        "ais_recorded_hours": str(ais_hours),
        "threads": "one scene at a time; 2 CPU threads (CNN chips, persistence, torch, OpenMP) and 4 I/O threads for COG tile "
                   "fetches; the process runs at nice 10 because the four cores are shared",
        "identity": IDENTITY_TEXT,
        "weather": f"Sidecar data/live/{run_id}_weather.parquet (per det_id): {WEATHER_TEXT}" if run_id != "all passes" else
                   f"Sidecars data/live/<run_id>_weather.parquet (per det_id): {WEATHER_TEXT}",
        "ais_only_layer": "AIS vessels placed at the scene time inside the scene footprint and the AOI that no contact matched: "
                          "the radar did not see them as a contact (it may have seen them as a low object: nearest_object_*; "
                          "oversized_det_id names the oversized return a vessel was paired with). ambiguous_det_id marks a vessel "
                          "held back by the ambiguity test. " + TESTED_SEA_TEXT,
        "recall_rule": "recall_by_ais_length = matched / (matched + AIS-only) per AIS length bin, over AIS-only vessels on tested "
                       "sea that are not held back as ambiguous (a vessel in the shore buffer, on land cells or outside the AOI was "
                       "never testable). Vessels paired with an oversized return count as missed contacts; their number is given "
                       "apart (ais_only_on_oversized_return).",
        "review_note": REVIEW_NOTE_RULE,
        "scenes_layer": "one row per processed scene with the footprint, sea tested, object counts and AIS window counts: "
                        "ais_aoi_* = heard anywhere in the AOI during the window (the feed was up), ais_footprint_* = heard inside "
                        "the scene footprint, ais_near_footprint_mmsi = within 0.3 degree of it (what the matcher sees)",
        "crs": "EPSG:4326 and WGS 84 / UTM zone 49N (EPSG:32649)",
        **{f"source_{k}": v for k, v in SOURCES.items()},
        "data_credit": "Contains modified Copernicus Sentinel data 2026; ESA WorldCover 2021 v200 (CC BY 4.0); Natural Earth (public domain); "
                       "AIS relayed by aisstream.io",
        "script": "scripts/30_live_pass.py (darkvessel.live)",
    }


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Types pyogrio writes cleanly: tz-aware times as ISO text, inf as null, object columns as str or None."""
    out = df.copy()
    for c in out.columns:
        s = out[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            out[c] = s.dt.strftime("%Y-%m-%dT%H:%M:%S%z").where(s.notna(), None)
        elif s.dtype == object:
            out[c] = s.map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA else str(v))
        elif pd.api.types.is_float_dtype(s):
            out[c] = s.replace([np.inf, -np.inf], np.nan)
    return out


def typed_contacts(contacts: pd.DataFrame) -> pd.DataFrame:
    """Contacts with the CONTACT_DTYPES types, so passes processed before a column existed (null there) still write
    INTEGER and BOOLEAN fields: missing booleans read false (match_ambiguous, dark_lead), missing numbers null."""
    out = contacts.copy()
    for c, t in CONTACT_DTYPES.items():
        if c not in out:
            continue
        if t is bool:
            out[c] = out[c].astype(object).where(out[c].notna(), False).astype(bool)
        elif t in ("Int64", "boolean"):
            vals = pd.to_numeric(out[c], errors="coerce") if t == "Int64" else out[c].astype(object).where(out[c].notna(), None)
            out[c] = pd.array(vals.astype(float).round() if t == "Int64" else vals, dtype=t)
        elif t is float:
            out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)
    return out


def write_pass(path: Path, contacts: pd.DataFrame, ais_only: pd.DataFrame, scenes: pd.DataFrame, about: dict) -> Path:
    import geopandas as gpd
    import pyogrio
    from shapely import wkt

    from darkvessel.io import write_dual_crs

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp.gpkg")  # per-process name: --once and --watch never share it
    if tmp.exists():
        tmp.unlink()
    try:
        c = _clean(contacts)
        cg = gpd.GeoDataFrame(c, geometry=gpd.points_from_xy(c.lon.astype(float), c.lat.astype(float)), crs=CRS_GEO)
        write_dual_crs(cg, tmp, "contacts", utm_crs=CRS_UTM_REGIONAL, spatial_index=False)
        a = _clean(ais_only) if len(ais_only) else empty_ais_only()
        ag = gpd.GeoDataFrame(a, geometry=gpd.points_from_xy(a.lon.astype(float), a.lat.astype(float)) if len(a) else [], crs=CRS_GEO)
        write_dual_crs(ag, tmp, "ais_only", utm_crs=CRS_UTM_REGIONAL, spatial_index=False)
        s = _clean(scenes.drop(columns=["footprint_wkt"]))
        sg = gpd.GeoDataFrame(s, geometry=[wkt.loads(w) for w in scenes.footprint_wkt], crs=CRS_GEO)
        write_dual_crs(sg, tmp, "scenes", utm_crs=CRS_UTM_REGIONAL)
        pyogrio.write_dataframe(pd.DataFrame([about]), tmp, layer="about", driver="GPKG")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def length_bins(contacts: pd.DataFrame, ais_only: pd.DataFrame, bins=LENGTH_BINS) -> dict:
    labels = [f"{lo}-{hi} m" for lo, hi in zip(bins[:-1], bins[1:])]
    out = {}
    if len(contacts):
        b = pd.cut(pd.to_numeric(contacts.length_est_m, errors="coerce").clip(upper=bins[-1] - 0.01), bins, labels=labels)
        tab = pd.crosstab(b, contacts.ais_status).reindex(labels, fill_value=0)
        for lab in labels:
            row = tab.loc[lab] if lab in tab.index else {}
            out[lab] = {"contacts": int(sum(row)) if len(row) else 0, **{k: int(row.get(k, 0)) for k in ("matched", "unmatched", "no_coverage")}}
    else:
        out = {lab: {"contacts": 0, "matched": 0, "unmatched": 0, "no_coverage": 0} for lab in labels}
    if len(ais_only):
        ab = pd.cut(pd.to_numeric(ais_only.length_ais_m, errors="coerce"), bins, labels=labels).value_counts().reindex(labels, fill_value=0)
        for lab in labels:
            out[lab]["ais_only"] = int(ab[lab])
        out["ais_only_length_unknown"] = int(pd.to_numeric(ais_only.length_ais_m, errors="coerce").isna().sum())
    else:
        for lab in labels:
            out[lab]["ais_only"] = 0
    return out


def pass_summary(run_id: str, contacts: pd.DataFrame, ais_only: pd.DataFrame, scenes: pd.DataFrame, records: list[dict] | None = None) -> dict:
    from darkvessel.ais.match import recall_by_length

    m = contacts[contacts.ais_status == "matched"] if len(contacts) else contacts
    rec = {}
    amb_ao = ais_only.ambiguous_det_id.notna() if len(ais_only) and "ambiguous_det_id" in ais_only else pd.Series(False, index=ais_only.index)
    tested_ao = ais_only.on_tested_sea.fillna(False).astype(bool) if len(ais_only) else pd.Series(False, index=ais_only.index)
    ais_only_recall = ais_only[(~amb_ao & tested_ao).to_numpy(bool)] if len(ais_only) else ais_only
    if len(m) or len(ais_only_recall):
        r = recall_by_length(m.assign(ais_length_m=pd.to_numeric(m.length_ais_m, errors="coerce")) if len(m) else pd.DataFrame(columns=["ais_status", "ais_length_m"]),
                             ais_only_recall.assign(length_m=pd.to_numeric(ais_only_recall.length_ais_m, errors="coerce")) if len(ais_only_recall) else pd.DataFrame(columns=["length_m"]),
                             bins=LENGTH_BINS)
        rec = {k: {kk: (None if (isinstance(vv, float) and np.isnan(vv)) else (float(vv) if kk == "recall" else int(vv))) for kk, vv in v.items()}
               for k, v in r.to_dict(orient="index").items()}
    cls = contacts.confidence.value_counts().to_dict() if len(contacts) else {}
    by_class = {c: {s: int(((contacts.confidence == c) & (contacts.ais_status == s)).sum()) if len(contacts) else 0 for s in STATUS_ORDER}
                for c in CLASS_ORDER}
    sc_int = lambda col, fn="sum": int(getattr(pd.to_numeric(scenes[col], errors="coerce").fillna(0), fn)()) if len(scenes) and col in scenes else 0  # noqa: E731
    return {
        "run_id": run_id, "mission": scenes.mission.iloc[0] if len(scenes) else None,
        "pass_start_utc": str(scenes.start_utc.min()) if len(scenes) else None, "pass_end_utc": str(scenes.stop_utc.max()) if len(scenes) else None,
        "scenes": int(len(scenes)), "scene_ids": scenes.product_id.tolist(), "tested_km2": round(float(scenes.tested_km2.sum()), 1) if len(scenes) else 0.0,
        "objects_all_classes": int(scenes.n_objects.sum()) if len(scenes) else 0, "low_objects": int(scenes.n_low.sum()) if len(scenes) else 0,
        "contacts": int(len(contacts)), "contacts_by_class": {k: int(v) for k, v in cls.items()},
        "cnn": {"scored": int(contacts.cnn_score.notna().sum()) if len(contacts) else 0,
                "vessel": int(contacts.cnn_vessel.fillna(False).astype(bool).sum()) if len(contacts) else 0},
        "ais_status": {k: int((contacts.ais_status == k).sum()) if len(contacts) else 0 for k in STATUS_ORDER},
        "ais_status_by_class": by_class,
        "dark_leads": int(contacts.dark_lead.fillna(False).astype(bool).sum()) if len(contacts) and "dark_lead" in contacts else 0,
        "dark_leads_note": ("unmatched high or medium contacts; fixed contacts are structures and never leads; ambiguous contacts "
                            "(match_ambiguous) are never leads; no_coverage is not a lead"),
        "matched_with_static_identity": int((m.identity_source == IDENTITY_STATIC).sum()) if len(m) and "identity_source" in m else 0,
        "matched_with_name": int(m.vessel_name.notna().sum()) if len(m) else 0,
        "matched_with_ais_length": int(pd.to_numeric(m.length_ais_m, errors="coerce").notna().sum()) if len(m) else 0,
        "match_quality": {k: int((m.match_quality == k).sum()) for k in ("high", "medium", "low")} if len(m) else {},
        "match_dist_m_median": round(float(pd.to_numeric(m.match_dist_m, errors="coerce").median()), 1) if len(m) else None,
        "ais_only": int(len(ais_only)), "ais_only_on_tested_sea": int(ais_only.on_tested_sea.fillna(False).astype(bool).sum()) if len(ais_only) else 0,
        "ais_only_with_weak_return_within_500m": int((pd.to_numeric(ais_only.nearest_object_m, errors="coerce") <= 500).sum()) if len(ais_only) else 0,
        "ais_window": {"aoi_positions_sum_over_scenes": sc_int("ais_aoi_positions"), "aoi_mmsi_max_per_scene": sc_int("ais_aoi_mmsi", "max"),
                       "footprint_positions_sum_over_scenes": sc_int("ais_footprint_positions"),
                       "footprint_mmsi_max_per_scene": sc_int("ais_footprint_mmsi", "max"),
                       "near_footprint_mmsi_max_per_scene": sc_int("ais_near_footprint_mmsi", "max"),
                       "note": "aoi = heard anywhere in the AOI during the window (the feed was up); footprint = inside the scene footprint; "
                               "near_footprint = within 0.3 degree of it"},
        "io_retries": sc_int("io_retries"), "detect_attempts_max": sc_int("detect_attempts", "max"),
        "by_length_bin": length_bins(contacts, ais_only), "recall_by_ais_length": rec,
        "recall_note": "matched / (matched + AIS-only) per AIS length bin, over AIS-only vessels on tested sea (on_tested_sea) not held "
                       "back as ambiguous (ambiguous_det_id); vessels in the shore buffer, on land cells or outside the AOI were never testable",
        **_pairing_summary(contacts, m, ais_only, amb_ao, scenes, records),
    }


def _pairing_summary(contacts, m, ais_only, amb_ao, scenes, records) -> dict:
    """Ambiguity, gear-beacon and azimuth-correction counts of a pass (round 3 matcher)."""
    out = {"ambiguous_contacts": int(contacts.match_ambiguous.fillna(False).astype(bool).sum()) if len(contacts) and "match_ambiguous" in contacts else 0,
           "ais_only_ambiguous": int(amb_ao.sum()) if len(ais_only) else 0,
           "ais_only_on_tested_sea_not_ambiguous": int((ais_only.on_tested_sea.fillna(False).astype(bool) & ~amb_ao).sum()) if len(ais_only) else 0,
           "ais_only_on_oversized_return": int(ais_only.oversized_det_id.notna().sum()) if len(ais_only) and "oversized_det_id" in ais_only else 0,
           "tested_area_source": sorted({str(v) for v in scenes.get("tested_area_source", pd.Series(dtype=object)).dropna()}) if len(scenes) else [],
           "hand_check": _hand_check_counts(contacts),
           "gear_beacons_excluded_max_per_scene": int(pd.to_numeric(scenes.get("ais_gear_beacons_excluded"), errors="coerce").fillna(0).max()) if len(scenes) and "ais_gear_beacons_excluded" in scenes else 0}
    if len(m) and "match_dist_uncorr_m" in m:
        d, du = pd.to_numeric(m.match_dist_m, errors="coerce"), pd.to_numeric(m.match_dist_uncorr_m, errors="coerce")
        sh = pd.to_numeric(m.az_shift_m, errors="coerce").abs()
        big = sh >= 150
        out["match_distance_m"] = {"median": round(float(d.median()), 1) if d.notna().any() else None,
                                   "p90": round(float(d.quantile(0.9)), 1) if d.notna().any() else None,
                                   "pairs_with_shift_150m_or_more": int(big.sum()),
                                   "their_median_corrected": round(float(d[big].median()), 1) if big.any() else None,
                                   "their_median_uncorrected": round(float(du[big].median()), 1) if big.any() else None}
    checks = [r.get("azimuth_check") for r in (records or []) if isinstance(r.get("azimuth_check"), dict) and r["azimuth_check"].get("vessels")]
    if checks:
        out["azimuth_check_by_scene"] = checks
        out["azimuth_check_note"] = ("per scene, for AIS vessels with a predicted shift of at least 150 m: median distance to the nearest contact "
                                     "from the expected position without the shift, with it, and with its sign flipped; the smallest is the "
                                     "version the radar agrees with (independent of the assignment)")
    return out


def _hand_check_counts(contacts: pd.DataFrame) -> dict:
    """Hand-check grades in review_note, by AIS status (REVIEW_NOTE_RULE)."""
    if not len(contacts) or REVIEW_NOTE not in contacts:
        return {}
    g = contacts[REVIEW_NOTE].dropna().astype(str).str.split(":").str[0]
    if g.empty:
        return {}
    st = contacts.loc[g.index, "ais_status"]
    return {s_: {k: int(((st == s_) & (g == k)).sum()) for k in ("confirmed", "plausible", "doubtful")} for s_ in STATUS_ORDER if (st == s_).any()}


def rebuild(scene_records: list[dict], contacts: pd.DataFrame, ais_only: pd.DataFrame, model_id: str | None, threshold: float | None,
            ais_hours: int, since_utc: str, out_dir: Path = LIVE_DIR, log=print) -> dict:
    """Write per-pass GeoPackages, the combined GeoPackage and live_summary.json from the checkpoints. Returns the summary."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    scenes = pd.DataFrame(scene_records)
    for c in SCENE_COLUMNS + ["footprint_wkt"]:
        if c not in scenes:
            scenes[c] = None
    scenes["caveat"] = DARK_CAVEAT
    for c in ("n_ais_only_on_tested_sea", "n_ais_only_oversized"):  # scenes matched before these counts existed
        known_zero = pd.to_numeric(scenes.n_ais_only, errors="coerce").eq(0)
        scenes[c] = pd.array(pd.to_numeric(scenes[c], errors="coerce").where(~(scenes[c].isna() & known_zero), 0).round(), dtype="Int64")
    scenes = scenes[SCENE_COLUMNS + ["footprint_wkt"]]
    if len(contacts) == 0:
        contacts = empty_contacts()
    for c in D1_COLUMNS:
        if c not in contacts:
            contacts[c] = None
    contacts = typed_contacts(contacts)
    contacts[REVIEW_NOTE] = review_notes(contacts, out_dir) if len(contacts) else pd.Series(dtype=object)
    contacts = contacts[[c for c in contacts.columns if c != REVIEW_NOTE] + [REVIEW_NOTE]]
    if len(ais_only) == 0:
        ais_only = empty_ais_only()
    summary = {"generated_utc": now, "since_utc": since_utc, "caveat": DARK_CAVEAT, "ais_recorded_hours": ais_hours,
               "cnn_model_id": model_id, "passes": {}, "files": []}
    wrote = []
    for run_id, sc in scenes.groupby("run_id", sort=True):
        c = contacts[contacts.run_id == run_id] if len(contacts) else contacts
        a = ais_only[ais_only.run_id == run_id] if len(ais_only) else ais_only
        path = out_dir / f"{run_id}.gpkg"
        write_pass(path, c, a, sc, about_row(run_id, sc, model_id, threshold, ais_hours, now))
        summary["passes"][run_id] = pass_summary(run_id, c, a, sc, [r for r in scene_records if r.get("run_id") == run_id])
        summary["files"].append(str(path.relative_to(out_dir.parent.parent)))
        wrote.append(path)
        log(f"  wrote {path.name}: {len(sc)} scenes, {len(c)} contacts, {len(a)} AIS-only ({path.stat().st_size / 1e6:.1f} MB)")
    for stale in out_dir.glob("live_S1*.gpkg"):  # a pass renamed or removed from the cache
        if stale not in wrote:
            stale.unlink()
    if len(scenes):
        about = about_row("all passes", scenes, model_id, threshold, ais_hours, now)
        about["product"] = "SCS Vessel Watch live passes, combined: every processed Sentinel-1 scene with live AIS"
        write_pass(COMBINED_GPKG if out_dir == LIVE_DIR else out_dir / COMBINED_GPKG.name, contacts, ais_only, scenes, about)
        summary["files"].append(str((out_dir / COMBINED_GPKG.name).relative_to(out_dir.parent.parent)))
    summary["totals"] = {
        "passes": int(scenes.run_id.nunique()) if len(scenes) else 0, "scenes": int(len(scenes)),
        "tested_km2": round(float(scenes.tested_km2.fillna(0).sum()), 1) if len(scenes) else 0.0, "contacts": int(len(contacts)),
        "ais_status": {k: int((contacts.ais_status == k).sum()) if len(contacts) else 0 for k in STATUS_ORDER},
        "dark_leads": int(contacts.dark_lead.fillna(False).astype(bool).sum()) if len(contacts) and "dark_lead" in contacts else 0,
        "ais_only": int(len(ais_only)),
    }
    sp = SUMMARY_JSON if out_dir == LIVE_DIR else out_dir / SUMMARY_JSON.name
    tmp = sp.with_name(f"{sp.stem}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(summary, indent=1, default=str))
    os.replace(tmp, sp)
    return summary
