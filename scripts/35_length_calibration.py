"""Radar length calibration: AIS hull length predicted from the raw radar extent (length_est_m), with an 80 % interval.
An open build from live aisstream pairs and a research build from Global Fishing Watch (GFW) registry lengths, kept apart.

Method (src/darkvessel/detect/length_cal.py; docs/length_calibration.md):
  pairs     open: matched contacts of the live passes (data/live/live_S1*.gpkg, contacts_4326) with an AIS static length
            (A + B of message 5 or 24, relayed by aisstream.io). Research: matched contacts of the September regional run
            with a GFW registry length (data/research/regional_identity.parquet; vessel identities only, never gear).
            Kept: match position quality high or medium with the length terms of the quality rule removed (the stored
            quality also grades the radar/AIS length ratio, so selecting on it truncates the very error measured; the
            stored-quality subset is reported as a sensitivity, --select stored makes it the fit set), review_note not
            'doubtful', radar/AIS ratio at least 0.25 (the live matcher's feasibility rule; below it the return is not
            that hull). Open validation only: CFAR candidates within 50 m of an AI2 Sentinel-1A point label with a Length
            attribute (data/ml/candidates.parquet and ai2_s1_labels.parquet, Apache-2.0; same CFAR settings; 2022 scenes).
  diagnosis pixel floor, additive and multiplicative bias, brightness (sidelobes, blooming), speed and blob fill (wakes,
            smearing), orientation to the azimuth axis, dual-polarisation maximum: Spearman of the size-adjusted log
            ratio (log radar/hull minus its median fit on log hull) with each candidate cause.
  models    ratio, loglinear (median fit, at most 2 covariates), isotonic; structure by grouped CV (select_model); 80 %
            intervals from held-out log residuals (split conformal), nested inside the CV for the coverage figures.
            Applied, a calibration returns null with a reason code at the 2-pixel floor, outside the pairs' radar range,
            where it would make the hull longer than the radar return (short_return) and below 25 m.
            Open: live pairs only. All of them come from one scene so far, so there is no grouped CV of the open model:
            its form (ratio or loglinear, no covariates) is chosen by grouped CV on the AI2 pairs, its interval comes
            from leave-one-pair-out residuals, and it is validated on the AI2 pairs (other scenes, other years). The open
            files hold no GFW-derived number. Research: GFW plus live pairs, leave-one-pass-out CV, external check on AI2.
Inputs: data/live/live_S1*.gpkg; data/cache/live/scenes/*.objects.parquet and data/cache/ais/aisstream/positions/ (optional:
        azimuth axis and AIS heading for the orientation test); data/ml/candidates.parquet, ai2_s1_labels.parquet;
        data/research/regional_identity.parquet; data/detections_regional.gpkg; data/detections_regional_all.gpkg
        (optional, git-ignored: pixel count and polarisation of the research pairs)
Output: data/length_calibration.json (open calibration, diagnosis, validation), data/length_calibration_open_pairs.parquet,
        data/research/length_calibration_research.json, data/research/length_calibration_research_pairs.parquet (GFW
        licence, attribution, use and caveat in the file metadata), docs/figures/length_calibration.png
Usage: python scripts/35_length_calibration.py [--select position|stored] [--no-figure]
"""

import argparse

ap = argparse.ArgumentParser()
ap.add_argument("--select", choices=("position", "stored"), default="position",
                help="fit set: match position quality without the length terms (default) or the stored match_quality")
ap.add_argument("--no-figure", action="store_true")
args = ap.parse_args()

import darkvessel  # noqa: F401,E402  (sets PROJ_DATA before pyogrio)
import datetime as dt  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
import pyogrio  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

from darkvessel.ais.gfw_identity import sar_quality  # noqa: E402
from darkvessel.config import CACHE_DIR, DARK_CAVEAT, DATA_DIR, FIG_DIR, PRODUCT_CAVEAT  # noqa: E402
from darkvessel.detect import length_cal as LC  # noqa: E402
from darkvessel.live.rules import MIN_LENGTH_RATIO, live_match_quality  # noqa: E402

LIVE_DIR = DATA_DIR / "live"
RESEARCH = DATA_DIR / "research"
ML = DATA_DIR / "ml"
OUT_OPEN = DATA_DIR / "length_calibration.json"
OUT_OPEN_PAIRS = DATA_DIR / "length_calibration_open_pairs.parquet"
OUT_RES = RESEARCH / "length_calibration_research.json"
OUT_RES_PAIRS = RESEARCH / "length_calibration_research_pairs.parquet"
FIG = FIG_DIR / "length_calibration.png"
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
FLOOR_M = LC.PIXEL_FLOOR_M  # a 2-pixel object reads 20 m along an axis, 24.1 m on the diagonal
AIS_LABEL = "live AIS relayed by aisstream.io; terms UNVERIFIED"
AI2_LICENCE = "Apache-2.0 (https://raw.githubusercontent.com/allenai/vessel-detection-sentinels/main/LICENSE)"
LENGTH_CAVEAT = (
    "A calibrated radar length is a statistical estimate from the extent of the radar return, not a measurement of the "
    "hull. It describes typical pairs of steel ships of 25 m and longer that carry AIS; it makes no claim below 25 m, and "
    "a return that holds two hulls side by side or a hull and its wake reads long whatever the calibration.")
RADAR_BINS = [(25, 50), (50, 100), (100, 200), (200, 451)]
DIAG_COVS = ["scr_max_db", "fill_width_m", "speed_kn", "diagonality", "azimuth_alignment", "inc_angle_deg", "dual_pol"]
log = print


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    return o


def write_json(path, obj):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(jsonable(obj), indent=1, ensure_ascii=True, allow_nan=False))
    tmp.replace(path)


def write_parquet(path, df, meta: dict):
    t = pa.Table.from_pandas(df, preserve_index=False)
    md = dict(t.schema.metadata or {})
    md.update({k.encode(): (v if isinstance(v, str) else json.dumps(jsonable(v))).encode() for k, v in meta.items()})
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(t.replace_schema_metadata(md), tmp)
    tmp.replace(path)


# ----------------------------------------------------------------------------------------------- orientation helpers
def axis_angle_deg(heading_deg, az_e, az_n) -> np.ndarray:
    """Angle between the ship's axis (heading, degrees clockwise from north) and the image azimuth (row) axis, folded to
    0 (along azimuth) .. 90 (along range)."""
    h = np.radians(np.asarray(heading_deg, float))
    he, hn = np.sin(h), np.cos(h)
    nrm = np.hypot(az_e, az_n)
    with np.errstate(invalid="ignore", divide="ignore"):
        c = np.abs(he * az_e / nrm + hn * az_n / nrm)
    return np.degrees(np.arccos(np.clip(c, 0, 1)))


def add_orientation(df: pd.DataFrame) -> pd.DataFrame:
    """diagonality = |sin 2a| (0 along an image axis, 1 on the diagonal), azimuth_alignment = cos^2 a."""
    a = np.radians(df["axis_angle_deg"].to_numpy(float))
    df["diagonality"] = np.abs(np.sin(2 * a))
    df["azimuth_alignment"] = np.cos(a) ** 2
    return df


