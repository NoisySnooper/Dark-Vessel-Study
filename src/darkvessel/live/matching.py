"""Match one scene's contacts to the recorded AIS, assign the AIS status with evidence, attach identity.

Inputs: the scene's objects (all classes, with lon, lat, confidence, det_id), the AIS positions of the plus or minus
30 min window over the whole AOI, the latest static message per MMSI, every recorded position (for ais_reach) and the
scene footprint. Contacts are the high, medium and fixed objects; low objects stay in the checkpoint and only serve
as "a weak return was there" evidence for AIS-only vessels.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import shapely
from scipy.spatial import cKDTree

from darkvessel.ais.match import match_detections, predict_positions
from darkvessel.config import CRS_GEO, DARK_CAVEAT, DATA_DIR
from darkvessel.live.identity import attach_identity, identity_table
from darkvessel.live.rules import LIVE_MATCH, assign_status, chord_to_m, ship_stations, unit_vectors
from darkvessel.live.scene import CONTACT_CLASSES
from darkvessel.live.schema import AIS_ONLY_COLUMNS, AIS_ONLY_DTYPES, AIS_SOURCE, D1_COLUMNS, EXTRA_COLUMNS, MATCH_METHOD, empty_ais_only

DIST_COAST_COG = DATA_DIR / "outputs" / "small" / "dist_coast_km_4326.tif"
SHORE_BUFFER_KM = 1.0  # the detector's land buffer: AIS vessels closer to the coast were never testable


def ais_with_static(ais_window: pd.DataFrame, static_latest: pd.DataFrame | None) -> pd.DataFrame:
    """Join the AIS length (static dimensions) onto the position reports so the matcher can grade length agreement."""
    a = ais_window.copy()
    if static_latest is not None and len(static_latest) and "length_m" in static_latest:
        ln = static_latest[["mmsi", "length_m"]].dropna().drop_duplicates("mmsi")
        ln["mmsi"] = ln.mmsi.astype("int64")
        a = a.merge(ln, on="mmsi", how="left")
    else:
        a["length_m"] = np.nan
    return a


def dist_coast_km(lon, lat, path=DIST_COAST_COG) -> np.ndarray:
    """Distance to the coast (km) from the ocean static layer; NaN when the layer is missing or the point is off-grid."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    out = np.full(len(lon), np.nan)
    if len(lon) == 0 or not path.exists():
        return out
    import rasterio

    with rasterio.open(path) as ds:
        vals = np.array([v[0] for v in ds.sample(zip(lon, lat))], dtype=float)
        nod = ds.nodata
    out[:] = np.where((vals == nod) | ~np.isfinite(vals), np.nan, vals)
    return out


def nearest_object(lon, lat, objects: pd.DataFrame):
    """(distance m, confidence class) of the nearest radar object of any class to each point."""
    n = len(np.asarray(lon))
    d = np.full(n, np.nan)
    cls = np.full(n, None, dtype=object)
    if n == 0 or objects is None or objects.empty:
        return d, cls
    tree = cKDTree(unit_vectors(objects.lon.values, objects.lat.values))
    c, j = tree.query(unit_vectors(lon, lat), k=1)
    d[:] = np.round(chord_to_m(c), 1)
    cls[:] = objects.confidence.to_numpy()[j]
    return d, cls


