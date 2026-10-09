"""AIS status and identity of radar contacts from Global Fishing Watch data. Research build only (CC BY-NC 4.0).

What this module does
---------------------
Gives every contact of a radar run (the September regional run, data/detections_regional.gpkg) one of three AIS
statuses, and an identity where one can be found, from two GFW products pulled by scripts/27_gfw_pull.py and
scripts/31_gfw_identity.py (cache under data/cache/gfw/):

* GFW SAR detections (dataset public-global-sar-presence): GFW's own Sentinel-1 detections on the same scenes, with
  GFW's own AIS match. The 4Wings report gives one row per 0.01 degree cell and hour with the detection time
  (entryTimestamp) and, for GFW-matched detections, the vessel id, MMSI, ship name, call sign, IMO, flag and type.
* GFW AIS presence (dataset public-global-presence): hours of AIS presence per 0.01 degree cell and hour grouped by
  vessel id, requested per radar pass over the pass footprint for the pass hour and its neighbours; and per day at
  0.1 degree over the whole window for the coverage rule.

GFW reports are cell counts, never vessel positions, so every match here is by cell and hour. Pure functions only;
the IO is in scripts/31_gfw_identity.py.

Rules (D1 schema of docs/PROJECT_BOARD.md; the same text goes into the output's 'about' layer)
---------------------------------------------------------------------------------------------
(a) gfw_sar_cell_hour. A contact and a GFW SAR detection pair when the detection lies in the contact's 0.01 degree
    cell or one of its 8 neighbours and within 30 minutes of the contact's scene time. Pairs are made one to one,
    cheapest first (cost = distance to the GFW cell centre in km, plus |log2(radar length / AIS length)| when GFW
    publishes a registry length). A pair with a GFW-matched detection gives the identity. match_quality: high when
    the pair is one to one (the contact had one candidate and the detection one rival) within 1 km and the lengths
    do not disagree by more than a factor 2; low when both sides were ambiguous (several candidates and several
    rivals) or the lengths disagree by more than a factor 3; medium otherwise. A pair with a GFW-unmatched detection
    is evidence (gfw_sar_pair = unmatched: GFW saw the same object and found no AIS for it), not a match.
(b) gfw_presence_cell_hour, only for contacts with no GFW SAR pair. GFW's HOURLY by-vessel presence report gives one
    cell per vessel and hour, so a moving vessel's cell can lie far from where it was at the scene time. A vessel is a
    candidate when one of its cells in the pass hour or the hour before or after lies within 6 km; its position at the
    scene time is interpolated along its hourly cells and its speed proxy is the largest hourly displacement. The
    candidate is kept when the interpolated position is within 3 km and the vessel has at least two hourly cells with
    a speed proxy of at most 10 km/h (single-cell and faster vessels are rejected). GEAR buoys and vessels that GFW's
    SAR matcher already placed in the same pass are excluded. One to one per vessel, cheapest first (interpolated km
    + speed/10). match_quality: medium when one to one, within 1 km, speed at most 3 km/h and the vessel has a cell
    in the contact's hour; low otherwise. The thresholds come from a calibration on the rule (a) matches
    (presence_calibration, numbers in the summary and in docs/gfw_identity.md): the interpolated position of a vessel
    at or under 3 km/h lies within 1 km of the contact in 86 % of cases and within 3 km in 97 %; 3 to 10 km/h, 33 % and
    82 %; over 10 km/h, 10 % and 39 %; single-cell vessels, 26 % and 44 %.
(c) ais_status: matched when (a) or (b) gives a vessel; otherwise no_coverage when GFW AIS presence shows zero hours
    in the 0.3 x 0.3 degree block (the contact's 0.1 degree cell and its 8 neighbours) over the whole pull window;
    otherwise unmatched. ais_presence_h_day and ais_presence_h_window carry the block hours on the pass day and over
    the window, so the rule can be re-cut.
(d) Evidence for every contact: nearest GFW AIS vessel in the pass window (nearest_ais_*; the time offset is the
    hour-bucket offset), distinct AIS vessels within 10 km in the pass hour (n_ais_10km), GFW gap events within
    50 km and 24 h, GFW encounter and loitering events within 10 km and 24 h, GFW's neural vessel type at the cell,
    and ais_reach = share of window days with any AIS presence in the contact's 0.25 degree cell. identity_kind says
    whether a matched identity is a vessel, a GEAR buoy (an AIS net or gear buoy; it is not the vessel's identity and
    the match is low quality) or of unknown type.

Cells follow GFW's grid: a report value is the cell centre, cell k spans [(k - 0.5) res, (k + 0.5) res).

Dark means only "no AIS match". AIS is not compulsory for many vessels, can be off lawfully, and GFW's AIS feed has
blind spots; GFW's products are another model's output. An AIS gap is not proof of intent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from darkvessel.ais import gfw as G
from darkvessel.ais.gfw_compare import REGIONS, chord_to_m, haversine_m, in_box, length_bin, m_to_chord, spearman, unit_vectors
from darkvessel.config import DARK_CAVEAT

RUN_ID = "regional_2026-09"
RES = 0.01            # GFW HIGH cell, degrees
RES_LOW = 0.1         # GFW LOW cell
RES_REACH = 0.25      # ais_reach cell (the project's model grid)
MAX_DT_SAR_S = 1800
MAX_DH_PRESENCE = 1
CAND_RADIUS_M = 6000.0            # rule (b): a vessel's hourly cell this close makes it a candidate
MAX_DIST_PRESENCE_M = 3000.0      # rule (b): interpolated position must be this close to the contact
MAX_SPEED_PRESENCE_KMH = 10.0     # rule (b): faster vessels are rejected (their hourly cell says little about the position)
SLOW_KMH = 3.0                    # rule (b): at most this speed for a medium match (near-stationary)
GEAR_TYPES = {"GEAR"}             # GFW vessel type of AIS net and gear buoys: not a vessel identity
MATCH_SAR = "gfw_sar_cell_hour"
MATCH_PRESENCE = "gfw_presence_cell_hour"
IDENTITY_SOURCE = "GFW 4Wings report vessel fields (AIS self-reported identity as GFW publishes it) + GFW vessels API registry fields"

D1_COLUMNS = ["det_id", "run_id", "mission", "acq_utc", "lon", "lat", "length_est_m", "confidence", "cnn_score", "cnn_vessel",
              "ais_status", "ais_source", "match_method", "match_dist_m", "match_dt_s", "match_quality",
              "mmsi", "imo", "vessel_name", "call_sign", "flag", "ship_type", "length_ais_m", "identity_source", "gfw_vessel_id",
              "nearest_ais_mmsi", "nearest_ais_dist_m", "nearest_ais_dt_s", "n_ais_10km", "ais_reach", "research_only", "caveat"]
EVIDENCE_COLUMNS = ["pass_id", "identity_kind", "gfw_sar_pair", "gfw_sar_n_cand", "gfw_sar_n_rivals", "gfw_sar_ambiguous_cell", "gfw_geartype",
                    "gfw_neural_type", "pres_speed_kmh", "pres_n_cells", "pres_n_cand", "n_gear_10km", "ais_presence_h_day", "ais_presence_h_window",
                    "nearest_ais_vessel_id", "nearest_ais_name", "n_gfw_gaps_50km_24h", "nearest_gfw_gap_km",
                    "n_gfw_encounters_10km_24h", "n_gfw_loitering_10km_24h"]

RULES = {
    "a_sar": ("gfw_sar_cell_hour: GFW SAR detection in the contact's 0.01 degree cell or one of the 8 neighbours, within "
              "30 min of the scene time; one-to-one, cheapest first (km to the cell centre + |log2 length ratio| when a "
              "registry length exists). Identity from GFW-matched detections only; a GFW-unmatched pair is evidence."),
    "b_presence": ("gfw_presence_cell_hour: for contacts with no SAR pair. GFW's HOURLY by-vessel presence gives one 0.01 degree "
                   "cell per vessel and hour; a vessel is a candidate when a cell of its in the pass hour or the hour before or "
                   "after lies within 6 km. Its position at the scene time is interpolated along its hourly cells (each at the "
                   "hour's midpoint) and its speed proxy is the largest hourly displacement. Kept when the interpolated position "
                   "is within 3 km and the vessel has at least two hourly cells with a speed proxy of at most 10 km/h (single-cell "
                   "vessels and faster vessels are rejected); GEAR buoys and vessels GFW SAR already matched in the pass are "
                   "excluded; cost = interpolated km + speed/10; one-to-one per vessel. "
                   "medium: one-to-one, within 1 km, speed known and at most 3 km/h, cell in the contact's hour; low otherwise. "
                   "match_dist_m is the interpolated distance; match_dt_s the hour offset of the nearest cell."),
    "c_status": ("matched when (a) or (b) gives a vessel; no_coverage when GFW AIS presence shows zero hours in the 0.3 x 0.3 "
                 "degree block around the contact over the whole pull window; unmatched otherwise (ais_presence_h_day = 0 marks "
                 "the unmatched contacts whose block had no AIS presence on the pass day itself)."),
    "quality_sar": ("high: one-to-one, within 1 km, lengths within a factor 2 (or no AIS length); low: ambiguous on both sides, "
                    "lengths off by more than a factor 3, identity taken from a cell with several GFW-matched detections "
                    "(gfw_sar_ambiguous_cell), or a GEAR buoy identity (identity_kind = gear); medium: everything else."),
    "identity_kind": ("vessel: GFW publishes a vessel type other than GEAR; gear: GFW types the matched AIS device as GEAR (an AIS net "
                      "or gear buoy, which can sit on or near the vessel tending it but is not that vessel's identity); unknown: no "
                      "type published; empty when unmatched."),
    "sar_pair_values": ("gfw_sar_pair: matched = paired with a GFW-matched detection carrying an identity; matched_no_identity = "
                        "GFW matched it but the report gives no usable identity (several detections in the cell, no by-vessel "
                        "rows), rule (b) is then tried; unmatched = paired with a detection GFW found no AIS for, rule (b) is not "
                        "tried; empty = no GFW SAR detection in the block within 30 min."),
    "d_evidence": ("nearest GFW AIS vessel in the pass window (hour-bucket offset; GEAR buoys excluded), distinct vessels within "
                   "10 km in the pass hour (n_ais_10km) and GEAR buoys within 10 km (n_gear_10km), GFW gap events within 50 km and "
                   "24 h, encounters and loitering within 10 km and 24 h, GFW neural vessel type within 1 km on the same date, "
                   "ais_reach = share of window days with AIS presence in the 0.25 degree cell."),
}


# -- passes -----------------------------------------------------------------------------------------------------
def passes_from_scenes(scenes, gap_s: float = 1200):
    """Group the processed scenes into passes: same mission, consecutive start times less than `gap_s` apart.

    scenes: GeoDataFrame with mission, start_utc, scene_idx and footprints. Returns a GeoDataFrame with pass_id
    (<mission>_<yyyymmddThhmm> of the first scene), mission, t0, t1, n_scenes, scene_idx (list) and the union footprint.
    """
    sc = scenes.copy()
    sc["t"] = pd.to_datetime(sc.start_utc, utc=True)
    sc = sc.sort_values(["t", "scene_idx"]).reset_index(drop=True)
    gap = sc.t.diff().dt.total_seconds().fillna(np.inf)
    new = (gap > gap_s) | (sc.mission != sc.mission.shift())
    sc["grp"] = new.cumsum()
    rows = []
    for _, g in sc.groupby("grp", sort=True):
        t0, t1 = g.t.min(), g.t.max()
        rows.append({"pass_id": f"{g.mission.iloc[0]}_{t0:%Y%m%dT%H%M}", "mission": g.mission.iloc[0], "t0": t0, "t1": t1,
                     "n_scenes": int(len(g)), "scene_idx": sorted(int(x) for x in g.scene_idx),
                     "geometry": g.geometry.union_all() if hasattr(g.geometry, "union_all") else g.geometry.unary_union})
    import geopandas as gpd
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=scenes.crs)


def pass_window(t0, t1, before_h: int = 1, after_h: int = 2) -> tuple[str, str]:
    """ISO date-range of a pass for the presence report: the hour of t0 minus `before_h` to the hour of t1 plus
    `after_h` (exclusive), so the report holds the pass hour and one hour on each side."""
    a = pd.Timestamp(t0).floor("h") - pd.Timedelta(hours=before_h)
    b = pd.Timestamp(t1).floor("h") + pd.Timedelta(hours=after_h)
    return a.strftime("%Y-%m-%dT%H:%M:%S.000Z"), b.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def assign_pass(contacts: pd.DataFrame, passes) -> pd.Series:
    """pass_id of each contact through its scene_idx (exact: a contact's acq_utc is its scene's start time)."""
    lookup = {int(i): p.pass_id for p in passes.itertuples() for i in p.scene_idx}
    return contacts.scene_idx.map(lambda i: lookup.get(int(i)))


# -- cells ------------------------------------------------------------------------------------------------------
def cell_ij(lon, lat, res: float):
    """Integer column and row of the cell of size `res` that holds each point, on GFW's grid: cell k is centred on
    k * res and spans [(k - 0.5) res, (k + 0.5) res). GFW reports give cell centres, so a GFW row maps to its own
    cell and a contact to the GFW cell that holds it (darkvessel.ais.gfw.cell_key, same convention)."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    return np.floor(lon / res + 0.5).astype(np.int64), np.floor(lat / res + 0.5).astype(np.int64)


