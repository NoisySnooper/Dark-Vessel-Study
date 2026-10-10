"""Passes (contract 3.6): the ESA plan and repeat predictions (`data/s1_next_passes.json`, footprints from
`ais_live.gpkg` `s1_next_passes_4326`), the processed live passes (`live_contacts.gpkg` `scenes_4326`) and the
September regional passes (`scenes_processed_4326`, grouped by mission and start within 10 min).
"""

from __future__ import annotations

import pandas as pd
import shapely

from ..records import iso_z, to_utc_series
from .common import geojson
from .contacts import group_passes

AIS_SUM = ["ais_aoi_positions", "ais_footprint_positions"]
AIS_MAX = ["ais_aoi_mmsi", "ais_footprint_mmsi", "ais_near_footprint_mmsi"]


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
    if sc is not None and len(sc):
        for rid, s in sc.groupby("run_id"):
            n = counts.get(str(rid), {})
            row = {
                "pass_id": str(rid), "mission": str(s["mission"].iloc[0]),
                "relative_orbit": int(s["orbit_rel"].iloc[0]) if pd.notna(s["orbit_rel"].iloc[0]) else None,
                "pass_dir": s["pass_dir"].iloc[0], "start_utc": iso_z(to_utc_series(s["start_utc"]).min()),
                "stop_utc": iso_z(to_utc_series(s["stop_utc"]).max()), "status": "past", "sources": ["processed"],
                "footprint": _footprint(s.geometry), "aoi_overlap_km2": float(pd.to_numeric(s["aoi_overlap_km2"], errors="coerce").sum()),
                "aoi_parts": None, "scenes": [str(x) for x in s["product_id"]], "processed": True, "n_contacts": n,
                "ais_heard_share": None, "note": coverage_note(n, s), "_src": "det_live",
                "_extra": {"scene_counts": s.drop(columns=["geometry", "caveat"], errors="ignore").to_dict("records"),
                           "summary": (summary.get("passes") or {}).get(str(rid))},
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
