"""Pairing live-pass radar contacts with AIS vessels in dense traffic: contact time, SAR azimuth shift, gate,
minimum-cost assignment, ambiguity test.

Why not darkvessel.ais.match.match_detections alone. It places every vessel at one scene time, compares it with the
radar position as is, and takes the one-to-one assignment with the most pairs. In an anchorage where vessels lie
100 to 300 m apart, with ships under way passing through, three things go wrong: (1) a moving ship is imaged
displaced along the satellite track, by up to a kilometre, onto its anchored neighbours; (2) the most-pairs
assignment will pair a contact with a far vessel so that a second, closer pair can exist, which hands two wrong
identities out where one right one was available; (3) nothing marks a pair whose second-best alternative is as close
as the pair itself (two vessels rafted together, one radar return). This module keeps the placement of the AIS
vessels (ais.match.predict_positions: interpolation between the two reports around the scene time, else dead
reckoning from a report within 10 min) and the speed-aware gate (ais.match.gate_metres with rules.LIVE_MATCH), and
changes the pairing:

1. Time. Each contact is compared with the vessel's position at the contact's own zero-Doppler azimuth time (from
   the annotation geolocation grid, live.scene.sar_geometry), not the middle of the 25 s slice: a 20 kn ship moves
   up to 130 m in 12.5 s.
2. Azimuth shift. A target moving with slant-range rate v_r (positive away from the radar) is imaged displaced along
   the direction of satellite motion by dx = -(R / V) v_r (Raney 1971, doi:10.1109/TAES.1971.310292; R slant range,
   V platform speed). v_r = (ground velocity . range unit vector) x sin(incidence): the range unit vector points away
   from the ground track (Sentinel-1 looks right; the annotation grid gives both unit vectors per contact). The
   vessel velocity is its SOG and COG at the report nearest the scene time, else the track velocity between the
   two reports around it. The expected radar position of vessel j at contact i is its AIS position at i's time plus
   dx along i's azimuth direction; `match_dist_m` is the distance to that point and `match_dist_uncorr_m` the
   distance without the shift. Without the scene geometry (older checkpoints) the shift is 0.
3. Gate, pairing rules and assignment. A pair is feasible when match_dist_m is within the vessel's gate (500 m +
   125 s x SOG, at most 2000 m; unknown SOG and no track speed 2000 m) and it passes two physical tests
   (PAIRING_RULES_TEXT): the radar length is at least a quarter of the AIS length, and a fixed contact (a return that
   recurs on earlier passes) is never paired with a vessel under way (2 kn or more). The assignment minimises the summed
   distance of the pairs plus the gate of every AIS vessel left unpaired (scipy linear_sum_assignment on each connected
   group of feasible pairs, padded with "unpaired" columns and rows): equivalently it maximises the summed saving
   (gate - distance) of the pairs. Every feasible pair saves something, so two pairs can still beat one closer pair when
   their savings add up to more; such competing pairs are what the ambiguity test below holds back.
4. Ambiguity. b is "clearly worse" than a when b >= max(a + 100 m, 1.5 a). A pair (contact i, vessel j) at distance d
   is ambiguous when another feasible vessel k of contact i is not clearly worse than d and k has no pair of its own
   that is clearly better than (i, k), or when another feasible contact l of vessel j is not clearly worse than d and
   l has no pair of its own that is clearly better than (l, j). An ambiguous pair is not a match: the contact gets
   ais_status unmatched with match_ambiguous = true and the candidate MMSIs in ambiguous_mmsi, and it is never a dark
   lead (it is very likely one of those AIS vessels; which one cannot be told). The unpaired contacts that competed
   for the vessel get the same flag. The vessel, and every other candidate vessel left unpaired, goes to the AIS-only
   layer with ambiguous_det_id set, and the recall counts leave it out.
5. Oversized returns. The detector drops objects longer than 450 m as low ('oversized'); in a port these are often
   large ships whose bright return spreads through sidelobes. They are not contacts, but they take part in the pairing
   as candidate returns (`extra_returns`): a large vessel whose expected position lies on such a return pairs with it,
   so a faint return nearby cannot inherit its identity. A vessel paired with one stays in the AIS-only layer (no
   contact names it) with oversized_det_id set.

AIS-only vessels are the placed vessels inside `valid_area` (footprint and AOI) left unpaired; pairs are searched
over every placed vessel near the footprint, so a contact at the edge is not lost because its vessel's AIS position
lies a few hundred metres outside.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from darkvessel.ais.match import KN_TO_MS, MatchConfig, gate_metres, predict_positions
from darkvessel.config import CRS_GEO, DARK_CAVEAT
from darkvessel.live.rules import FIXED_MAX_SOG_KN, MIN_LENGTH_RATIO, OVERSIZED_DUPLICATE_M, live_match_quality

SAT_SPEED_MS = 7598.0      # Earth-fixed speed of Sentinel-1D from the orbit state vectors of a 2026-09-28 annotation
AMBIG_MARGIN_M = 100.0     # b is clearly worse than a when b >= max(a + margin, ratio x a)
AMBIG_RATIO = 1.5
_BIG = 1e12

AZIMUTH_TEXT = (
    "Each contact is compared with the AIS vessel's position at the contact's own azimuth time plus the SAR azimuth "
    "shift of a moving target, dx = -(R / V) x v_r along the direction of satellite motion (Raney 1971, "
    "doi:10.1109/TAES.1971.310292), with R the contact's slant range and V the platform speed from the product "
    "annotation, v_r = (vessel ground velocity . range unit vector) x sin(incidence), positive away from the radar, and "
    "the vessel velocity from SOG and COG of the report nearest the scene time (else the track between the two reports "
    "around it). match_dist_m is the distance to that expected radar position, match_dist_uncorr_m the distance "
    "without the shift, az_shift_m the shift (positive = along the satellite's direction of motion)."
)
ASSIGN_TEXT = (
    "Feasible pair: match_dist_m within the vessel's gate and the pairing rules (pairing_rules field). Assignment: minimum "
    "of the summed pair distances plus the gate of every AIS vessel left unpaired (Hungarian algorithm per connected group "
    "of feasible pairs), that is the maximum summed saving (gate - distance) of the pairs; competing pairs are then held "
    "back by the ambiguity test."
)
PAIRING_RULES_TEXT = (
    f"A pair is not feasible when the radar length estimate is less than {MIN_LENGTH_RATIO:g} times the AIS length (a return "
    "far shorter than the hull is not that hull; the pixel-extent estimate is biased long, not short), or "
    f"when the contact is fixed (a bright return at the same place on earlier passes) and the vessel moves at "
    f"{FIXED_MAX_SOG_KN:g} kn or more. Low objects the detector dropped for size (low_reason 'oversized', longer than 450 m: "
    "often a large ship with bright sidelobes) take part as candidate returns: an AIS vessel paired with one is not given "
    "to any contact and stays in the AIS-only layer with oversized_det_id. An oversized return within "
    f"{OVERSIZED_DUPLICATE_M:.0f} m of a contact is the same target seen in the other polarisation and is left out. Rules set "
    "by the hand check of the Pearl River pass of 2026-10-10 (docs/live_pass.md)."
)
AMBIGUITY_TEXT = (
    f"b is clearly worse than a when b >= max(a + {AMBIG_MARGIN_M:.0f} m, {AMBIG_RATIO:g} x a). A pair at distance d is "
    "ambiguous when the contact has another feasible AIS vessel not clearly worse than d that has no clearly better pair "
    "of its own, or the vessel has another feasible contact not clearly worse than d that has no clearly better pair of "
    "its own. An ambiguous pair is not a match: the contact (and any unpaired contact that competed for the vessel) is "
    "unmatched with match_ambiguous = true and the candidate MMSIs in ambiguous_mmsi, and is never a dark lead; the "
    "vessel and every other candidate vessel left unpaired go to the AIS-only layer with ambiguous_det_id and are left "
    "out of the recall counts."
)


def clearly_worse(a, b, margin: float = AMBIG_MARGIN_M, ratio: float = AMBIG_RATIO):
    """True where distance b is clearly worse than distance a (see AMBIGUITY_TEXT)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    return b >= np.maximum(a + margin, ratio * a)