# ----------------------------------------------------------------------------------------------- open: live pairs
def live_heading(m: pd.DataFrame) -> pd.Series:
    """AIS true heading (else COG at 2 kn or more) of the report nearest each contact's azimuth time, within 10 min."""
    out = pd.Series(np.nan, index=m.index)
    pos_dir = CACHE_DIR / "ais" / "aisstream" / "positions"
    if not pos_dir.exists() or not len(m):
        return out
    t = pd.to_datetime(m.get("az_time_utc", m["acq_utc"]), utc=True, errors="coerce").fillna(pd.to_datetime(m["acq_utc"], utc=True))
    hours = sorted({(ts - pd.Timedelta(minutes=10)).floor("h") for ts in t} | {(ts + pd.Timedelta(minutes=10)).floor("h") for ts in t})
    frames = []
    for h in hours:
        p = pos_dir / f"{h:%Y%m%d}" / f"{h:%H}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p, columns=["mmsi", "timestamp", "sog_kn", "cog_deg", "heading"]))
    if not frames:
        return out
    pos = pd.concat(frames)
    pos = pos[pos.mmsi.isin(pd.to_numeric(m.mmsi, errors="coerce").dropna().astype("int64"))]
    for idx, mm, ts in zip(m.index, pd.to_numeric(m.mmsi, errors="coerce"), t):
        if not np.isfinite(mm):
            continue
        p = pos[(pos.mmsi == int(mm)) & ((pos.timestamp - ts).abs() <= pd.Timedelta(minutes=10))]
        if not len(p):
            continue
        p = p.assign(dt=(p.timestamp - ts).abs()).sort_values("dt")
        hd = p.heading.dropna()
        if len(hd):
            out[idx] = float(hd.iloc[0])
            continue
        cg = p[(p.sog_kn >= 2) & p.cog_deg.notna()]
        if len(cg):
            out[idx] = float(cg.cog_deg.iloc[0])
    return out


def live_axes(m: pd.DataFrame) -> pd.DataFrame:
    """Azimuth axis (east, north) of each contact from the live watcher's object cache, when present."""
    out = pd.DataFrame({"az_e": np.nan, "az_n": np.nan}, index=m.index)
    for sid in m.scene_id.dropna().unique():
        f = CACHE_DIR / "live" / "scenes" / f"{sid}.objects.parquet"
        if not f.exists():
            continue
        o = pd.read_parquet(f, columns=["det_id", "az_e", "az_n"]).drop_duplicates("det_id").set_index("det_id")
        sel = m.scene_id == sid
        out.loc[sel, "az_e"] = o.az_e.reindex(m.loc[sel, "det_id"]).to_numpy()
        out.loc[sel, "az_n"] = o.az_n.reindex(m.loc[sel, "det_id"]).to_numpy()
    return out


def selection(df: pd.DataFrame, quality_col: str) -> pd.Series:
    """Exclusion reason per pair (None = used): quality, review_doubtful, ratio_below_feasible, radar_at_pixel_floor
    (the calibration makes no claim there, so those pairs do not shape its low end)."""
    r = pd.Series([None] * len(df), index=df.index, dtype=object)
    r[~df[quality_col].isin(["high", "medium"])] = f"{quality_col}_not_high_or_medium"
    note = df["review_note"].astype(object) if "review_note" in df else pd.Series([None] * len(df), index=df.index)
    doubt = note.fillna("").astype(str).str.lower().str.startswith("doubtful")
    r[r.isna() & doubt] = "review_doubtful"
    r[r.isna() & (df.ratio < MIN_LENGTH_RATIO)] = "ratio_below_feasible"
    r[r.isna() & (df.length_est_m <= FLOOR_M + 0.05)] = "radar_at_pixel_floor"
    return r


def load_live() -> tuple[pd.DataFrame, dict]:
    files = [f for f in sorted(glob.glob(str(LIVE_DIR / "live_S1*.gpkg")))]
    frames = [pyogrio.read_dataframe(f, layer="contacts_4326", read_geometry=False) for f in files]
    c = pd.concat(frames, ignore_index=True).drop_duplicates("det_id") if frames else pd.DataFrame()
    info = {"files": [str(f).split("/")[-1] for f in files], "contacts": int(len(c)),
            "matched": int((c.ais_status == "matched").sum()) if len(c) else 0}
    if not len(c):
        return pd.DataFrame(), info
    m = c[c.ais_status == "matched"].copy()
    info["matched_with_ais_length"] = int((pd.to_numeric(m.length_ais_m, errors="coerce") > 0).sum())
    for k in ("length_est_m", "length_ais_m", "match_dist_m", "match_dt_s", "ais_sog_kn"):
        m[k] = pd.to_numeric(m[k], errors="coerce")
    m = m[(m.length_ais_m > 0) & (m.length_est_m > 0)].copy()
    m["quality_position"] = live_match_quality(m.match_dist_m, m.match_dt_s, m.ais_sog_kn, None, None)
    check = live_match_quality(m.match_dist_m, m.match_dt_s, m.ais_sog_kn, m.length_est_m, m.length_ais_m)
    info["stored_quality_reproduced"] = float(np.mean(check == m.match_quality.to_numpy(object))) if len(m) else None
    m["source"] = "live_aisstream"
    m["hull_length_m"] = m.length_ais_m
    m["group"] = m.scene_id.astype(str)
    m["pass_id"] = m.run_id.astype(str)
    m["ratio"] = m.length_est_m / m.hull_length_m
    m["speed_kn"] = m.ais_sog_kn
    ax = live_axes(m)
    m["az_e"], m["az_n"] = ax.az_e, ax.az_n
    m["ais_heading_deg"] = live_heading(m)
    m["axis_angle_deg"] = axis_angle_deg(m.ais_heading_deg, m.az_e, m.az_n)
    m = add_orientation(m)
    return m, info


# ----------------------------------------------------------------------------------------------- open validation: AI2
def window_axes(c: pd.DataFrame) -> pd.DataFrame:
    """Azimuth (row) axis per AI2 window from an affine fit of lon, lat on row, col over the window's candidates."""
    rows = []
    for wid, g in c.groupby("window_id"):
        if len(g) < 4 or g.row.nunique() < 2 or g.col.nunique() < 2:
            continue
        A = np.column_stack([np.ones(len(g)), g.row.to_numpy(float), g.col.to_numpy(float)])
        B, *_ = np.linalg.lstsq(A, np.column_stack([g.lon.to_numpy(float), g.lat.to_numpy(float)]), rcond=None)
        lat0 = math.radians(float(g.lat.mean()))
        rows.append({"window_id": wid, "az_e": B[1, 0] * math.cos(lat0) * 111320.0, "az_n": B[1, 1] * 110574.0})
    return pd.DataFrame(rows)


def load_ai2() -> tuple[pd.DataFrame, dict]:
    c = pd.read_parquet(ML / "candidates.parquet")
    lab = pd.read_parquet(ML / "ai2_s1_labels.parquet", columns=["label_id", "length_m", "width_m", "heading_deg", "speed_kn",
                                                                 "ship_type", "license"])
    p = c[c.is_vessel.astype(bool) & (c.label_length_m > 0) & (c.length_est_m > 0)].copy()
    p = p.merge(lab, left_on="match_label_id", right_on="label_id", how="left")
    p = p.merge(window_axes(c), on="window_id", how="left")
    p["source"] = "ai2_s1a"
    p["hull_length_m"] = p.label_length_m
    p["group"] = p.product_id.astype(str)
    p["det_id"] = p.cand_id.astype(str)
    p["ratio"] = p.length_est_m / p.hull_length_m
    p["axis_angle_deg"] = axis_angle_deg(p.heading_deg, p.az_e, p.az_n)
    p = add_orientation(p)
    p["exclusion"] = np.select([~p.confidence.isin(["high", "medium"]), p.ratio < MIN_LENGTH_RATIO, p.length_est_m <= FLOOR_M + 0.05],
                               ["class_not_contact", "ratio_below_feasible", "radar_at_pixel_floor"], default=None)
    info = {"candidates_within_50m_with_length": int(len(p)), "scenes": int(p.product_id.nunique()),
            "missions": p.mission.value_counts().to_dict(), "label_length_matches_label_table": float(np.mean(np.isclose(p.label_length_m, p.length_m))),
            "licence": sorted(p.license.dropna().unique().tolist())}
    return p, info


