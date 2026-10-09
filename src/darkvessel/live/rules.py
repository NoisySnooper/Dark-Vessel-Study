"""AIS status rules for live-pass contacts, their constants and the evidence columns.

Rules (quoted in every about layer):
  matched      the Hungarian assignment paired the contact with an AIS vessel inside the gate (ais.match).
  unmatched    not matched, and the live feed heard at least one AIS position during the window of plus or minus
               AIS_WINDOW_S around the scene time either in the contact's 0.25 degree cell (the VIIRS / model grid of
               darkvessel.ocean.grid) or within NEAR_KM of the contact. The feed was listening there. For a high or
               medium contact this is a dark lead: a vessel the radar saw and AIS did not place (dark_lead = true).
               A fixed contact is a structure that recurs on earlier passes; it is never a lead. Not evidence of
               anything else.
  no_coverage  not matched, and nothing was heard in the cell or within NEAR_KM during the window. Terrestrial AIS
               reaches a few tens of kilometres offshore; silence there says nothing about the contact.

AIS used by every rule: ship-station MMSIs only (nine digits, MID 201 to 775, so 201000000 to 775999999). Coast
stations (00MID), SAR aircraft (111MID), aids to navigation (99MID), craft of a parent ship (98MID), group calls (0MID)
and malformed numbers are dropped before matching, the coverage test, the evidence counts and the AIS-only layer.

Evidence on every contact:
  nearest_ais_mmsi, nearest_ais_dist_m, nearest_ais_dt_s   nearest AIS vessel placed at the scene time (any distance)
                                                            and the time from the scene to its nearest report
  n_ais_10km      distinct MMSI with at least one report within N_AIS_KM of the contact during the window
  ais_reach       share of recorded hours (hours with any AIS anywhere in the AOI) in which the contact's 0.25 degree
                  cell had at least one AIS position; 0 means the feed never heard that cell
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from darkvessel.ais.match import MatchConfig, QUALITY_RULE_TEXT
from darkvessel.config import CRS_UTM_REGIONAL

AIS_WINDOW_S = 1800          # positions within this of the scene time are "the window"
NEAR_KM = 20.0               # unmatched vs no_coverage: AIS heard within this distance counts as coverage
N_AIS_KM = 10.0              # radius of the n_ais_10km count
R_EARTH_M = 6371008.8
MMSI_SHIP_MIN, MMSI_SHIP_MAX = 201_000_000, 775_999_999   # ship stations: MID 201 to 775 in the first three digits
LEAD_CLASSES = ("high", "medium")                        # an unmatched contact of these classes is a dark lead

# Matching settings for live passes (see ais.match for the reasoning behind the gate)
LIVE_MATCH = MatchConfig(max_dist_m=2000.0, max_gap_s=AIS_WINDOW_S, max_extrap_s=600.0, utm_crs=CRS_UTM_REGIONAL,
                         base_gate_m=500.0, r_over_v_s=125.0)

GATE_TEXT = (
    f"Distance gate per AIS vessel = min({LIVE_MATCH.max_dist_m:.0f} m, {LIVE_MATCH.base_gate_m:.0f} m + "
    f"{LIVE_MATCH.r_over_v_s:.0f} s x SOG in m/s); unknown SOG uses {LIVE_MATCH.max_dist_m:.0f} m. The base absorbs "
    "AIS position and timing error, track interpolation and radar geolocation; the speed term absorbs the Sentinel-1 "
    "azimuth shift of a moving target, about (R / V) x radial speed with R / V 110 to 135 s for IW (slant range 790 to "
    "950 km at 693 km altitude, effective velocity about 7.1 km/s; Raney 1971, doi:10.1109/TAES.1971.310292). "
    "A stopped vessel therefore gets 500 m, a 20 kn vessel about 1,790 m. One-to-one assignment by the Hungarian "
    "algorithm on distance."
)
WINDOW_TEXT = (
    f"AIS positions from {AIS_WINDOW_S // 60} min before to {AIS_WINDOW_S // 60} min after the scene time (middle of the "
    "25 s slice). A vessel is placed at the scene time by linear interpolation between the two reports that bracket it "
    f"(both within {LIVE_MATCH.max_gap_s / 60:.0f} min), else by dead reckoning from its nearest report using SOG and "
    f"COG when that report is within {LIVE_MATCH.max_extrap_s / 60:.0f} min; otherwise the vessel is not placed and "
    "cannot be matched (it still counts as coverage for the unmatched rule)."
)
STATUS_TEXT = (
    "matched = paired inside the gate. unmatched = not matched and at least one AIS position was heard during the "
    f"window in the contact's 0.25 degree cell or within {NEAR_KM:.0f} km of the contact (the feed was listening there). "
    "An unmatched high or medium contact is a dark lead (dark_lead = true): a vessel the radar saw and AIS did not "
    "place, not evidence of wrongdoing. An unmatched fixed contact is a structure, never a lead. no_coverage = not "
    f"matched and nothing heard in the cell or within {NEAR_KM:.0f} km during the window (terrestrial AIS does not "
    "reach there; nothing can be said)."
)
MMSI_FILTER_TEXT = (
    f"Only ship-station MMSIs are used (nine digits, first three digits = MID 201 to 775, so {MMSI_SHIP_MIN} to "
    f"{MMSI_SHIP_MAX}): coast stations, SAR aircraft, aids to navigation, craft of a parent ship, group calls and "
    "malformed numbers are dropped before matching, the coverage test, the evidence counts and the AIS-only layer."
)
EVIDENCE_TEXT = (
    "nearest_ais_* = nearest AIS vessel placed at the scene time (any distance) and the time from the scene to its "
    f"nearest report; n_ais_10km = distinct MMSI with a report within {N_AIS_KM:.0f} km during the window; ais_reach = "
    "share of recorded hours (hours with any AIS in the AOI) in which the contact's 0.25 degree cell had AIS; "
    "ais_footprint_positions = AIS positions heard inside the scene footprint during the window."
)
QUALITY_TEXT = QUALITY_RULE_TEXT


def ship_stations(ais: pd.DataFrame) -> pd.DataFrame:
    """Rows whose MMSI is a ship station (MMSI_FILTER_TEXT); keeps the frame's columns and order."""
    if ais is None or len(ais) == 0 or "mmsi" not in ais:
        return ais
    m = pd.to_numeric(ais.mmsi, errors="coerce")
    keep = (m >= MMSI_SHIP_MIN) & (m <= MMSI_SHIP_MAX)
    return ais[keep.to_numpy(bool)].reset_index(drop=True)


