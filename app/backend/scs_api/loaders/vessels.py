"""Vessels (contract 3.2): aisstream identities (both builds) and, in the research build, GFW identities with stubs for
nearest-AIS vessels the GFW table does not hold. Tracks come from the aisstream position cache (local app only) and, in
the research build, from GFW hourly presence cells of the pass hours.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import GAP_NOTE, PRODUCT_CAVEAT
from ..records import iso_series, to_utc_series
from .contacts import bool_series, digit_series

IDENTITY_NOTE = "Identity fields are self-reported by the transponder; they can be wrong, reused or spoofed."
GFW_NOTE = IDENTITY_NOTE + " Identity as published by Global Fishing Watch."
GAP_HOURS = 6.0
_AIS_MAP = {"callsign": "call_sign", "ship_type_label": "ship_type", "lon": "last_lon", "lat": "last_lat"}
_GFW_MAP = {"vessel_name": "name", "geartype": "gear_type", "transmission_from": "first_seen_utc",
            "transmission_to": "last_seen_utc", "ais_positions": "n_positions", "ais_messages": "n_messages"}
_EVT_MAP = {"ssvid": "mmsi", "shipname": "name", "callsign": "call_sign", "shiptype": "ship_type", "geartype": "gear_type",
            "transmission_from": "first_seen_utc", "transmission_to": "last_seen_utc", "ais_positions": "n_positions",
            "ais_messages": "n_messages"}
DROP = {"use", "licence", "geometry"}  # feed the registry entry, not the record


def _mid(mmsi: pd.Series) -> pd.Series:
    m = pd.to_numeric(mmsi, errors="coerce")
    ok = (m >= 200_000_000) & (m <= 799_999_999)
    return (m // 1_000_000).where(ok).astype("Int64").astype(object).where(ok, None)


class VesselsData:
    def __init__(self, df: pd.DataFrame, extra_cols: dict, recording_gaps: list, summary: dict):
        self.df = df
        self.extra_cols = extra_cols
        self.recording_gaps = recording_gaps
        self.summary = summary or {}
        self.pos = pd.Series(np.arange(len(df)), index=df["vessel_key"].astype(str)) if len(df) else pd.Series(dtype=int)
        self.pos = self.pos[~self.pos.index.duplicated()]


class TracksData:
    """Recorded aisstream positions by MMSI and, in the research build, GFW presence hours by vessel. A separate loader
    from the identities: the recorder rewrites its current hour file every minute, and that must not reload vessels."""

    def __init__(self, positions: pd.DataFrame | None, pos_index: dict, recorded_hours: set, presence: pd.DataFrame | None,
                 pres_index: dict, parts: dict):
        self.positions = positions
        self.pos_index = pos_index
        self.recorded_hours = recorded_hours
        self.presence = presence
        self.pres_index = pres_index
        self.parts = parts  # path -> (mtime_ns, frame): the next reload re-reads only changed hour files


def _ais_vessels(cat) -> tuple[pd.DataFrame | None, list[str]]:
    v = cat.read_gpkg("ais_vessels")
    if v is None or not len(v):
        return None, []
    from darkvessel.live.mid import flag_from_mmsi

    if "ship_type" in v and "ship_type_label" in v:  # the file's ship_type is the ITU-R M.1371 code; the label wins
        v["ship_type_code"] = v.pop("ship_type")
    v = v.rename(columns={k: x for k, x in _AIS_MAP.items() if k in v})
    v["mmsi"] = digit_series(v["mmsi"])
    v["imo"] = digit_series(v["imo"]) if "imo" in v else None
    v["vessel_key"] = "mmsi:" + v["mmsi"].astype(str)
    v["mid"] = _mid(v["mmsi"]) if "mid" not in v else pd.to_numeric(v["mid"], errors="coerce").astype("Int64").astype(object)
    v["flag"] = v["mmsi"].map(flag_from_mmsi)
    v["length_ais_m"] = v.get("length_m")
    v["identity_source"] = "aisstream static message"
    v["stub"] = False
    v["research_only"] = False
    v["_src"] = "aisstream"
    v["_note"] = IDENTITY_NOTE
    for c in ("in_aoi", "ever_in_aoi", "gear_beacon_like"):
        if c in v:
            v[c] = bool_series(v[c])
    if "ais_note" in v:
        v["_source_caveat"] = v.pop("ais_note")
    return v, []


def _gfw_table(df: pd.DataFrame, mapping: dict, identity_source: str | None) -> pd.DataFrame:
    df = df.rename(columns={k: x for k, x in mapping.items() if k in df})
    df["mmsi"] = digit_series(df["mmsi"]) if "mmsi" in df else None
    df["imo"] = digit_series(df["imo"]) if "imo" in df else None
    df["vessel_key"] = "gfw:" + df["vessel_id"].astype(str)
    df["mid"] = _mid(df["mmsi"])
    if identity_source and "identity_source" not in df:
        df["identity_source"] = identity_source
    if "registry_records" in df:
        df["registry_records"] = pd.to_numeric(df["registry_records"], errors="coerce").round().astype("Int64").astype(object)
    df["stub"] = False
    df["research_only"] = True
    df["_src"] = "gfw_vessels"
    df["_note"] = GFW_NOTE
    return df.drop(columns=[c for c in DROP if c in df])


def load(cat, settings, contacts_df: pd.DataFrame | None, fields: list[str]) -> VesselsData:
    frames = []
    ais, _ = _ais_vessels(cat)
    if ais is not None:
        frames.append(ais)
    if settings.research:
        g = cat.read_parquet("gfw_vessels")
        if g is not None and len(g):
            frames.append(_gfw_table(g, _GFW_MAP, None))
        have = set(frames[-1]["vessel_key"]) if g is not None and len(g) else set()
        ev = cat.read_parquet("gfw_events_vessels")
        if ev is not None and len(ev):
            ev = ev.drop_duplicates("vessel_id")
            ev = ev[np.array([("gfw:" + str(v)) not in have for v in ev["vessel_id"]], bool)]
            if len(ev):
                frames.append(_gfw_table(ev, _EVT_MAP, "GFW vessels API"))
                have |= set("gfw:" + ev["vessel_id"].astype(str))
        if contacts_df is not None and "nearest_ais_vessel_id" in contacts_df:
            m = (contacts_df["_source"].eq("research") & contacts_df["nearest_ais_vessel_id"].notna()).to_numpy()
            c = contacts_df.loc[m, ["nearest_ais_vessel_id", "nearest_ais_mmsi", "nearest_ais_name"]]
            c = c.drop_duplicates("nearest_ais_vessel_id")
            keys = ("gfw:" + c["nearest_ais_vessel_id"].astype(str)).to_numpy(dtype=object)
            c = c[np.array([k not in have for k in keys], bool)]
            if len(c):
                stub = pd.DataFrame({"vessel_id": c["nearest_ais_vessel_id"].astype(str).to_numpy(),
                                     "mmsi": c["nearest_ais_mmsi"].to_numpy(), "vessel_name": c.get("nearest_ais_name")})
                stub = _gfw_table(stub, _GFW_MAP, "GFW 4Wings presence (nearest-AIS vessel; no vessels-API record)")
                stub["stub"] = True
                frames.append(stub)
    df = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame(columns=fields + ["_src"])
    for c in ("first_seen_utc", "last_seen_utc", "static_seen_utc"):
        if c in df:
            df["_t_" + c] = to_utc_series(df[c])
            df[c] = iso_series(df[c])
    if settings.research and ais is not None and len(df):
        heard = set(ais["mmsi"].dropna())
        g = df["vessel_key"].str.startswith("gfw:") & df["mmsi"].isin(heard)
        df["_aisstream_key"] = np.where(g, "mmsi:" + df["mmsi"].astype(str), None)
    internal = {"_src", "_note", "_source_caveat", "_aisstream_key", "vessel_id"} | {c for c in df if c.startswith("_t_")}
    extra = [c for c in df.columns if c not in set(fields) and c not in internal]
    if "vessel_id" in df:
        extra.append("vessel_id")
    summary = cat.read_json("ais_summary") or {}
    gaps = summary.get("recording_gaps_over_10_min") or []
    return VesselsData(df, {"all": extra}, gaps, summary)


def load_tracks(cat, settings, prev: TracksData | None = None, changed: set | None = None) -> TracksData:
    """Positions from the aisstream hour files and, in the research build, the GFW presence hours of the pass hours.
    With `prev`, only new or changed hour files are re-read, and the presence table is kept unless `changed` names it."""
    positions, pos_index, hours, parts = _positions(cat, prev.parts if prev is not None else {})
    presence, pres_index = (None, {})
    if settings.research:
        if prev is not None and prev.presence is not None and "gfw_presence_passes" not in (changed or set()):
            presence, pres_index = prev.presence, prev.pres_index
        else:
            presence, pres_index = _presence(cat)
    return TracksData(positions, pos_index, hours, presence, pres_index, parts)


_POS_COLS = ["mmsi", "timestamp", "lon", "lat", "sog_kn", "cog_deg", "msg_type"]


def _positions(cat, prev_parts: dict):
    files = [p for p in cat.paths("ais_positions") if p.stem.isdigit() and len(p.stem) == 2]
    if not files:
        return None, {}, set(), {}
    parts = {}
    for p in files:
        try:
            mt = p.stat().st_mtime_ns
        except FileNotFoundError:  # removed between the listing and the read
            continue
        old = prev_parts.get(p)
        if old is not None and old[0] == mt:
            parts[p] = old
            continue
        df = cat.read_parquet("ais_positions", _POS_COLS, path=p)
        df["mmsi"] = digit_series(df["mmsi"])
        df["timestamp"] = to_utc_series(df["timestamp"])
        parts[p] = (mt, df.dropna(subset=["mmsi", "timestamp"]))
    if not parts:
        return None, {}, set(), {}
    df = pd.concat([f for _, f in parts.values()], ignore_index=True)
    df = df.sort_values(["mmsi", "timestamp"], kind="stable").reset_index(drop=True)
    starts = df.groupby("mmsi", sort=False).indices
    index = {k: (int(v[0]), int(v[-1]) + 1) for k, v in starts.items()}
    hours = {f"{p.parent.name}T{p.stem}" for p in parts}  # yyyymmddTHH with data
    return df, index, hours, parts


def _presence(cat):
    df = cat.read_parquet("gfw_presence_passes", ["vessel_id", "hour_ts", "lon", "lat", "hours", "pass_id"])
    if df is None or not len(df):
        return None, {}
    df["t"] = to_utc_series(df["hour_ts"])
    df = df.sort_values(["vessel_id", "t"]).reset_index(drop=True)
    idx = {"gfw:" + str(k): (int(v[0]), int(v[-1]) + 1) for k, v in df.groupby("vessel_id", sort=False).indices.items()}
    return df, idx


def track(data: TracksData, vessel_key: str, mmsi: str | None, t0=None, t1=None, max_points: int = 2000) -> dict:
    """FeatureCollection of positions with `t`, plus gaps over 6 h, each with the gap note (contract 3.2, Track)."""
    pts, note = None, None
    if vessel_key in data.pres_index and data.presence is not None:
        a, b = data.pres_index[vessel_key]
        p = data.presence.iloc[a:b]
        pts = pd.DataFrame({"t": p["t"], "lon": p["lon"], "lat": p["lat"], "sog_kn": np.nan, "cog_deg": np.nan,
                            "msg_type": "gfw_presence_hour"})
        note = ("GFW hourly AIS presence cells (0.01 degree) of the September pass hours, as published by Global "
                "Fishing Watch; not a full track.")
    elif mmsi and mmsi in data.pos_index and data.positions is not None:
        a, b = data.pos_index[mmsi]
        p = data.positions.iloc[a:b]
        pts = pd.DataFrame({"t": p["timestamp"], "lon": p["lon"], "lat": p["lat"], "sog_kn": p["sog_kn"],
                            "cog_deg": p["cog_deg"], "msg_type": p["msg_type"]})
        note = "Positions recorded from aisstream.io (shore receivers); the recorder has gaps of its own."
    if pts is None:
        return {"type": "FeatureCollection", "features": [], "gaps": [], "n_points": 0, "simplified": False,
                "note": "No recorded positions for this vessel in this build."}
    if t0 is not None:
        pts = pts[pts["t"] >= t0]
    if t1 is not None:
        pts = pts[pts["t"] <= t1]
    gaps = []
    ts = pts["t"].reset_index(drop=True)
    if len(ts) > 1:
        dt = ts.diff().dt.total_seconds().to_numpy() / 3600
        for i in np.flatnonzero(dt > GAP_HOURS):
            a, b = ts.iloc[i - 1], ts.iloc[i]
            hours = pd.date_range(a.floor("h"), b.floor("h"), freq="h")
            missing = any(h.strftime("%Y%m%dT%H") not in data.recorded_hours for h in hours) if data.recorded_hours else False
            gaps.append({"from_utc": a.strftime("%Y-%m-%dT%H:%M:%SZ"), "to_utc": b.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "minutes": round(float(dt[i]) * 60, 1), "note": GAP_NOTE,
                         "overlaps_recorder_gap": bool(missing)})
    n = len(pts)
    simplified = n > max_points > 1
    if simplified:
        keep = np.unique(np.linspace(0, n - 1, max_points).round().astype(int))
        pts = pts.iloc[keep]
    times = iso_series(pts["t"])
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(x), 5), round(float(y), 5)]},
              "properties": {"t": t, "sog_kn": s, "cog_deg": c, "msg_type": m}}
             for x, y, t, s, c, m in zip(pts["lon"], pts["lat"], times, pts["sog_kn"], pts["cog_deg"], pts["msg_type"])]
    return {"type": "FeatureCollection", "features": feats, "gaps": gaps, "n_points": n, "simplified": simplified,
            "note": note, "gap_note": GAP_NOTE, "caveat": PRODUCT_CAVEAT}