def enu_to_grid(lon, lat, east, north, crs: str):
    """Rotate local east/north vector components into the grid axes of the projected `crs` (meridian convergence)."""
    from pyproj import Transformer

    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    east, north = np.asarray(east, float), np.asarray(north, float)
    if len(lon) == 0:
        return east.copy(), north.copy()
    tr = Transformer.from_crs(CRS_GEO, crs, always_xy=True)
    x0, y0 = tr.transform(lon, lat)
    x1, y1 = tr.transform(lon, lat + 1e-3)
    beta = np.arctan2(x1 - x0, y1 - y0)  # grid azimuth of true north
    cb, sb = np.cos(beta), np.sin(beta)
    return east * cb + north * sb, -east * sb + north * cb


def vessel_velocity(ais: pd.DataFrame, pred: pd.DataFrame, sar_time: pd.Timestamp, cfg: MatchConfig) -> pd.DataFrame:
    """Per placed vessel: vx, vy (m/s in cfg.utm_crs), speed_kn and velocity_source ('sog_cog', 'track' or None).

    SOG and COG of the report nearest `sar_time` when both are known and that report is within cfg.max_extrap_s;
    otherwise the track velocity between the last report before and the first after the scene time (each within
    cfg.max_gap_s); otherwise unknown.
    """
    from pyproj import Transformer

    out = pd.DataFrame({"mmsi": pred.mmsi.to_numpy() if len(pred) else np.array([], dtype="int64")})
    out["vx"], out["vy"], out["speed_kn"], out["velocity_source"] = np.nan, np.nan, np.nan, None
    if len(pred) == 0 or len(ais) == 0:
        return out
    a = ais[ais.mmsi.isin(set(pred.mmsi))].copy()
    a["dt_s"] = (a.timestamp - sar_time).dt.total_seconds()
    tr = Transformer.from_crs(CRS_GEO, cfg.utm_crs, always_xy=True)
    a["x"], a["y"] = tr.transform(a.lon.to_numpy(float), a.lat.to_numpy(float))
    a = a.sort_values(["mmsi", "timestamp"])
    rows, enu = {}, []
    for mmsi, g in a.groupby("mmsi"):
        near = g.iloc[int(np.argmin(np.abs(g.dt_s.to_numpy())))]
        sog = float(near.sog_kn) if "sog_kn" in g and pd.notna(near.get("sog_kn")) else np.nan
        cog = float(near.cog_deg) if "cog_deg" in g and pd.notna(near.get("cog_deg")) else np.nan
        if np.isfinite(sog) and np.isfinite(cog) and abs(near.dt_s) <= cfg.max_extrap_s:
            enu.append((mmsi, near.lon, near.lat, sog * KN_TO_MS * np.sin(np.radians(cog)), sog * KN_TO_MS * np.cos(np.radians(cog)), sog))
            continue
        before, after = g[g.dt_s <= 0], g[g.dt_s > 0]
        if len(before) and len(after) and -before.dt_s.iloc[-1] <= cfg.max_gap_s and after.dt_s.iloc[0] <= cfg.max_gap_s:
            b, f = before.iloc[-1], after.iloc[0]
            dt = float(f.dt_s - b.dt_s)
            if dt > 0:
                vx, vy = (f.x - b.x) / dt, (f.y - b.y) / dt
                rows[mmsi] = (float(vx), float(vy), float(np.hypot(vx, vy) / KN_TO_MS), "track")
    if enu:  # one projection call for every SOG/COG vessel
        e = np.array([r[1:5] for r in enu], float)
        gx, gy = enu_to_grid(e[:, 0], e[:, 1], e[:, 2], e[:, 3], cfg.utm_crs)
        for (mmsi, *_rest, sog), vx, vy in zip(enu, gx, gy):
            rows[mmsi] = (float(vx), float(vy), float(sog), "sog_cog")
    if rows:
        v = pd.DataFrame.from_dict(rows, orient="index", columns=["vx", "vy", "speed_kn", "velocity_source"])
        out = out.drop(columns=["vx", "vy", "speed_kn", "velocity_source"]).merge(v, left_on="mmsi", right_index=True, how="left")
    return out