def unit_vectors(lon, lat) -> np.ndarray:
    lon, lat = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    return np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])


def chord(dist_m: float) -> float:
    """Chord length on the unit sphere for a great-circle distance in metres."""
    return 2.0 * np.sin(dist_m / (2.0 * R_EARTH_M))


def chord_to_m(c: np.ndarray) -> np.ndarray:
    return 2.0 * R_EARTH_M * np.arcsin(np.clip(np.asarray(c, float) / 2.0, 0, 1))


def heard_nearby(lon, lat, ais_window: pd.DataFrame, transform, shape, near_km: float = NEAR_KM) -> np.ndarray:
    """True where AIS was heard during the window in the point's grid cell or within `near_km`."""
    from darkvessel.ocean.grid import cell_index

    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    out = np.zeros(len(lon), bool)
    if ais_window.empty or len(lon) == 0:
        return out
    r, c, inside = cell_index(transform, shape, lon, lat)
    ar, ac, ainside = cell_index(transform, shape, ais_window.lon.values, ais_window.lat.values)
    heard_cells = set((ar[ainside] * shape[1] + ac[ainside]).tolist())
    key = r * shape[1] + c
    out |= inside & np.isin(key, list(heard_cells))
    tree = cKDTree(unit_vectors(ais_window.lon.values, ais_window.lat.values))
    d, _ = tree.query(unit_vectors(lon, lat), k=1)
    out |= chord_to_m(d) <= near_km * 1000.0
    return out