# ----------------------------------------------------------------------------------------------- research: GFW
def load_research() -> tuple[pd.DataFrame, dict, dict]:
    path = RESEARCH / "regional_identity.parquet"
    meta = {k.decode(): v.decode() for k, v in (pq.read_schema(path).metadata or {}).items() if k != b"pandas"}
    r = pd.read_parquet(path)
    m = r[(r.ais_status == "matched") & (r.identity_kind == "vessel") & (r.length_ais_m > 0) & (r.length_est_m > 0)].copy()
    info = {"matched": int((r.ais_status == "matched").sum()), "matched_vessel_with_registry_length": int(len(m)),
            "gear_with_length": int(((r.identity_kind == "gear") & (r.length_ais_m > 0)).sum())}
    sar = (m.match_method == "gfw_sar_cell_hour").to_numpy()
    nan = np.full(len(m), np.nan)
    blind = sar_quality(m.match_dist_m, m.gfw_sar_n_cand, m.gfw_sar_n_rivals, nan, nan, m.gfw_sar_ambiguous_cell,
                        m.identity_kind == "gear")
    check = sar_quality(m.match_dist_m, m.gfw_sar_n_cand, m.gfw_sar_n_rivals, m.length_est_m, m.length_ais_m,
                        m.gfw_sar_ambiguous_cell, m.identity_kind == "gear")
    info["stored_quality_reproduced_sar"] = float(np.mean(check[sar] == m.match_quality.to_numpy(object)[sar]))
    m["quality_position"] = np.where(sar, blind, m.match_quality.to_numpy(object))
    det = pyogrio.read_dataframe(DATA_DIR / "detections_regional.gpkg", layer="detections_regional_4326", read_geometry=False,
                                 columns=["det_id", "scr_vv_db", "scr_vh_db", "inc_angle_deg"])
    m = m.merge(det, on="det_id", how="left")
    allp = DATA_DIR / "detections_regional_all.gpkg"
    info["pixel_counts"] = "absent (data/detections_regional_all.gpkg not found)"
    if allp.exists():
        w = "det_id IN (" + ",".join(f"'{d}'" for d in m.det_id) + ")"
        a = pyogrio.read_dataframe(allp, layer="detections_regional_4326", where=w, read_geometry=False,
                                   columns=["det_id", "n_pixels", "pol_class", "width_est_m", "scene_id"])
        m = m.merge(a.drop_duplicates("det_id"), on="det_id", how="left")
        info["pixel_counts"] = f"data/detections_regional_all.gpkg, {int(m.n_pixels.notna().sum())} of {len(m)} pairs"
    m["source"] = "gfw_registry"
    m["hull_length_m"] = m.length_ais_m
    m["group"] = m.pass_id.astype(str)
    m["ratio"] = m.length_est_m / m.hull_length_m
    return m, info, meta


# ----------------------------------------------------------------------------------------------- diagnosis
def diagnose(df: pd.DataFrame) -> dict:
    """Bias diagnosis on the used pairs of one set (see module docstring of the script)."""
    if len(df) < 5:
        return {"n": int(len(df)), "note": "too few pairs"}
    x, y = df.length_est_m.to_numpy(float), df.hull_length_m.to_numpy(float)
    ratio = x / y
    out = {"n": int(len(df)), "median_ratio": round(float(np.median(ratio)), 3)}
    out["pixel_floor"] = {"pairs_at_floor": int(np.sum(x <= FLOOR_M + 0.05)), "pairs_hull_under_25m": int(np.sum(y < 25)),
                          "median_ratio_hull_under_25m": round(float(np.median(ratio[y < 25])), 2) if np.any(y < 25) else None,
                          "min_radar_length_m": round(float(np.min(x)), 1)}
    b_lin = LC._lad(np.column_stack([np.ones_like(y), y]), x)
    b_log = LC._lad(np.column_stack([np.ones_like(y), np.log(y)]), np.log(x))
    out["linear_fit_radar_on_hull"] = {"intercept_m": round(float(b_lin[0]), 1), "slope": round(float(b_lin[1]), 3)}
    out["loglog_fit_radar_on_hull"] = {"intercept": round(float(b_log[0]), 3), "slope": round(float(b_log[1]), 3)}
    bins = [0, 25, 50, 100, 200, 1e9]
    labs = ["<25", "25-50", "50-100", "100-200", ">=200"]
    cut = pd.cut(y, bins, right=False, labels=labs)
    out["ratio_by_hull_length"] = {str(k): {"n": int((cut == k).sum()), "median_ratio": round(float(np.median(ratio[cut == k])), 2)
                                            if (cut == k).any() else None} for k in labs}
    # size-adjusted log ratio: residual of log(radar/hull) on log(hull), median fit
    lr = np.log(ratio)
    b = LC._lad(np.column_stack([np.ones_like(y), np.log(y)]), lr)
    e = lr - (b[0] + b[1] * np.log(y))
    Z = LC.derive_covariates(df).reset_index(drop=True)
    tests = {}
    for c in DIAG_COVS:
        if c in Z:
            v = Z[c].to_numpy(float)
        elif c in df:
            v = pd.to_numeric(df[c], errors="coerce").to_numpy(float)
        else:
            v = np.full(len(df), np.nan)
        ok = np.isfinite(v) & np.isfinite(e)
        if ok.sum() >= 8 and np.unique(v[ok]).size > 1:
            rho, p = spearmanr(v[ok], e[ok])
            tests[c] = {"n": int(ok.sum()), "rho": round(float(rho), 3), "p": float(f"{p:.3g}")}
        else:
            tests[c] = {"n": int(ok.sum()), "rho": None, "p": None}
    out["spearman_size_adjusted_log_ratio"] = tests
    if "speed_kn" in df:
        s = df.speed_kn.to_numpy(float)
        still, moving = ratio[s < 1], ratio[s >= 5]
        out["by_speed"] = {"stationary_lt_1kn": {"n": int(still.size), "median_ratio": round(float(np.median(still)), 2) if still.size else None,
                                                 "share_ratio_gt_3": round(float(np.mean(still > 3)), 3) if still.size else None},
                           "moving_ge_5kn": {"n": int(moving.size), "median_ratio": round(float(np.median(moving)), 2) if moving.size else None,
                                             "share_ratio_gt_3": round(float(np.mean(moving > 3)), 3) if moving.size else None}}
    f = Z.fill_width_m.to_numpy(float)
    if np.isfinite(f).sum() >= 8:
        thin = f < 15
        out["by_fill_width"] = {"thin_lt_15m": {"n": int(np.sum(thin & np.isfinite(f))), "median_ratio": round(float(np.median(ratio[thin])), 2) if thin.any() else None},
                                "wide_ge_15m": {"n": int(np.sum(~thin & np.isfinite(f))), "median_ratio": round(float(np.median(ratio[~thin & np.isfinite(f)])), 2)},
                                "share_thin_among_ratio_gt_3": round(float(np.mean(thin[(ratio > 3) & np.isfinite(f)])), 3) if np.any((ratio > 3) & np.isfinite(f)) else None,
                                "share_thin_among_ratio_le_3": round(float(np.mean(thin[(ratio <= 3) & np.isfinite(f)])), 3)}
    if "axis_angle_deg" in df and df.axis_angle_deg.notna().sum() >= 8:
        a = df.axis_angle_deg.to_numpy(float)
        groups = {"along_azimuth_0_30": a < 30, "diagonal_30_60": (a >= 30) & (a < 60), "along_range_60_90": a >= 60}
        out["by_orientation"] = {k: {"n": int(v.sum()), "median_ratio": round(float(np.median(ratio[v])), 2) if v.any() else None}
                                 for k, v in groups.items()}
        if "speed_kn" in df:
            s = df.speed_kn.to_numpy(float)
            sp = {"stationary_lt_1kn": s < 1, "slow_1_5kn": (s >= 1) & (s < 5), "moving_ge_5kn": s >= 5}
            out["by_speed_and_orientation"] = {f"{ks}|{ko}": {"n": int((vs & vo).sum()), "median_ratio": round(float(np.median(ratio[vs & vo])), 2)
                                                             if (vs & vo).sum() >= 3 else None}
                                               for ks, vs in sp.items() for ko, vo in groups.items()}
    if "speed_kn" in df:
        still = df.speed_kn.to_numpy(float) < 1
        sub = {}
        for c, v in (("scr_max_db", Z.scr_max_db.to_numpy(float)), ("azimuth_alignment", df.get("azimuth_alignment", pd.Series(np.nan, index=df.index)).to_numpy(float))):
            ok = still & np.isfinite(v)
            if ok.sum() >= 8:
                rho, p = spearmanr(v[ok], e[ok])
                sub[c] = {"n": int(ok.sum()), "rho": round(float(rho), 3), "p": float(f"{p:.3g}")}
        out["spearman_stationary_only"] = sub
    if "dual_pol" in Z and np.isfinite(Z.dual_pol).sum() >= 8:
        d = Z.dual_pol.to_numpy(float)
        out["by_polarisation"] = {"dual": {"n": int(np.sum(d == 1)), "median_ratio": round(float(np.median(ratio[d == 1])), 2) if np.any(d == 1) else None},
                                  "single": {"n": int(np.sum(d == 0)), "median_ratio": round(float(np.median(ratio[d == 0])), 2) if np.any(d == 0) else None}}
    return out


