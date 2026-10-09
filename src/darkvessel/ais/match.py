"""SAR detection <-> AIS matching.

Input contract
--------------
detections : GeoDataFrame of points (any CRS) with a unique `det_id` column.
ais        : DataFrame of AIS position reports with columns
               mmsi (int), timestamp (tz-aware UTC), lon, lat (EPSG:4326),
               optional: sog_kn, cog_deg, length_m, shiptype, ais_class, ship_name.
sar_time   : tz-aware UTC timestamp of the SAR acquisition (the middle of a 25 s IW slice;
             the slice start is close enough too).

Method
------
1. Predict each vessel's position at `sar_time`: linear interpolation between the two
   reports that bracket it (in UTM metres) when both are within `max_gap_s`; otherwise
   dead reckoning from the nearest report using SOG/COG when within `max_extrap_s`.
2. Cost = distance (m) between each detection and each predicted AIS position, gated.
   The gate must absorb AIS timing and interpolation error, geolocation error, and the SAR
   azimuth shift of moving targets. A target with range velocity v_r is imaged displaced
   along the satellite track by about (R / V) v_r (Raney 1971, doi:10.1109/TAES.1971.310292).
   For Sentinel-1 IW, slant range R is about 790 to 950 km (incidence 29 to 46 degrees at
   693 km altitude) and the effective velocity V about 7.1 km/s, so R / V is 110 to 135 s:
   a vessel at 10 kn (5.1 m/s) radial speed shifts by about 650 m, at 20 kn by about 1.3 km.
   With `base_gate_m` set, the gate for vessel j is
       min(max_dist_m, base_gate_m + r_over_v_s * SOG_j)
   so a stopped vessel gets the base gate only and a fast one gets room for its shift
   (unknown SOG: max_dist_m). With `base_gate_m` None the gate is the fixed `max_dist_m`.
3. One-to-one assignment minimising total cost (Hungarian algorithm).
4. `match_quality` grades each pair from distance, time to the nearest AIS report and
   radar-to-AIS length agreement (see the function for the rule).

Outputs carry `ais_status` (matched / unmatched / ais_only) and the mandatory caveat.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from darkvessel.config import CRS_GEO, CRS_UTM, DARK_CAVEAT

KN_TO_MS = 0.514444
R_OVER_V_S = 125.0  # Sentinel-1 IW slant range over effective velocity, mid swath (see module docstring)

# match_quality thresholds (metres, seconds, radar length over AIS length)
QUALITY_RULE = {
    "high": {"dist_m": 500.0, "dt_s": 300.0, "ratio": (0.4, 3.0)},
    "medium": {"dist_m": 1000.0, "dt_s": 900.0, "ratio": (0.25, 4.0)},
}
QUALITY_RULE_TEXT = (
    "high = distance <= 500 m, nearest AIS report within 300 s of the scene time, and radar length estimate between "
    "0.4 and 3.0 times the AIS length (or AIS length unknown); medium = distance <= 1000 m, report within 900 s, "
    "length ratio 0.25 to 4.0 (or unknown); low = any other pair inside the gate. The radar length is a crude, "
    "upward-biased extent of the detected pixels, so the ratio bands are wide."
)


@dataclass
class MatchConfig:
    max_dist_m: float = 1000.0
    max_gap_s: float = 1800.0       # bracketing reports must both be within this of sar_time
    max_extrap_s: float = 600.0     # dead-reckoning limit from a single report
    length_weight_m: float = 0.0    # optional cost (m per m of length mismatch); 0 = off
    utm_crs: str = CRS_UTM
    base_gate_m: float | None = None  # speed-aware gate: base + r_over_v_s * SOG, capped by max_dist_m
    r_over_v_s: float = R_OVER_V_S
    carry: tuple = ("length_m", "shiptype", "ais_class", "ship_name")  # last non-null value per vessel


def predict_positions(ais: pd.DataFrame, sar_time: pd.Timestamp, cfg: MatchConfig = MatchConfig()) -> gpd.GeoDataFrame:
    """AIS position of each MMSI at `sar_time` (one row per vessel that can be placed).

    Columns: mmsi, method (interp, extrap, nearest), dt_s (time to the nearest report used), sog_kn (of the
    nearest report), n_reports (reports of that vessel in `ais`), carried static fields, geometry (utm_crs).
    """
    empty_cols = ["mmsi", "method", "dt_s", "sog_kn", "n_reports", "geometry"]
    if ais.empty:
        return gpd.GeoDataFrame(columns=empty_cols, geometry="geometry", crs=cfg.utm_crs)
    a = gpd.GeoDataFrame(ais.copy(), geometry=gpd.points_from_xy(ais.lon, ais.lat), crs=CRS_GEO).to_crs(cfg.utm_crs)
    a["x"], a["y"] = a.geometry.x, a.geometry.y
    a["dt_s"] = (a.timestamp - sar_time).dt.total_seconds()
    has_motion = "sog_kn" in a and "cog_deg" in a
    rows = []
    for mmsi, g in a.sort_values("timestamp").groupby("mmsi"):
        before, after = g[g.dt_s <= 0], g[g.dt_s > 0]
        static = {k: g[k].dropna().iloc[-1] for k in cfg.carry if k in g and g[k].notna().any()}
        near = g.iloc[int(np.argmin(np.abs(g.dt_s.values)))]
        sog = float(near.sog_kn) if "sog_kn" in g and np.isfinite(near.get("sog_kn", np.nan)) else np.nan
        base = {"mmsi": mmsi, "sog_kn": sog, "n_reports": int(len(g)), **static}
        if len(before) and len(after) and -before.dt_s.iloc[-1] <= cfg.max_gap_s and after.dt_s.iloc[0] <= cfg.max_gap_s:
            b, f = before.iloc[-1], after.iloc[0]
            w = -b.dt_s / (f.dt_s - b.dt_s)
            x, y = b.x + w * (f.x - b.x), b.y + w * (f.y - b.y)
            rows.append({**base, "x": x, "y": y, "method": "interp", "dt_s": min(-b.dt_s, f.dt_s)})
            continue
        if abs(near.dt_s) > cfg.max_extrap_s:
            continue
        x, y, method = near.x, near.y, "nearest"
        if has_motion and np.isfinite(near.get("sog_kn", np.nan)) and np.isfinite(near.get("cog_deg", np.nan)):
            d = near.sog_kn * KN_TO_MS * (-near.dt_s)
            x += d * np.sin(np.radians(near.cog_deg))
            y += d * np.cos(np.radians(near.cog_deg))
            method = "extrap"
        rows.append({**base, "x": x, "y": y, "method": method, "dt_s": abs(near.dt_s)})
    p = pd.DataFrame(rows)
    if p.empty:
        return gpd.GeoDataFrame(columns=empty_cols, geometry="geometry", crs=cfg.utm_crs)
    return gpd.GeoDataFrame(p, geometry=gpd.points_from_xy(p.x, p.y), crs=cfg.utm_crs)


def gate_metres(pred: gpd.GeoDataFrame, cfg: MatchConfig) -> np.ndarray:
    """Distance gate per predicted AIS position (see the module docstring)."""
    if cfg.base_gate_m is None or "sog_kn" not in pred:
        return np.full(len(pred), float(cfg.max_dist_m))
    sog_ms = pd.to_numeric(pred.sog_kn, errors="coerce").to_numpy(float) * KN_TO_MS
    g = cfg.base_gate_m + cfg.r_over_v_s * np.abs(sog_ms)
    g = np.where(np.isfinite(g), np.minimum(g, cfg.max_dist_m), cfg.max_dist_m)
    return g.astype(float)


def match_quality(dist_m, dt_s, length_est_m=None, length_ais_m=None) -> np.ndarray:
    """'high', 'medium' or 'low' per pair (QUALITY_RULE_TEXT). Arrays of any equal length; None where no match."""
    d = np.asarray(dist_m, float)
    t = np.asarray(dt_s, float)
    le = np.full(d.shape, np.nan) if length_est_m is None else np.asarray(length_est_m, float)
    la = np.full(d.shape, np.nan) if length_ais_m is None else np.asarray(length_ais_m, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = le / la
    known = np.isfinite(ratio) & (la > 0)
    out = np.full(d.shape, None, dtype=object)
    matched = np.isfinite(d)
    for grade in ("medium", "high"):  # high overwrites medium
        r = QUALITY_RULE[grade]
        ok_len = ~known | ((ratio >= r["ratio"][0]) & (ratio <= r["ratio"][1]))
        sel = matched & (d <= r["dist_m"]) & (t <= r["dt_s"]) & ok_len
        out[sel] = grade
    out[matched & (out == None)] = "low"  # noqa: E711
    return out


def match_detections(detections: gpd.GeoDataFrame, ais: pd.DataFrame, sar_time: pd.Timestamp,
                     cfg: MatchConfig = MatchConfig(), valid_area=None) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Match detections to AIS. Returns (detections_with_status, ais_only_positions).

    `valid_area` (shapely, EPSG:4326) limits AIS-only positions to water the detector
    actually tested; AIS vessels outside it are neither matches nor misses.
    Detection columns added: ais_status, mmsi, match_dist_m, ais_method, ais_dt_s, match_gate_m,
    ais_length_m (when the AIS frame carries length_m), ais_sog_kn, match_quality, dark_candidate, caveat.
    """
    det = detections.to_crs(cfg.utm_crs).copy()
    pred = predict_positions(ais, sar_time, cfg)
    if valid_area is not None and len(pred):
        va = gpd.GeoSeries([valid_area], crs=CRS_GEO).to_crs(cfg.utm_crs).iloc[0]
        pred = pred[pred.within(va)].reset_index(drop=True)
    det["ais_status"], det["mmsi"], det["match_dist_m"], det["ais_method"], det["ais_dt_s"] = "unmatched", pd.NA, np.nan, None, np.nan
    det["match_gate_m"], det["ais_sog_kn"] = np.nan, np.nan
    if "length_m" in pred:
        det["ais_length_m"] = np.nan
    matched_pred = np.zeros(len(pred), bool)
    if len(det) and len(pred):
        dx = det.geometry.x.values[:, None] - pred.geometry.x.values[None, :]
        dy = det.geometry.y.values[:, None] - pred.geometry.y.values[None, :]
        dist = np.hypot(dx, dy)
        cost = dist.copy()
        if cfg.length_weight_m and "length_est_m" in det and "length_m" in pred:
            dl = np.abs(det.length_est_m.values[:, None] - pred.length_m.values[None, :].astype(float))
            cost = cost + cfg.length_weight_m * np.nan_to_num(dl)
        gate = gate_metres(pred, cfg)
        gated = np.where(dist <= gate[None, :], cost, 1e12)
        ri, ci = linear_sum_assignment(gated)
        ok = gated[ri, ci] < 1e12
        for r, c in zip(ri[ok], ci[ok]):
            det.iloc[r, det.columns.get_loc("ais_status")] = "matched"
            det.iloc[r, det.columns.get_loc("mmsi")] = pred.mmsi.iloc[c]
            det.iloc[r, det.columns.get_loc("match_dist_m")] = float(dist[r, c])
            det.iloc[r, det.columns.get_loc("ais_method")] = pred.method.iloc[c]
            det.iloc[r, det.columns.get_loc("ais_dt_s")] = float(pred.dt_s.iloc[c])
            det.iloc[r, det.columns.get_loc("match_gate_m")] = float(gate[c])
            det.iloc[r, det.columns.get_loc("ais_sog_kn")] = float(pred.sog_kn.iloc[c]) if "sog_kn" in pred else np.nan
            if "length_m" in pred:
                det.loc[det.index[r], "ais_length_m"] = pred.length_m.iloc[c]
            matched_pred[c] = True
    det["match_quality"] = match_quality(det.match_dist_m, det.ais_dt_s, det.get("length_est_m"), det.get("ais_length_m"))
    vessel_like = det["confidence"].isin(["high", "medium"]) if "confidence" in det else True
    det["dark_candidate"] = (det.ais_status == "unmatched") & vessel_like
    det["caveat"] = DARK_CAVEAT
    ais_only = pred[~matched_pred].copy()
    ais_only["ais_status"] = "ais_only"
    ais_only["caveat"] = DARK_CAVEAT
    return det.to_crs(detections.crs), ais_only.to_crs(CRS_GEO)


def recall_by_length(det_matched: gpd.GeoDataFrame, ais_only: gpd.GeoDataFrame,
                     bins=(0, 15, 25, 50, 100, 200, 500)) -> pd.DataFrame:
    """Detection rate of AIS vessels by AIS length bin: matched / (matched + ais_only).

    This is the core quantity of the planned 'how many vessels does SAR miss' paper.
    Only meaningful when AIS covers the scene well and `valid_area` was applied.
    """
    m = det_matched.loc[det_matched.ais_status == "matched", "ais_length_m"] if "ais_length_m" in det_matched else pd.Series(dtype=float)
    a = ais_only["length_m"] if "length_m" in ais_only else pd.Series(dtype=float)
    labels = [f"{lo}-{hi} m" for lo, hi in zip(bins[:-1], bins[1:])]
    hit = pd.cut(m.astype(float), bins, labels=labels).value_counts().reindex(labels, fill_value=0)
    miss = pd.cut(a.astype(float), bins, labels=labels).value_counts().reindex(labels, fill_value=0)
    out = pd.DataFrame({"detected": hit, "missed": miss})
    out["n_ais"] = out.detected + out.missed
    out["recall"] = (out.detected / out.n_ais.replace(0, np.nan)).round(3)
    return out
