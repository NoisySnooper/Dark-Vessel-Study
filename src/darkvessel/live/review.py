"""Hand-check support for a processed live pass: the evidence behind each matched contact and each contact left
without a match, the reviewer's grades, and the radar chips.

For a matched contact: the vessel's last AIS report before and first after the contact's azimuth time (time offset,
distance from the radar position, SOG, COG, navigational status), the vessel's expected radar position with and
without the azimuth shift (the matcher's own computation, darkvessel.live.assign), the other AIS vessels whose
expected position lies within 1 km of the contact, the other contacts within 1 km of the vessel's expected position,
the identity fields and their source, the length agreement and the gear-beacon test of the name.
For a contact without a match (unmatched or no_coverage): the k nearest AIS vessels by expected radar position at
that contact, their gate and their fate (paired with another contact, held back as ambiguous, AIS-only, outside the
tested area), which says why the contact stayed unmatched.

The `check` column is a mechanical first reading (CHECK_RULE). The reviewer's reading is kept in `grade` (one of
HAND_GRADES) and `reason`; --review keeps them across reruns for the same det_id (and, for a matched row, the same
MMSI), and the products carry them in the contacts column `review_note` (REVIEW_NOTE_RULE). Both tables carry the dark
caveat and the board D4.7 label of the aisstream identities on every row (`caveat`, `identity_label`).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from darkvessel.ais.aisstream import gear_beacon_like
from darkvessel.ais.match import gate_metres, predict_positions
from darkvessel.config import CRS_GEO, DARK_CAVEAT
from darkvessel.live.rules import LIVE_MATCH, chord_to_m, unit_vectors

CHECK_RULE = ("consistent = the AIS reports before and after the contact time lie within 10 min of it and within the gate "
              "of the radar position (after the azimuth shift), the name is not a gear beacon, and the radar length is 0.25 to "
              "4 times the AIS length or one of them is unknown; otherwise the failing parts are named.")
HAND_GRADES = ("confirmed", "plausible", "doubtful")
HAND_COLUMNS = ["sample", "grade", "reason", "reviewed_utc"]
REVIEW_NOTE_RULE = (
    "review_note: null unless a person hand-checked the row (data/live/<run_id>_review_matched.csv and "
    "_review_unmatched.csv, columns grade and reason); then '<grade>: <reason>'. Grades of a matched contact: confirmed = "
    "the AIS track passes through or next to the radar return at the contact time (after the azimuth shift), no other "
    "AIS vessel competes and the radar return and the AIS length agree within the method's bands; plausible = the pairing "
    "is the best available but one part is weak (a long gap in the AIS track, a neighbour within a few hundred metres, a "
    "length outside the bands, a faint or smeared return); doubtful = the return is probably not this vessel (wrong "
    "size, a better candidate return nearby, or a track that does not reach the return). A match on a fixed return is at "
    "most plausible: the return of the earlier passes cannot be tied to this vessel without AIS from those dates. For a "
    "contact without a match the grade says whether its current status is right: confirmed (the status follows from the "
    "evidence), plausible, doubtful; a former match removed by a rematch is graded the same way, on its current status, "
    "and its reason names the old pairing. A grade "
    "never removes or changes a match: the row keeps its status and match_quality; the product shows a doubtful or "
    "low-quality pairing as 'low-quality pairing, identity not confirmed' (board D6.2). A hand check applies only while "
    "the pairing or status it read is unchanged: a rematch that pairs the contact with another MMSI, or changes the status "
    "of a contact without a match, drops the note."
)
NEAR_M = 1000.0
IDENTITY_LABEL = "live AIS relayed by aisstream.io; terms UNVERIFIED"  # board D4.7: every aisstream identity carries it
LABEL_COLUMNS = ["identity_label", "caveat"]


def _labelled(df: pd.DataFrame) -> pd.DataFrame:
    """The D4.7 identity label and the dark caveat on every row of a review table (after the evidence columns)."""
    if df.empty:
        return df
    return df.assign(identity_label=IDENTITY_LABEL, caveat=DARK_CAVEAT)


def _dist_m(lon1, lat1, lon2, lat2) -> np.ndarray:
    a, b = unit_vectors(lon1, lat1), unit_vectors(lon2, lat2)
    return chord_to_m(np.linalg.norm(a - b, axis=1))


def _names(static_latest: pd.DataFrame | None) -> dict:
    if static_latest is None or not len(static_latest) or "name" not in static_latest:
        return {}
    return {int(m): n for m, n in zip(static_latest.mmsi.astype("int64"), static_latest["name"]) if isinstance(n, str)}


def _lengths(static_latest: pd.DataFrame | None) -> dict:
    if static_latest is None or not len(static_latest) or "length_m" not in static_latest:
        return {}
    return {int(m): float(v) for m, v in zip(static_latest.mmsi.astype("int64"), static_latest["length_m"]) if pd.notna(v)}


class SceneGeometry:
    """Every placed AIS vessel's expected radar position at every contact of one scene, as the matcher computes it
    (darkvessel.live.assign: position at the contact's azimuth time plus the azimuth shift; `unc` without the shift)."""

    def __init__(self, contacts: pd.DataFrame, vessels: pd.DataFrame, scene_time: pd.Timestamp, sat_speed_ms: float | None = None,
                 cfg=LIVE_MATCH):
        from pyproj import Transformer

        from darkvessel.live import assign as asg

        self.cfg = cfg
        self.contacts = contacts.reset_index(drop=True)
        pred = predict_positions(vessels, scene_time, cfg)
        pred = pd.DataFrame(pred.drop(columns="geometry")) if len(pred) else pd.DataFrame(columns=["mmsi", "x", "y", "method", "dt_s", "sog_kn"])
        pred = pred.reset_index(drop=True)
        if len(pred):
            vel = asg.vessel_velocity(vessels, pred, scene_time, cfg)
            pred = pred.merge(vel, on="mmsi", how="left")
            pred["sog_gate_kn"] = pd.to_numeric(pred.sog_kn, errors="coerce").fillna(pred.speed_kn)
            pred["gate_m"] = gate_metres(pred.assign(sog_kn=pred.sog_gate_kn), cfg)
        self.pred = pred
        self.index = {int(m): j for j, m in enumerate(pred.mmsi.astype("int64"))} if len(pred) else {}
        self._fwd = Transformer.from_crs(CRS_GEO, cfg.utm_crs, always_xy=True)
        self._inv = Transformer.from_crs(cfg.utm_crs, CRS_GEO, always_xy=True)
        n = len(self.contacts)
        cx, cy = self._fwd.transform(self.contacts.lon.to_numpy(float), self.contacts.lat.to_numpy(float)) if n else ([], [])
        self.cx, self.cy = np.asarray(cx, float), np.asarray(cy, float)
        dt, rng, sin_inc, az, rg, has = asg.contact_geometry(self.contacts, scene_time, cfg.utm_crs)
        V = float(sat_speed_ms or asg.SAT_SPEED_MS)
        if n and len(pred):
            px, py, vx, vy = (pred[c].to_numpy(float) for c in ("x", "y", "vx", "vy"))
            self.ex, self.ey, self.shift = asg.expected_positions(dt, rng, sin_inc, az, rg, px, py, vx, vy, V, correct=True)
            self.ux = px[None, :] + np.nan_to_num(vx)[None, :] * dt[:, None]
            self.uy = py[None, :] + np.nan_to_num(vy)[None, :] * dt[:, None]
            self.D = np.hypot(self.cx[:, None] - self.ex, self.cy[:, None] - self.ey)
            self.D_unc = np.hypot(self.cx[:, None] - self.ux, self.cy[:, None] - self.uy)
        else:
            self.ex = self.ey = self.shift = self.ux = self.uy = self.D = self.D_unc = np.zeros((n, len(pred)))

    def lonlat(self, x, y):
        lo, la = self._inv.transform(np.asarray(x, float), np.asarray(y, float))
        return np.asarray(lo, float), np.asarray(la, float)


def matched_evidence(contacts: pd.DataFrame, ais: pd.DataFrame, static_latest: pd.DataFrame | None = None,
                     scene_times: dict | None = None, sat_speed: dict | None = None, gear: set | None = None) -> pd.DataFrame:
    """One row per matched contact with the AIS track and neighbourhood evidence around its azimuth time.

    `scene_times` (scene_id -> scene time) enables the expected positions and neighbours (the contacts then need the
    live.scene geometry columns; without them the shift is 0). `gear` MMSIs are left out of the neighbours.
    """
    m = contacts[contacts.ais_status == "matched"].copy()
    if m.empty:
        return pd.DataFrame()
    gear = set(gear or ())
    names, lengths = _names(static_latest), _lengths(static_latest)
    a = ais.sort_values("timestamp")
    by = {int(k): g for k, g in a[a.mmsi.isin(set(m.mmsi.astype("int64")))].groupby("mmsi")}
    st = static_latest.set_index("mmsi") if static_latest is not None and len(static_latest) else None
    geo = {}
    if scene_times:
        vessels = ais[~ais.mmsi.astype("int64").isin(gear)]
        for sid, g in contacts.groupby("scene_id"):
            if sid in scene_times and (g.ais_status == "matched").any():
                geo[sid] = SceneGeometry(g, vessels, pd.Timestamp(scene_times[sid]), (sat_speed or {}).get(sid))
    rows = []
    for _, c in m.iterrows():
        mmsi = int(c.mmsi)
        t = pd.Timestamp(c.az_time_utc) if isinstance(c.get("az_time_utc"), str) else pd.Timestamp(c.acq_utc)
        t = t.tz_localize("UTC") if t.tzinfo is None else t
        g = by.get(mmsi, pd.DataFrame(columns=a.columns))
        before, after = g[g.timestamp <= t], g[g.timestamp > t]
        r = {"det_id": c.det_id, "scene_id": c.get("scene_id"), "mmsi": mmsi, "vessel_name": c.vessel_name, "call_sign": c.call_sign,
             "flag": c.flag, "ship_type": c.ship_type, "ais_class": c.get("ais_class"), "identity_source": c.identity_source,
             "lon": c.lon, "lat": c.lat, "row": c.get("row"), "col": c.get("col"), "az_time_utc": c.get("az_time_utc"),
             "confidence": c.confidence, "cnn_score": c.cnn_score, "length_est_m": c.length_est_m, "length_ais_m": c.length_ais_m,
             "length_ratio": c.get("length_ratio"), "match_dist_m": c.match_dist_m, "match_dist_uncorr_m": c.get("match_dist_uncorr_m"),
             "az_shift_m": c.get("az_shift_m"), "match_dt_s": c.match_dt_s, "match_quality": c.match_quality, "match_gate_m": c.get("match_gate_m"),
             "ais_sog_kn": c.get("ais_sog_kn"), "velocity_source": c.get("velocity_source"), "pred_method": c.get("pred_method"),
             "n_reports_window": int(len(g))}
        for side, s, pick in (("before", before, -1), ("after", after, 0)):
            if len(s):
                p = s.iloc[pick]
                r[f"{side}_dt_s"] = round((p.timestamp - t).total_seconds(), 1)
                r[f"{side}_dist_m"] = round(float(_dist_m([c.lon], [c.lat], [p.lon], [p.lat])[0]), 1)
                r[f"{side}_sog_kn"] = p.get("sog_kn")
                r[f"{side}_cog_deg"] = p.get("cog_deg")
                r[f"{side}_nav_status"] = p.get("nav_status")
            else:
                for k in ("dt_s", "dist_m", "sog_kn", "cog_deg"):
                    r[f"{side}_{k}"] = np.nan
                r[f"{side}_nav_status"] = None
        sg = geo.get(c.get("scene_id"))
        if sg is not None and mmsi in sg.index:
            i = int(np.nonzero(sg.contacts.det_id.to_numpy() == c.det_id)[0][0])
            j = sg.index[mmsi]
            lo, la = sg.lonlat([sg.ex[i, j], sg.ux[i, j]], [sg.ey[i, j], sg.uy[i, j]])
            r.update(pred_lon=round(lo[0], 6), pred_lat=round(la[0], 6), pred_uncorr_lon=round(lo[1], 6), pred_uncorr_lat=round(la[1], 6),
                     check_dist_m=round(float(sg.D[i, j]), 1))
            near_v = [k for k in np.argsort(sg.D[i]) if k != j and sg.D[i, k] <= NEAR_M]
            r["n_other_ais_1km"] = len(near_v)
            r["nearest_other_ais_m"] = round(float(sg.D[i, near_v[0]]), 1) if near_v else np.nan
            r["other_ais_1km"] = "; ".join(
                f"{int(sg.pred.mmsi.iloc[k])} {names.get(int(sg.pred.mmsi.iloc[k]), '?')} {sg.D[i, k]:.0f} m"
                f" (L {lengths.get(int(sg.pred.mmsi.iloc[k]), float('nan')):.0f} m, gate {sg.pred.gate_m.iloc[k]:.0f} m)" for k in near_v[:6]) or None
            dv = np.hypot(sg.cx - sg.ex[i, j], sg.cy - sg.ey[i, j])
            near_c = [q for q in np.argsort(dv) if q != i and dv[q] <= NEAR_M]
            r["n_other_contacts_1km"] = len(near_c)
            r["nearest_other_contact_m"] = round(float(dv[near_c[0]]), 1) if near_c else np.nan
            oc = sg.contacts
            r["other_contacts_1km"] = "; ".join(
                f"{oc.det_id.iloc[q]} {oc.confidence.iloc[q]} {oc.length_est_m.iloc[q]:.0f} m {oc.ais_status.iloc[q]}"
                f"{' ' + str(int(oc.mmsi.iloc[q])) if pd.notna(oc.mmsi.iloc[q]) else ''} {dv[q]:.0f} m" for q in near_c[:6]) or None
        static_name = st["name"].get(mmsi) if st is not None and "name" in st else None
        r["static_seen_utc"] = st["seen_utc"].get(mmsi) if st is not None and "seen_utc" in st else None
        r["gear_beacon_like"] = bool(gear_beacon_like(static_name if isinstance(static_name, str) else c.vessel_name, mmsi))
        rows.append(r)
    out = pd.DataFrame(rows)
    out["check"] = [_check(r) for _, r in out.iterrows()]
    return _labelled(out)


def _check(r) -> str:
    problems = []
    for side in ("before", "after"):
        if not np.isfinite(r[f"{side}_dt_s"]):
            problems.append(f"no report {side}")
        elif abs(r[f"{side}_dt_s"]) > 600:
            problems.append(f"report {side} {abs(r[f'{side}_dt_s']) / 60:.0f} min away")
    if r.match_gate_m and np.isfinite(r.match_dist_m) and r.match_dist_m > r.match_gate_m:
        problems.append("outside gate")
    if r.gear_beacon_like:
        problems.append("gear beacon name")
    ratio = r.length_ratio
    if ratio is not None and np.isfinite(ratio) and not (0.25 <= ratio <= 4.0):
        problems.append(f"length ratio {ratio:.2f}")
    return "consistent" if not problems else "; ".join(problems)


def unmatched_evidence(contacts: pd.DataFrame, ais: pd.DataFrame, ais_only: pd.DataFrame, scene_time: pd.Timestamp,
                       k: int = 3, sat_speed_ms: float | None = None, gear: set | None = None,
                       statuses=("unmatched", "no_coverage"), static_latest: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per contact of `statuses` in one scene: the k nearest AIS vessels by expected radar position at that
    contact (the matcher's computation: contact azimuth time and azimuth shift when the geometry columns are present),
    their gate and their fate. Gear beacons are not vessels and are left out."""
    u = contacts[contacts.ais_status.isin(statuses)].copy()
    if u.empty:
        return pd.DataFrame()
    gear = set(gear or ())
    vessels = ais[~ais.mmsi.astype("int64").isin(gear)] if len(ais) else ais
    names = _names(static_latest)
    sg = SceneGeometry(u, vessels, scene_time, sat_speed_ms) if len(vessels) else None
    # the coverage rule's own number: the nearest AIS report of the window (any ship station, gear beacons included)
    near_rep = np.full(len(u), np.nan)
    w = ais[(ais.timestamp - scene_time).abs() <= pd.Timedelta(seconds=LIVE_MATCH.max_gap_s)] if len(ais) else ais
    if len(w):
        from scipy.spatial import cKDTree

        d, _ = cKDTree(unit_vectors(w.lon.values, w.lat.values)).query(unit_vectors(u.lon.values, u.lat.values), k=1)
        near_rep = np.round(chord_to_m(d), 0)
    m_rows = contacts[contacts.ais_status == "matched"]
    paired = dict(zip(m_rows.mmsi.astype("int64"), m_rows.det_id)) if len(m_rows) else {}
    has_amb = len(ais_only) and "ambiguous_det_id" in ais_only
    amb = set(ais_only.mmsi[ais_only.ambiguous_det_id.notna()].astype("int64")) if has_amb else set()
    over = (set(ais_only.mmsi[ais_only.oversized_det_id.notna()].astype("int64"))
            if len(ais_only) and "oversized_det_id" in ais_only else set())
    only = set(ais_only.mmsi.astype("int64")) if len(ais_only) else set()
    rows = []
    for i, (_, c) in enumerate(sg.contacts.iterrows() if sg is not None else u.reset_index(drop=True).iterrows()):
        r = {"det_id": c.det_id, "scene_id": c.get("scene_id"), "lon": c.lon, "lat": c.lat, "row": c.get("row"), "col": c.get("col"),
             "ais_status": c.ais_status, "confidence": c.confidence, "cnn_score": c.cnn_score, "length_est_m": c.length_est_m,
             "dark_lead": c.get("dark_lead"), "match_ambiguous": c.get("match_ambiguous"), "ambiguous_mmsi": c.get("ambiguous_mmsi"),
             "match_alt_dist_m": c.get("match_alt_dist_m"), "n_ais_10km": c.n_ais_10km, "ais_reach": c.ais_reach,
             "nearest_ais_mmsi": c.nearest_ais_mmsi, "nearest_ais_dist_m": c.nearest_ais_dist_m, "persist_dates": c.get("persist_dates"),
             "n_low_1km": c.get("n_low_1km"), "nearest_report_m": near_rep[i]}
        notes = []
        in_gate = False
        if sg is not None and len(sg.pred):
            d = sg.D[i]
            for n, j in enumerate(np.argsort(d)[:k], 1):
                mm = int(sg.pred.mmsi.iloc[j])
                gate = float(sg.pred.gate_m.iloc[j])
                fate = (f"paired with {paired[mm]}" if mm in paired else "ambiguous" if mm in amb
                        else "on an oversized return" if mm in over else "AIS-only" if mm in only else "outside tested area")
                r[f"ais{n}_mmsi"], r[f"ais{n}_name"] = mm, names.get(mm)
                r[f"ais{n}_dist_m"], r[f"ais{n}_gate_m"], r[f"ais{n}_fate"] = round(float(d[j]), 1), round(gate, 1), fate
                notes.append(f"{mm} {d[j]:.0f} m (gate {gate:.0f} m, {fate})")
                in_gate = in_gate or d[j] <= gate
        if c.get("match_ambiguous"):
            why = "ambiguous: " + str(c.get("ambiguous_mmsi"))
        elif c.ais_status == "no_coverage":
            why = "no AIS heard in the cell or within 20 km during the window"
        elif not in_gate:
            why = "no AIS vessel within its gate (nearest beyond the gate)"
        else:
            why = "AIS vessel(s) within the gate taken by a closer return or ruled out by the pairing rules"
        r["why_unmatched"] = why
        r["nearest_ais"] = "; ".join(notes) or None
        rows.append(r)
    return _labelled(pd.DataFrame(rows))


def pick_samples(unmatched: pd.DataFrame, n_leads: int = 20, n_ambiguous: int = 10, n_no_coverage: int = 10, seed: int = 0) -> pd.Series:
    """Hand-check samples: the `n_leads` dark leads with the highest cnn_score, `n_ambiguous` ambiguous contacts at random
    (seeded) and `n_no_coverage` no_coverage contacts nearest to a placed AIS vessel (the hardest test of that rule), one
    per 0.05 degree box so the sample is not one anchorage."""
    s = pd.Series(None, index=unmatched.index, dtype=object)
    if unmatched.empty:
        return s
    leads = unmatched[unmatched.dark_lead.fillna(False).astype(bool)]
    s[leads.sort_values("cnn_score", ascending=False).index[:n_leads]] = "top_cnn_dark_lead"
    amb = unmatched[unmatched.match_ambiguous.fillna(False).astype(bool)]
    if len(amb):
        s[amb.sample(min(n_ambiguous, len(amb)), random_state=seed).index] = "random_ambiguous"
    nc = unmatched[unmatched.ais_status == "no_coverage"].sort_values("nearest_ais_dist_m")
    if len(nc):
        box = (np.floor(nc.lon.to_numpy(float) / 0.05)).astype(int) * 100000 + (np.floor(nc.lat.to_numpy(float) / 0.05)).astype(int)
        nc = nc[~pd.Series(box, index=nc.index).duplicated()]
    s[nc.index[:n_no_coverage]] = "no_coverage_nearest_ais"
    return s


def keep_hand_check(new: pd.DataFrame, old: pd.DataFrame | None, key=("det_id", "mmsi")) -> pd.DataFrame:
    """Carry `sample`, `grade`, `reason` and `reviewed_utc` of the hand-graded rows of an earlier review table onto `new`
    for the same key (det_id plus mmsi for matched rows, det_id plus ais_status otherwise: a grade read on one pairing
    or status never applies to another). Ungraded rows take the new table's sample."""
    out = new.copy()
    key = [k for k in key if k in out]
    for c in HAND_COLUMNS:
        if c not in out:
            out[c] = None
    if old is None or old.empty or "grade" not in old:
        return out[[c for c in out.columns if c not in HAND_COLUMNS] + HAND_COLUMNS]
    o = old.copy()
    if "mmsi" in key:
        o = o[pd.to_numeric(o.get("mmsi"), errors="coerce").notna()] if "mmsi" in o else o.iloc[:0]
        o["mmsi"] = pd.to_numeric(o.mmsi, errors="coerce").astype("int64")
        out["mmsi"] = pd.to_numeric(out.mmsi, errors="coerce").astype("int64")
    o = o[o.grade.isin(HAND_GRADES)] if "grade" in o else o.iloc[:0]
    o = o[[c for c in key + HAND_COLUMNS if c in o]].drop_duplicates(key, keep="last")
    keep_sample = out["sample"].copy()
    out = out.drop(columns=HAND_COLUMNS).merge(o, on=key, how="left")
    for c in HAND_COLUMNS:
        if c not in out:
            out[c] = None
    graded = out["grade"].isin(HAND_GRADES).to_numpy()
    out["sample"] = np.where(graded & out["sample"].notna().to_numpy(), out["sample"].to_numpy(), keep_sample.to_numpy())
    return out[[c for c in out.columns if c not in HAND_COLUMNS] + HAND_COLUMNS]


def review_paths(run_id: str, live_dir: Path) -> tuple[Path, Path]:
    return Path(live_dir) / f"{run_id}_review_matched.csv", Path(live_dir) / f"{run_id}_review_unmatched.csv"


def read_review(path: Path) -> pd.DataFrame | None:
    try:
        return pd.read_csv(path, dtype={"det_id": str, "grade": str, "reason": str, "sample": str}) if Path(path).exists() else None
    except (OSError, ValueError):
        return None


def review_notes(contacts: pd.DataFrame, live_dir: Path) -> pd.Series:
    """review_note per contact row from the review tables of each run in `contacts` (REVIEW_NOTE_RULE); null elsewhere.
    A matched row takes a note only when the reviewed MMSI is the one it is paired with now."""
    note = pd.Series([None] * len(contacts), index=contacts.index, dtype=object)
    if contacts.empty or "run_id" not in contacts:
        return note
    for run_id in contacts.run_id.dropna().unique():
        sel = contacts.run_id == run_id
        for path, matched in zip(review_paths(run_id, live_dir), (True, False)):
            rv = read_review(path)
            if rv is None or "grade" not in rv:
                continue
            rv = rv[rv.grade.isin(HAND_GRADES)]
            if rv.empty:
                continue
            text = rv.grade + ": " + rv.reason.fillna("").str.strip()
            if matched:
                lut = dict(zip(zip(rv.det_id, pd.to_numeric(rv.mmsi, errors="coerce")), text))
                cur = contacts[sel & (contacts.ais_status == "matched")]
                for idx, d, m in zip(cur.index, cur.det_id, pd.to_numeric(cur.mmsi, errors="coerce")):
                    if (d, m) in lut:
                        note[idx] = lut[(d, m)]
            else:
                has_status = "ais_status" in rv
                lut = dict(zip(zip(rv.det_id, rv.ais_status if has_status else [None] * len(rv)), text))
                cur = contacts[sel & (contacts.ais_status != "matched")]
                for idx, d, st in zip(cur.index, cur.det_id, cur.ais_status):
                    k = (d, st if has_status else None)
                    if k in lut:
                        note[idx] = lut[k]
    return note


# ----------------------------------------------------------------------------------------------- radar chips
CHIP_CACHE = Path("data") / "cache" / "live" / "chips"


def chip_windows(scene_path: str, items: pd.DataFrame, half: int = 120, cache_dir: Path | None = None, log=print) -> dict:
    """VV and VH sigma0 (dB) windows of 2 x `half` pixels around each item (det_id, row, col) of one scene, from the
    mirror's GRD (darkvessel.live.scene.LiveGRDScene, retried reads), cached per det_id with the scene's annotation so
    the figure can be redrawn offline. Returns {det_id: {"vv", "vh", "r0", "c0"}} and stores the annotation XML."""
    from rasterio.windows import Window

    from darkvessel.config import DATA_DIR

    cache = Path(cache_dir) if cache_dir else DATA_DIR / "cache" / "live" / "chips"
    pid = scene_path.rstrip("/").rsplit("/", 1)[-1]
    d = cache / pid
    d.mkdir(parents=True, exist_ok=True)
    out, scene = {}, None
    for _, it in items.iterrows():
        f = d / f"{it.det_id}_{half}.npz"
        if f.exists():
            z = np.load(f)
            out[it.det_id] = {k: z[k] for k in ("vv", "vh")} | {"r0": int(z["r0"]), "c0": int(z["c0"])}
            continue
        if scene is None:
            from darkvessel.live.scene import LiveGRDScene

            scene = LiveGRDScene(scene_path, io_threads=2, log=log)
            ann = d / "annotation.xml"
            if not ann.exists():
                ann.write_bytes(scene.annotation_xml)
        H, W = scene.shape
        r0, c0 = max(0, int(it.row) - half), max(0, int(it.col) - half)
        win = Window(c0, r0, min(W, int(it.col) + half) - c0, min(H, int(it.row) + half) - r0)
        with np.errstate(divide="ignore", invalid="ignore"):
            vv = (10 * np.log10(scene.read_sigma0("VV", win, denoise=False))).astype(np.float32)
            vh = (10 * np.log10(scene.read_sigma0("VH", win, denoise=False))).astype(np.float32)
        tmp = d / f"{it.det_id}_{half}.tmp.npz"
        np.savez_compressed(tmp, vv=vv, vh=vh, r0=r0, c0=c0)
        tmp.replace(f)
        out[it.det_id] = {"vv": vv, "vh": vh, "r0": r0, "c0": c0}
    if scene is None and not (d / "annotation.xml").exists():
        from darkvessel.live.scene import LiveGRDScene

        (d / "annotation.xml").write_bytes(LiveGRDScene(scene_path, io_threads=1, log=log).annotation_xml)
    return out


def scene_geocoder(scene_path: str, cache_dir: Path | None = None):
    """Geocoder of a scene from the annotation cached by chip_windows."""
    from darkvessel.config import DATA_DIR
    from darkvessel.s1.grd import Geocoder

    cache = Path(cache_dir) if cache_dir else DATA_DIR / "cache" / "live" / "chips"
    pid = scene_path.rstrip("/").rsplit("/", 1)[-1]
    return Geocoder.from_annotation((cache / pid / "annotation.xml").read_bytes())
