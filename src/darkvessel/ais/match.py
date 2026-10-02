"""SAR detection <-> AIS matching.

Input contract
--------------
detections : GeoDataFrame of points (any CRS) with a unique `det_id` column.
ais        : DataFrame of AIS position reports with columns
               mmsi (int), timestamp (tz-aware UTC), lon, lat (EPSG:4326),
               optional: sog_kn, cog_deg, length_m, shiptype.
sar_time   : tz-aware UTC timestamp of the SAR acquisition (scene start is close enough
             for one 25 s IW slice).

Method
------
1. Predict each vessel's position at `sar_time`: linear interpolation between the two
   reports that bracket it (in UTM metres) when both are within `max_gap_s`; otherwise
   dead reckoning from the nearest report using SOG/COG when within `max_extrap_s`.
2. Cost = distance (m) between each detection and each predicted AIS position, gated at
   `max_dist_m`. The gate must absorb AIS timing error and the SAR azimuth shift of
   moving targets (hundreds of metres at Sentinel-1 geometry).
3. One-to-one assignment minimising total cost (Hungarian algorithm).

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


@dataclass
class MatchConfig:
    max_dist_m: float = 1000.0
    max_gap_s: float = 1800.0       # bracketing reports must both be within this of sar_time
    max_extrap_s: float = 600.0     # dead-reckoning limit from a single report
    length_weight_m: float = 0.0    # optional cost (m per m of length mismatch); 0 = off
    utm_crs: str = CRS_UTM


def predict_positions(ais: pd.DataFrame, sar_time: pd.Timestamp, cfg: MatchConfig = MatchConfig()) -> gpd.GeoDataFrame:
    """AIS position of each MMSI at `sar_time` (one row per vessel that can be placed)."""
    if ais.empty:
        return gpd.GeoDataFrame(columns=["mmsi", "method", "dt_s", "geometry"], geometry="geometry", crs=cfg.utm_crs)
    a = gpd.GeoDataFrame(ais.copy(), geometry=gpd.points_from_xy(ais.lon, ais.lat), crs=CRS_GEO).to_crs(cfg.utm_crs)
    a["x"], a["y"] = a.geometry.x, a.geometry.y
    a["dt_s"] = (a.timestamp - sar_time).dt.total_seconds()
    rows = []
    for mmsi, g in a.sort_values("timestamp").groupby("mmsi"):
        before, after = g[g.dt_s <= 0], g[g.dt_s > 0]
        static = {k: g[k].dropna().iloc[-1] for k in ("length_m", "shiptype") if k in g and g[k].notna().any()}
        if len(before) and len(after) and -before.dt_s.iloc[-1] <= cfg.max_gap_s and after.dt_s.iloc[0] <= cfg.max_gap_s:
            b, f = before.iloc[-1], after.iloc[0]
            w = -b.dt_s / (f.dt_s - b.dt_s)
            x, y = b.x + w * (f.x - b.x), b.y + w * (f.y - b.y)
            rows.append({"mmsi": mmsi, "x": x, "y": y, "method": "interp", "dt_s": min(-b.dt_s, f.dt_s), **static})
            continue
        near = g.iloc[int(np.argmin(np.abs(g.dt_s.values)))]
        if abs(near.dt_s) > cfg.max_extrap_s:
            continue
        x, y, method = near.x, near.y, "nearest"
        if "sog_kn" in g and "cog_deg" in g and np.isfinite(near.get("sog_kn", np.nan)) and np.isfinite(near.get("cog_deg", np.nan)):
            d = near.sog_kn * KN_TO_MS * (-near.dt_s)
            x += d * np.sin(np.radians(near.cog_deg))
            y += d * np.cos(np.radians(near.cog_deg))
            method = "extrap"
        rows.append({"mmsi": mmsi, "x": x, "y": y, "method": method, "dt_s": abs(near.dt_s), **static})
    p = pd.DataFrame(rows)
    if p.empty:
        return gpd.GeoDataFrame(columns=["mmsi", "method", "dt_s", "geometry"], geometry="geometry", crs=cfg.utm_crs)
    return gpd.GeoDataFrame(p, geometry=gpd.points_from_xy(p.x, p.y), crs=cfg.utm_crs)


def match_detections(detections: gpd.GeoDataFrame, ais: pd.DataFrame, sar_time: pd.Timestamp,
                     cfg: MatchConfig = MatchConfig(), valid_area=None) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Match detections to AIS. Returns (detections_with_status, ais_only_positions).

    `valid_area` (shapely, EPSG:4326) limits AIS-only positions to water the detector
    actually tested; AIS vessels outside it are neither matches nor misses.
    """
    det = detections.to_crs(cfg.utm_crs).copy()
    pred = predict_positions(ais, sar_time, cfg)
    if valid_area is not None and len(pred):
        va = gpd.GeoSeries([valid_area], crs=CRS_GEO).to_crs(cfg.utm_crs).iloc[0]
        pred = pred[pred.within(va)].reset_index(drop=True)
    det["ais_status"], det["mmsi"], det["match_dist_m"], det["ais_method"], det["ais_dt_s"] = "unmatched", pd.NA, np.nan, None, np.nan
    matched_pred = np.zeros(len(pred), bool)
    if len(det) and len(pred):
        dx = det.geometry.x.values[:, None] - pred.geometry.x.values[None, :]
        dy = det.geometry.y.values[:, None] - pred.geometry.y.values[None, :]
        cost = np.hypot(dx, dy)
        if cfg.length_weight_m and "length_est_m" in det and "length_m" in pred:
            dl = np.abs(det.length_est_m.values[:, None] - pred.length_m.values[None, :].astype(float))
            cost = cost + cfg.length_weight_m * np.nan_to_num(dl)
        gated = np.where(np.hypot(dx, dy) <= cfg.max_dist_m, cost, 1e12)
        ri, ci = linear_sum_assignment(gated)
        ok = gated[ri, ci] < 1e12
        for r, c in zip(ri[ok], ci[ok]):
            det.iloc[r, det.columns.get_loc("ais_status")] = "matched"
            det.iloc[r, det.columns.get_loc("mmsi")] = pred.mmsi.iloc[c]
            det.iloc[r, det.columns.get_loc("match_dist_m")] = float(np.hypot(dx[r, c], dy[r, c]))
            det.iloc[r, det.columns.get_loc("ais_method")] = pred.method.iloc[c]
            det.iloc[r, det.columns.get_loc("ais_dt_s")] = float(pred.dt_s.iloc[c])
            if "length_m" in pred:
                det.loc[det.index[r], "ais_length_m"] = pred.length_m.iloc[c]
            matched_pred[c] = True
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