# ----------------------------------------------------------------------------------------------- evaluation helpers
def evaluate(model: dict, df: pd.DataFrame, cal_build: str, fallback: dict | None = None) -> dict:
    """Metrics before (raw) and after (calibrated with the model's interval) on a pair table; rows the calibration
    leaves null (outside range, under the minimum claim) are counted, not scored."""
    cal = LC.make_calibration(cal_build, model, fallback)
    o = LC.apply_frame(df.reset_index(drop=True), cal)
    ok = o.length_cal_reason.isna().to_numpy()
    y = df.hull_length_m.to_numpy(float)
    x = df.length_est_m.to_numpy(float)
    by_radar = {}
    for lo_b, hi_b in RADAR_BINS:
        k = ok & (x >= lo_b) & (x < hi_b)
        if k.sum() >= 5:
            by_radar[f"{lo_b:g}-{hi_b:g}"] = LC.metrics(y[k], o.length_cal_m.to_numpy(float)[k], o.length_cal_lo_m.to_numpy(float)[k],
                                                        o.length_cal_hi_m.to_numpy(float)[k])
    return {"n": int(len(df)), "n_scored": int(ok.sum()), "null_reasons": o.length_cal_reason[~ok].value_counts().to_dict(),
            "before": LC.metrics(y[ok], x[ok]),
            "after": LC.metrics(y[ok], o.length_cal_m.to_numpy(float)[ok], o.length_cal_lo_m.to_numpy(float)[ok],
                                o.length_cal_hi_m.to_numpy(float)[ok]),
            "after_by_radar_length_m": by_radar}


def model_summary(m: dict) -> dict:
    keep = {k: v for k, v in m.items() if k not in ("knots_log_x", "knots_log_y")}
    if m["kind"] == "isotonic":
        keep["n_knots"] = len(m["knots_log_x"])
    return keep


def length_only_kind(table: list[dict]) -> str:
    r = next(t for t in table if t["kind"] == "ratio")
    ll = next(t for t in table if t["kind"] == "loglinear" and not t["covariates"])
    return "loglinear" if ll["male"] <= r["male"] * 0.95 else "ratio"


# =============================================================================================== main
quality_col = "quality_position" if args.select == "position" else "match_quality"
log(f"selection: {quality_col}")

live, live_info = load_live()
if len(live):
    live["exclusion"] = selection(live, quality_col)
    live["excl_stored"] = selection(live, "match_quality")
live_used = live[live.exclusion.isna()].copy() if len(live) else live
if len(live_used) < 5:
    raise SystemExit(f"only {len(live_used)} usable live pairs: an open calibration needs at least 5")
log(f"live: {live_info}; pairs {len(live)}, used {len(live_used)} in {live_used.group.nunique() if len(live_used) else 0} scene(s)")

ai2, ai2_info = load_ai2()
ai2_used = ai2[ai2.exclusion.isna()].reset_index(drop=True)
log(f"AI2: {ai2_info['candidates_within_50m_with_length']} pairs, used {len(ai2_used)} on {ai2_used.group.nunique()} scenes")

res, res_info, res_meta = load_research()
res["exclusion"] = selection(res, quality_col)
res["excl_stored"] = selection(res, "match_quality")
res_used = res[res.exclusion.isna()].reset_index(drop=True)
log(f"research: {res_info}; used {len(res_used)} on {res_used.group.nunique()} passes")

# --- diagnosis
diag_open = {"live_aisstream": diagnose(live_used), "ai2_s1a": diagnose(ai2_used)}
diag_res = {"gfw_registry": diagnose(res_used)}
all_live_contacts = pd.concat([pyogrio.read_dataframe(f, layer="contacts_4326", read_geometry=False, columns=["det_id", "length_est_m"])
                               for f in sorted(glob.glob(str(LIVE_DIR / "live_S1*.gpkg")))]).drop_duplicates("det_id")
floor_share_live = float(np.mean(pd.to_numeric(all_live_contacts.length_est_m, errors="coerce") <= FLOOR_M + 0.05))
reg = pyogrio.read_dataframe(DATA_DIR / "detections_regional.gpkg", layer="detections_regional_4326", read_geometry=False,
                             columns=["length_est_m"])
floor_share_reg = float(np.mean(reg.length_est_m <= FLOOR_M + 0.05))
diag_open["contacts_at_pixel_floor"] = {"live_contacts": round(floor_share_live, 3), "regional_contacts": round(floor_share_reg, 3),
                                        "floor_m": round(FLOOR_M, 1)}

# --- structure from the open AI2 grouped CV
ai2_Z = LC.derive_covariates(ai2_used)
sel_ai2 = LC.select_model(ai2_used.length_est_m, ai2_used.hull_length_m, ai2_used.group, ai2_Z,
                          candidates=["scr_max_db", "fill_width_m", "dual_pol"])
open_kind = length_only_kind(sel_ai2["table"])
log(f"AI2 selection: {sel_ai2['kind']} {sel_ai2['covariates']}; open length-only form: {open_kind}")