def block_join(a: pd.DataFrame, b: pd.DataFrame, res: float, keys: list[str] | None = None) -> pd.DataFrame:
    """Pairs (ia, ib) of rows of `a` and `b` whose cells are the same or neighbours (3 x 3 block), optionally also
    equal on `keys`. ia and ib are positional indices. dist_m is the great-circle distance between the rows."""
    keys = keys or []
    if a.empty or b.empty:
        return pd.DataFrame(columns=["ia", "ib", "dist_m"])
    ax, ay = cell_ij(a.lon.values, a.lat.values, res)
    bx, by = cell_ij(b.lon.values, b.lat.values, res)
    af = pd.DataFrame({"ia": np.arange(len(a)), "cx": ax, "cy": ay, **{k: a[k].values for k in keys}})
    bf = pd.DataFrame({"ib": np.arange(len(b)), "cx": bx, "cy": by, **{k: b[k].values for k in keys}})
    out = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            s = af.assign(cx=af.cx + dx, cy=af.cy + dy)
            out.append(s.merge(bf, on=["cx", "cy"] + keys, how="inner")[["ia", "ib"]])
    p = pd.concat(out, ignore_index=True)
    if p.empty:
        return pd.DataFrame(columns=["ia", "ib", "dist_m"])
    p["dist_m"] = haversine_m(a.lon.values[p.ia], a.lat.values[p.ia], b.lon.values[p.ib], b.lat.values[p.ib])
    return p


def assign_one_to_one(pairs: pd.DataFrame) -> pd.DataFrame:
    """Greedy one-to-one assignment, cheapest pair first. `pairs` has ia, ib, cost. Returns the chosen pairs with
    n_cand (how many b candidates ia had) and n_rivals (how many a candidates ib had)."""
    if pairs.empty:
        return pd.DataFrame(columns=["ia", "ib", "cost", "n_cand", "n_rivals"])
    p = pairs.sort_values(["cost", "ia", "ib"], kind="mergesort").reset_index(drop=True)
    n_cand = p.groupby("ia").ib.transform("size")
    n_rivals = p.groupby("ib").ia.transform("size")
    used_a, used_b, keep = set(), set(), np.zeros(len(p), bool)
    for k, (ia, ib) in enumerate(zip(p.ia.values, p.ib.values)):
        if ia in used_a or ib in used_b:
            continue
        used_a.add(ia), used_b.add(ib)
        keep[k] = True
    out = p[keep].copy()
    out["n_cand"], out["n_rivals"] = n_cand[keep].values, n_rivals[keep].values
    return out.reset_index(drop=True)


