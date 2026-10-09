"""Identity of a matched AIS vessel from aisstream static messages, flag from the MMSI's MID.

Static messages (ITU-R M.1371 message 5 for class A, message 24 parts A and B for class B) carry name, call sign,
IMO number (class A only), ship type code and the four hull dimensions from which the recorder derives a length.
`latest_static` of darkvessel.ais.aisstream keeps the latest non-null value per MMSI. A vessel heard only in
position reports has no static row; its name may still come from the position report metadata (aisstream fills
MetaData.ShipName from its own static memory) and its flag from the MID. Every identity field is a self-report of
the transponder: MMSI, name and dimensions can be wrong or spoofed, and the live feed may not have heard the static
message yet.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from darkvessel.live.mid import MID_FETCHED_UTC, MID_SOURCE_URL, flag_from_mmsi, mid_of

IDENTITY_STATIC = "aisstream static message (ITU-R M.1371 msg 5 or 24) for name, call sign, IMO, type, length; flag from the MMSI MID (ITU table)"
IDENTITY_POSITION = "MMSI only: no static message recorded for it; name (if any) from position report metadata; flag from the MMSI MID (ITU table)"
IDENTITY_TEXT = (f"{IDENTITY_STATIC}. When no static message was heard: {IDENTITY_POSITION}. MID table: {MID_SOURCE_URL} "
                 f"(fetched {MID_FETCHED_UTC}). All fields are transponder self-reports and can be wrong or spoofed.")


def identity_table(static_latest: pd.DataFrame, positions: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per MMSI: imo, vessel_name, call_sign, ship_type, length_ais_m, ais_class, identity_source, flag, mmsi_mid.

    `positions` (optional) supplies ais_class and a fallback name from the position reports.
    """
    cols = ["mmsi", "imo", "vessel_name", "call_sign", "ship_type", "length_ais_m", "ais_class", "identity_source", "flag", "mmsi_mid"]
    s = static_latest if static_latest is not None else pd.DataFrame()
    rows = pd.DataFrame({"mmsi": pd.Series(dtype="int64")})
    if len(s):
        rows = pd.DataFrame({
            "mmsi": s.mmsi.astype("int64").values,
            "imo": pd.array(s["imo"].values if "imo" in s else [None] * len(s), dtype="Int64"),
            "vessel_name": s["name"].astype(object).where(s["name"].notna(), None).values if "name" in s else None,
            "call_sign": s["callsign"].astype(object).where(s["callsign"].notna(), None).values if "callsign" in s else None,
            "ship_type": _type_label(s),
            "length_ais_m": pd.to_numeric(s["length_m"], errors="coerce").astype(float).values if "length_m" in s else np.nan,
        })
        rows["identity_source"] = IDENTITY_STATIC
    if positions is not None and len(positions):
        p = positions.sort_values("timestamp").drop_duplicates("mmsi", keep="last")
        extra = pd.DataFrame({"mmsi": p.mmsi.astype("int64").values,
                              "ais_class": p["ais_class"].values if "ais_class" in p else None,
                              "name_pos": p["ship_name"].values if "ship_name" in p else None})
        rows = rows.merge(extra, on="mmsi", how="outer")
        no_static = rows.identity_source.isna() if "identity_source" in rows else pd.Series(True, index=rows.index)
        if "vessel_name" not in rows:
            rows["vessel_name"] = None
        rows["vessel_name"] = rows.vessel_name.where(rows.vessel_name.notna(), rows.name_pos)
        rows.loc[no_static, "identity_source"] = IDENTITY_POSITION
        rows = rows.drop(columns=["name_pos"])
    for c in cols:
        if c not in rows:
            rows[c] = None
    rows["mmsi_mid"] = pd.array([mid_of(m) for m in rows.mmsi], dtype="Int64")
    rows["flag"] = [flag_from_mmsi(m) for m in rows.mmsi]
    return rows[cols].reset_index(drop=True)


def _type_label(s: pd.DataFrame):
    lab = s["ship_type_label"] if "ship_type_label" in s else pd.Series([None] * len(s), index=s.index)
    code = s["ship_type"] if "ship_type" in s else pd.Series([None] * len(s), index=s.index)
    out = []
    for a, b in zip(lab, code):
        if a is not None and not (isinstance(a, float) and np.isnan(a)) and a is not pd.NA:
            out.append(str(a))
        elif b is not None and b is not pd.NA and not (isinstance(b, float) and np.isnan(b)):
            out.append(f"code {int(b)}")
        else:
            out.append(None)
    return out


def attach_identity(det: pd.DataFrame, ident: pd.DataFrame, mmsi_col: str = "mmsi") -> pd.DataFrame:
    """Join identity fields onto rows with an MMSI; rows without one get nulls and identity_source None."""
    out = det.copy()
    fields = ["imo", "vessel_name", "call_sign", "ship_type", "length_ais_m", "ais_class", "identity_source", "flag", "mmsi_mid"]
    for c in fields:
        if c in out:
            out = out.drop(columns=c)
    m = pd.to_numeric(out[mmsi_col], errors="coerce")
    key = pd.array(np.where(m.notna(), m.fillna(0).astype("int64"), 0), dtype="Int64")
    key[m.isna().to_numpy()] = pd.NA
    out["_key"] = key
    j = ident.copy()
    j["_key"] = pd.array(j.mmsi.astype("int64"), dtype="Int64")
    j = j.drop(columns="mmsi").drop_duplicates("_key")
    out = out.merge(j, on="_key", how="left").drop(columns="_key")
    has = m.notna().to_numpy()
    # an MMSI that was matched but has no identity row still gets flag and MID from the number itself
    miss = has & out.identity_source.isna().to_numpy()
    if miss.any():
        mm = m[miss].astype("int64")
        out.loc[miss, "flag"] = [flag_from_mmsi(x) for x in mm]
        out.loc[miss, "mmsi_mid"] = pd.array([mid_of(x) for x in mm], dtype="Int64")
        out.loc[miss, "identity_source"] = IDENTITY_POSITION
    out.loc[~has, fields] = None
    out["imo"] = pd.array(pd.to_numeric(out["imo"], errors="coerce").astype("float").round(), dtype="Int64")
    out["mmsi_mid"] = pd.array(pd.to_numeric(out["mmsi_mid"], errors="coerce").astype("float").round(), dtype="Int64")
    out["length_ais_m"] = pd.to_numeric(out["length_ais_m"], errors="coerce").astype(float)
    return out
