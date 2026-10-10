"""Review priority, 0 to 100: five factors with fixed maximum points, summed and clipped (spec 4.1, MDA brief 2.6).

It is a review priority, not a risk score, and not a probability of anything. "No AIS match" is the gate that makes an
L1 lead and adds 0 points by itself. Start weights, uncalibrated (model id PRIORITY_MODEL_ID); every lead stores its
factor list (factor, value, points, max_points, source) and one integer points column per factor for the bundle.

L1 (one radar contact)
- evidence_quality, 0 to 30: CNN score above 0.5 scaled to 0 to 20 points at 1.0; both channels 5; wind and deep
  convection both known, wind below 12 m/s and no deep convection 5 (either one missing: 0 and the extra entry
  "weather unknown" naming the missing part).
- corroboration, 0 to 25: a VIIRS light within 2 km and 3 h, 15; an AIS behaviour event within 2 km and 3 h, 10 (the
  research build reads its event tables; the open build has no event source yet). A nearby AIS vessel that did not
  match is evidence on the card, not corroboration, and adds nothing.
- ais_reach, 0 to 20: 12 x ais_reach of the cell (share) plus 8 x min(1, n_ais_10km / 10): how much an absence of AIS
  can mean here.
- persistence, 0 to 15: 15 when an unmatched high or medium contact, not ambiguous, lies within 2 km on another pass
  within 72 h, else 0.
- area_weight, 0 to 10: analyst-set per reporting box, default 0.

L7 (one 0.25 degree cell, a coverage lead), scored within the same factor meanings
- evidence_quality, 0 to 5 of 30: 5 x min(1, ln(1 + n_lights) / ln(31)). A light shows lit activity, not a vessel at a
  radar look (no length, no class, no time match), so L7 uses a sixth of the L1 scale.
- corroboration, 0: radar did not image the cell in 90 days and the lights are not matched to AIS, so nothing can
  corroborate them; by construction of the type.
- ais_reach, 0: an L7 lead makes no claim about AIS (lights are not matched to AIS in this build); the cell's
  ais_reach_share is shown as context only.
- persistence, 0 to 5 of 15: lights on another night, the L7 reading of "seen again": 5 x min(1, (n_nights - 1) / 6),
  so one night scores 0 and seven or more nights score 5.
- area_weight, 0 to 10: as L1.
Without an analyst area weight an L7 lead scores at most 10 (L7_CEILING, low band). That keeps vessel detection and
identification first (owner priority P0): an L1 lead always has both channels (5, a gate condition where the
polarisation is known) and, when its wind and deep convection are known and calm, 5 weather points, so its floor is 10
(L1_FLOOR_WEATHER_KNOWN) and it sorts above or level with every L7 lead (ties sort L1 first by lead_id). Model v0 capped
L7 at 20; on the first real open L1 leads (Pearl River pass, 2026-10-10) 21 of 395 L1 leads scored 11 to 20 and fell
among the coverage cells, which is why v1 halves both L7 parts. An L1 lead with weather unknown can still score 5 to 9.

Factor `source` values are registry keys of app/CONTRACT.md section 2, several joined by "; ".
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from darkvessel.leads import PRIORITY_MODEL_ID

FACTORS = ["evidence_quality", "corroboration", "ais_reach", "persistence", "area_weight"]
MAX_POINTS = {"evidence_quality": 30, "corroboration": 25, "ais_reach": 20, "persistence": 15, "area_weight": 10}
POINTS_COLUMNS = {f: f"pts_{f}" for f in FACTORS}
BANDS = (("low", 0, 33), ("medium", 34, 66), ("high", 67, 100))

# L1 sub-weights
L1_CNN_PTS = 20
L1_CNN_FLOOR = 0.5
L1_BOTH_CHANNELS_PTS = 5
L1_WEATHER_PTS = 5
L1_LIGHT_PTS = 15
L1_AIS_EVENT_PTS = 10
L1_REACH_PTS = 12
L1_DENSITY_PTS = 8
L1_DENSITY_SATURATION = 10      # n_ais_10km at which the density part saturates
L1_PERSISTENCE_PTS = 15
# L7 sub-weights
L7_EVIDENCE_PTS = 5             # a sixth of the evidence_quality maximum
L7_PERSISTENCE_PTS = 5          # a third of the persistence maximum
L7_LIGHTS_SATURATION = 30
L7_NIGHTS_SATURATION = 7        # persistence full at lights on 7 or more nights
L7_CEILING = L7_EVIDENCE_PTS + L7_PERSISTENCE_PTS   # 10: the most an L7 lead scores without an analyst area weight
# The least an L1 lead scores when its weather is known and calm: both channels (a gate condition) plus calm weather.
L1_FLOOR_WEATHER_KNOWN = L1_BOTH_CHANNELS_PTS + L1_WEATHER_PTS
assert L7_CEILING <= L1_FLOOR_WEATHER_KNOWN, "an L7 lead must not outrank an L1 lead with known weather (owner P0)"

WEIGHTS = {
    "model_id": PRIORITY_MODEL_ID, "calibrated": False, "max_points": MAX_POINTS,
    "bands": {name: [lo, hi] for name, lo, hi in BANDS},
    "L1": {"cnn": f"{L1_CNN_PTS} x (cnn_score - {L1_CNN_FLOOR}) / (1 - {L1_CNN_FLOOR}), clipped to 0..{L1_CNN_PTS}",
           "both_channels": L1_BOTH_CHANNELS_PTS, "weather_known_calm": L1_WEATHER_PTS, "weather_unknown": 0,
           "light_2km_3h": L1_LIGHT_PTS, "ais_event_2km_3h": L1_AIS_EVENT_PTS,
           "ais_reach": f"{L1_REACH_PTS} x ais_reach", "ais_density": f"{L1_DENSITY_PTS} x min(1, n_ais_10km / {L1_DENSITY_SATURATION})",
           "persistence_72h_2km": L1_PERSISTENCE_PTS, "no_ais_match": 0, "area_weight_default": 0},
    "L7": {"evidence_quality": f"{L7_EVIDENCE_PTS} x min(1, ln(1 + n_lights) / ln(1 + {L7_LIGHTS_SATURATION})) (a sixth of the L1 scale)",
           "corroboration": "0 by construction (radar did not look; lights not matched to AIS)",
           "ais_reach": "0 (an L7 lead makes no AIS claim; ais_reach_share is context)",
           "persistence": f"{L7_PERSISTENCE_PTS} x min(1, (n_nights - 1) / {L7_NIGHTS_SATURATION - 1})", "area_weight_default": 0,
           "ceiling_without_area_weight": L7_CEILING, "l1_floor_weather_known": L1_FLOOR_WEATHER_KNOWN},
}


def band(priority) -> np.ndarray:
    p = np.asarray(priority, dtype=float)
    out = np.full(p.shape, "low", dtype=object)
    out[p >= 34] = "medium"
    out[p >= 67] = "high"
    return out.astype(str)


def total(points: pd.DataFrame) -> pd.Series:
    """Sum of the five factor columns, clipped to 0..100, as int."""
    s = points[[POINTS_COLUMNS[f] for f in FACTORS]].sum(axis=1)
    return s.clip(0, 100).round().astype(int)


def _r(x) -> int:
    return int(math.floor(float(x) + 0.5))


def _fmt(v, nd=2):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "unknown"
    return f"{float(v):.{nd}f}"


def _sources(build: str | None, ais_source) -> tuple[str, str, str]:
    """(detector key, AIS key, corroboration keys) of the registry for a build, or per row from ais_source."""
    research = build == "research" if build is not None else ais_source == "gfw"
    return (("det_regional", "gfw_4wings", "viirs_dnb; gfw_events") if research else ("det_live", "aisstream", "viirs_dnb"))


def l1_points(df: pd.DataFrame, area_weights: dict | None = None, build: str | None = None) -> tuple[pd.DataFrame, list[list[dict]]]:
    """Points per factor and the factor list of every L1 row. `df` needs cnn_score, both_channels (True/False/None),
    wind_ms, deep_convection, n_lights_2km_3h, n_ais_events_2km_3h, ais_reach, n_ais_10km, n_persist_72h, region_box,
    ais_source, and optionally weather_row (a weather sample was joined). `build` picks the registry keys of the
    sources; when None they follow each row's ais_source (gfw = research)."""
    from darkvessel.leads.rules import weather_parts

    area_weights = area_weights or {}
    cnn = pd.to_numeric(df["cnn_score"], errors="coerce").to_numpy(float)
    cnn_pts = np.clip((cnn - L1_CNN_FLOOR) / (1 - L1_CNN_FLOOR), 0, 1) * L1_CNN_PTS
    cnn_pts = np.where(np.isfinite(cnn_pts), cnn_pts, 0)
    both = df["both_channels"].map(lambda v: v is True).to_numpy(bool)
    wp = weather_parts(df)
    known = (wp.wind_known & wp.conv_known).to_numpy(bool)
    missing = wp.missing.to_numpy()
    wind = pd.to_numeric(df["wind_ms"], errors="coerce").to_numpy(float)
    conv = wp.conv_fail.to_numpy(bool)
    calm = known & ~wp.wind_fail.to_numpy(bool) & ~conv
    joined = df["weather_row"].map(lambda v: bool(v) if v is not None and v == v else False).to_numpy(bool) \
        if "weather_row" in df.columns else np.ones(len(df), bool)
    n_light = pd.to_numeric(df["n_lights_2km_3h"], errors="coerce").fillna(0).to_numpy(float)
    n_ev = pd.to_numeric(df["n_ais_events_2km_3h"], errors="coerce").fillna(0).to_numpy(float)
    reach = pd.to_numeric(df["ais_reach"], errors="coerce").to_numpy(float)
    reach_c = np.clip(np.where(np.isfinite(reach), reach, 0.0), 0, 1)
    n10 = pd.to_numeric(df["n_ais_10km"], errors="coerce").to_numpy(float)
    n10_c = np.clip(np.where(np.isfinite(n10), n10, 0.0) / L1_DENSITY_SATURATION, 0, 1)
    npers = pd.to_numeric(df["n_persist_72h"], errors="coerce").fillna(0).to_numpy(float)
    regions = df["region_box"].astype(str).to_numpy()
    srcs = [_sources(build, a) for a in df["ais_source"].astype(object)]

    pts = pd.DataFrame(index=df.index)
    pts[POINTS_COLUMNS["evidence_quality"]] = [_r(c + (L1_BOTH_CHANNELS_PTS if b else 0) + (L1_WEATHER_PTS if w else 0))
                                               for c, b, w in zip(cnn_pts, both, calm)]
    pts[POINTS_COLUMNS["corroboration"]] = [_r((L1_LIGHT_PTS if a > 0 else 0) + (L1_AIS_EVENT_PTS if b > 0 else 0))
                                            for a, b in zip(n_light, n_ev)]
    pts[POINTS_COLUMNS["ais_reach"]] = [_r(L1_REACH_PTS * r + L1_DENSITY_PTS * d) for r, d in zip(reach_c, n10_c)]
    pts[POINTS_COLUMNS["persistence"]] = [L1_PERSISTENCE_PTS if n > 0 else 0 for n in npers]
    pts[POINTS_COLUMNS["area_weight"]] = [int(np.clip(area_weights.get(r, 0), 0, MAX_POINTS["area_weight"])) for r in regions]

    factors: list[list[dict]] = []
    for i in range(len(df)):
        det_src, ais_src, cor_src = srcs[i]
        wind_txt = f"wind {_fmt(wind[i], 1)} m/s" if np.isfinite(wind[i]) else "wind unknown"
        conv_txt = "deep convection " + ("unknown" if "convection" in missing[i] else ("yes" if conv[i] else "no"))
        ch_txt = "both channels" if both[i] else "channel unknown"
        ev_txt = f"AIS behaviour events within 2 km and 3 h: {int(n_ev[i])}" + ("" if "gfw_events" in cor_src else " (no event source in this build yet)")
        lst = [
            {"factor": "evidence_quality", "value": f"cnn {_fmt(cnn[i])} ({_r(cnn_pts[i])}), {ch_txt} ({L1_BOTH_CHANNELS_PTS if both[i] else 0}), {wind_txt}, {conv_txt} ({L1_WEATHER_PTS if calm[i] else 0})",
             "points": int(pts.iloc[i, 0]), "max_points": MAX_POINTS["evidence_quality"], "source": f"cnn_v0; {det_src}; gfs_wind; himawari_ctt"},
            {"factor": "corroboration", "value": f"lights within 2 km and 3 h: {int(n_light[i])}; {ev_txt}",
             "points": int(pts.iloc[i, 1]), "max_points": MAX_POINTS["corroboration"], "source": cor_src},
            {"factor": "ais_reach", "value": f"ais_reach {_fmt(reach[i])}, AIS vessels within 10 km {int(n10[i]) if np.isfinite(n10[i]) else 'unknown'}; no AIS match adds 0",
             "points": int(pts.iloc[i, 2]), "max_points": MAX_POINTS["ais_reach"], "source": ais_src},
            {"factor": "persistence", "value": f"unmatched contacts within 2 km on another pass within 72 h: {int(npers[i])}",
             "points": int(pts.iloc[i, 3]), "max_points": MAX_POINTS["persistence"], "source": det_src},
            {"factor": "area_weight", "value": f"reporting box {regions[i]}, analyst weight",
             "points": int(pts.iloc[i, 4]), "max_points": MAX_POINTS["area_weight"], "source": "analyst"},
        ]
        if not known[i]:
            why = "no weather sample joined for this pass yet" if not joined[i] else "the weather sample has no value here"
            known_txt = "" if missing[i] == "wind and deep convection" else "; the known part passes"
            lst.append({"factor": "weather unknown", "value": f"{missing[i]} unknown ({why}){known_txt}: lead kept, 0 weather points (board D4.5)",
                        "points": 0, "max_points": 0, "source": "gfs_wind; himawari_ctt"})
        factors.append(lst)
    return pts, factors


