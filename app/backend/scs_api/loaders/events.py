"""Events (contract 3.4). Open: `data/events_open.gpkg` (pending, round 2 events task). Research: GFW gaps,
encounters, loitering and port visits mapped as the contract says (gap to AIS_SILENCE E8, encounter to ENCOUNTER E10,
loitering to LOITERING E11, port_visit to PORT_VISIT E14). GFW's `eez` column is not served: the only boundary layer
of the product is Marine Regions, off by default (owner rule).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from ..config import GAP_NOTE
from ..records import to_utc_series

GFW_TYPES = {"gap": ("E8", "AIS_SILENCE"), "encounter": ("E10", "ENCOUNTER"), "loitering": ("E11", "LOITERING"),
             "port_visit": ("E14", "PORT_VISIT")}
TYPE_CAVEAT = {"E8": GAP_NOTE, "E10": "Lawful transshipment, bunkering, pilot and supply transfers look identical."}
RULE_TEXT = {
    "E8": ("GFW AIS gap event as published by Global Fishing Watch: gaps of at least 12 h starting at least 50 nm from "
           "shore (GFW rule). gap_intentional_disabling is GFW's model flag, never shown as intent."),
    "E10": "GFW encounter event as published by Global Fishing Watch; its own parameters are in params.",
    "E11": "GFW loitering event as published by Global Fishing Watch; its own parameters are in params.",
    "E14": "GFW port visit event as published by Global Fishing Watch; its own parameters are in params.",
}
PARAM_PREFIX = ("gap_", "encounter_", "loitering_", "port_visit_", "port_")
DROP = {"eez", "licence", "use", "geometry"}
KEEP = ["event_id", "type", "start", "end", "lat", "lon", "vessel_id", "ssvid", "vessel_name", "flag", "vessel_type",
        "duration_h", "start_dist_shore_km", "end_dist_shore_km", "start_dist_port_km", "overlaps_window_only",
        "starts_in_window"]
BIG_EXTRA = {"gfw_loitering": ["loitering_total_time_h", "loitering_total_distance_km", "loitering_avg_speed_kn",
                               "loitering_avg_dist_shore_km"],
             "gfw_port_visits": ["port_visit_id", "port_visit_confidence", "port_visit_duration_h", "port_name", "port_flag",
                                 "port_id"]}


class EventsData:
    def __init__(self, df: pd.DataFrame, param_cols: list[str], extra_cols: list[str], open_fields: list[str] | None = None):
        self.df = df
        self.param_cols = param_cols
        self.extra_cols = extra_cols
        self.open_fields = open_fields
        self._pos = None

    @property
    def pos(self) -> pd.Series:
        """event_id to row position, built on first use (600k ids in the research build)."""
        if self._pos is None:
            p = pd.Series(np.arange(len(self.df)), index=self.df["event_id"].astype(str)) if len(self.df) else pd.Series(dtype=int)
            self._pos = p[~p.index.duplicated()]
        return self._pos


def grade(code: str, hours) -> str | None:
    if code != "E8" or hours is None or not np.isfinite(hours):
        return None
    return "silence" if hours < 12 else "extended" if hours <= 72 else "prolonged"


def load(cat, settings) -> EventsData:
    if not settings.research:
        df = cat.read_gpkg("events_open")
        if df is None or not len(df):
            return EventsData(pd.DataFrame(columns=["event_id", "_t", "_t_end", "lon", "lat", "code", "event_type"]), [], [])
        df["_t"] = to_utc_series(df["start_utc"])
        df["_t_end"] = to_utc_series(df["end_utc"]) if "end_utc" in df else pd.NaT
        df["source"] = df.get("source", "aisstream")
        return EventsData(df.reset_index(drop=True), [], [c for c in df.columns if not c.startswith("_")], open_fields=list(df.columns))
    tables = []
    for key in ("gfw_gaps", "gfw_encounters"):
        tables += [cat.read_arrow(key, path=p) for p in cat.paths(key)]
    for key in ("gfw_loitering", "gfw_port_visits"):  # 597k rows: only the columns the API serves
        tables += [cat.read_arrow(key, KEEP + BIG_EXTRA[key], path=p) for p in cat.paths(key)]
    tables = [t for t in tables if t is not None and t.num_rows]
    if not tables:
        return EventsData(pd.DataFrame(columns=["event_id", "_t", "_t_end", "lon", "lat", "code", "event_type"]), [], [])
    tab = pa.concat_tables(tables, promote_options="permissive")
    try:  # Arrow parses the ISO 8601 times much faster than pandas on 600k rows
        t_start = pc.cast(tab["start"], pa.timestamp("us", tz="UTC")).to_pandas()
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        t_start = None
    df = tab.to_pandas()
    df = df.drop(columns=[c for c in DROP if c in df])
    t = df["type"].to_numpy(dtype=object)
    df["code"] = np.select([t == k for k in GFW_TYPES], [v[0] for v in GFW_TYPES.values()], None)
    df["event_type"] = np.select([t == k for k in GFW_TYPES], [v[1] for v in GFW_TYPES.values()], None)
    df["_t"] = t_start.array if t_start is not None else to_utc_series(df["start"])  # end time, cell id: per record
    df = df[df["code"].notna()].reset_index(drop=True)
    params = [c for c in df.columns if c.startswith(PARAM_PREFIX)]
    internal = {"_t", "code", "event_type", "event_id", "type", "start", "end", "lat", "lon",
                "vessel_id", "ssvid", "duration_h"} | set(params)
    extra = [c for c in df.columns if c not in internal]
    return EventsData(df, params, extra)