# --- open model: live pairs only
open_groups = live_used.group.to_numpy(object)
open_model = LC.build_model(open_kind, live_used.length_est_m, live_used.hull_length_m, open_groups)
cal_open_tmp = LC.make_calibration("open", open_model)
o = LC.apply_frame(live_used.reset_index(drop=True), cal_open_tmp)
open_insample = {"before": LC.metrics(live_used.hull_length_m, live_used.length_est_m),
                 "after_in_sample": LC.metrics(live_used.hull_length_m.to_numpy(), o.length_cal_m.to_numpy(float),
                                               o.length_cal_lo_m.to_numpy(float), o.length_cal_hi_m.to_numpy(float)),
                 "null_reasons": o.length_cal_reason.dropna().value_counts().to_dict()}
open_cv = None
if live_used.group.nunique() >= 2:
    b, a = LC.cv_report(open_kind, live_used.length_est_m, live_used.hull_length_m, open_groups)
    a.pop("cv")
    open_cv = {"before": b, "after": a}
open_on_ai2 = evaluate(open_model, ai2_used, "open")
open_kinds = {}
for k in ("ratio", "loglinear", "isotonic"):  # in-sample comparison only; no grouped CV possible on one scene
    mk = LC.fit_model(k, live_used.length_est_m, live_used.hull_length_m)
    open_kinds[k] = {"ai2_external": evaluate({**mk, "interval": open_model["interval"],
                                               "valid_length_est_m": open_model["valid_length_est_m"]}, ai2_used, "open")["after"]}
stored_live = live[live.excl_stored.isna()]
open_sens = {"fit_set": "stored match_quality high or medium", "n": int(len(stored_live)),
             "median_ratio_before": round(float(np.median(stored_live.ratio)), 3) if len(stored_live) else None}
if len(stored_live) >= 5:
    ms = LC.build_model(open_kind, stored_live.length_est_m, stored_live.hull_length_m, stored_live.group)
    open_sens["model"] = model_summary(ms)
    open_sens["ai2_external"] = evaluate(ms, ai2_used, "open")["after"]

# AI2 alternative (open data, not applied)
ai2_alt = LC.build_model(sel_ai2["kind"], ai2_used.length_est_m, ai2_used.hull_length_m, ai2_used.group, ai2_Z, sel_ai2["covariates"])
b, a = LC.cv_report(sel_ai2["kind"], ai2_used.length_est_m, ai2_used.hull_length_m, ai2_used.group, ai2_Z, sel_ai2["covariates"])
ai2_cv_arrays = a.pop("cv")
ai2_alt_cv = {"before": b, "after": a}
ai2_fb = (LC.build_model(open_kind, ai2_used.length_est_m, ai2_used.hull_length_m, ai2_used.group)
          if sel_ai2["covariates"] else None)
if True:
    cal_alt = LC.make_calibration("open", ai2_alt, ai2_fb)
    oo = LC.apply_frame(live_used.reset_index(drop=True), cal_alt)
    ok = oo.length_cal_reason.isna().to_numpy()
    ai2_alt_on_live = {"n": int(len(live_used)), "n_scored": int(ok.sum()),
                       "before": LC.metrics(live_used.hull_length_m.to_numpy()[ok], live_used.length_est_m.to_numpy()[ok]),
                       "after": LC.metrics(live_used.hull_length_m.to_numpy()[ok], oo.length_cal_m.to_numpy(float)[ok],
                                           oo.length_cal_lo_m.to_numpy(float)[ok], oo.length_cal_hi_m.to_numpy(float)[ok])}
log(f"open model {model_summary(open_model)}")
log(f"open in-sample {open_insample}")
log(f"open on AI2 {open_on_ai2}")
log(f"AI2 alternative CV {ai2_alt_cv}")

# --- research model: GFW pairs plus live pairs, leave-one-pass-out
rcols = ["det_id", "source", "group", "pass_id", "length_est_m", "hull_length_m", "scr_vv_db", "scr_vh_db", "n_pixels",
         "pol_class", "inc_angle_deg", "cnn_score", "mission"]
both = pd.concat([res_used.reindex(columns=rcols), live_used.assign(group=live_used.pass_id).reindex(columns=rcols)],
                 ignore_index=True)
both_Z = LC.derive_covariates(both)
cands = ["scr_max_db", "fill_width_m", "inc_angle_deg", "dual_pol", "mission_s1d"]  # measurement covariates only (no cnn_score)
LOPO = 40  # max folds above the number of passes: one fold per pass (leave-one-pass-out)
sel_res = LC.select_model(both.length_est_m, both.hull_length_m, both.group, both_Z, candidates=cands, max_folds=LOPO)
log(f"research selection: {sel_res['kind']} {sel_res['covariates']}")
res_model = LC.build_model(sel_res["kind"], both.length_est_m, both.hull_length_m, both.group, both_Z, sel_res["covariates"],
                           max_folds=LOPO)
res_fb_kind = length_only_kind(sel_res["table"])
res_fallback = (LC.build_model(res_fb_kind, both.length_est_m, both.hull_length_m, both.group, max_folds=LOPO)
                if sel_res["covariates"] else None)
b, a = LC.cv_report(sel_res["kind"], both.length_est_m, both.hull_length_m, both.group, both_Z, sel_res["covariates"],
                    max_folds=LOPO)
res_cv_arrays = a.pop("cv")
res_cv = {"before": b, "after": a}
by_source = {}
for s in ("gfw_registry", "live_aisstream"):
    k = (both.source == s).to_numpy()
    if k.any():
        by_source[s] = {"before": LC.metrics(both.hull_length_m[k], both.length_est_m[k]),
                        "after": LC.metrics(both.hull_length_m.to_numpy()[k], np.exp(res_cv_arrays["pred_log"][k]),
                                            np.exp(res_cv_arrays["lo_log"][k]), np.exp(res_cv_arrays["hi_log"][k]))}
res_cv["by_source"] = by_source
res_kinds = {}
for k in ("ratio", "loglinear", "isotonic"):
    bb, aa = LC.cv_report(k, both.length_est_m, both.hull_length_m, both.group, max_folds=LOPO)
    aa.pop("cv")
    res_kinds[k] = aa
gfw_only = res_used
bg, ag = LC.cv_report(res_fb_kind, gfw_only.length_est_m, gfw_only.hull_length_m, gfw_only.group, max_folds=LOPO)
ag.pop("cv")
res_gfw_only = {"kind": res_fb_kind, "before": bg, "after": ag}
stored_res = res[res.excl_stored.isna()].reset_index(drop=True)
bs, as_ = LC.cv_report(res_fb_kind, stored_res.length_est_m, stored_res.hull_length_m, stored_res.group, max_folds=LOPO)
as_.pop("cv")
res_sens = {"fit_set": "stored match_quality high or medium (GFW pairs only)", "n": int(len(stored_res)),
            "passes": int(stored_res.group.nunique()), "before": bs, "after": as_}
res_on_ai2 = evaluate(res_model, ai2_used, "research", res_fallback)
# cnn_score is not a candidate covariate: on GFW cell-level pairs a low score marks a return that is probably not the
# matched ship (radar 20 to 60 m against hulls of 140 m and more), so it lowers the CV error by flagging pairing errors.
cnn_check = {}
for covs in ([], ["cnn_score"]):
    _, aa = LC.cv_report("loglinear", both.length_est_m, both.hull_length_m, both.group, both_Z, covs, max_folds=LOPO)
    aa.pop("cv")
    cnn_check["loglinear+" + ("+".join(covs) or "none")] = aa
