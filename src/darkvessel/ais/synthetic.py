"""Synthetic AIS + SAR detections for testing the matcher. Clearly not real data.

Model
-----
- Vessels: uniform positions in a box, random heading, speed 0-12 kn, lognormal length.
- A fraction carry no AIS ("dark" in this simulation).
- AIS reports every 2-15 min with gaps, small position noise.
- SAR detection probability rises with length (logistic, 50% at `l50_m`).
- SAR position = true position + noise + azimuth shift of moving targets:
      shift_az = -(R / V) * v_range
  with R/V about 110 s for Sentinel-1 IW (slant range ~850 km / orbital speed ~7.6 km/s,
  an approximation). The shift runs along the satellite track (`track_deg`).
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

from darkvessel.config import CRS_GEO, CRS_UTM

KN_TO_MS = 0.514444


def synthetic_scene(n_vessels: int = 60, frac_no_ais: float = 0.25, seed: int = 0,
                    sar_time: str = "2026-09-29T11:10:30Z", bbox=(105.0, 8.3, 105.6, 8.9),
                    l50_m: float = 15.0, pos_noise_m: float = 30.0, r_over_v_s: float = 110.0,
                    track_deg: float = 350.0, look_right: bool = True):
    rng = np.random.default_rng(seed)
    t0 = pd.Timestamp(sar_time)
    west, south, east, north = bbox
    lon = rng.uniform(west, east, n_vessels)
    lat = rng.uniform(south, north, n_vessels)
    pts = gpd.GeoSeries(gpd.points_from_xy(lon, lat), crs=CRS_GEO).to_crs(CRS_UTM)
    x0, y0 = pts.x.values, pts.y.values
    heading = rng.uniform(0, 360, n_vessels)
    speed = rng.uniform(0, 12, n_vessels) * KN_TO_MS
    length = np.clip(rng.lognormal(np.log(22), 0.7, n_vessels), 6, 300).round(1)
    has_ais = rng.random(n_vessels) > frac_no_ais
    mmsi = 574000000 + np.arange(n_vessels)  # 574 = Vietnam MID; synthetic numbers

    vx, vy = speed * np.sin(np.radians(heading)), speed * np.cos(np.radians(heading))
    # AIS reports
    ais_rows = []
    for i in np.where(has_ais)[0]:
        t = -rng.uniform(0, 900)
        while t < 1800:
            if rng.random() > 0.3:  # 30% of reports lost (satellite AIS gaps)
                px = x0[i] + vx[i] * t + rng.normal(0, 10)
                py = y0[i] + vy[i] * t + rng.normal(0, 10)
                ais_rows.append({"mmsi": int(mmsi[i]), "t_s": t, "x": px, "y": py,
                                 "sog_kn": speed[i] / KN_TO_MS, "cog_deg": heading[i], "length_m": length[i]})
            t += rng.uniform(120, 900)
    ais = pd.DataFrame(ais_rows)
    g = gpd.GeoSeries(gpd.points_from_xy(ais.x, ais.y), crs=CRS_UTM).to_crs(CRS_GEO)
    ais = pd.DataFrame({"mmsi": ais.mmsi, "timestamp": t0 + pd.to_timedelta(ais.t_s, unit="s"),
                        "lon": g.x.values, "lat": g.y.values, "sog_kn": ais.sog_kn, "cog_deg": ais.cog_deg,
                        "length_m": ais.length_m})

    # SAR detections
    p_det = 1 / (1 + np.exp(-(length - l50_m) / 3.0))
    detected = rng.random(n_vessels) < p_det
    az = np.radians(track_deg)
    rg = az + (np.pi / 2 if look_right else -np.pi / 2)
    v_range = vx * np.sin(rg) + vy * np.cos(rg)
    shift = -r_over_v_s * v_range
    sx = x0 + shift * np.sin(az) + rng.normal(0, pos_noise_m, n_vessels)
    sy = y0 + shift * np.cos(az) + rng.normal(0, pos_noise_m, n_vessels)
    idx = np.where(detected)[0]
    dets = gpd.GeoDataFrame(
        {"det_id": [f"SYN_{i:04d}" for i in idx], "confidence": "high",
         "length_est_m": (length[idx] * rng.uniform(0.8, 1.6, len(idx))).round(1)},
        geometry=gpd.points_from_xy(sx[idx], sy[idx]), crs=CRS_UTM,
    ).to_crs(CRS_GEO)
    truth = pd.DataFrame({"vessel": np.arange(n_vessels), "mmsi": mmsi, "has_ais": has_ais, "detected": detected,
                          "length_m": length, "speed_kn": speed / KN_TO_MS, "azimuth_shift_m": shift,
                          "det_id": [f"SYN_{i:04d}" if detected[i] else None for i in range(n_vessels)]})
    return dets, ais, truth, t0