# -- GFW SAR detections -----------------------------------------------------------------------------------------
_ID_FIELDS = {"vesselId": "vessel_id", "mmsi": "mmsi", "shipName": "ship_name", "callsign": "call_sign", "imo": "imo",
              "flag": "flag", "vesselType": "gfw_vessel_type", "geartype": "gfw_geartype"}


def _identity_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for src, dst in _ID_FIELDS.items():
        if src in df:
            keep = (df[src].notna() & (df[src].astype(str) != "")).values
            out[dst] = np.where(keep, df[src].astype(object).values, None)
        else:
            out[dst] = None
    return out


def sar_detections(hourly: pd.DataFrame, by_vessel: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per GFW SAR detection from the HOURLY report frames (report_to_frame output with a `matched` column).

    Cells with n detections become n rows at the cell centre. ts is GFW's entryTimestamp (the scene time), with the
    report hour as fallback. Identity fields come from the row for matched cells that hold one detection. A matched
    cell with several detections carries one identity for several objects, so its identities are taken instead from
    the DAILY report grouped by vessel id (`by_vessel`, report_to_frame output: one row per vessel and cell and day)
    when that report lists as many vessels in the cell on that day; those rows are flagged ambiguous_cell, because
    which object is which vessel cannot be told. Without `by_vessel` such rows keep no identity.
    """
    cols = ["lon", "lat", "ts", "date", "hour", "matched", "n_in_cell", "ambiguous_cell"] + list(_ID_FIELDS.values())
    if hourly is None or hourly.empty:
        return pd.DataFrame(columns=cols)
    h = hourly.copy()
    n = pd.to_numeric(h.detections, errors="coerce").fillna(1).astype(int).clip(lower=1).values
    hour_ts = pd.to_datetime(h.date, utc=True, errors="coerce")
    ts = pd.to_datetime(h.entryTimestamp, utc=True, errors="coerce") if "entryTimestamp" in h else hour_ts
    ts = ts.fillna(hour_ts)
    ident = _identity_columns(h)
    matched = h.matched.astype(bool).values
    single = n == 1
    for c in ident.columns:
        ident[c] = np.where(matched & single, ident[c].values, None)
    base = pd.DataFrame({"lon": h.lon.values, "lat": h.lat.values, "ts": ts.values, "date": ts.dt.strftime("%Y-%m-%d").values,
                         "hour": ts.dt.hour.values, "matched": matched, "n_in_cell": n, "ambiguous_cell": False})
    base = pd.concat([base, ident.reset_index(drop=True)], axis=1)
    out = base.loc[np.repeat(np.arange(len(base)), n)].reset_index(drop=True)
    out["ts"] = pd.to_datetime(out.ts, utc=True)
    multi = out.matched.values & (out.n_in_cell.values > 1)
    if by_vessel is not None and not by_vessel.empty and multi.any() and "vesselId" in by_vessel:
        bv = by_vessel[by_vessel.vesselId.notna() & (by_vessel.vesselId.astype(str) != "")].copy()
        bv["date"] = pd.to_datetime(bv.date, utc=True, errors="coerce").dt.strftime("%Y-%m-%d")
        bx, by_ = cell_ij(bv.lon.values, bv.lat.values, RES)
        bv["key"] = [f"{x}_{y}_{d}" for x, y, d in zip(bx, by_, bv.date.values)]
        bv = bv.sort_values(["key", "vesselId"]).drop_duplicates(["key", "vesselId"])
        bv_ident = _identity_columns(bv)
        groups = {k: idx for k, idx in bv.groupby("key").indices.items()}
        ox, oy = cell_ij(out.lon.values, out.lat.values, RES)
        okey = np.array([f"{x}_{y}_{d}" for x, y, d in zip(ox, oy, out.date.values)], dtype=object)
        for key in np.unique(okey[multi]):
            rows = np.flatnonzero(multi & (okey == key))
            cand = groups.get(key)
            if cand is None or len(cand) < len(rows):
                continue
            for r, ci in zip(rows, cand[:len(rows)]):
                for c in ident.columns:
                    out.at[r, c] = bv_ident[c].values[ci]
                out.at[r, "ambiguous_cell"] = True
    out["ambiguous_cell"] = out.ambiguous_cell.astype(bool)
    return out[cols]


def shift_cells(frame: pd.DataFrame, d_deg: float) -> pd.DataFrame:
    """Copy of a GFW cell frame with lon and lat moved by `d_deg` (to test the reading of GFW's cell coordinate)."""
    f = frame.copy()
    f["lon"], f["lat"] = f.lon + d_deg, f.lat + d_deg
    return f


def alignment_check(contacts: pd.DataFrame, sar: pd.DataFrame, max_dt_s: float = MAX_DT_SAR_S) -> dict:
    """Which reading of GFW's cell coordinate puts our contacts inside the GFW detection's cell?

    report_to_frame reads the value as the cell centre (the docs' reading). The other reading, south-west corner,
    moves every cell by half a step. Both are scored on the contacts paired with a GFW detection of the same pass
    (3 x 3 block, 30 min, nearest detection per contact): the share of pairs whose contact lies within half a cell
    of the GFW coordinate in both axes, the median lon and lat offset, and the median distance. The reading with
    the larger within-half-cell share wins (smaller median distance breaks a tie). The data decide; the result is
    reported in the summary and the about layer.
    """
    out = {}
    for name, d in (("value_is_centre", 0.0), ("corner_plus_half", RES / 2)):
        s = shift_cells(sar, d)
        p = block_join(contacts, s, RES)
        if p.empty:
            out[name] = {"pairs": 0}
            continue
        dt = np.abs((s.ts.values[p.ib] - contacts.ts.values[p.ia]).astype("timedelta64[s]").astype(float))
        p = p[dt <= max_dt_s]
        p = p.sort_values("dist_m").drop_duplicates("ia")
        dlon = contacts.lon.values[p.ia] - s.lon.values[p.ib]
        dlat = contacts.lat.values[p.ia] - s.lat.values[p.ib]
        within = (np.abs(dlon) <= RES / 2) & (np.abs(dlat) <= RES / 2)
        out[name] = {"pairs": int(len(p)), "within_half_cell_share": round(float(within.mean()), 4) if len(p) else None,
                     "median_dlon_deg": round(float(np.median(dlon)), 4), "median_dlat_deg": round(float(np.median(dlat)), 4),
                     "median_dist_m": round(float(p.dist_m.median()), 1)}
    scored = [k for k in out if out[k].get("pairs")]
    best = max(scored, key=lambda k: (out[k]["within_half_cell_share"], -out[k]["median_dist_m"]), default="value_is_centre")
    out["chosen"] = best
    out["shift_applied_deg"] = 0.0 if best == "value_is_centre" else RES / 2
    return out


# -- (a) pairing with GFW SAR detections ------------------------------------------------------------------------
def length_penalty(len_radar, len_ais) -> np.ndarray:
    """|log2(radar / AIS)| where both lengths are known and positive, else 0."""
    a, b = np.asarray(len_radar, float), np.asarray(len_ais, float)
    ok = np.isfinite(a) & np.isfinite(b) & (a > 0) & (b > 0)
    out = np.zeros(len(a))
    out[ok] = np.abs(np.log2(a[ok] / b[ok]))
    return out


def pair_sar(contacts: pd.DataFrame, sar: pd.DataFrame, vessel_length: pd.Series | None = None,
             max_dt_s: float = MAX_DT_SAR_S) -> pd.DataFrame:
    """Rule (a). contacts: lon, lat, ts (UTC), length_est_m. sar: output of sar_detections (optionally shifted).

    Returns one row per contact (positional order) with gfw_sar_idx (row of `sar`, -1 when none), gfw_sar_pair
    (matched, unmatched, None), match_dist_m, match_dt_s, gfw_sar_n_cand, gfw_sar_n_rivals and length_ais_m.
    """
    n = len(contacts)
    out = pd.DataFrame({"gfw_sar_idx": np.full(n, -1), "gfw_sar_pair": [None] * n, "match_dist_m": np.full(n, np.nan),
                        "match_dt_s": np.full(n, np.nan), "gfw_sar_n_cand": np.zeros(n, int), "gfw_sar_n_rivals": np.zeros(n, int),
                        "length_ais_m": np.full(n, np.nan), "gfw_sar_ambiguous_cell": np.zeros(n, bool)})
    if n == 0 or sar.empty:
        return out
    p = block_join(contacts, sar, RES)
    if p.empty:
        return out
    dt = (sar.ts.values[p.ib] - contacts.ts.values[p.ia]).astype("timedelta64[s]").astype(float)
    p = p[np.abs(dt) <= max_dt_s].copy()
    p["dt_s"] = dt[np.abs(dt) <= max_dt_s]
    if p.empty:
        return out
    vlen = np.full(len(p), np.nan)
    if vessel_length is not None and len(vessel_length):
        vid = sar.vessel_id.values[p.ib]
        vlen = pd.Series(vid).map(vessel_length).astype(float).values
    p["len_ais"] = vlen
    p["cost"] = p.dist_m / 1000 + length_penalty(contacts.length_est_m.values[p.ia], vlen)
    chosen = assign_one_to_one(p[["ia", "ib", "cost"]])
    chosen = chosen.merge(p[["ia", "ib", "dist_m", "dt_s", "len_ais"]], on=["ia", "ib"], how="left")
    ia = chosen.ia.values
    out.loc[ia, "gfw_sar_idx"] = chosen.ib.values
    out.loc[ia, "gfw_sar_pair"] = np.where(sar.matched.values[chosen.ib.values], "matched", "unmatched")
    out.loc[ia, "match_dist_m"] = chosen.dist_m.round(1).values
    out.loc[ia, "match_dt_s"] = chosen.dt_s.round(0).values
    out.loc[ia, "gfw_sar_n_cand"] = chosen.n_cand.values
    out.loc[ia, "gfw_sar_n_rivals"] = chosen.n_rivals.values
    out.loc[ia, "length_ais_m"] = chosen.len_ais.values
    if "ambiguous_cell" in sar:
        out.loc[ia, "gfw_sar_ambiguous_cell"] = sar.ambiguous_cell.values[chosen.ib.values].astype(bool)
    # contacts that had candidates but lost them all to other contacts still record how many they had
    lost = p[~p.ia.isin(ia)].groupby("ia").size()
    out.loc[lost.index.values, "gfw_sar_n_cand"] = lost.values
    return out


def sar_quality(dist_m, n_cand, n_rivals, len_radar, len_ais, ambiguous_cell=None, gear=None) -> np.ndarray:
    """match_quality of rule (a) pairs: high, medium or low (see RULES['quality_sar']). `ambiguous_cell` marks pairs
    whose identity came from a cell with several GFW-matched detections, `gear` pairs whose GFW identity is a GEAR
    buoy, not a vessel (both always low)."""
    dist_m, n_cand, n_rivals = (np.asarray(x, float) for x in (dist_m, n_cand, n_rivals))
    a, b = np.asarray(len_radar, float), np.asarray(len_ais, float)
    known = np.isfinite(a) & np.isfinite(b) & (a > 0) & (b > 0)
    ratio = np.where(known, a / np.where(known, b, 1), 1.0)
    bad2 = known & ((ratio < 0.5) | (ratio > 2))
    bad3 = known & ((ratio < 1 / 3) | (ratio > 3))
    one = (n_cand == 1) & (n_rivals == 1)
    q = np.where(one & (dist_m <= 1000) & ~bad2, "high", "medium")
    amb = np.zeros(len(q), bool) if ambiguous_cell is None else np.asarray(ambiguous_cell, bool)
    gear_ = np.zeros(len(q), bool) if gear is None else np.asarray(gear, bool)
    q = np.where(((n_cand > 1) & (n_rivals > 1)) | bad3 | amb | gear_, "low", q)
    return q.astype(object)


# -- (b) GFW AIS presence per pass ------------------------------------------------------------------------------
def presence_rows(frame: pd.DataFrame, pass_id: str) -> pd.DataFrame:
    """Normalise a HOURLY x HIGH x VESSEL_ID presence report frame: one row per vessel, cell and hour."""
    cols = ["pass_id", "lon", "lat", "hour_ts", "hours"] + list(_ID_FIELDS.values())
    if frame is None or frame.empty:
        return pd.DataFrame(columns=cols)
    f = frame.copy()
    ident = _identity_columns(f)
    out = pd.DataFrame({"pass_id": pass_id, "lon": f.lon.values, "lat": f.lat.values,
                        "hour_ts": pd.to_datetime(f.date, utc=True, errors="coerce").values,
                        "hours": pd.to_numeric(f.get("hours", 1), errors="coerce").fillna(1).values})
    out = pd.concat([out, ident.reset_index(drop=True)], axis=1)
    out["hour_ts"] = pd.to_datetime(out.hour_ts, utc=True)
    return out[cols].dropna(subset=["vessel_id"]).reset_index(drop=True)


def hour_offset(hour_ts, ts) -> np.ndarray:
    """Whole hours from the contact's hour bucket to the presence hour bucket (0 = same hour)."""
    a = pd.to_datetime(pd.Series(hour_ts), utc=True).dt.floor("h")
    b = pd.to_datetime(pd.Series(ts), utc=True).dt.floor("h")
    return ((a.values - b.values).astype("timedelta64[s]").astype(float) / 3600).round().astype(int)


def is_gear(vessel_type, geartype=None) -> np.ndarray:
    """True for AIS devices GFW types as GEAR (net and gear buoys that transmit AIS): not a vessel identity."""
    vt = pd.Series(vessel_type).astype(str).str.upper().isin(GEAR_TYPES).values
    if geartype is None:
        return vt
    return vt | pd.Series(geartype).astype(str).str.upper().isin(GEAR_TYPES).values


def track_keys(frame: pd.DataFrame, by_pass: bool) -> np.ndarray:
    """Track key per row: the vessel id, or '<pass_id>|<vessel_id>' when tracks must stay separate per pass."""
    v = frame.vessel_id.astype(str).values
    return (frame.pass_id.astype(str).values + "|" + v) if by_pass and "pass_id" in frame else v


def vessel_tracks(presence: pd.DataFrame, by_pass: bool = False) -> dict:
    """Hourly track of every vessel of a presence frame: {key: (t_mid_s, lon, lat, speed_kmh)}, key = vessel id, or
    '<pass_id>|<vessel_id>' with `by_pass` (a frame holding several passes).

    GFW's HOURLY by-vessel report gives one cell per vessel and hour, so a track has one point per hour, placed at the
    hour's midpoint. speed_kmh is the largest distance between consecutive cells divided by their time gap (NaN with
    a single cell): a proxy for how far the vessel moved around the scene time.
    """
    out = {}
    if presence is None or presence.empty:
        return out
    t = pd.to_datetime(pd.Series(presence.hour_ts.values), utc=True).dt.floor("h") + pd.Timedelta(minutes=30)
    f = pd.DataFrame({"v": track_keys(presence, by_pass), "t": t.values.astype("datetime64[s]").astype(np.int64),
                      "lon": presence.lon.values, "lat": presence.lat.values}).sort_values(["v", "t"])
    f = f.drop_duplicates(["v", "t"])
    for v, g in f.groupby("v", sort=False):
        ts, lon, lat = g.t.values.astype(float), g.lon.values, g.lat.values
        if len(ts) > 1:
            d = haversine_m(lon[:-1], lat[:-1], lon[1:], lat[1:]) / 1000
            speed = float(np.max(d / (np.diff(ts) / 3600)))
        else:
            speed = np.nan
        out[v] = (ts, lon, lat, speed)
    return out


def track_position(track, ts_s: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(lon, lat, hour offset of the nearest track point in whole hours, n points) of a vessel at the times `ts_s`
    (epoch seconds): linear interpolation between the hourly points, held at the ends."""
    t, lon, lat, _ = track
    ts_s = np.asarray(ts_s, float)
    if len(t) == 1:
        x, y = np.full(len(ts_s), lon[0]), np.full(len(ts_s), lat[0])
    else:
        x, y = np.interp(ts_s, t, lon), np.interp(ts_s, t, lat)
    dh = np.round((t[np.abs(t[None, :] - ts_s[:, None]).argmin(axis=1)] - ts_s) / 3600).astype(int)
    return x, y, dh, np.full(len(ts_s), len(t), int)


def pair_presence(contacts: pd.DataFrame, presence: pd.DataFrame, exclude_vessels: set | None = None,
                  max_dh: int = MAX_DH_PRESENCE, cand_radius_m: float = CAND_RADIUS_M, max_dist_m: float = MAX_DIST_PRESENCE_M,
                  max_speed_kmh: float = MAX_SPEED_PRESENCE_KMH, slow_kmh: float = SLOW_KMH) -> pd.DataFrame:
    """Rule (b). contacts: lon, lat, ts (one pass). presence: presence_rows of the same pass.

    A vessel is a candidate when one of its hourly cells in the contact's hour or the hour before or after lies
    within `cand_radius_m`, it is not typed GEAR and not in `exclude_vessels`. Its position at the scene time is
    interpolated along its hourly track (vessel_tracks, track_position); the candidate is kept when that position
    is within `max_dist_m` and the vessel has at least two hourly cells with a speed proxy of at most
    `max_speed_kmh` (a single cell gives no speed and, in the calibration, lies far from the vessel's position at
    the scene time, so it is rejected). Cost = interpolated distance in km + speed_kmh / 10. One to one per vessel.
    match_quality: medium when one to one, interpolated distance within 1 km, speed known and at most `slow_kmh`,
    and the vessel has a cell in the contact's hour; low otherwise.
    Returns one row per contact with pres_vessel_id (None when none), match_dist_m (interpolated), match_dt_s (hour
    offset of the nearest track point in seconds), pres_speed_kmh, pres_n_cells, pres_n_cand, pres_n_rivals,
    match_quality.
    """
    n = len(contacts)
    out = pd.DataFrame({"pres_vessel_id": [None] * n, "match_dist_m": np.full(n, np.nan), "match_dt_s": np.full(n, np.nan),
                        "pres_speed_kmh": np.full(n, np.nan), "pres_n_cells": np.zeros(n, int),
                        "pres_n_cand": np.zeros(n, int), "pres_n_rivals": np.zeros(n, int), "match_quality": [None] * n})
    if n == 0 or presence is None or presence.empty:
        return out
    pres = presence[~is_gear(presence.gfw_vessel_type.values, presence.gfw_geartype.values if "gfw_geartype" in presence else None)]
    if exclude_vessels:
        pres = pres[~pres.vessel_id.isin(exclude_vessels)]
    pres = pres.reset_index(drop=True)
    if pres.empty:
        return out
    tree = cKDTree(unit_vectors(pres.lon.values, pres.lat.values))
    hits = tree.query_ball_point(unit_vectors(contacts.lon.values, contacts.lat.values), m_to_chord(cand_radius_m))
    ia = np.repeat(np.arange(n), [len(h) for h in hits])
    ib = np.concatenate([np.asarray(h, int) for h in hits]) if len(ia) else np.zeros(0, int)
    if not len(ia):
        return out
    dh_cell = hour_offset(pres.hour_ts.values[ib], contacts.ts.values[ia])
    keep = np.abs(dh_cell) <= max_dh
    p = pd.DataFrame({"ia": ia[keep], "vessel_id": pres.vessel_id.values[ib[keep]]}).drop_duplicates()
    if p.empty:
        return out
    tracks = vessel_tracks(pres)
    ts_s = pd.to_datetime(pd.Series(contacts.ts.values), utc=True).values.astype("datetime64[s]").astype(np.int64).astype(float)
    dist, dh, speed, ncell = (np.full(len(p), np.nan), np.zeros(len(p), int), np.full(len(p), np.nan), np.zeros(len(p), int))
    for v, idx in p.groupby("vessel_id").indices.items():
        tr = tracks[v]
        x, y, d, k = track_position(tr, ts_s[p.ia.values[idx]])
        dist[idx] = haversine_m(contacts.lon.values[p.ia.values[idx]], contacts.lat.values[p.ia.values[idx]], x, y)
        dh[idx], speed[idx], ncell[idx] = d, tr[3], k
    p["dist_m"], p["dh"], p["speed"], p["n_cells"] = dist, dh, speed, ncell
    ok = (p.dist_m <= max_dist_m) & p.speed.notna() & (p.speed <= max_speed_kmh)
    # contacts that had candidates but keep none still record how many vessels were near
    near = p.groupby("ia").vessel_id.nunique()
    out.loc[near.index.values, "pres_n_cand"] = near.values
    p = p[ok].copy()
    if p.empty:
        return out
    p["cost"] = p.dist_m / 1000 + p.speed / 10
    vids = {v: k for k, v in enumerate(sorted(p.vessel_id.unique()))}
    p["ib_v"] = p.vessel_id.map(vids)
    chosen = assign_one_to_one(p[["ia", "ib_v", "cost"]].rename(columns={"ib_v": "ib"}))
    chosen = chosen.merge(p[["ia", "ib_v", "vessel_id", "dist_m", "dh", "speed", "n_cells"]].rename(columns={"ib_v": "ib"}),
                          on=["ia", "ib"], how="left")
    ia = chosen.ia.values
    out.loc[ia, "pres_vessel_id"] = chosen.vessel_id.values
    out.loc[ia, "match_dist_m"] = chosen.dist_m.round(1).values
    out.loc[ia, "match_dt_s"] = (chosen.dh * 3600).astype(float).values
    out.loc[ia, "pres_speed_kmh"] = chosen.speed.round(2).values
    out.loc[ia, "pres_n_cells"] = chosen.n_cells.values
    out.loc[ia, "pres_n_cand"] = chosen.n_cand.values
    out.loc[ia, "pres_n_rivals"] = chosen.n_rivals.values
    one = (chosen.n_cand.values == 1) & (chosen.n_rivals.values == 1)
    slow = np.isfinite(chosen.speed.values) & (chosen.speed.values <= slow_kmh)
    out.loc[ia, "match_quality"] = np.where(one & slow & (chosen.dh.values == 0) & (chosen.dist_m.values <= 1000), "medium", "low")
    lost = p[~p.ia.isin(ia)].groupby("ia").vessel_id.nunique()
    out.loc[lost.index.values, "pres_n_cand"] = lost.values
    return out


def presence_calibration(contacts: pd.DataFrame, presence: pd.DataFrame, slow_kmh: float = SLOW_KMH,
                         max_speed_kmh: float = MAX_SPEED_PRESENCE_KMH) -> dict:
    """How well a vessel's hourly presence cells locate it at the scene time, measured on contacts whose vessel GFW's
    SAR matcher identified (rule (a)): contacts has lon, lat, ts, vessel_id (one pass); presence is that pass's frame.
    Returns counts and, by speed class, the median distance from the contact to the vessel's interpolated position
    and to its hour-h cell, with the shares within 1 and 3 km. These numbers set the thresholds of rule (b)."""
    out = {"n_reference": 0}
    if contacts.empty or presence is None or presence.empty:
        return out
    by_pass = "pass_id" in contacts and "pass_id" in presence
    tracks = vessel_tracks(presence, by_pass=by_pass)
    c = contacts.assign(_key=track_keys(contacts, by_pass))
    c = c[c._key.isin(tracks.keys())].reset_index(drop=True)
    if c.empty:
        return out
    ts_s = pd.to_datetime(pd.Series(c.ts.values), utc=True).values.astype("datetime64[s]").astype(np.int64).astype(float)
    d_i, d_h, sp = np.full(len(c), np.nan), np.full(len(c), np.nan), np.full(len(c), np.nan)
    for v, idx in c.groupby("_key").indices.items():
        tr = tracks[v]
        x, y, _, _ = track_position(tr, ts_s[idx])
        d_i[idx] = haversine_m(c.lon.values[idx], c.lat.values[idx], x, y) / 1000
        sp[idx] = tr[3]
        hour_mid = (ts_s[idx] // 3600) * 3600 + 1800
        at_h = np.isin(hour_mid, tr[0])
        pos = np.searchsorted(tr[0], hour_mid[at_h])
        d_h[idx[at_h]] = haversine_m(c.lon.values[idx][at_h], c.lat.values[idx][at_h], tr[1][pos], tr[2][pos]) / 1000
    out["n_reference"] = int(len(c))
    out["n_with_hour_h_cell"] = int(np.isfinite(d_h).sum())

    def block(sel):
        sel = np.asarray(sel, bool)
        if not sel.any():
            return {"n": 0}
        di, dh = d_i[sel], d_h[sel]
        return {"n": int(sel.sum()), "interp_median_km": round(float(np.nanmedian(di)), 2),
                "interp_within_1km": round(float(np.mean(di <= 1)), 3), "interp_within_3km": round(float(np.mean(di <= 3)), 3),
                "hour_cell_median_km": round(float(np.nanmedian(dh)), 2) if np.isfinite(dh).any() else None,
                "hour_cell_within_1km": round(float(np.nanmean(dh <= 1)), 3) if np.isfinite(dh).any() else None}

    out["all"] = block(np.ones(len(c), bool))
    out["speed_unknown_single_cell"] = block(~np.isfinite(sp))
    out[f"speed_0_to_{slow_kmh:g}_kmh"] = block(np.isfinite(sp) & (sp <= slow_kmh))
    out[f"speed_{slow_kmh:g}_to_{max_speed_kmh:g}_kmh"] = block(np.isfinite(sp) & (sp > slow_kmh) & (sp <= max_speed_kmh))
    out[f"speed_over_{max_speed_kmh:g}_kmh"] = block(np.isfinite(sp) & (sp > max_speed_kmh))
    out["rule_b_accepts"] = {"max_dist_km": MAX_DIST_PRESENCE_M / 1000, "max_speed_kmh": max_speed_kmh, "medium_needs_speed_at_most_kmh": slow_kmh}
    return out


# -- (c) status and reach from the LOW daily presence grid -------------------------------------------------------
def presence_daily(client, geojson: dict, dates: list[str], refresh_empty: bool = False, log=None,
                   checkpoint=None) -> pd.DataFrame:
    """LOW (0.1 degree) DAILY AIS presence over `geojson`, one 4Wings report per date (cached; the same request keys
    serve scripts 27 and 31). GFW returns one row per vessel, cell and day (about 150,000 rows and 75 MB a day over
    the AOI), so the rows are summed per cell and day here: lon, lat (cell centres), date, hours, n_vessels.

    `checkpoint` (parquet path) stores the aggregate with the dates it was built for; it is reused when the same
    dates are asked again and rewritten otherwise. It is not written while a date is missing from an offline cache.
    """
    cols = ["lon", "lat", "date", "hours", "n_vessels"]
    from pathlib import Path
    cp = Path(checkpoint) if checkpoint else None
    if cp and cp.exists():
        try:
            prev = pd.read_parquet(cp)
            if sorted(set(prev.attrs.get("dates", [])) if prev.attrs else []) == sorted(dates) or (
                    "_dates" in prev.columns and sorted(set(prev["_dates"].iloc[0].split(","))) == sorted(dates)):
                return prev.drop(columns=[c for c in ("_dates",) if c in prev.columns])
        except Exception:
            pass
    frames, missing = [], 0
    for d in dates:
        rng = (d, (pd.Timestamp(d) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
        try:
            resp = client.report(G.DATASETS["presence"], rng, geojson, "LOW", "DAILY", refresh_empty=refresh_empty)
        except G.GFWError as e:
            missing += 1
            if log:
                log("presence LOW DAILY", d, "not available:", str(e)[:120])
            continue
        f = G.report_to_frame(resp, "LOW")
        if not f.empty and "hours" in f:
            f["hours"] = pd.to_numeric(f.hours, errors="coerce").fillna(0)
            f["date"] = pd.to_datetime(f.date, utc=True, errors="coerce").dt.strftime("%Y-%m-%d").fillna(d)
            vid = f["vesselId"] if "vesselId" in f else pd.Series(np.arange(len(f)), index=f.index)
            agg = f.assign(_v=vid).groupby(["lon", "lat", "date"], as_index=False).agg(hours=("hours", "sum"), n_vessels=("_v", "nunique"))
            frames.append(agg)
        if log:
            log("presence LOW DAILY", d, "rows", 0 if f.empty else len(f), "cells", 0 if f.empty else len(agg),
                client.last_headers.get("x-ratelimit-daily-current-usage", ""))
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
    out = out[cols]
    if cp and not (missing and getattr(client, "offline", False)):
        cp.parent.mkdir(parents=True, exist_ok=True)
        out.assign(_dates=",".join(dates)).to_parquet(cp, index=False)
    return out


def presence_block_hours(contacts: pd.DataFrame, low_daily: pd.DataFrame, res: float = RES_LOW) -> tuple[np.ndarray, np.ndarray]:
    """(hours on the contact's date, hours over the whole frame) of AIS presence in the 3 x 3 block of `res` cells
    around each contact. contacts: lon, lat, date (YYYY-MM-DD). low_daily: lon, lat, date, hours."""
    n = len(contacts)
    h_day, h_win = np.zeros(n), np.zeros(n)
    if n == 0 or low_daily.empty:
        return h_day, h_win
    agg = low_daily.groupby(["lon", "lat"], as_index=False).hours.sum()
    p = block_join(contacts, agg, res)
    if not p.empty:
        s = pd.Series(agg.hours.values[p.ib]).groupby(p.ia.values).sum()
        h_win[s.index.values] = s.values
    pd_ = block_join(contacts, low_daily, res, keys=["date"])
    if not pd_.empty:
        s = pd.Series(low_daily.hours.values[pd_.ib]).groupby(pd_.ia.values).sum()
        h_day[s.index.values] = s.values
    return h_day.round(2), h_win.round(2)


def ais_reach(contacts: pd.DataFrame, low_daily: pd.DataFrame, n_days: int, res: float = RES_REACH) -> np.ndarray:
    """Share of the window's days with any AIS presence in the contact's `res` degree cell (NaN when n_days is 0)."""
    if n_days <= 0 or contacts.empty:
        return np.full(len(contacts), np.nan)
    if low_daily.empty:
        return np.zeros(len(contacts))
    cx, cy = cell_ij(low_daily.lon.values, low_daily.lat.values, res)
    days_per_cell = pd.DataFrame({"cx": cx, "cy": cy, "date": low_daily.date.values})[low_daily.hours.values > 0]
    days_per_cell = days_per_cell.drop_duplicates().groupby(["cx", "cy"]).size()
    ax, ay = cell_ij(contacts.lon.values, contacts.lat.values, res)
    idx = pd.MultiIndex.from_arrays([ax, ay])
    return (days_per_cell.reindex(idx).fillna(0).values / n_days).round(3)


def ais_status(has_vessel, h_window) -> np.ndarray:
    """Rule (c)."""
    has_vessel, h_window = np.asarray(has_vessel, bool), np.asarray(h_window, float)
    return np.where(has_vessel, "matched", np.where(h_window > 0, "unmatched", "no_coverage")).astype(object)


# -- (d) evidence -----------------------------------------------------------------------------------------------
def nearest_presence(contacts: pd.DataFrame, presence: pd.DataFrame, max_dh: int = MAX_DH_PRESENCE,
                     r10_m: float = 10_000) -> pd.DataFrame:
    """Nearest GFW AIS vessel of the pass window for each contact, and distinct vessels within 10 km in the pass hour.

    contacts and presence belong to one pass. Returns nearest_ais_vessel_id, nearest_ais_mmsi, nearest_ais_name,
    nearest_ais_dist_m, nearest_ais_dt_s (hour-bucket offset in seconds) and n_ais_10km.
    """
    n = len(contacts)
    out = pd.DataFrame({"nearest_ais_vessel_id": [None] * n, "nearest_ais_mmsi": [None] * n, "nearest_ais_name": [None] * n,
                        "nearest_ais_dist_m": np.full(n, np.nan), "nearest_ais_dt_s": np.full(n, np.nan), "n_ais_10km": np.zeros(n, int)})
    if n == 0 or presence.empty:
        return out
    uv_c = unit_vectors(contacts.lon.values, contacts.lat.values)
    c_hour = pd.to_datetime(pd.Series(contacts.ts.values), utc=True).dt.floor("h").values
    p_hour = pd.to_datetime(pd.Series(presence.hour_ts.values), utc=True).dt.floor("h").values
    best_d = np.full(n, np.inf)
    for dh in range(-max_dh, max_dh + 1):
        for h in np.unique(c_hour):
            sel_c = np.flatnonzero(c_hour == h)
            sel_p = np.flatnonzero(p_hour == h + np.timedelta64(dh, "h"))
            if not len(sel_c) or not len(sel_p):
                continue
            tree = cKDTree(unit_vectors(presence.lon.values[sel_p], presence.lat.values[sel_p]))
            d, i = tree.query(uv_c[sel_c])
            d_m = chord_to_m(d)
            better = d_m < best_d[sel_c]
            idx = sel_c[better]
            best_d[idx] = d_m[better]
            src = sel_p[i[better]]
            out.loc[idx, "nearest_ais_vessel_id"] = presence.vessel_id.values[src]
            out.loc[idx, "nearest_ais_mmsi"] = presence.mmsi.values[src]
            out.loc[idx, "nearest_ais_name"] = presence.ship_name.values[src]
            out.loc[idx, "nearest_ais_dist_m"] = np.round(d_m[better], 1)
            out.loc[idx, "nearest_ais_dt_s"] = float(dh * 3600)
            if dh == 0:
                hits = tree.query_ball_point(uv_c[sel_c], m_to_chord(r10_m))
                vid = presence.vessel_id.values[sel_p]
                out.loc[sel_c, "n_ais_10km"] = [len(set(vid[h_]) ) if len(h_) else 0 for h_ in hits]
    return out


def events_nearby(contacts: pd.DataFrame, events: pd.DataFrame, max_km: float, max_h: float) -> np.ndarray:
    """Number of events (lon, lat, start, end) within max_km of each contact whose time span, widened by max_h hours
    on each side, contains the contact's time. contacts: lon, lat, ts."""
    n = len(contacts)
    if n == 0 or events is None or events.empty:
        return np.zeros(n, int)
    ev = events.dropna(subset=["lon", "lat"]).reset_index(drop=True)
    if ev.empty:
        return np.zeros(n, int)
    t0 = (pd.to_datetime(ev.start, utc=True) - pd.Timedelta(hours=max_h)).values.astype("datetime64[s]").astype("int64")
    t1 = (pd.to_datetime(ev.end, utc=True) + pd.Timedelta(hours=max_h)).values.astype("datetime64[s]").astype("int64")
    ts = pd.to_datetime(pd.Series(contacts.ts.values), utc=True).values.astype("datetime64[s]").astype("int64")
    tree = cKDTree(unit_vectors(ev.lon.values, ev.lat.values))
    hits = tree.query_ball_point(unit_vectors(contacts.lon.values, contacts.lat.values), m_to_chord(max_km * 1000))
    out = np.zeros(n, int)
    for k, h in enumerate(hits):
        if h:
            h = np.asarray(h)
            out[k] = int(((t0[h] <= ts[k]) & (ts[k] <= t1[h])).sum())
    return out


def gaps_nearby(contacts: pd.DataFrame, gaps: pd.DataFrame, max_km: float = 50, max_h: float = 24) -> tuple[np.ndarray, np.ndarray]:
    """(count, nearest km) of GFW gap events whose AIS-off position and start, or AIS-on position and end, lie within
    max_km and max_h of each contact. gaps: off_lon, off_lat, start, on_lon, on_lat, end."""
    n = len(contacts)
    count, nearest = np.zeros(n, int), np.full(n, np.nan)
    if n == 0 or gaps is None or gaps.empty:
        return count, nearest
    ts = pd.to_datetime(pd.Series(contacts.ts.values), utc=True).values.astype("datetime64[s]").astype("int64")
    for _, g in gaps.iterrows():
        hit = np.zeros(n, bool)
        dmin = np.full(n, np.inf)
        for lon_c, lat_c, t_c in (("off_lon", "off_lat", "start"), ("on_lon", "on_lat", "end")):
            if pd.isna(g.get(lon_c)) or pd.isna(g.get(lat_c)) or pd.isna(g.get(t_c)):
                continue
            t = int(pd.Timestamp(g[t_c]).tz_convert("UTC").timestamp()) if pd.Timestamp(g[t_c]).tzinfo else int(pd.Timestamp(g[t_c], tz="UTC").timestamp())
            d_km = haversine_m(contacts.lon.values, contacts.lat.values, g[lon_c], g[lat_c]) / 1000
            ok = (d_km <= max_km) & (np.abs(ts - t) <= max_h * 3600)
            hit |= ok
            dmin = np.where(ok, np.minimum(dmin, d_km), dmin)
        count += hit
        nearest = np.where(hit, np.fmin(np.nan_to_num(nearest, nan=np.inf), dmin), nearest)
    nearest = np.where(np.isfinite(nearest), np.round(nearest, 1), np.nan)
    return count, nearest


# -- identities --------------------------------------------------------------------------------------------------
def merge_identity(report_identity: pd.DataFrame, vessels: pd.DataFrame | None) -> pd.DataFrame:
    """Final identity columns per vessel id: the report's AIS fields, filled from the vessels API where the report
    is empty, plus registry length and ship type. report_identity: vessel_id, mmsi, ship_name, call_sign, imo, flag,
    gfw_vessel_type, gfw_geartype (one row per vessel id). vessels: vessels_to_frame output."""
    r = report_identity.drop_duplicates("vessel_id").set_index("vessel_id")
    out = pd.DataFrame(index=r.index)
    out["mmsi"], out["vessel_name"], out["call_sign"], out["imo"], out["flag"] = r.mmsi, r.ship_name, r.call_sign, r.imo, r.flag
    out["ship_type"] = r.gfw_vessel_type
    out["gfw_geartype"] = r.gfw_geartype
    out["length_ais_m"] = np.nan
    out["identity_source"] = "GFW 4Wings report vessel fields"
    if vessels is not None and not vessels.empty:
        v = vessels.drop_duplicates("vessel_id").set_index("vessel_id").reindex(out.index)
        for dst, src in (("mmsi", "ssvid"), ("vessel_name", "shipname"), ("call_sign", "callsign"), ("imo", "imo"), ("flag", "flag"),
                         ("ship_type", "shiptype")):
            if src in v:
                out[dst] = out[dst].where(out[dst].notna(), v[src])
        if "length_m" in v:
            out["length_ais_m"] = pd.to_numeric(v.length_m, errors="coerce")
        has_api = v.ssvid.notna() if "ssvid" in v else pd.Series(False, index=out.index)
        out.loc[has_api, "identity_source"] = IDENTITY_SOURCE
    for c in ("mmsi", "vessel_name", "call_sign", "imo", "flag", "ship_type", "gfw_geartype"):
        out[c] = out[c].where(out[c].notna() & (out[c].astype(str) != ""), None)
    return out.reset_index()


# -- checks and summary ------------------------------------------------------------------------------------------
def length_check(df: pd.DataFrame) -> dict:
    """Radar length estimate against the AIS (registry) length of matched contacts: Spearman, median ratio, by bin."""
    m = df[(df.ais_status == "matched") & df.length_ais_m.notna() & df.length_est_m.notna()]
    out = {"n_matched_with_ais_length": int(len(m)), "n_matched": int((df.ais_status == "matched").sum())}
    if len(m):
        ratio = m.length_est_m / m.length_ais_m
        out.update({"spearman": spearman(m.length_est_m, m.length_ais_m), "median_ratio_radar_over_ais": round(float(ratio.median()), 3),
                    "share_within_factor_2": round(float(((ratio >= 0.5) & (ratio <= 2)).mean()), 4),
                    "median_radar_m": round(float(m.length_est_m.median()), 1), "median_ais_m": round(float(m.length_ais_m.median()), 1)})
        bins = length_bin(m.length_ais_m.values)
        bins.index = m.index
        out["by_ais_length_bin"] = {str(k): {"n": int(len(g)), "median_radar_m": round(float(g.length_est_m.median()), 1),
                                             "median_ais_m": round(float(g.length_ais_m.median()), 1)}
                                    for k, g in m.groupby(bins, observed=True)}
    return out


def summarize(df: pd.DataFrame) -> dict:
    """Counts by status, method and quality, mission, length bin, sub-region and CNN verdict, plus the length check."""
    def by(col, sub=None):
        d = df if sub is None else df[sub]
        t = pd.crosstab(d[col].fillna("none"), d.ais_status)
        return {str(k): {**{c: int(v) for c, v in row.items()}, "n": int(row.sum()),
                         "matched_share": round(float(row.get("matched", 0) / row.sum()), 4) if row.sum() else None}
                for k, row in t.iterrows()}

    matched = df[df.ais_status == "matched"]
    out = {"n_contacts": int(len(df)), "by_status": df.ais_status.value_counts().to_dict(),
           "by_method_quality": {f"{m}|{q}": int(c) for (m, q), c in matched.groupby(["match_method", "match_quality"]).size().items()},
           "by_sar_pair": df.gfw_sar_pair.fillna("none").value_counts().to_dict(),
           "by_mission": by("mission"), "by_confidence": by("confidence")}
    if "identity_kind" in df:
        out["matched_by_identity_kind"] = matched.identity_kind.fillna("none").value_counts().to_dict()
        out["matched_by_method_identity_kind"] = {f"{m}|{k}": int(c) for (m, k), c in
                                                  matched.groupby(["match_method", matched.identity_kind.fillna("none")]).size().items()}
        out["n_matched_vessel_identity"] = int((matched.identity_kind == "vessel").sum())
        out["n_matched_gear_identity"] = int((matched.identity_kind == "gear").sum())
    if "pres_speed_kmh" in df:
        pm = matched[matched.match_method == MATCH_PRESENCE]
        if len(pm):
            out["presence_matches"] = {"n": int(len(pm)), "speed_known": int(pm.pres_speed_kmh.notna().sum()),
                                       "speed_kmh_median": round(float(pm.pres_speed_kmh.median()), 2) if pm.pres_speed_kmh.notna().any() else None,
                                       "dist_m_median": round(float(pm.match_dist_m.median()), 1),
                                       "share_dist_within_1km": round(float((pm.match_dist_m <= 1000).mean()), 4),
                                       "n_cells_median": float(pm.pres_n_cells.median()) if "pres_n_cells" in pm else None}
    df = df.copy()
    df["length_bin"] = length_bin(df.length_est_m).astype(str)
    out["by_length_bin"] = by("length_bin")
    reg = pd.Series("other", index=df.index, dtype=object)
    for name, box in REGIONS.items():
        reg[in_box(df.lon, df.lat, box) & (reg == "other")] = name
    df["region"] = reg
    out["by_region"] = by("region")
    if "cnn_score" in df and df.cnn_score.notna().any():
        scored = df[df.cnn_score.notna()]
        acc = scored[scored.cnn_vessel.astype(bool)]
        out["cnn"] = {"n_scored": int(len(scored)), "n_cnn_vessel": int(len(acc)),
                      "matched_share_cnn_vessel": round(float((acc.ais_status == "matched").mean()), 4) if len(acc) else None,
                      "matched_share_cnn_rejected": round(float((scored[~scored.cnn_vessel.astype(bool)].ais_status == "matched").mean()), 4)
                      if (~scored.cnn_vessel.astype(bool)).any() else None,
                      "status_cnn_vessel": acc.ais_status.value_counts().to_dict()}
    out["length_check"] = length_check(df)
    for c in ("n_ais_10km", "ais_reach", "ais_presence_h_window"):
        if c in df:
            out[f"{c}_median_by_status"] = {k: round(float(v), 3) for k, v in df.groupby("ais_status")[c].median().items()}
    out["evidence_unmatched"] = {}
    um = df[df.ais_status == "unmatched"]
    if len(um):
        out["evidence_unmatched"] = {"n": int(len(um)), "with_nearest_ais": int(um.nearest_ais_mmsi.notna().sum()),
                                     "no_ais_presence_in_block_on_pass_day": int((um.ais_presence_h_day == 0).sum()) if "ais_presence_h_day" in um else None,
                                     "presence_candidates_rejected": int((um.pres_n_cand > 0).sum()) if "pres_n_cand" in um else None,
                                     "nearest_ais_dist_m_median": round(float(um.nearest_ais_dist_m.median()), 1) if um.nearest_ais_dist_m.notna().any() else None,
                                     "gfw_sar_pair_unmatched": int((um.gfw_sar_pair == "unmatched").sum()),
                                     "with_gap_event_50km_24h": int((um.n_gfw_gaps_50km_24h > 0).sum()) if "n_gfw_gaps_50km_24h" in um else None,
                                     "with_encounter_10km_24h": int((um.n_gfw_encounters_10km_24h > 0).sum()) if "n_gfw_encounters_10km_24h" in um else None,
                                     "with_loitering_10km_24h": int((um.n_gfw_loitering_10km_24h > 0).sum()) if "n_gfw_loitering_10km_24h" in um else None,
                                     "gfw_neural_type": um.gfw_neural_type.fillna("none").value_counts().to_dict() if "gfw_neural_type" in um else None}
    return out


def about_rows(dataset_versions: dict, accessed, window: tuple[str, str], extra: dict | None = None) -> pd.DataFrame:
    """One-row 'about' table: method, rules, dataset versions, access dates, licence, attribution and caveats.
    `accessed` is one date string for every dataset or a dict {dataset key: date string} (the fetch dates of the cache)."""
    acc = accessed if isinstance(accessed, dict) else {k: accessed for k in dataset_versions}
    row = {"run_id": RUN_ID, "ais_source": "gfw", "use": G.RESEARCH_TAG, "licence": G.LICENCE, "licence_url": G.LICENCE_URL,
           "terms_url": G.TERMS_URL, "accessed": "; ".join(f"{k}: {v}" for k, v in acc.items()) if isinstance(accessed, dict) else accessed,
           "window": f"{window[0]} to {window[1]}",
           "attribution": "; ".join(G.attribution(v, window, acc.get(k, next(iter(acc.values()), ""))) for k, v in dataset_versions.items()),
           **{f"dataset_{k}": v for k, v in dataset_versions.items()}, **{f"rule_{k}": v for k, v in RULES.items()},
           "caveat_full": DARK_CAVEAT, "gfw_caveat": G.GFW_CAVEAT, "row_caveat": "every contact row carries caveat_full (darkvessel.config.DARK_CAVEAT)",
           "no_coverage_meaning": "no_coverage means GFW's AIS feed heard nothing in the 0.3 degree block over the whole window; it never means dark",
           "sources": ", ".join(f"{k}: {v}" for k, v in G.SOURCES.items())}
    row.update(extra or {})
    return pd.DataFrame([row])