def contact_geometry(contacts: pd.DataFrame, sar_time: pd.Timestamp, crs: str):
    """(dt_s from sar_time, slant range m, sin(incidence), az unit x/y, rg unit x/y in `crs`, has_geometry) per contact."""
    n = len(contacts)
    dt = np.zeros(n)
    has = np.zeros(n, bool)
    rng = np.full(n, np.nan)
    sin_inc = np.full(n, np.nan)
    az = np.zeros((n, 2))
    rg = np.zeros((n, 2))
    if n == 0:
        return dt, rng, sin_inc, az, rg, has
    if "az_time_utc" in contacts:
        t = pd.to_datetime(contacts.az_time_utc, utc=True, errors="coerce")
        ok = t.notna().to_numpy()
        dt[ok] = (t[ok] - sar_time).dt.total_seconds().to_numpy()
    cols = ["slant_range_m", "az_e", "az_n", "rg_e", "rg_n", "inc_angle_deg"]
    if all(c in contacts for c in cols):
        g = contacts[cols].apply(pd.to_numeric, errors="coerce").to_numpy(float)
        has = np.isfinite(g).all(axis=1)
        lon, lat = contacts.lon.to_numpy(float), contacts.lat.to_numpy(float)
        ax, ay = enu_to_grid(lon, lat, g[:, 1], g[:, 2], crs)
        rx, ry = enu_to_grid(lon, lat, g[:, 3], g[:, 4], crs)
        az[:] = np.c_[ax, ay]
        rg[:] = np.c_[rx, ry]
        rng[:] = g[:, 0]
        sin_inc[:] = np.sin(np.radians(g[:, 5]))
    az[~has] = 0.0
    rg[~has] = 0.0
    return dt, rng, sin_inc, az, rg, has


