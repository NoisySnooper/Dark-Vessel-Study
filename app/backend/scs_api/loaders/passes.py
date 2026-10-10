"""Passes (contract 3.6): the ESA plan and repeat predictions (`data/s1_next_passes.json`, footprints from
`ais_live.gpkg` `s1_next_passes_4326`), the processed live passes (`live_contacts.gpkg` `scenes_4326`) and the
September regional passes (`scenes_processed_4326`, grouped by mission and start within 10 min).
"""

from __future__ import annotations

import pandas as pd
import shapely

from ..config import AISSTREAM_LABEL
from ..records import iso_z, to_utc_series
from .common import geojson
from .contacts import group_passes

AIS_SUM = ["ais_aoi_positions", "ais_footprint_positions"]
AIS_MAX = ["ais_aoi_mmsi", "ais_footprint_mmsi", "ais_near_footprint_mmsi"]
# AIS-only vessel fields of a Pass record (contract 1.3.0; live layer ais_only_4326, darkvessel.live.schema)
AIS_ONLY_FIELDS = ["mmsi", "vessel_key", "vessel_name", "call_sign", "imo", "flag", "ship_type", "ais_class", "length_ais_m",
                   "sog_kn", "lon", "lat", "scene_id", "pred_method", "pred_dt_s", "n_reports", "on_tested_sea",
                   "dist_coast_km", "nearest_object_m", "nearest_object_class", "ambiguous_det_id", "oversized_det_id",
                   "identity_source", "identity_label"]


def ais_only_by_pass(cat) -> dict[str, list[dict]]:
    """AIS vessels placed inside a live pass's footprint that no contact matched, per run_id: vessels on tested sea
    first, then by AIS length (longest first). Every row carries the board D4.7 aisstream label."""
    from ..records import clean
    from .contacts import bool_series, digit_series

    a = cat.read_gpkg("live_ais_only")
    if a is None or not len(a) or "run_id" not in a:
        return {}
    a = a.copy()
    for c in ("mmsi", "imo"):
        if c in a:
            a[c] = digit_series(a[c])
    a["vessel_key"] = ("mmsi:" + a["mmsi"].astype(str)).where(a["mmsi"].notna(), None) if "mmsi" in a else None
    if "on_tested_sea" in a:
        a["on_tested_sea"] = bool_series(a["on_tested_sea"])
    a["identity_label"] = AISSTREAM_LABEL
    a["_tested"] = a["on_tested_sea"].map(lambda v: v is True) if "on_tested_sea" in a else False
    a["_len"] = pd.to_numeric(a.get("length_ais_m"), errors="coerce").fillna(-1.0)
    a = a.sort_values(["run_id", "_tested", "_len", "mmsi"], ascending=[True, False, False, True], kind="stable")
    keep = [c for c in AIS_ONLY_FIELDS if c in a]
    out = {}
    for rid, g in a.groupby("run_id", sort=False):
        rows = g[keep].to_dict("records")
        out[str(rid)] = [{c: clean(r.get(c)) for c in AIS_ONLY_FIELDS} for r in rows]
    return out


def azimuth_check(summary_pass: dict | None) -> dict | None:
    """The pass's azimuth-shift check as data/live/live_summary.json states it, with the scene id per entry.

    The producer (darkvessel.live.outputs) writes no scene id in an entry and leaves out scenes with no shifted vessel,
    so an entry's scene is known only when the entry names it or when every scene of `scene_ids` has an entry (both
    lists are in the producer's scene order). Otherwise `scene_id` is null (contract 3.6)."""
    if not summary_pass or not summary_pass.get("azimuth_check_by_scene"):
        return None
    rows = [dict(r) for r in summary_pass["azimuth_check_by_scene"] if isinstance(r, dict)]
    ids = summary_pass.get("scene_ids") or []
    whole = isinstance(ids, list) and len(ids) == len(rows)
    for k, r in enumerate(rows):
        if r.get("scene_id") is None:
            r["scene_id"] = ids[k] if whole else None
    return {"by_scene": rows, "note": summary_pass.get("azimuth_check_note")}


def _footprint(geoms) -> dict | None:
    gs = [g for g in geoms if g is not None and not g.is_empty]
    if not gs:
        return None
    u = shapely.union_all(gs)
    return geojson(u.simplify(0.005, preserve_topology=True), 4)