lowcnn = res_used[res_used.cnn_score < 0.632]
cnn_check["gfw_pairs_cnn_below_threshold"] = {"n": int(len(lowcnn)), "median_ratio": round(float(lowcnn.ratio.median()), 3) if len(lowcnn) else None,
                                              "share_ratio_below_1": round(float((lowcnn.ratio < 1).mean()), 3) if len(lowcnn) else None}
open_on_gfw = evaluate(open_model, res_used, "open")
log(f"research model {model_summary(res_model)}")
log(f"research CV {res_cv}")
log(f"open model on GFW pairs {open_on_gfw}")

# --- what each calibration returns on the project's own contacts (open data: live passes and the regional run)
def coverage_on_contacts(cal: dict) -> dict:
    out = {}
    live_c = pd.concat([pyogrio.read_dataframe(f, layer="contacts_4326", read_geometry=False)
                        for f in sorted(glob.glob(str(LIVE_DIR / "live_S1*.gpkg")))]).drop_duplicates("det_id")
    reg_c = pyogrio.read_dataframe(DATA_DIR / "detections_regional.gpkg", layer="detections_regional_4326", read_geometry=False)
    for name, d in (("live_contacts", live_c), ("regional_contacts", reg_c)):
        o = LC.apply_frame(d, cal)
        out[name] = {"n": int(len(d)), "share_by_reason": {str(k if isinstance(k, str) else "calibrated"): round(float(v), 4) for k, v in
                                                           o.length_cal_reason.fillna("calibrated").value_counts(normalize=True).items()},
                     "median_length_cal_m": round(float(o.length_cal_m.median()), 1) if o.length_cal_m.notna().any() else None}
    out["note"] = "regional contacts carry no n_pixels in data/detections_regional.gpkg, so a model with fill_width_m uses its fallback"
    return out


# =============================================================================================== outputs
SOURCES = {
    "s1_grd_spec": "https://sentiwiki.copernicus.eu/web/s1-products (IW GRDH: resolution 20 x 22 m, pixel spacing 10 x 10 m, "
                   "5 x 1 looks, ENL 4.4; Hamming weighting 0.70 to 0.75; resolved 2026-10-10)",
    "ai2_labels": "https://github.com/allenai/vessel-detection-sentinels (README: point labels with properties Length, Width, "
                  "Heading, ShipAndCargoType, Speed; LICENSE Apache-2.0; resolved 2026-10-10)",
    "stasolla_greidanus_2016": "doi:10.1080/2150704X.2016.1226522 (Remote Sensing Letters 7(12):1219-1228; abstract read via "
                               "https://api.openalex.org/works/doi:10.1080/2150704X.2016.1226522, resolved 2026-10-10)",
    "conformal_jackknife": "doi:10.1214/20-AOS1965 (Barber, Candes, Ramdas, Tibshirani 2021, Annals of Statistics 49(1); "
                           "resolved 2026-10-10)",
}
selection_text = (f"matched pairs with a hull length; {quality_col} high or medium"
                  + (" (position quality: the match quality rule without its radar/AIS length terms)" if args.select == "position" else "")
                  + f"; review_note not 'doubtful'; radar/hull ratio at least {MIN_LENGTH_RATIO:g} (live matcher feasibility rule)")
reliability = (
    f"The open calibration rests on {len(live_used)} pairs from {live_used.group.nunique()} scene(s) of one live pass. No "
    "grouped cross-validation is possible with one scene, so its in-sample numbers are optimistic and its interval comes "
    "from leave-one-pair-out residuals within that scene. The AI2 Sentinel-1A pairs (other scenes, Sentinel-1A in 2022, same "
    "detector) are the open out-of-sample check. Treat the open calibration as provisional until pairs from at least "
    "three more scenes land; rerun this script after each live pass.")

open_cal = LC.make_calibration(
    "open", open_model, None,
    created_utc=NOW, target="AIS self-reported hull length (static message 5 or 24, dimension A + B), " + AIS_LABEL,
    contains_gfw_data=False, ais_source_label=AIS_LABEL,
    pairs={"source": "live passes, aisstream.io AIS", **live_info, "pairs_with_length": int(len(live)), "used": int(len(live_used)),
           "scenes": int(live_used.group.nunique()) if len(live_used) else 0,
           "passes": sorted(live_used.pass_id.unique().tolist()) if len(live_used) else [],
           "excluded": live.exclusion.value_counts().to_dict() if len(live) else {}, "selection": selection_text},
    structure={"form": open_kind, "chosen_by": "grouped CV on the AI2 Sentinel-1A pairs (open data): " + sel_ai2["rule"],
               "ai2_selection_table": sel_ai2["table"], "covariates": "none (too few pairs to select any)"},
    in_sample=open_insample, grouped_cv=open_cv if open_cv else "not possible: all open pairs come from one scene",
    external_validation_ai2={"data": "CFAR candidates within 50 m of an AI2 Sentinel-1A point label with a Length attribute "
                                     "(data/ml/candidates.parquet), same CFAR settings; " + AI2_LICENCE,
                             **ai2_info, "used": int(len(ai2_used)), "result": open_on_ai2, "by_form_fitted_on_live": open_kinds},
    alternative_ai2={"note": "open model fitted on the AI2 pairs; reported, not applied (the open calibration uses live pairs only)",
                     "selection": sel_ai2["covariates"], "model": model_summary(ai2_alt), "grouped_cv_by_scene": ai2_alt_cv,
                     "on_live_pairs": ai2_alt_on_live},
    sensitivity_stored_quality=open_sens, diagnosis=diag_open, reliability=reliability,
    on_project_contacts=coverage_on_contacts(LC.make_calibration("open", open_model)),
    use=("Producers add length_cal_m, length_cal_lo_m, length_cal_hi_m, length_cal_reason and length_cal_id next to the raw "
         "length_est_m with darkvessel.detect.length_cal.apply_frame(df, load_calibration(path)); length_est_m stays raw."),
    caveat=LENGTH_CAVEAT + " " + PRODUCT_CAVEAT, sources={k: v for k, v in SOURCES.items()}, script="scripts/35_length_calibration.py")
write_json(OUT_OPEN, open_cal)

pair_cols = ["det_id", "source", "run_id", "scene_id", "pass_id", "mission", "acq_utc", "mmsi", "ship_type", "length_est_m",
             "hull_length_m", "ratio", "match_quality", "quality_position", "review_note", "match_dist_m", "match_dt_s",
             "speed_kn", "confidence", "cnn_score", "inc_angle_deg", "pol_class", "n_pixels", "scr_vv_db", "scr_vh_db",
             "ais_heading_deg", "axis_angle_deg", "exclusion"]
op = live.reindex(columns=pair_cols).reset_index(drop=True)
op = pd.concat([op, LC.derive_covariates(live.reset_index(drop=True))[["scr_max_db", "fill_width_m"]]], axis=1)
oc = LC.apply_frame(live.reset_index(drop=True), LC.make_calibration("open", open_model)) if len(live) else pd.DataFrame()
for k in ("length_cal_m", "length_cal_lo_m", "length_cal_hi_m", "length_cal_reason"):
    op[k] = oc[k].to_numpy() if len(oc) else []
op["used"] = op.exclusion.isna()
for k in ("mmsi",):
    op[k] = pd.to_numeric(op[k], errors="coerce").astype("Int64")