def expected_positions(cx_dt, rng, sin_inc, az, rg, px, py, vx, vy, sat_speed_ms: float, correct: bool = True, sign: float = 1.0):
    """(ex, ey, shift) arrays of shape (n_contacts, n_vessels): vessel j's expected radar position at contact i."""
    vx0, vy0 = np.nan_to_num(vx), np.nan_to_num(vy)
    ex = px[None, :] + vx0[None, :] * cx_dt[:, None]
    ey = py[None, :] + vy0[None, :] * cx_dt[:, None]
    shift = np.zeros_like(ex)
    if correct:
        v_rg = vx0[None, :] * rg[:, 0][:, None] + vy0[None, :] * rg[:, 1][:, None]
        v_r = v_rg * np.nan_to_num(sin_inc)[:, None]
        shift = sign * -(np.nan_to_num(rng) / float(sat_speed_ms))[:, None] * v_r
        ex = ex + shift * az[:, 0][:, None]
        ey = ey + shift * az[:, 1][:, None]
    return ex, ey, shift


def _assign_component(D: np.ndarray, feasible: np.ndarray, gate: np.ndarray) -> list[tuple[int, int]]:
    """Minimum of summed pair distances plus the gate of each unpaired vessel, over one group (see ASSIGN_TEXT)."""
    nc, nv = D.shape
    M = np.full((nc + nv, nv + nc), _BIG)
    M[:nc, :nv] = np.where(feasible, D, _BIG)
    M[np.arange(nc), nv + np.arange(nc)] = 0.0          # contact left unpaired: no cost
    M[nc + np.arange(nv), np.arange(nv)] = gate          # vessel left unpaired: its gate
    M[nc:, nv:] = 0.0
    r, c = linear_sum_assignment(M)
    return [(int(i), int(j)) for i, j in zip(r, c) if i < nc and j < nv and feasible[i, j]]


