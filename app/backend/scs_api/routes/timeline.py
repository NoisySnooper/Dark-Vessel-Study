"""Timeline rows (spec section 7): passes, contacts per pass by AIS status, AIS recording hours and recorder gaps,
VIIRS nights, events and upcoming passes, inside a time window.
"""

from __future__ import annotations

from typing import Literal, Optional

import pandas as pd
from fastapi import APIRouter

from ..envelope import item_response
from .common import window

MAX_EVENTS = 500


def router(store) -> APIRouter:
    r = APIRouter(tags=["timeline"])

    @r.get("/timeline", responses={200: {"description": "item: passes, contactsByPass, aisHours, aisGaps, viirsNights, events, upcoming"}})
    def timeline(t0: Optional[str] = None, t1: Optional[str] = None, bin: Literal["pass", "hour", "day"] = "pass"):
        w = window(t0, t1)
        cav = store.cav()
        pos, _ = store.passes_query({"t0": w["t0"], "t1": w["t1"]})
        passes = store.pass_rows(pos, footprint=False)
        df = store.data["contacts"].df
        rows = []
        if len(df):
            m = store.product_mask.copy()
            if w["t0"] is not None:
                m &= (df["_t"] >= w["t0"]).to_numpy()
            if w["t1"] is not None:
                m &= (df["_t"] <= w["t1"]).to_numpy()
            d = df[m]
            if bin == "pass":
                g = d.groupby(["pass_id", "ais_status"]).size().unstack(fill_value=0)
                start = d.groupby("pass_id")["_t"].min()
                for pid, cnt in g.iterrows():
                    rows.append({"pass_id": pid, "start_utc": start[pid].strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 "counts": {k: int(v) for k, v in cnt.items() if v}, "caveat": cav})
            else:
                key = d["_t"].dt.floor("h" if bin == "hour" else "D")
                g = d.groupby([key, "ais_status"]).size().unstack(fill_value=0)
                for t, cnt in g.iterrows():
                    rows.append({"pass_id": None, "start_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 "counts": {k: int(v) for k, v in cnt.items() if v}, "caveat": cav})
            rows.sort(key=lambda x: x["start_utc"])
        hours = sorted(store.data["tracks"].recorded_hours)
        ts = [pd.to_datetime(h, format="%Y%m%dT%H", utc=True) for h in hours]
        ais_hours, gaps = [], []
        for i, t in enumerate(ts):
            if (w["t0"] is None or t + pd.Timedelta(hours=1) >= w["t0"]) and (w["t1"] is None or t <= w["t1"]):
                ais_hours.append({"start_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  "end_utc": (t + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")})
            if i and (t - ts[i - 1]) > pd.Timedelta(hours=1):
                gaps.append({"start_utc": (ts[i - 1] + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                             "end_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "label": "recorder gap",
                             "note": "no AIS was recorded in these hours; this is the recorder, not any vessel"})
        nights = []
        n = store.data["lights"].nights
        if n is not None and len(n):
            for _, row in n.iterrows():
                nights.append({"night": str(row["night"]), "n": int(row.get("lit_candidates_clear") or 0),
                               "n_all": int(row.get("lit_candidates") or 0), "moon_illum_pct_median": row.get("moon_illum_pct_median")})
        epos, etotal = store.events_query({"t0": w["t0"], "t1": w["t1"]})
        events = store.event_rows(epos[:MAX_EVENTS])
        upcoming = [p for p in passes if p["status"] == "upcoming"]
        item = {"passes": passes, "contactsByPass": rows, "aisHours": ais_hours, "aisGaps": gaps, "viirsNights": nights,
                "events": events, "events_total": int(etotal), "events_shown": len(events), "upcoming": upcoming,
                "bin": bin, "caveat": cav, "gap_note": "An AIS gap is not proof of intent."}
        return item_response(store.settings, item)

    return r