def l7_points(df: pd.DataFrame, area_weights: dict | None = None) -> tuple[pd.DataFrame, list[list[dict]]]:
    """Points per factor and the factor list of every L7 row. `df` needs n_lights, n_nights, ais_reach_share,
    region_box. Corroboration and AIS reach are 0 by construction; see the module docstring."""
    area_weights = area_weights or {}
    n = pd.to_numeric(df["n_lights"], errors="coerce").fillna(0).to_numpy(float)
    nights = pd.to_numeric(df["n_nights"], errors="coerce").fillna(0).to_numpy(float)
    reach = pd.to_numeric(df["ais_reach_share"], errors="coerce").to_numpy(float)
    regions = df["region_box"].astype(str).to_numpy()

    pts = pd.DataFrame(index=df.index)
    pts[POINTS_COLUMNS["evidence_quality"]] = [_r(L7_EVIDENCE_PTS * min(1.0, math.log1p(v) / math.log1p(L7_LIGHTS_SATURATION))) for v in n]
    pts[POINTS_COLUMNS["corroboration"]] = 0
    pts[POINTS_COLUMNS["ais_reach"]] = 0
    pts[POINTS_COLUMNS["persistence"]] = [_r(L7_PERSISTENCE_PTS * min(1.0, max(0.0, v - 1) / (L7_NIGHTS_SATURATION - 1))) for v in nights]
    pts[POINTS_COLUMNS["area_weight"]] = [int(np.clip(area_weights.get(r, 0), 0, MAX_POINTS["area_weight"])) for r in regions]

    factors: list[list[dict]] = []
    for i in range(len(df)):
        factors.append([
            {"factor": "evidence_quality", "value": f"clear-sky lights never imaged by radar in 90 d: {int(n[i])} (L7 scale, at most {L7_EVIDENCE_PTS}: a light is not a vessel at a radar look)",
             "points": int(pts.iloc[i, 0]), "max_points": MAX_POINTS["evidence_quality"], "source": "viirs_dnb; s1_grd"},
            {"factor": "corroboration", "value": "none by construction: no radar look in 90 d; lights not matched to AIS",
             "points": 0, "max_points": MAX_POINTS["corroboration"], "source": "s1_grd"},
            {"factor": "ais_reach", "value": f"no AIS claim (AIS heard in {_fmt(reach[i])} of recorded hours, context)",
             "points": 0, "max_points": MAX_POINTS["ais_reach"], "source": "aisstream"},
            {"factor": "persistence", "value": f"nights with such lights: {int(nights[i])} (L7 scale, at most {L7_PERSISTENCE_PTS}, at {L7_NIGHTS_SATURATION} or more nights)",
             "points": int(pts.iloc[i, 3]), "max_points": MAX_POINTS["persistence"], "source": "viirs_dnb"},
            {"factor": "area_weight", "value": f"reporting box {regions[i]}, analyst weight",
             "points": int(pts.iloc[i, 4]), "max_points": MAX_POINTS["area_weight"], "source": "analyst"},
        ])
    return pts, factors


def check_factors(factors: list[dict], priority: int) -> bool:
    """True when the clipped sum of the factor points equals `priority`."""
    s = sum(int(f["points"]) for f in factors)
    return int(min(100, max(0, s))) == int(priority)
