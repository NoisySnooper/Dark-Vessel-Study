"""Lead rules: the L1 gate, the L7 selection and the pre-listed explanations per lead type (spec 4.1, MDA brief 2.5).

L1, unmatched radar contact in AIS reach: ais_status unmatched, detector class high or medium, CNN score at least 0.5,
both channels where the polarisation is known (live pol_class VV+VH; regional class high = VV and VH), not in a clutter
zone and not near a fixed structure where those flags exist (the regional run already downgraded such objects to low),
and no known weather failure. Wind and deep convection are gated one by one (board D4.5): a contact fails when its wind
is known and at least 12 m/s, or when deep convection is known and true. When either value is missing and the known one
does not fail, the lead is kept and its factor list says "weather unknown" (naming the missing part) with 0 weather
points.

L7, lit activity where radar does not look: clear-sky VIIRS lit-vessel candidates (class lit_vessel_candidate) with no
Sentinel-1 look in the 90-day coverage window (s1_passes_90d = 0), more than 1 km from a Satlas marine-infrastructure
point, grouped by 0.25 degree model-grid cell. It is a coverage statement for tasking, not a vessel lead, and it names
no vessel.

Every threshold is a start value, uncalibrated until the owner's labels exist (data/labels/owner_2026-10.csv).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

L1_CNN_MIN = 0.5
L1_WIND_MAX_MS = 12.0
L1_CLASSES = ("high", "medium")
CLUTTER_LOW_1KM = 5          # detector rule: 5 or more low objects within 1 km
NEAR_FIXED_M = 250.0         # detector rule: within 250 m of a fixed structure
CORROBORATION_KM = 2.0
CORROBORATION_H = 3.0
PERSISTENCE_KM = 2.0
PERSISTENCE_H = 72.0
PERSISTENCE_MIN_GAP_S = 600.0   # another pass: at least 10 minutes apart (scenes of one pass are seconds apart)
L7_SATLAS_EXCLUDE_M = 1000.0
L7_QUALITY = "clear"
L7_CLASS = "lit_vessel_candidate"
L7_EVIDENCE_LIGHTS_MAX = 20     # light evidence rows per L7 cell: the brightest 20; n_lights keeps the full count

LEAD_NAMES = {"L1": "Unmatched radar contact in AIS reach", "L7": "Lit activity where radar does not look"}
PRIMARY_TYPE = {"L1": "contact", "L7": "cell"}

L1_CONDITIONS = ["unmatched", "vessel_class", "cnn", "both_channels", "not_clutter", "not_near_fixed", "weather"]

RULE_TEXT = {
    "L1": ("ais_status = unmatched (AIS was heard near the contact, so an absence of a match means something here); "
           "confidence high or medium (never low or fixed); cnn_score >= 0.5; both channels where the polarisation is "
           "known (pol_class VV+VH, or regional class high which is VV and VH); not in a clutter zone (n_low_1km < 5) and "
           "not near a fixed structure (near_fixed_m > 250 m) where those flags exist; weather gated per component: "
           "excluded when wind_ms is known and >= 12, or deep_convection is known and true; when either is missing and "
           "the known one passes, the lead is kept with the factor 'weather unknown' at 0 points (board D4.5)."),
    "L7": ("VIIRS Day/Night Band lights of class lit_vessel_candidate with quality clear and s1_passes_90d = 0 (no "
           "Sentinel-1 pass over the 0.05 degree coverage cell in the 90-day window), excluding lights within 1 km of a "
           "Satlas marine infrastructure point, grouped by 0.25 degree model-grid cell; one lead per cell over the VIIRS "
           "window. A coverage lead for tasking: it names no vessel and is not a vessel lead."),
}

# Explanation codes per lead type. Rows carry the codes (JSON list of strings); the about layer and docs/leads.md carry
# the sentence of each code, so 13,000 rows do not repeat 1.3 KB of constant text twice.
LAWFUL_EXPLANATIONS = {
    "L1": {
        "no_carriage_requirement": (
            "No carriage requirement: the IMO AIS rule covers ships of 300 gross tonnage and upwards on international "
            "voyages, cargo ships of 500 gross tonnage and upwards and passenger ships; most fishing boats and small craft "
            "on domestic voyages are outside it."),
        "vms_fleet": "VMS fleet: in Vietnam fishing vessels of 15 m and over report by VMS, which this product does not see (UNVERIFIED).",
        "class_b_out_of_range": (
            "Class B out of range: class B transponders transmit at 5 W (2 W for carrier-sense units) against 12.5 W for "
            "class A (USCG NAVCEN class comparison), so receivers hear them over a shorter range; satellite AIS misses "
            "messages in busy coastal waters."),
        "lawful_switch_off": (
            "Lawful switch-off: IMO Resolution A.1106(29) paragraph 22 lets the master switch AIS off where its operation "
            "might compromise the safety or security of the ship."),
        "detector_false_positive": (
            "Detector false positive: the CNN verifier's held-out precision is 0.77 on Sentinel-1A/1B labels; sea clutter, "
            "rain cells, fixed structures, sidelobes and ambiguities remain possible."),
    },
    "L7": {
        "lawful_fishing_lights": "Lit activity at night is mostly lawful fishing; bright lights are used to attract squid and other species.",
        "recurring_light_not_vessel": "Recurring lights can be platforms, flares, islands, navigation lights or anchorages, not vessels.",
        "dnb_false_sources": "Moonlit clouds, lightning, auroral light and image artefacts are known false sources of Day/Night Band spikes.",
        "coverage_statement_only": "This lead states a radar coverage gap for tasking. It is not a vessel lead and it names no vessel.",
    },
}

CHANGE_INDICATORS = {
    "L1": {
        "late_ais_match": "An AIS match on the late re-check of recorded AIS (positions that arrived after the pass).",
        "next_radar_look": "The next Sentinel-1 look at this spot (next_look_utc): the contact is there again, or gone.",
        "optical_view": "An optical view (Sentinel-2) of the spot within the same days.",
        "chip_false_alarm": "A fixed-structure or clutter explanation on the radar chip (false alarm).",
        "owner_label": "An owner label (vessel, structure, clutter, unsure), which also calibrates the weights.",
    },
    "L7": {
        "radar_acquisition": "A Sentinel-1 acquisition whose footprint covers the cell turns the coverage gap into radar evidence.",
        "more_clear_nights": "More clear nights with lights at the same spots strengthen the activity statement; clear nights without lights weaken it.",
        "ais_reach_improves": "AIS reach improving in the cell (a receiver that hears it) allows light-to-AIS checks.",
    },
}


def _col(df: pd.DataFrame, name: str, default=np.nan) -> pd.Series:
    """Column `name`, or a default-filled series of the same index when the input file lacks it."""
    if name in df.columns:
        return df[name]
    return pd.Series(default, index=df.index)


def both_channels(df: pd.DataFrame) -> pd.Series:
    """True when the contact is bright in VV and VH, False when one channel only, None when unknown.

    Live files carry pol_class ('VV+VH', 'VH only', 'VV only'); the regional run encodes it in the class (high = VV and
    VH; medium = one channel). Returns an object series of True, False or None.
    """
    pol = _col(df, "pol_class", None).astype(object)
    conf = _col(df, "confidence", None).astype(object)
    out = pd.Series([None] * len(df), index=df.index, dtype=object)
    has_pol = pol.notna()
    out[has_pol] = (pol[has_pol].astype(str) == "VV+VH")
    rest = ~has_pol & conf.notna()
    out[rest] = (conf[rest].astype(str) == "high")
    return out


def _bool_or_none(v):
    """True or False for a known flag (bool, 0/1, numpy bool), None for a missing one (None, NaN, pd.NA)."""
    if v is None or v is pd.NA:
        return None
    if isinstance(v, (float, np.floating)) and not np.isfinite(v):
        return None
    return bool(v)


def weather_parts(df: pd.DataFrame) -> pd.DataFrame:
    """Per contact: wind_known, conv_known, wind_fail (known and >= 12 m/s), conv_fail (deep convection known true)
    and missing (text naming the missing part, '' when both are known)."""
    wind = pd.to_numeric(_col(df, "wind_ms"), errors="coerce")
    conv = _col(df, "deep_convection", None).astype(object).map(_bool_or_none)
    out = pd.DataFrame(index=df.index)
    out["wind_known"] = wind.notna().to_numpy(bool)
    out["conv_known"] = conv.map(lambda v: v is not None).to_numpy(bool)
    out["wind_fail"] = (out.wind_known & (wind >= L1_WIND_MAX_MS)).to_numpy(bool)
    out["conv_fail"] = conv.map(lambda v: v is True).to_numpy(bool)
    out["missing"] = [("wind and deep convection" if not w and not c else "wind" if not w else "deep convection" if not c else "")
                      for w, c in zip(out.wind_known, out.conv_known)]
    return out


def weather_known(df: pd.DataFrame) -> pd.Series:
    """True when both wind and deep convection are known (the 5 weather points need both)."""
    p = weather_parts(df)
    return p.wind_known & p.conv_known


def l1_gate(df: pd.DataFrame) -> pd.DataFrame:
    """One boolean column per L1 condition (True = the condition is met or unknown-and-kept), plus `l1` (all met),
    `weather_known` and `channels` (text for the card)."""
    g = pd.DataFrame(index=df.index)
    g["unmatched"] = _col(df, "ais_status", None).astype(str) == "unmatched"
    g["vessel_class"] = _col(df, "confidence", None).astype(str).isin(L1_CLASSES)
    cnn = pd.to_numeric(_col(df, "cnn_score"), errors="coerce")
    g["cnn"] = cnn.notna() & (cnn >= L1_CNN_MIN)
    bc = both_channels(df)
    g["both_channels"] = bc.map(lambda v: v is not False).astype(bool)
    n_low = pd.to_numeric(_col(df, "n_low_1km"), errors="coerce")
    reason = _col(df, "low_reason", None).astype(object).fillna("").astype(str)
    g["not_clutter"] = ~((n_low.notna() & (n_low >= CLUTTER_LOW_1KM)) | (reason == "clutter_zone"))
    near = pd.to_numeric(_col(df, "near_fixed_m"), errors="coerce")
    g["not_near_fixed"] = ~((near.notna() & (near <= NEAR_FIXED_M)) | (reason == "near_fixed"))
    wp = weather_parts(df)
    g["weather"] = ~(wp.wind_fail | wp.conv_fail)
    g["weather_known"] = wp.wind_known & wp.conv_known
    g["weather_missing"] = wp.missing
    g["l1"] = g[L1_CONDITIONS].all(axis=1)
    pol = _col(df, "pol_class", None).astype(object)
    conf = _col(df, "confidence", None).astype(object)
    g["channels"] = [
        (str(p) if p is not None and p == p else ("VV and VH (class high)" if c == "high" else ("one channel (class medium)" if c == "medium" else "unknown")))
        for p, c in zip(pol, conf)
    ]
    return g


def l1_gate_counts(gate: pd.DataFrame) -> dict:
    """Counts for the about layer and the docs: unmatched contacts, how many fail each later condition (among the
    unmatched), how many pass, and how many pass with weather unknown."""
    um = gate[gate.unmatched]
    out = {"unmatched": int(gate.unmatched.sum()), "fail_by_condition_among_unmatched": {}}
    for c in L1_CONDITIONS[1:]:
        out["fail_by_condition_among_unmatched"][c] = int((~um[c]).sum())
    out["pass_all"] = int(gate.l1.sum())
    out["pass_with_weather_unknown"] = int((gate.l1 & ~gate.weather_known).sum())
    out["pass_with_weather_known"] = int((gate.l1 & gate.weather_known).sum())
    miss = gate.get("weather_missing", pd.Series("", index=gate.index))
    out["pass_weather_missing_by_part"] = {m: int((gate.l1 & (miss == m)).sum()) for m in ("wind", "deep convection", "wind and deep convection")}
    # Both weather values known and calm: the strictest reading, for comparison with older counts.
    out["pass_strict_weather_known"] = int((gate.l1 & gate.weather_known).sum())
    return out


def l7_select(lights: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Clear-sky lights never imaged by Sentinel-1 in 90 days, away from Satlas infrastructure. Returns the subset and
    the counts of what was excluded and why."""
    q = _col(lights, "quality", None).astype(str) == L7_QUALITY
    if "class" in lights.columns:
        q &= lights["class"].astype(str) == L7_CLASS
    never = pd.to_numeric(_col(lights, "s1_passes_90d"), errors="coerce") == 0
    satlas = pd.to_numeric(_col(lights, "satlas_infra_m"), errors="coerce")
    near_infra = satlas.notna() & (satlas <= L7_SATLAS_EXCLUDE_M)
    keep = q & never & ~near_infra
    counts = {"lights": int(len(lights)), "clear_lit_vessel_candidates": int(q.sum()), "clear_never_imaged_90d": int((q & never).sum()),
              "excluded_near_satlas_1km": int((q & never & near_infra).sum()), "kept": int(keep.sum())}
    return lights[keep].copy(), counts