def feasible_pairs(D: np.ndarray, gate: np.ndarray, length_radar, length_ais, confidence, speed_kn) -> np.ndarray:
    """Feasible (contact, vessel) pairs: inside the vessel's gate and through the pairing rules (PAIRING_RULES_TEXT)."""
    feas = D <= np.asarray(gate, float)[None, :]
    lr = np.asarray(pd.to_numeric(pd.Series(length_radar), errors="coerce"), float)
    la = np.asarray(pd.to_numeric(pd.Series(length_ais), errors="coerce"), float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = lr[:, None] / np.where(la > 0, la, np.nan)[None, :]
    feas &= ~(np.isfinite(ratio) & (ratio < MIN_LENGTH_RATIO))
    fixed = np.asarray(pd.Series(confidence).astype(str) == "fixed", bool)
    moving = np.nan_to_num(np.asarray(pd.to_numeric(pd.Series(speed_kn), errors="coerce"), float)) >= FIXED_MAX_SOG_KN
    feas &= ~(fixed[:, None] & moving[None, :])
    return feas


def assign(contacts: pd.DataFrame, ais: pd.DataFrame, sar_time: pd.Timestamp, cfg: MatchConfig, valid_area=None,
           sat_speed_ms: float | None = None, correct: bool = True, extra_returns: pd.DataFrame | None = None):
    """Pair contacts (lon, lat, det_id, confidence, length_est_m and the live.scene geometry columns) with AIS vessels.

    `extra_returns` (same columns; the detector's oversized low objects) take part in the pairing as candidate returns
    but never receive an identity: a vessel paired with one goes to the AIS-only frame with oversized_det_id.
    Returns (contacts with pairing columns, ais_only frame in EPSG:4326 coordinates, diagnostics dict). Pairing
    columns: ais_status (matched or unmatched), mmsi, match_dist_m, match_dist_uncorr_m, az_shift_m, ais_method,
    ais_dt_s, match_gate_m, ais_sog_kn, ais_length_m, match_quality, match_ambiguous, ambiguous_mmsi,
    match_alt_dist_m, velocity_source, dark_candidate, caveat.
    """
    from pyproj import Transformer

    V = float(sat_speed_ms or SAT_SPEED_MS)
    det = contacts.copy().reset_index(drop=True)
    n = len(det)
    extra = pd.DataFrame(extra_returns).reset_index(drop=True) if extra_returns is not None and len(extra_returns) else pd.DataFrame()
    allc = pd.concat([det, extra], ignore_index=True) if len(extra) else det
    na = len(allc)
    pred = predict_positions(ais, sar_time, cfg)
    pred = pd.DataFrame(pred.drop(columns="geometry")) if len(pred) else pd.DataFrame(columns=["mmsi", "x", "y", "method", "dt_s", "sog_kn", "n_reports"])
    pred = pred.reset_index(drop=True)
    vel = vessel_velocity(ais, pred, sar_time, cfg) if len(pred) else pd.DataFrame(columns=["mmsi", "vx", "vy", "speed_kn", "velocity_source"])
    if len(pred):
        pred = pred.merge(vel, on="mmsi", how="left")
        # the gate uses the report's SOG, else the track speed
        pred["sog_gate_kn"] = pd.to_numeric(pred.sog_kn, errors="coerce").fillna(pred.speed_kn)
    m = len(pred)
    tr = Transformer.from_crs(CRS_GEO, cfg.utm_crs, always_xy=True)
    cx, cy = tr.transform(allc.lon.to_numpy(float), allc.lat.to_numpy(float)) if na else (np.array([]), np.array([]))
    cx, cy = np.asarray(cx, float), np.asarray(cy, float)
    dt_c, rng, sin_inc, az, rg, has_geo = contact_geometry(allc, sar_time, cfg.utm_crs)

    for c, v in (("ais_status", "unmatched"), ("ais_method", None), ("ambiguous_mmsi", None), ("velocity_source", None)):
        det[c] = v
    det["mmsi"] = pd.array([pd.NA] * n, dtype="Int64")
    for c in ("match_dist_m", "match_dist_uncorr_m", "az_shift_m", "ais_dt_s", "match_gate_m", "ais_sog_kn", "ais_length_m", "match_alt_dist_m"):
        det[c] = np.nan
    det["match_ambiguous"] = False
    diag = {"placed_vessels": int(m), "contacts": int(n), "extra_returns": int(len(extra)), "pairs": 0, "ambiguous_pairs": 0,
            "ambiguous_contacts": 0, "pairs_with_oversized": 0, "contacts_with_geometry": int(has_geo[:n].sum()),
            "azimuth_correction": bool(correct and has_geo[:n].any()), "sat_speed_ms": V}
    paired_vessel = np.full(m, -1)
    amb_vessel_det = np.full(m, None, dtype=object)
    oversized_det = np.full(m, None, dtype=object)
    if na and m:
        px, py = pred.x.to_numpy(float), pred.y.to_numpy(float)
        vx, vy = pred.vx.to_numpy(float), pred.vy.to_numpy(float)
        ex, ey, shift = expected_positions(dt_c, rng, sin_inc, az, rg, px, py, vx, vy, V, correct=correct)
        D = np.hypot(cx[:, None] - ex, cy[:, None] - ey)
        ux = px[None, :] + np.nan_to_num(vx)[None, :] * dt_c[:, None]
        uy = py[None, :] + np.nan_to_num(vy)[None, :] * dt_c[:, None]
        D_unc = np.hypot(cx[:, None] - ux, cy[:, None] - uy)
        gate = gate_metres(pred.assign(sog_kn=pred.sog_gate_kn), cfg)
        ais_len = pd.to_numeric(pred.length_m, errors="coerce").to_numpy(float) if "length_m" in pred else np.full(m, np.nan)
        feas = feasible_pairs(D, gate, allc.get("length_est_m", pd.Series(np.nan, index=allc.index)).to_numpy(),
                              ais_len, allc.get("confidence", pd.Series("", index=allc.index)).to_numpy(), pred.sog_gate_kn.to_numpy())
        pairs: list[tuple[int, int]] = []
        ii, jj = np.nonzero(feas)
        if len(ii):
            adj = coo_matrix((np.ones(len(ii)), (ii, na + jj)), shape=(na + m, na + m))
            _, lab = connected_components(adj, directed=False)
            for comp in np.unique(lab[ii]):
                ci = np.nonzero(lab[:na] == comp)[0]
                vj = np.nonzero(lab[na:] == comp)[0]
                for a, b in _assign_component(D[np.ix_(ci, vj)], feas[np.ix_(ci, vj)], gate[vj]):
                    pairs.append((int(ci[a]), int(vj[b])))
        partner_c = np.full(na, -1)
        partner_v = np.full(m, -1)
        for i, j in pairs:
            partner_c[i], partner_v[j] = j, i
        # ambiguity test (over contacts and oversized returns alike)
        amb_pair = []
        amb_cands: dict[int, set] = {}
        competing_contacts: dict[int, list[int]] = {}
        alt_dist: dict[int, float] = {}
        for i, j in pairs:
            d = D[i, j]
            alts = []
            cand = set()
            for k in np.nonzero(feas[i])[0]:
                if k == j or clearly_worse(d, D[i, k]):
                    continue
                l = partner_v[k]
                if l >= 0 and clearly_worse(D[l, k], D[i, k]):
                    continue  # k is explained by its own, clearly better pair
                alts.append(D[i, k])
                cand.add(int(k))
            comp_c = []
            for l in np.nonzero(feas[:, j])[0]:
                if l == i or clearly_worse(d, D[l, j]):
                    continue
                k = partner_c[l]
                if k >= 0 and clearly_worse(D[l, k], D[l, j]):
                    continue
                alts.append(D[l, j])
                comp_c.append(int(l))
            if alts:
                amb_pair.append((i, j))
                amb_cands[i] = cand | {int(j)}
                competing_contacts[i] = comp_c
                alt_dist[i] = round(float(min(alts)), 1)
        amb_set = set(amb_pair)
        for i, j in pairs:
            if (i, j) in amb_set:
                continue
            paired_vessel[j] = i
            if i >= n:  # an oversized return: the vessel is accounted for, no contact is named
                oversized_det[j] = allc.det_id.iloc[i] if "det_id" in allc else str(i)
                diag["pairs_with_oversized"] += 1
                continue
            det.loc[i, "ais_status"] = "matched"
            det.loc[i, "mmsi"] = int(pred.mmsi.iloc[j])
            det.loc[i, "match_dist_m"] = float(D[i, j])
            det.loc[i, "match_dist_uncorr_m"] = round(float(D_unc[i, j]), 1)
            det.loc[i, "az_shift_m"] = round(float(shift[i, j]), 1)
            det.loc[i, "ais_method"] = pred.method.iloc[j]
            det.loc[i, "ais_dt_s"] = float(pred.dt_s.iloc[j])
            det.loc[i, "match_gate_m"] = float(gate[j])
            det.loc[i, "ais_sog_kn"] = float(pred.sog_gate_kn.iloc[j]) if pd.notna(pred.sog_gate_kn.iloc[j]) else np.nan
            det.loc[i, "velocity_source"] = pred.velocity_source.iloc[j]
            det.loc[i, "ais_length_m"] = ais_len[j]
        for i, j in amb_pair:
            others = [i] + [l for l in competing_contacts.get(i, []) if partner_c[l] < 0]
            mm = {str(int(pred.mmsi.iloc[k])) for k in amb_cands[i]}
            for l in others:
                if l >= n:
                    continue  # oversized returns are not in the contacts table
                det.loc[l, "match_ambiguous"] = True
                prev = set(str(det.loc[l, "ambiguous_mmsi"]).split(";")) if isinstance(det.loc[l, "ambiguous_mmsi"], str) else set()
                det.loc[l, "ambiguous_mmsi"] = ";".join(sorted(prev | mm))
                if l == i:
                    det.loc[l, "match_alt_dist_m"] = alt_dist[i]
                elif not np.isfinite(det.loc[l, "match_alt_dist_m"]):
                    det.loc[l, "match_alt_dist_m"] = round(float(D[l, j]), 1)
            ref = others[0] if others[0] < n else next((l for l in others if l < n), None)
            label = (det.det_id.iloc[ref] if "det_id" in det else str(ref)) if ref is not None else (allc.det_id.iloc[i] if "det_id" in allc else str(i))
            for k in amb_cands[i]:  # every candidate vessel left unpaired is "held back", not missed
                if paired_vessel[k] < 0 and amb_vessel_det[k] is None:
                    amb_vessel_det[k] = label
        diag["pairs"] = int(len(pairs) - len(amb_pair) - diag["pairs_with_oversized"])
        diag["ambiguous_pairs"] = int(len(amb_pair))
        diag["ambiguous_contacts"] = int(det.match_ambiguous.sum())
        g = np.arange(na) < n  # the azimuth check uses the contacts only
        diag["azimuth_check"] = azimuth_check(dt_c[g], rng[g], sin_inc[g], az[g], rg[g], has_geo[g], cx[g], cy[g], pred, V, cfg)
    det["match_quality"] = live_match_quality(det.match_dist_m, det.ais_dt_s, det.ais_sog_kn, det.get("length_est_m"), det.get("ais_length_m"))
    vessel_like = det["confidence"].isin(["high", "medium"]) if "confidence" in det else True
    det["dark_candidate"] = (det.ais_status == "unmatched") & vessel_like & ~det.match_ambiguous
    det["caveat"] = DARK_CAVEAT

    # AIS-only: placed vessels inside the tested area that no contact took (oversized pairs included, marked)
    unp = (paired_vessel < 0) | pd.notna(oversized_det)
    ao = pred[unp].copy() if m else pred.copy()
    ao["ambiguous_det_id"] = amb_vessel_det[unp] if m else None
    ao["oversized_det_id"] = oversized_det[unp] if m else None
    if len(ao):
        inv = Transformer.from_crs(cfg.utm_crs, CRS_GEO, always_xy=True)
        ao["lon"], ao["lat"] = inv.transform(ao.x.to_numpy(float), ao.y.to_numpy(float))
        if valid_area is not None:
            import shapely

            ao = ao[shapely.contains_xy(valid_area, ao.lon.to_numpy(float), ao.lat.to_numpy(float))]
    ao = ao.reset_index(drop=True)
    diag["ais_only"] = int(len(ao))
    diag["ais_only_ambiguous"] = int(ao.ambiguous_det_id.notna().sum()) if len(ao) else 0
    diag["ais_only_oversized"] = int(ao.oversized_det_id.notna().sum()) if len(ao) else 0
    return det, ao, diag


def azimuth_check(dt_c, rng, sin_inc, az, rg, has_geo, cx, cy, pred: pd.DataFrame, V: float, cfg: MatchConfig,
                  min_shift_m: float = 150.0, max_dist_m: float = 2000.0) -> dict:
    """Is the azimuth correction borne out by the scene? For every AIS vessel whose predicted shift is at least
    `min_shift_m`, the nearest contact (within `max_dist_m`) to its expected position without the shift, with it, and
    with the sign flipped. The version whose nearest contacts lie closest is the one the radar agrees with. This does
    not use the assignment, so it cannot favour the correction the assignment used. Also per vessel (those with a
    contact within `max_dist_m` in all three versions): how many have their nearest contact under each version.
    A scene whose vessels have no contact within `max_dist_m` (for example a slice whose tested water lies kilometres
    from every AIS vessel) reports null medians."""
    out = {"min_shift_m": min_shift_m, "vessels": 0}
    if not has_geo.any() or len(pred) == 0:
        return out
    px, py = pred.x.to_numpy(float), pred.y.to_numpy(float)
    vx, vy = pred.vx.to_numpy(float), pred.vy.to_numpy(float)
    g = np.nonzero(has_geo)[0]
    res = {}
    for name, corr, sign in (("uncorrected", False, 1.0), ("corrected", True, 1.0), ("sign_flipped", True, -1.0)):
        ex, ey, shift = expected_positions(dt_c[g], rng[g], sin_inc[g], az[g], rg[g], px, py, vx, vy, V, correct=corr, sign=sign)
        D = np.hypot(cx[g][:, None] - ex, cy[g][:, None] - ey)
        res[name] = (D, shift)
    _, shift = res["corrected"]
    # the shift a vessel would have at its nearest contact (uncorrected geometry)
    i_near = np.argmin(res["uncorrected"][0], axis=0)
    s_j = np.abs(shift[i_near, np.arange(len(px))])
    sel = np.isfinite(s_j) & (s_j >= min_shift_m) & np.isfinite(vx)
    out["vessels"] = int(sel.sum())
    if not sel.any():
        return out
    dmins = {}
    for name, (D, _) in res.items():
        dmin = D[:, sel].min(axis=0)
        dmins[name] = dmin
        ok = dmin <= max_dist_m
        out[f"{name}_median_nearest_m"] = round(float(np.median(dmin[ok])), 1) if ok.any() else None
        out[f"{name}_within_200m"] = int((dmin <= 200).sum())
    out["median_predicted_shift_m"] = round(float(np.median(s_j[sel])), 1)
    # paired reading: per vessel, which version puts a contact closest (vessels with a contact within max_dist_m in all three)
    stack = np.vstack([dmins[k] for k in ("uncorrected", "corrected", "sign_flipped")])
    both = (stack <= max_dist_m).all(axis=0)
    out["vessels_compared"] = int(both.sum())
    best = np.argmin(stack[:, both], axis=0) if both.any() else np.array([], int)
    out["closest_uncorrected"], out["closest_corrected"], out["closest_sign_flipped"] = (int((best == q).sum()) for q in range(3))
    return out