def coverage_note(n_contacts: dict, scenes: pd.DataFrame | None) -> str | None:
    """The Pass page's coverage result in words when most contacts are no_coverage (spec 4.7)."""
    total = sum(n_contacts.values()) if n_contacts else 0
    noc = n_contacts.get("no_coverage", 0) if n_contacts else 0
    if not total or noc * 2 <= total:
        return None
    ns = 0 if scenes is None else len(scenes)
    lo = hi = None
    if scenes is not None and "ais_aoi_positions" in scenes and len(scenes):
        a = pd.to_numeric(scenes["ais_aoi_positions"], errors="coerce").dropna()
        if len(a):
            lo, hi = int(a.min()), int(a.max())
    near = 0 if scenes is None or "ais_near_footprint_mmsi" not in scenes else int(pd.to_numeric(scenes["ais_near_footprint_mmsi"], errors="coerce").fillna(0).max())
    inside = 0 if scenes is None or "ais_footprint_positions" not in scenes else int(pd.to_numeric(scenes["ais_footprint_positions"], errors="coerce").fillna(0).sum())
    if near == 0 and inside == 0:
        s = (f"No AIS was heard inside or within 0.3 degree of any of the {ns} scenes, so none of the {total:,} contacts "
             f"could be checked against AIS.")
    else:
        s = f"{noc:,} of the {total:,} contacts lie where no AIS was heard during the window (no_coverage)."
    if lo is not None:
        s += (f" The feed was up: each scene window recorded {lo:,} to {hi:,} positions elsewhere in the AOI."
              if lo != hi else f" The feed was up: each scene window recorded {lo:,} positions elsewhere in the AOI.")
    return s + " no_coverage says nothing about the contact and never makes a lead."