def n_within(lon, lat, ais_window: pd.DataFrame, radius_km: float = N_AIS_KM) -> np.ndarray:
    """Distinct MMSI with at least one report within `radius_km` of each point."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    out = np.zeros(len(lon), int)
    if ais_window.empty or len(lon) == 0:
        return out
    tree = cKDTree(unit_vectors(ais_window.lon.values, ais_window.lat.values))
    mmsi = ais_window.mmsi.to_numpy()
    for i, idx in enumerate(tree.query_ball_point(unit_vectors(lon, lat), chord(radius_km * 1000.0))):
        out[i] = len(set(mmsi[idx].tolist())) if idx else 0
    return out


def nearest_placed(lon, lat, pred_ll: pd.DataFrame):
    """(mmsi, dist_m, dt_s) of the nearest AIS vessel placed at the scene time; `pred_ll` has lon, lat, mmsi, dt_s."""
    n = len(np.asarray(lon))
    mm = np.full(n, None, dtype=object)
    dist = np.full(n, np.nan)
    dt = np.full(n, np.nan)
    if pred_ll is None or len(pred_ll) == 0 or n == 0:
        return mm, dist, dt
    tree = cKDTree(unit_vectors(pred_ll.lon.values, pred_ll.lat.values))
    d, j = tree.query(unit_vectors(lon, lat), k=1)
    mm[:] = pred_ll.mmsi.to_numpy()[j]
    dist[:] = chord_to_m(d)
    dt[:] = pred_ll.dt_s.to_numpy(float)[j]
    return mm, dist, dt


def reach_at(lon, lat, positions_all: pd.DataFrame, transform, shape) -> tuple[np.ndarray, int]:
    """(ais_reach per point, recorded hours): share of recorded hours with AIS in the point's 0.25 degree cell."""
    from darkvessel.ais.aisstream import reach_grids
    from darkvessel.ocean.grid import sample

    share, _, hours = reach_grids(positions_all, transform, shape)
    vals = sample(share, transform, np.asarray(lon, float), np.asarray(lat, float), fill=np.nan)
    return np.where(np.isfinite(vals), vals, 0.0).astype(float), int(len(hours))


def assign_status(det: pd.DataFrame, ais_window: pd.DataFrame, pred_ll: pd.DataFrame, positions_all: pd.DataFrame,
                  transform, shape) -> pd.DataFrame:
    """Fill ais_status (keeping 'matched'), dark_lead, nearest_ais_*, n_ais_10km and ais_reach on `det`.

    `det` needs lon, lat, ais_status (and confidence for dark_lead; without it every unmatched row is a lead).
    """
    out = det.copy()
    lon, lat = out.lon.to_numpy(float), out.lat.to_numpy(float)
    covered = heard_nearby(lon, lat, ais_window, transform, shape)
    status = np.where(out.ais_status.to_numpy() == "matched", "matched", np.where(covered, "unmatched", "no_coverage"))
    out["ais_status"] = status
    lead_class = out.confidence.isin(LEAD_CLASSES).to_numpy() if "confidence" in out else np.ones(len(out), bool)
    out["dark_lead"] = (status == "unmatched") & lead_class
    mm, dist, dt = nearest_placed(lon, lat, pred_ll)
    out["nearest_ais_mmsi"] = pd.array([None if m is None else int(m) for m in mm], dtype="Int64")
    out["nearest_ais_dist_m"] = np.round(dist, 1)
    out["nearest_ais_dt_s"] = np.round(dt, 1)
    out["n_ais_10km"] = n_within(lon, lat, ais_window)
    reach, hours = reach_at(lon, lat, positions_all, transform, shape)
    out["ais_reach"] = np.round(reach, 4)
    out["ais_recorded_hours"] = hours
    return out