def match_scene(objects: pd.DataFrame, scene: dict, ais_window: pd.DataFrame, static_latest: pd.DataFrame | None,
                positions_all: pd.DataFrame, grid, aoi, log=print) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Contacts with D1 columns, AIS-only vessels, and counts.

    `scene` needs scene_time (tz-aware), run_id, footprint (shapely, EPSG:4326), product_id, mission, acq_utc, pass_dir,
    orbit_rel. `grid` is (transform, shape) of the 0.25 degree model grid.
    """
    import geopandas as gpd

    transform, shape = grid
    scene_time = pd.Timestamp(scene["scene_time"])
    ais_window = ship_stations(ais_window)
    positions_all = ship_stations(positions_all)
    obj = objects.copy()
    contacts = obj[obj.confidence.isin(CONTACT_CLASSES)].copy()
    cg = gpd.GeoDataFrame(contacts, geometry=gpd.points_from_xy(contacts.lon, contacts.lat), crs=CRS_GEO)
    footprint = scene["footprint"]
    valid_area = footprint.intersection(aoi) if aoi is not None else footprint
    near_fp = footprint.buffer(0.3)
    ais_near = ais_window[(ais_window.lon >= near_fp.bounds[0]) & (ais_window.lon <= near_fp.bounds[2])
                          & (ais_window.lat >= near_fp.bounds[1]) & (ais_window.lat <= near_fp.bounds[3])]
    in_fp = ais_near[shapely.contains_xy(footprint, ais_near.lon.to_numpy(float), ais_near.lat.to_numpy(float))] if len(ais_near) else ais_near
    ais_near = ais_with_static(ais_near, static_latest)
    matched, ais_only = match_detections(cg, ais_near, scene_time, LIVE_MATCH, valid_area=valid_area)
    matched = pd.DataFrame(matched.drop(columns="geometry"))
    # nearest placed vessel over the whole window (evidence for every contact, matched or not)
    pred = predict_positions(ais_window, scene_time, LIVE_MATCH)
    pred_ll = pd.DataFrame()
    if len(pred):
        p4 = pred.to_crs(CRS_GEO)
        pred_ll = pd.DataFrame({"mmsi": p4.mmsi.values, "lon": p4.geometry.x.values, "lat": p4.geometry.y.values,
                                "dt_s": p4.dt_s.values})
    out = assign_status(matched, ais_window, pred_ll, positions_all, transform, shape)
    ident = identity_table(static_latest, ais_window)
    out = attach_identity(out, ident)
    out["run_id"] = scene["run_id"]
    out["ais_source"] = AIS_SOURCE
    is_m = out.ais_status.to_numpy() == "matched"
    out["match_method"] = np.where(is_m, MATCH_METHOD, None)
    out["match_dt_s"] = np.where(is_m, out.ais_dt_s.to_numpy(float), np.nan)
    out["match_dist_m"] = np.where(is_m, np.round(out.match_dist_m.to_numpy(float), 1), np.nan)
    out["match_quality"] = np.where(is_m, out.match_quality, None)
    out["pred_method"] = np.where(is_m, out.ais_method, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = out.length_est_m.to_numpy(float) / out.length_ais_m.to_numpy(float)
    out["length_ratio"] = np.where(is_m & np.isfinite(ratio), np.round(ratio, 2), np.nan)
    out["mmsi"] = pd.array(pd.to_numeric(out.mmsi, errors="coerce").astype(float).round(), dtype="Int64")
    out["ais_footprint_positions"] = int(len(in_fp))
    out["research_only"] = False
    out["caveat"] = DARK_CAVEAT
    for c in ("inc_angle_deg", "scr_vv_db", "scr_vh_db", "length_est_m", "lon", "lat"):
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce").astype(float).round(5 if c in ("lon", "lat") else 1)
    cols = D1_COLUMNS + [c for c in EXTRA_COLUMNS + ["pred_method", "cnn_chip_valid_frac", "ais_recorded_hours", "row", "col"] if c in out]
    for c in cols:
        if c not in out:
            out[c] = None
    out = out[cols]

    # AIS-only vessels: placed inside the tested area, not matched to any contact
    ao = empty_ais_only()
    if len(ais_only):
        a4 = ais_only
        ao = pd.DataFrame({"mmsi": a4.mmsi.astype("int64").values, "lon": np.round(a4.geometry.x.values, 5),
                           "lat": np.round(a4.geometry.y.values, 5), "pred_method": a4.method.values,
                           "pred_dt_s": np.round(a4.dt_s.to_numpy(float), 1),
                           "n_reports": a4.n_reports.values if "n_reports" in a4 else None,
                           "sog_kn": np.round(pd.to_numeric(a4.sog_kn, errors="coerce").to_numpy(float), 1) if "sog_kn" in a4 else np.nan})
        ao = attach_identity(ao, ident)
        ao["run_id"], ao["scene_id"], ao["mission"], ao["acq_utc"] = scene["run_id"], scene["product_id"], scene["mission"], scene["acq_utc"]
        ao["dist_coast_km"] = np.round(dist_coast_km(ao.lon.values, ao.lat.values), 2)
        known = np.isfinite(ao.dist_coast_km.to_numpy(float))
        ao["on_tested_sea"] = pd.array(np.where(known, ao.dist_coast_km.to_numpy(float) >= SHORE_BUFFER_KM, None), dtype="boolean")
        d, cls = nearest_object(ao.lon.values, ao.lat.values, obj)
        ao["nearest_object_m"], ao["nearest_object_class"] = d, cls
        ao["ais_status"], ao["research_only"], ao["caveat"] = "ais_only", False, DARK_CAVEAT
        ao["mmsi"] = pd.array(ao.mmsi.astype("int64"), dtype="Int64")
        ao["n_reports"] = pd.array(pd.to_numeric(ao.n_reports, errors="coerce").astype(float).round(), dtype="Int64")
        for c in AIS_ONLY_COLUMNS:
            if c not in ao:
                ao[c] = pd.Series([None] * len(ao), dtype=AIS_ONLY_DTYPES[c]) if AIS_ONLY_DTYPES[c] in ("Int64", "boolean") else None
        ao = ao[AIS_ONLY_COLUMNS]
    counts = {"n_contacts": int(len(out)), "n_matched": int((out.ais_status == "matched").sum()),
              "n_unmatched": int((out.ais_status == "unmatched").sum()),
              "n_no_coverage": int((out.ais_status == "no_coverage").sum()), "n_dark_leads": int(out.dark_lead.sum()),
              "n_ais_only": int(len(ao)),
              "ais_aoi_positions": int(len(ais_window)), "ais_aoi_mmsi": int(ais_window.mmsi.nunique()) if len(ais_window) else 0,
              "ais_footprint_positions": int(len(in_fp)), "ais_footprint_mmsi": int(in_fp.mmsi.nunique()) if len(in_fp) else 0,
              "ais_near_footprint_mmsi": int(ais_near.mmsi.nunique()) if len(ais_near) else 0,
              "ais_placed_in_tested_area": int(len(ais_only) + (out.ais_status == "matched").sum())}
    log(f"  match: {counts['n_contacts']} contacts, {counts['n_matched']} matched, {counts['n_unmatched']} unmatched "
        f"({counts['n_dark_leads']} dark leads), {counts['n_no_coverage']} no_coverage, {counts['n_ais_only']} AIS-only; AIS in the "
        f"window: {counts['ais_aoi_positions']} positions / {counts['ais_aoi_mmsi']} MMSI in the AOI, {counts['ais_footprint_mmsi']} MMSI "
        f"inside the footprint, {counts['ais_near_footprint_mmsi']} within 0.3 degree of it")
    return out, ao, counts