def load(cat, settings, contacts_df: pd.DataFrame | None) -> pd.DataFrame:
    rows = []
    counts = {}
    if contacts_df is not None and len(contacts_df):
        c = contacts_df.loc[contacts_df["confidence"].astype(str) != "low", ["pass_id", "ais_status"]]  # two columns, not 100
        g = c.groupby(["pass_id", "ais_status"]).size()
        for (pid, st), n in g.items():
            counts.setdefault(str(pid), {})[str(st)] = int(n)

    plan = cat.read_json("pass_plan") or {}
    generated = plan.get("generated_utc")
    fp = cat.read_gpkg("pass_plan_layer", ["pass_group", "product_ids"], geometry=True)
    fp_by = {} if fp is None or not len(fp) else {k: _footprint(v.geometry) for k, v in fp.groupby("pass_group")}
    prods = {} if fp is None or not len(fp) or "product_ids" not in fp else \
        {k: sorted({p for x in v["product_ids"].dropna() for p in str(x).replace(";", ",").split(",") if p.strip()})
         for k, v in fp.groupby("pass_group")}
    for g in plan.get("pass_groups") or []:
        pid = g.get("pass_group")
        rows.append({
            "pass_id": pid, "mission": g.get("mission"), "relative_orbit": g.get("relative_orbit"), "pass_dir": g.get("pass_dir"),
            "start_utc": iso_z(g.get("start_utc")), "stop_utc": iso_z(g.get("stop_utc")), "status": g.get("status"),
            "sources": list(g.get("sources") or []), "footprint": fp_by.get(pid), "aoi_overlap_km2": g.get("aoi_overlap_km2"),
            "aoi_parts": g.get("aoi_parts"), "scenes": [p.strip() for p in prods.get(pid, [])], "processed": False,
            "n_contacts": None, "ais_heard_share": g.get("ais_heard_share"), "_src": "esa_acq_plan",
            "_extra": {"aoi_overlap_bbox": g.get("aoi_overlap_bbox"), "plan_rows": g.get("rows"), "plan_generated_utc": generated,
                       "plan_note": "a repeat_cycle prediction is not ESA's plan"},
        })

    sc = cat.read_gpkg("live_scenes", geometry=True)
    summary = cat.read_json("live_summary") or {}
    ais_only = ais_only_by_pass(cat)
    if sc is not None and len(sc):
        for rid, s in sc.groupby("run_id"):
            n = counts.get(str(rid), {})
            sp = (summary.get("passes") or {}).get(str(rid))
            row = {
                "pass_id": str(rid), "mission": str(s["mission"].iloc[0]),
                "relative_orbit": int(s["orbit_rel"].iloc[0]) if pd.notna(s["orbit_rel"].iloc[0]) else None,
                "pass_dir": s["pass_dir"].iloc[0], "start_utc": iso_z(to_utc_series(s["start_utc"]).min()),
                "stop_utc": iso_z(to_utc_series(s["stop_utc"]).max()), "status": "past", "sources": ["processed"],
                "footprint": _footprint(s.geometry), "aoi_overlap_km2": float(pd.to_numeric(s["aoi_overlap_km2"], errors="coerce").sum()),
                "aoi_parts": None, "scenes": [str(x) for x in s["product_id"]], "processed": True, "n_contacts": n,
                "ais_heard_share": None, "note": coverage_note(n, s), "_src": "det_live",
                "n_ais_only": len(ais_only.get(str(rid), [])) if cat.exists("live_ais_only") else None,
                "_ais_only": ais_only.get(str(rid), []) if cat.exists("live_ais_only") else None,
                "azimuth_check": azimuth_check(sp), "identity_label": AISSTREAM_LABEL,
                "_ais_only_time": iso_z(to_utc_series(s["start_utc"]).min()),
                "_azimuth_time": iso_z(summary.get("generated_utc")),
                "_extra": {"scene_counts": s.drop(columns=["geometry", "caveat"], errors="ignore").to_dict("records"),
                           "summary": sp},
            }
            for k in AIS_SUM:
                row[k] = int(pd.to_numeric(s[k], errors="coerce").fillna(0).sum()) if k in s else None
            for k in AIS_MAX:
                row[k] = int(pd.to_numeric(s[k], errors="coerce").fillna(0).max()) if k in s else None
            rows.append(row)

    rs = cat.read_gpkg("regional_scenes", geometry=True)
    if rs is not None and len(rs):
        rs = group_passes(rs)
        for pid, s in rs.groupby("pass_id"):
            n = counts.get(str(pid), {})
            rows.append({
                "pass_id": str(pid), "mission": str(s["mission"].iloc[0]),
                "relative_orbit": int(s["orbit_rel"].iloc[0]) if pd.notna(s["orbit_rel"].iloc[0]) else None,
                "pass_dir": s["pass_dir"].iloc[0], "start_utc": iso_z(s["_start"].min()), "stop_utc": iso_z(s["_start"].max() + pd.Timedelta(seconds=25)),
                "status": "past", "sources": ["processed"], "footprint": _footprint(s.geometry),
                "aoi_overlap_km2": float(pd.to_numeric(s.get("tested_km2"), errors="coerce").sum()), "aoi_parts": None,
                "scenes": [str(x) for x in s["product_id"]], "processed": True, "n_contacts": n, "ais_heard_share": None,
                "note": coverage_note(n, None), "_src": "det_regional",
                "_extra": {"run_id": "regional_2026-09", "stop_note": "stop_utc is the last scene start plus 25 s",
                           "tested_km2": float(pd.to_numeric(s.get("tested_km2"), errors="coerce").sum())},
            })
    df = pd.DataFrame(rows)
    if len(df):
        df["_t"] = to_utc_series(df["start_utc"])
        df["_t1"] = to_utc_series(df["stop_utc"])
        # link processed passes and plan groups of the same physical pass (one mission, start within 10 min)
        plan_rows = df[df["_src"] == "esa_acq_plan"]
        for i, r in df[df["processed"]].iterrows():
            near = plan_rows[(plan_rows["mission"] == r["mission"]) & ((plan_rows["_t"] - r["_t"]).abs() <= pd.Timedelta(minutes=10))]
            if len(near):
                j = near.index[0]
                df.at[i, "_extra"] = {**df.at[i, "_extra"], "plan_pass_id": df.at[j, "pass_id"]}
                df.at[j, "_extra"] = {**df.at[j, "_extra"], "processed_pass_id": r["pass_id"]}
        df = df.sort_values("_t").reset_index(drop=True)
    return df