write_parquet(OUT_OPEN_PAIRS, op, {"table": "open length-calibration pairs: live radar contacts matched to aisstream AIS with a static "
                                            "length; length_cal_* from the open calibration (in-sample for used rows)",
                                   "calibration_id": open_cal["calibration_id"], "use": "open build", "ais_source": AIS_LABEL,
                                   "selection": selection_text, "caveat": LENGTH_CAVEAT + " " + PRODUCT_CAVEAT})

res_cal = LC.make_calibration(
    "research", res_model, res_fallback,
    created_utc=NOW, target=("hull length: GFW vessels API registry length (registryInfo lengthM) for the regional pairs; "
                             "AIS static A + B for the live pairs"),
    contains_gfw_data=True, research_only=True,
    pairs={"gfw": {**res_info, "used": int(len(res_used)), "passes": int(res_used.group.nunique()),
                   "excluded": res.exclusion.value_counts().to_dict()},
           "live": {"used": int(len(live_used)), "passes": sorted(live_used.pass_id.unique().tolist())},
           "total_used": int(len(both)), "groups": int(both.group.nunique()), "selection": selection_text},
    structure={"chosen": sel_res, "fallback_form": res_fb_kind},
    grouped_cv_leave_one_pass_out=res_cv, cv_by_form_length_only=res_kinds, gfw_pairs_only=res_gfw_only,
    cnn_score_covariate_check=cnn_check,
    on_project_contacts=coverage_on_contacts(LC.make_calibration("research", res_model, res_fallback)),
    sensitivity_stored_quality=res_sens, external_validation_ai2=res_on_ai2, open_model_on_gfw_pairs=open_on_gfw,
    diagnosis=diag_res, caveat=LENGTH_CAVEAT + " " + res_meta.get("caveat", ""),
    sources=SOURCES, script="scripts/35_length_calibration.py",
    **{k: res_meta[k] for k in ("use", "licence", "licence_url", "terms_url", "attribution", "dataset") if k in res_meta},
    datasets=json.loads(res_meta.get("datasets", "{}")), accessed_by_dataset=json.loads(res_meta.get("accessed_by_dataset", "{}")))
write_json(OUT_RES, res_cal)

rp = pd.concat([res.reindex(columns=pair_cols + ["pass_id", "match_method", "gfw_vessel_id", "width_est_m"]).loc[:, lambda d: ~d.columns.duplicated()],
                live.reindex(columns=pair_cols)], ignore_index=True)
rp["research_only"] = rp.source == "gfw_registry"
rp = pd.concat([rp, LC.derive_covariates(pd.concat([res, live], ignore_index=True))[["scr_max_db", "fill_width_m"]]], axis=1)
rc = LC.apply_frame(pd.concat([res, live], ignore_index=True), res_cal)
for k in ("length_cal_m", "length_cal_lo_m", "length_cal_hi_m", "length_cal_reason"):
    rp[k] = rc[k].to_numpy()
rp["used"] = rp.exclusion.isna()
cvp = pd.DataFrame({"det_id": both.det_id, "cv_pred_m": np.exp(res_cv_arrays["pred_log"]), "cv_lo_m": np.exp(res_cv_arrays["lo_log"]),
                    "cv_hi_m": np.exp(res_cv_arrays["hi_log"]), "cv_fold": res_cv_arrays["folds"]})
rp = rp.merge(cvp, on="det_id", how="left")
rp["mmsi"] = pd.to_numeric(rp["mmsi"], errors="coerce").astype("Int64")
write_parquet(OUT_RES_PAIRS, rp, {"table": "research length-calibration pairs: regional contacts matched by GFW with a registry "
                                           "length, plus the live aisstream pairs; cv_* are leave-one-pass-out predictions",
                                  "calibration_id": res_cal["calibration_id"], "selection": selection_text,
                                  **{k: res_meta[k] for k in ("use", "licence", "licence_url", "terms_url", "attribution", "dataset",
                                                               "datasets", "accessed_by_dataset", "caveat") if k in res_meta},
                                  "dark_caveat": DARK_CAVEAT, "length_caveat": LENGTH_CAVEAT})
log(f"wrote {OUT_OPEN}, {OUT_OPEN_PAIRS}, {OUT_RES}, {OUT_RES_PAIRS}")

# =============================================================================================== figure
if not args.no_figure:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from matplotlib.ticker import FuncFormatter, NullFormatter

    INK, MUTED, GRID = "#1f2328", "#6e7781", "#d0d7de"
    PLAIN = FuncFormatter(lambda v, _: f"{v:g}")

    def plain_axis(axis, ticks):
        axis.set_major_locator(matplotlib.ticker.FixedLocator(ticks))
        axis.set_major_formatter(PLAIN)
        axis.set_minor_formatter(NullFormatter())
    C_OPEN, C_AI2, C_RES = "#0969da", "#8c959f", "#bc4c00"
    fig, axes = plt.subplots(2, 3, figsize=(15, 9.6), constrained_layout=True)
    lim = (8, 700)

    def frame(ax, title, xlabel, ylabel="hull length (m)"):
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlim(*lim)
        ax.set_ylim(*lim)
        t = np.array(lim)
        ax.plot(t, t, color=INK, lw=0.9)
        ax.fill_between(t, t / 2, t * 2, color=GRID, alpha=0.35, lw=0)
        ax.axhline(25, color=MUTED, lw=0.7, ls=":")
        ax.set_title(title, fontsize=10, loc="left", color=INK)
        ax.set_xlabel(xlabel, fontsize=9)
        ax.set_ylabel(ylabel, fontsize=9)
        ax.grid(True, which="major", color=GRID, lw=0.5)
        ax.tick_params(labelsize=8)
        plain_axis(ax.xaxis, [10, 25, 50, 100, 200, 400])
        plain_axis(ax.yaxis, [10, 25, 50, 100, 200, 400])

    def stats_text(m, interval=True):
        s = f"median ratio {m['median_ratio']:.2f}\nMAE {m['mae_m']:.0f} m\nwithin x1.5 {m['within_1_5']:.0%}, x2 {m['within_2']:.0%}"
        if interval and "coverage" in m:
            s += f"\n80 % interval coverage {m['coverage']:.0%}"
        return s

    # open, before
    ax = axes[0, 0]
    frame(ax, "Open build, before: raw radar length", "radar length estimate, length_est_m (m)")
    ax.scatter(ai2_used.length_est_m, ai2_used.hull_length_m, s=5, color=C_AI2, alpha=0.35, lw=0, label=f"AI2 S1A pairs (validation, n={len(ai2_used)})")
    ax.scatter(live_used.length_est_m, live_used.hull_length_m, s=26, color=C_OPEN, edgecolor="white", lw=0.5,
               label=f"live aisstream pairs (fit, n={len(live_used)})")
    ax.text(0.98, 0.03, "live: " + stats_text(open_insample["before"], False), transform=ax.transAxes, ha="right", va="bottom", fontsize=8, color=C_OPEN)
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    # open, after
    ax = axes[0, 1]
    frame(ax, "Open build, after: calibrated (live in-sample; AI2 out-of-sample)", "calibrated length, length_cal_m (m)")
    oa = LC.apply_frame(ai2_used, LC.make_calibration("open", open_model))
    ax.scatter(oa.length_cal_m, ai2_used.hull_length_m, s=5, color=C_AI2, alpha=0.35, lw=0)
    ol = LC.apply_frame(live_used.reset_index(drop=True), LC.make_calibration("open", open_model))
    ax.errorbar(ol.length_cal_m, live_used.hull_length_m, xerr=[ol.length_cal_m - ol.length_cal_lo_m, ol.length_cal_hi_m - ol.length_cal_m],
                fmt="o", ms=4.5, color=C_OPEN, ecolor=C_OPEN, elinewidth=0.6, alpha=0.9, mec="white", mew=0.5)
    ax.text(0.98, 0.03, "AI2: " + stats_text(open_on_ai2["after"]), transform=ax.transAxes, ha="right", va="bottom", fontsize=8, color=MUTED)
    ax.text(0.02, 0.97, "live (in-sample): " + stats_text(open_insample["after_in_sample"]), transform=ax.transAxes, ha="left", va="top",
            fontsize=8, color=C_OPEN)
    # diagnosis: speed and orientation (AI2 open pairs, AIS heading and speed; live pairs as points)
    ax = axes[0, 2]
    spd_bins = [("< 1 kn", -1, 1), ("1 to 5 kn", 1, 5), (">= 5 kn", 5, 1e9)]
    ori = [("ship along azimuth (0 to 30 deg)", 0, 30, "#0550ae"), ("diagonal (30 to 60 deg)", 30, 60, "#8250df"),
           ("ship along range (60 to 90 deg)", 60, 91, "#bf8700")]
    for k, (lab, a0, a1, col) in enumerate(ori):
        xs, med, q1, q3 = [], [], [], []
        for i, (_, s0, s1) in enumerate(spd_bins):
            r = ai2_used[(ai2_used.speed_kn >= s0) & (ai2_used.speed_kn < s1) & (ai2_used.axis_angle_deg >= a0) & (ai2_used.axis_angle_deg < a1)].ratio
            if len(r) >= 10:
                xs.append(i + (k - 1) * 0.12)
                med.append(r.median())
                q1.append(r.quantile(0.25))
                q3.append(r.quantile(0.75))
        med = np.array(med)
        ax.errorbar(xs, med, yerr=[med - np.array(q1), np.array(q3) - med], fmt="o-", ms=5, lw=1.2, color=col, capsize=2,
                    label=f"AI2: {lab}")
    lv = live_used.dropna(subset=["speed_kn"])
    sb = np.select([lv.speed_kn < 1, lv.speed_kn < 5], [0, 1], 2) + 0.3
    ax.scatter(sb, lv.ratio, s=18, color=C_OPEN, edgecolor="white", lw=0.5, label="live aisstream pairs", zorder=3)
    ax.set_yscale("log")
    ax.axhline(1, color=INK, lw=0.9)
    ax.set_xticks(range(3))
    ax.set_xticklabels([b[0] for b in spd_bins], fontsize=8)
    ax.set_xlabel("AIS speed over ground", fontsize=9)
    ax.set_ylabel("radar / hull length, median and quartiles", fontsize=9)
    ax.set_title("Diagnosis: moving ships and ships along azimuth read longer", fontsize=10, loc="left", color=INK)
    plain_axis(ax.yaxis, [0.5, 1, 1.5, 2, 3, 4, 5])
    ax.grid(True, which="major", color=GRID, lw=0.5)
    ax.tick_params(labelsize=8)
    ax.legend(loc="upper left", fontsize=7.5, frameon=False)
    # research, before
    ax = axes[1, 0]
    frame(ax, "Research build (GFW, CC BY-NC 4.0, noncommercial), before", "radar length estimate, length_est_m (m)")
    g = both.source == "gfw_registry"
    ax.scatter(both.length_est_m[g], both.hull_length_m[g], s=22, color=C_RES, edgecolor="white", lw=0.5,
               label=f"GFW registry lengths (n={int(g.sum())})")
    ax.scatter(both.length_est_m[~g], both.hull_length_m[~g], s=22, color=C_OPEN, edgecolor="white", lw=0.5,
               label=f"live aisstream (n={int((~g).sum())})")
    ax.text(0.98, 0.03, stats_text(res_cv["before"], False), transform=ax.transAxes, ha="right", va="bottom", fontsize=8, color=C_RES)
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    # research, after (CV)
    ax = axes[1, 1]
    frame(ax, "Research build, after: leave-one-pass-out predictions", "calibrated length, held-out (m)")
    pr, lo_, hi_ = np.exp(res_cv_arrays["pred_log"]), np.exp(res_cv_arrays["lo_log"]), np.exp(res_cv_arrays["hi_log"])
    for sel_, col in ((g.to_numpy(), C_RES), (~g.to_numpy(), C_OPEN)):
        ax.errorbar(pr[sel_], both.hull_length_m.to_numpy()[sel_], xerr=[np.nan_to_num(pr[sel_] - lo_[sel_]), np.nan_to_num(hi_[sel_] - pr[sel_])],
                    fmt="o", ms=4.0, color=col, ecolor=col, elinewidth=0.6, alpha=0.9, mec="white", mew=0.5)
    ax.text(0.98, 0.03, stats_text(res_cv["after"]), transform=ax.transAxes, ha="right", va="bottom", fontsize=8, color=C_RES)
    # research diagnosis: size-adjusted ratio by hull length bins
    ax = axes[1, 2]
    for d_, col, lab in ((ai2_used, C_AI2, "AI2 S1A"), (res_used, C_RES, "GFW registry"), (live_used, C_OPEN, "live aisstream")):
        bins = np.array([10, 25, 50, 100, 200, 450])
        mid, med, q1, q3 = [], [], [], []
        for lo_b, hi_b in zip(bins[:-1], bins[1:]):
            s = d_[(d_.hull_length_m >= lo_b) & (d_.hull_length_m < hi_b)].ratio
            if len(s) >= 5:
                mid.append(math.sqrt(lo_b * hi_b))
                med.append(s.median())
                q1.append(s.quantile(0.25))
                q3.append(s.quantile(0.75))
        mid, med = np.array(mid), np.array(med)
        ax.errorbar(mid * (1.0 if lab == "AI2 S1A" else 1.06 if lab == "GFW registry" else 0.94), med,
                    yerr=[med - np.array(q1), np.array(q3) - med], fmt="o-", ms=4, lw=1, color=col, label=lab, capsize=2)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.axhline(1, color=INK, lw=0.9)
    ax.set_xlabel("hull length (m)", fontsize=9)
    ax.set_ylabel("radar / hull length, median and quartiles", fontsize=9)
    ax.set_title("Diagnosis: short hulls read relatively longer", fontsize=10, loc="left", color=INK)
    plain_axis(ax.yaxis, [0.5, 1, 1.5, 2, 3, 4, 5])
    plain_axis(ax.xaxis, [12, 25, 50, 100, 200, 400])
    ax.grid(True, which="major", color=GRID, lw=0.5)
    ax.tick_params(labelsize=8)
    ax.legend(loc="upper right", fontsize=8, frameon=False)
    fig.suptitle("Radar length calibration (shaded: within a factor 2; dotted: 25 m, no claim below). Open build uses aisstream "
                 "pairs only; research build adds GFW registry lengths (CC BY-NC 4.0, noncommercial).", fontsize=10, color=INK)
    fig.text(0.5, -0.01, "Contains modified Copernicus Sentinel data 2020-2026. Live AIS relayed by aisstream.io (terms UNVERIFIED). "
             "AI2 vessel labels, Apache-2.0. Global Fishing Watch vessel registry data, CC BY-NC 4.0 (research panels only). "
             "'Dark' means only no AIS match; it does not mean illegal.", ha="center", fontsize=7.5, color=MUTED)
    FIG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG, dpi=130, bbox_inches="tight")
    log(f"wrote {FIG}")
