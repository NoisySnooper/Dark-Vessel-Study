"""Live terrestrial AIS from aisstream.io: one websocket, normalised parquet partitions, reach statistics.

aisstream.io relays AIS messages from its network of shore receivers as JSON over a websocket
(wss://stream.aisstream.io/v0/stream, docs https://aisstream.io/documentation). The client opens
ONE connection (the service allows at most three per account), sends the subscription
{APIKey, BoundingBoxes [[[lat_min, lon_min], [lat_max, lon_max]], ...]} and reads until stopped.
It retries forever with capped exponential backoff and jitter (1 s doubling to 60 s, reset after the
first good frame), logs every failed attempt, keeps the socket alive with websocket pings, enables
permessage-deflate and treats a silent socket (no message for `idle_timeout_s`) as dead. A frame the
normaliser cannot handle is counted and skipped; it never drops the connection. A failed flush is
logged and retried at the next flush. Every logged error passes through `redact`, which removes the
registered key value and anything key-shaped.

Strings: text columns are object dtype with None for missing, in memory and after reading back.
pandas 3 reads parquet strings as its `str` dtype with NaN for missing; `positions_frame` and
`static_frame` convert that back, so old and new partitions load the same. Partitions are written with
fixed arrow schemas (POSITION_SCHEMA, STATIC_SCHEMA), so every hour file has the same column types.

Every message is kept raw (gzip JSON lines per hour) and normalised:
  positions  PositionReport (AIS 1, 2, 3), StandardClassBPositionReport (18),
             ExtendedClassBPositionReport (19), LongRangeAisBroadcastMessage (27)
             -> mmsi, timestamp (UTC, from MetaData.time_utc), lon, lat, sog_kn, cog_deg, heading,
                nav_status, msg_type, msg_id, ais_class (A or B), ship_name
  static     ShipStaticData (5), StaticDataReport (24 part A and B)
             -> mmsi, imo, name, callsign, ship_type, ship_type_label, length_m (A+B), width_m (C+D),
                destination, eta, seen_utc, msg_type
Not-available codes are nulled (lat 91, lon 181, SOG 102.3, COG 360, heading 511; LongRange SOG 63,
COG 511), ranges are checked and duplicates dropped.

Layout under data/cache/ais/aisstream/:
  positions/YYYYMMDD/HH.parquet   one partition per UTC hour, rewritten atomically at every flush
                                  (temp file, fsync, rename); an unreadable partition is moved aside
                                  as HH.parquet.corrupt-<time>, never overwritten
  static/YYYYMMDD.parquet         append-only static table (content-deduplicated, last seen kept)
  raw/YYYYMMDD/HH.jsonl.gz        the messages as received (one gzip member per flush; a kill can
                                  truncate only the last member, `read_raw` stops there)
  status.json, record.log, recorder.pid (watchdog files: see scripts/29_ais_watchdog.py)

Reach: `reach_grids` turns recorded positions into the share of recorded hours with at least one
position per grid cell and the distinct MMSI per cell. No AIS heard in a cell does not mean no
vessel: shore receivers reach a few tens of kilometres offshore, and many boats carry no AIS.

Terms: no terms-of-service or data licence page was found on aisstream.io on 2026-10-08; what is known is
recorded in AISSTREAM_TERMS (scripts/28_ais_reach.py); redistribution and commercial use are UNVERIFIED.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import math
import os
import random
import re
import time
import zlib
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from darkvessel.config import CACHE_DIR, DARK_CAVEAT

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
AISSTREAM_DOCS = "https://aisstream.io/documentation"
AIS_CACHE = CACHE_DIR / "ais" / "aisstream"
POS_DIR = AIS_CACHE / "positions"
STATIC_DIR = AIS_CACHE / "static"
RAW_DIR_AIS = AIS_CACHE / "raw"
STATUS_PATH = AIS_CACHE / "status.json"
PID_PATH = AIS_CACHE / "recorder.pid"

# Three lat/lon boxes that hug the South China Sea AOI (Natural Earth "South China Sea" + "Gulf of Tonkin" +
# "Gulf of Thailand", bounds 99.16E to 122.27E, 3.22S to 23.76N), each the lon extent of the AOI polygon in a
# latitude band plus a 0.25 degree margin. Computed from data/aoi.gpkg on 2026-10-08. aisstream wants
# [[lat_min, lon_min], [lat_max, lon_max]].
AOI_BOXES = [
    [[-3.47, 102.20], [6.00, 116.36]],   # Karimata and Natuna waters to the Sarawak coast
    [[6.00, 98.91], [14.00, 121.22]],    # Gulf of Thailand to Palawan
    [[14.00, 105.37], [24.01, 122.52]],  # Gulf of Tonkin, Hainan, Paracels to Luzon Strait
]

AIS_REACH_CAVEAT = (
    "Live AIS here comes from the shore receivers of aisstream.io. They hear class A transponders a few tens "
    "of kilometres offshore and class B less far; the open sea is silent whether or not vessels are there. No AIS "
    "heard in a cell does not mean no vessel. " + DARK_CAVEAT
)

POSITION_TYPES = {
    "PositionReport": ("A", 1),
    "StandardClassBPositionReport": ("B", 18),
    "ExtendedClassBPositionReport": ("B", 19),
    "LongRangeAisBroadcastMessage": ("A", 27),
}
STATIC_TYPES = {"ShipStaticData": 5, "StaticDataReport": 24}

POSITION_COLUMNS = ["mmsi", "timestamp", "lon", "lat", "sog_kn", "cog_deg", "heading", "nav_status", "msg_type",
                    "msg_id", "ais_class", "ship_name"]
STATIC_COLUMNS = ["mmsi", "imo", "name", "callsign", "ship_type", "ship_type_label", "length_m", "width_m",
                  "destination", "eta", "seen_utc", "msg_type"]
POSITION_TEXT = ["msg_type", "ais_class", "ship_name"]
STATIC_TEXT = ["name", "callsign", "ship_type_label", "destination", "eta", "msg_type"]

# Fixed on-disk types (the same as the partitions written since 2026-10-08 14:31 UTC).
_TS = pa.timestamp("us", tz="UTC")
POSITION_SCHEMA = pa.schema([("mmsi", pa.int64()), ("timestamp", _TS), ("lon", pa.float64()), ("lat", pa.float64()),
                             ("sog_kn", pa.float32()), ("cog_deg", pa.float32()), ("heading", pa.float32()),
                             ("nav_status", pa.int16()), ("msg_type", pa.large_string()), ("msg_id", pa.int16()),
                             ("ais_class", pa.large_string()), ("ship_name", pa.large_string())])
STATIC_SCHEMA = pa.schema([("mmsi", pa.int64()), ("imo", pa.int64()), ("name", pa.large_string()),
                           ("callsign", pa.large_string()), ("ship_type", pa.int16()),
                           ("ship_type_label", pa.large_string()), ("length_m", pa.float32()), ("width_m", pa.float32()),
                           ("destination", pa.large_string()), ("eta", pa.large_string()), ("seen_utc", _TS),
                           ("msg_type", pa.large_string())])

# Live recording: a message whose MetaData.time_utc is further than this from the local receive time is kept in the
# raw log but left out of the position and static tables (aisstream stamps time_utc on reception, so a large offset is
# a clock or parsing fault; re-stamping it would misplace the vessel in time). scripts/26_ais_record.py sets it; the
# Recorder default (None) applies no check, so tests and replays of old messages work.
MAX_CLOCK_SKEW = pd.Timedelta(hours=1)

# ITU-R M.1371 "type of ship and cargo type" first digit (tens) and the special 30 to 59 codes.
# Source: Recommendation ITU-R M.1371-5, Table 53 (https://www.itu.int/rec/R-REC-M.1371), as reproduced by the
# US Coast Guard Navigation Center AIS message pages (https://www.navcen.uscg.gov/ais-class-a-reports).
SHIP_TYPE_SPECIAL = {
    30: "fishing", 31: "towing", 32: "towing, long or wide", 33: "dredging or underwater ops", 34: "diving ops",
    35: "military ops", 36: "sailing", 37: "pleasure craft", 50: "pilot vessel", 51: "search and rescue",
    52: "tug", 53: "port tender", 54: "anti-pollution", 55: "law enforcement", 56: "spare, local", 57: "spare, local",
    58: "medical transport", 59: "noncombatant ship",
}
SHIP_TYPE_TENS = {2: "wing in ground", 4: "high speed craft", 6: "passenger", 7: "cargo", 8: "tanker", 9: "other"}


def ship_type_label(code) -> str | None:
    """Plain label for an AIS ship type code (ITU-R M.1371 Table 53). None when unknown or not available."""
    if code is None or (isinstance(code, float) and math.isnan(code)):
        return None
    code = int(code)
    if code <= 0 or code > 99:
        return None
    if code in SHIP_TYPE_SPECIAL:
        return SHIP_TYPE_SPECIAL[code]
    if code // 10 in SHIP_TYPE_TENS:
        return SHIP_TYPE_TENS[code // 10]
    return "reserved"


# ---------------------------------------------------------------------------------------------------------------
# Message normalisation (pure functions, tested offline)
# ---------------------------------------------------------------------------------------------------------------

_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?")


def parse_time_utc(s) -> pd.Timestamp | None:
    """MetaData.time_utc ('2026-10-08 03:04:05.123456789 +0000 UTC') -> tz-aware UTC Timestamp, microseconds.

    A numeric offset (+0700, -05:30) is applied; no offset or 'Z' means UTC.
    """
    if not s:
        return None
    m = _TIME_RE.match(str(s).strip())
    if not m:
        return None
    frac = (m.group(2) or ".0")[:7]  # keep at most 6 fractional digits
    try:
        ts = pd.Timestamp(m.group(1) + frac, tz="UTC")
    except (ValueError, TypeError):
        return None
    off = m.group(3)
    if off and off != "Z":
        sign = 1 if off[0] == "+" else -1
        digits = off[1:].replace(":", "")
        ts = ts - sign * pd.Timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return ts


def _num(v, na_values=()):
    """float(v) or NaN for None, non-numeric and listed not-available codes."""
    if v is None or isinstance(v, bool):
        return np.nan
    try:
        f = float(v)
    except (TypeError, ValueError):
        return np.nan
    if not math.isfinite(f):
        return np.nan
    for na in na_values:
        if abs(f - na) < 1e-6:
            return np.nan
    return f


def _text(v) -> str | None:
    if v is None:
        return None
    s = str(v).replace("@", "").strip()
    return s or None


def _mmsi(meta: dict, body: dict):
    for v in (meta.get("MMSI"), body.get("UserID"), meta.get("MMSI_String")):
        try:
            m = int(v)
        except (TypeError, ValueError):
            continue
        if 0 < m < 10**9 + 1:
            return m
    return None


def classify(msg: dict) -> str:
    """'position', 'static' or 'other' for a decoded aisstream message."""
    t = msg.get("MessageType")
    if t in POSITION_TYPES:
        return "position"
    if t in STATIC_TYPES:
        return "static"
    return "other"


def normalise_position(msg: dict, received: pd.Timestamp | None = None) -> dict | None:
    """One position row (POSITION_COLUMNS) or None when the message is not a usable vessel position."""
    t = msg.get("MessageType")
    if t not in POSITION_TYPES:
        return None
    meta = msg.get("MetaData") or {}
    body = (msg.get("Message") or {}).get(t) or {}
    mmsi = _mmsi(meta, body)
    if mmsi is None:
        return None
    ts = parse_time_utc(meta.get("time_utc")) or received
    if ts is None:
        return None
    lon = _num(body.get("Longitude", meta.get("longitude", meta.get("Longitude"))), na_values=(181.0,))
    lat = _num(body.get("Latitude", meta.get("latitude", meta.get("Latitude"))), na_values=(91.0,))
    if not (math.isfinite(lon) and math.isfinite(lat)) or abs(lon) > 180 or abs(lat) > 90:
        return None
    if lon == 0 and lat == 0:  # null island: unset GNSS
        return None
    ais_class, msg_id = POSITION_TYPES[t]
    if t == "LongRangeAisBroadcastMessage":
        sog = _num(body.get("Sog"), na_values=(63.0,))
        cog = _num(body.get("Cog"), na_values=(511.0,))
        heading = np.nan
    else:
        sog = _num(body.get("Sog"), na_values=(102.3,))
        cog = _num(body.get("Cog"), na_values=(360.0,))
        heading = _num(body.get("TrueHeading"), na_values=(511.0,))
    if math.isfinite(sog) and (sog < 0 or sog > 102.2):
        sog = np.nan
    if math.isfinite(cog) and (cog < 0 or cog >= 360):
        cog = np.nan
    if math.isfinite(heading) and (heading < 0 or heading >= 360):
        heading = np.nan
    nav = body.get("NavigationalStatus")
    try:
        nav = int(nav) if nav is not None else None
    except (TypeError, ValueError):
        nav = None
    if nav is not None and not 0 <= nav <= 15:
        nav = None
    mid = body.get("MessageID")
    try:
        msg_id = int(mid) if mid is not None else msg_id
    except (TypeError, ValueError):
        pass
    name = _text(meta.get("ShipName")) or _text(body.get("Name"))
    return {"mmsi": mmsi, "timestamp": ts, "lon": lon, "lat": lat, "sog_kn": sog, "cog_deg": cog, "heading": heading,
            "nav_status": nav, "msg_type": t, "msg_id": msg_id, "ais_class": ais_class, "ship_name": name}


def _dims(d: dict | None):
    if not isinstance(d, dict):
        return np.nan, np.nan
    a, b, c, e = (_num(d.get(k)) for k in ("A", "B", "C", "D"))
    length = a + b if math.isfinite(a) and math.isfinite(b) and a + b > 0 else np.nan
    width = c + e if math.isfinite(c) and math.isfinite(e) and c + e > 0 else np.nan
    if math.isfinite(length) and length > 1022:  # 511 + 511 is the field maximum
        length = np.nan
    if math.isfinite(width) and width > 126:
        width = np.nan
    return length, width


def _eta(e, seen: pd.Timestamp) -> str | None:
    """ETA {Month, Day, Hour, Minute} -> 'MM-DD HH:MM' (year is not in the message); None when unset."""
    if not isinstance(e, dict):
        return None
    try:
        mo, d, h, mi = (int(e.get(k, 0) or 0) for k in ("Month", "Day", "Hour", "Minute"))
    except (TypeError, ValueError):
        return None
    if mo == 0 or d == 0 or not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{mo:02d}-{d:02d} {min(h, 24):02d}:{min(mi, 60):02d}"


def normalise_static(msg: dict, received: pd.Timestamp | None = None) -> dict | None:
    """One static row (STATIC_COLUMNS) or None. StaticDataReport part A gives the name, part B type and size."""
    t = msg.get("MessageType")
    if t not in STATIC_TYPES:
        return None
    meta = msg.get("MetaData") or {}
    body = (msg.get("Message") or {}).get(t) or {}
    mmsi = _mmsi(meta, body)
    if mmsi is None:
        return None
    seen = parse_time_utc(meta.get("time_utc")) or received or pd.Timestamp.now(tz="UTC")
    row = {"mmsi": mmsi, "imo": None, "name": None, "callsign": None, "ship_type": None, "ship_type_label": None,
           "length_m": np.nan, "width_m": np.nan, "destination": None, "eta": None, "seen_utc": seen, "msg_type": t}
    if t == "ShipStaticData":
        imo = body.get("ImoNumber")
        try:
            imo = int(imo) if imo not in (None, 0, "0") else None
        except (TypeError, ValueError):
            imo = None
        row["imo"] = imo if imo and 1000000 <= imo <= 9999999 else None
        row["name"] = _text(body.get("Name")) or _text(meta.get("ShipName"))
        row["callsign"] = _text(body.get("CallSign"))
        st = body.get("Type")
        row["ship_type"] = int(st) if isinstance(st, (int, float)) and 0 <= st <= 255 else None
        row["length_m"], row["width_m"] = _dims(body.get("Dimension"))
        row["destination"] = _text(body.get("Destination"))
        row["eta"] = _eta(body.get("Eta"), seen)
    else:  # StaticDataReport (class B)
        a = body.get("ReportA") or {}
        b = body.get("ReportB") or {}
        part = body.get("PartNumber")
        if a and (a.get("Valid", True) or part in (False, 0)):
            row["name"] = _text(a.get("Name"))
        if b and (b.get("Valid", True) or part in (True, 1)):
            row["callsign"] = _text(b.get("CallSign"))
            st = b.get("ShipType")
            row["ship_type"] = int(st) if isinstance(st, (int, float)) and 0 <= st <= 255 else None
            row["length_m"], row["width_m"] = _dims(b.get("Dimension"))
        if row["name"] is None and row["callsign"] is None and row["ship_type"] is None:
            row["name"] = _text(meta.get("ShipName"))
        row["msg_type"] = f"{t}{'B' if row['name'] is None else 'A'}"
    row["ship_type_label"] = ship_type_label(row["ship_type"])
    if row["name"] is None and row["callsign"] is None and row["ship_type"] is None and not math.isfinite(row["length_m"]):
        return None
    return row


def as_text(values) -> pd.Series:
    """Object-dtype Series of str with None for every missing value (None, NaN, pd.NA, empty after strip).

    pandas 3 builds and reads string columns as its `str` dtype, whose missing value is NaN; the AIS tables
    promise None, so every frame helper passes its text columns through here.
    """
    s = values if isinstance(values, pd.Series) else pd.Series(values)
    return pd.Series([_text_value(v) for v in s.tolist()], index=s.index, dtype=object)


def _text_value(v) -> str | None:
    if isinstance(v, str):
        return v or None
    if v is None or v is pd.NA or v is pd.NaT or (isinstance(v, float) and math.isnan(v)):
        return None
    return str(v)


def _utc_us(values) -> pd.Series:
    """tz-aware UTC datetimes at microsecond resolution (the on-disk unit)."""
    t = pd.to_datetime(values, utc=True)
    return t.dt.floor("us").dt.as_unit("us")


def positions_frame(rows) -> pd.DataFrame:
    """Typed, de-duplicated DataFrame of position rows (a list of dicts or a DataFrame with POSITION_COLUMNS).

    Also the normaliser for partitions read back from disk: it accepts any older dtype layout and returns
    int64 mmsi, UTC microsecond timestamps, float lon/lat/speeds, Int16 codes and object text with None.
    """
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows), columns=POSITION_COLUMNS)
    for c in POSITION_COLUMNS:
        if c not in df:
            df[c] = None
    df = df[POSITION_COLUMNS].copy()
    if df.empty:
        df = df.astype({"mmsi": "int64", "lon": "float64", "lat": "float64", "sog_kn": "float32", "cog_deg": "float32",
                        "heading": "float32", "nav_status": "Int16", "msg_id": "Int16"})
        df["timestamp"] = pd.Series([], dtype="datetime64[us, UTC]")
        for c in POSITION_TEXT:
            df[c] = pd.Series([], dtype=object)
        return df
    df["mmsi"] = df.mmsi.astype("int64")
    df["timestamp"] = _utc_us(df.timestamp)
    for c, t in (("lon", "float64"), ("lat", "float64"), ("sog_kn", "float32"), ("cog_deg", "float32"), ("heading", "float32")):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(t)
    df["nav_status"] = pd.array(pd.to_numeric(df.nav_status, errors="coerce"), dtype="Int16")
    df["msg_id"] = pd.array(pd.to_numeric(df.msg_id, errors="coerce"), dtype="Int16")
    for c in POSITION_TEXT:
        df[c] = as_text(df[c])
    df = df.drop_duplicates(subset=["mmsi", "timestamp", "lon", "lat", "msg_type"]).sort_values(["timestamp", "mmsi"],
                                                                                                kind="stable")
    return df.reset_index(drop=True)


def static_frame(rows) -> pd.DataFrame:
    """Typed static DataFrame, content-deduplicated per MMSI (latest seen_utc kept). Text columns: object, None."""
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows), columns=STATIC_COLUMNS)
    for c in STATIC_COLUMNS:
        if c not in df:
            df[c] = None
    df = df[STATIC_COLUMNS].copy()
    if df.empty:
        df = df.astype({"mmsi": "int64", "length_m": "float32", "width_m": "float32", "imo": "Int64", "ship_type": "Int16"})
        df["seen_utc"] = pd.Series([], dtype="datetime64[us, UTC]")
        for c in STATIC_TEXT:
            df[c] = pd.Series([], dtype=object)
        return df
    df["mmsi"] = df.mmsi.astype("int64")
    df["seen_utc"] = _utc_us(df.seen_utc)
    df["imo"] = pd.array(pd.to_numeric(df.imo, errors="coerce"), dtype="Int64")
    df["ship_type"] = pd.array(pd.to_numeric(df.ship_type, errors="coerce"), dtype="Int16")
    for c in ("length_m", "width_m"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    for c in STATIC_TEXT:
        df[c] = as_text(df[c])
    keys = [c for c in STATIC_COLUMNS if c != "seen_utc"]
    df = df.sort_values("seen_utc", kind="stable").drop_duplicates(subset=keys, keep="last")
    return df.sort_values(["seen_utc", "mmsi"], kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------------------------------------------

def hour_key(ts: pd.Timestamp) -> tuple[str, str]:
    """('YYYYMMDD', 'HH') of a UTC timestamp."""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    ts = ts.tz_convert("UTC")
    return ts.strftime("%Y%m%d"), ts.strftime("%H")


def write_parquet_atomic(df: pd.DataFrame, path: Path, schema: pa.Schema | None = None) -> None:
    """Write `df` to `path` through a temp file in the same directory, fsync it, then rename over the target.

    A kill at any point leaves either the old file or the new one, never a torn file under the final name.
    With `schema`, the columns are cast to it so every partition has identical types.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        if schema is not None:
            table = pa.Table.from_pandas(df[schema.names], schema=schema, preserve_index=False, safe=False)
            pq.write_table(table, tmp)
        else:
            df.to_parquet(tmp, index=False)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _schema_for(frame_fn) -> pa.Schema | None:
    return {positions_frame: POSITION_SCHEMA, static_frame: STATIC_SCHEMA}.get(frame_fn)


def append_parquet(path: Path, df: pd.DataFrame, frame_fn) -> int:
    """Merge `df` into the parquet file at `path` (read, concat, dedupe with `frame_fn`, atomic replace). Returns rows.

    An unreadable existing file is moved aside (`<name>.corrupt-<UTC time>`) and the new rows are written; the raw
    gzip log still holds the moved rows.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            old = frame_fn(pd.read_parquet(path))
            df = pd.concat([old, df], ignore_index=True) if len(old) else df
        except Exception as exc:
            aside = path.with_name(f"{path.name}.corrupt-{pd.Timestamp.now(tz='UTC'):%Y%m%dT%H%M%S}")
            os.replace(path, aside)
            print(f"[aisstream] unreadable partition {path.name}: {type(exc).__name__}; moved aside to {aside.name}",
                  flush=True)
    df = frame_fn(df)
    write_parquet_atomic(df, path, _schema_for(frame_fn))
    return len(df)


def read_raw(path: Path) -> list[str]:
    """The JSON lines of one raw/YYYYMMDD/HH.jsonl.gz file. Reads member by member and stops at a truncated tail."""
    data = Path(path).read_bytes()
    out, pos = [], 0
    while pos < len(data):
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            chunk = d.decompress(data[pos:])
        except zlib.error:
            break
        if not d.eof:  # truncated last member: keep only complete lines
            text = chunk.decode("utf-8", "replace")
            out.extend(text.splitlines()[:-1] if not text.endswith("\n") else text.splitlines())
            break
        out.extend(chunk.decode("utf-8", "replace").splitlines())
        pos = len(data) - len(d.unused_data)
    return [line for line in out if line.strip()]


_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a credential so that `redact` removes its exact value from every log line and error."""
    if value and len(value.strip()) >= 6:
        _SECRETS.add(value.strip())


def redact(text) -> str:
    """Strip registered secrets and anything that looks like a key or Authorization header before logging."""
    text = str(text)
    for s in _SECRETS:
        text = text.replace(s, "<redacted>")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)\S+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(api[_-]?key\"?\s*[:=]\s*\"?)[A-Za-z0-9._-]{8,}", r"\1<redacted>", text)
    return re.sub(r"\b[0-9a-fA-F]{32,}\b", "<redacted>", text)


class Recorder:
    """Buffers normalised messages and flushes them to hourly parquet partitions, the static table and raw gzip."""

    def __init__(self, root: Path = AIS_CACHE, keep_raw: bool = True, max_clock_skew: pd.Timedelta | None = None):
        self.root = Path(root)
        self.keep_raw = keep_raw
        self.max_clock_skew = max_clock_skew
        self.pos_buf: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self.static_buf: dict[str, list[dict]] = defaultdict(list)
        self.raw_buf: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.mmsi_seen: set[int] = set()
        self.stats = {"started_utc": pd.Timestamp.now(tz="UTC").isoformat(), "messages": 0, "positions": 0, "static": 0,
                      "other": 0, "bad_json": 0, "dropped": 0, "handler_errors": 0, "clock_skew_dropped": 0, "by_type": {},
                      "connects": 0, "connection": "starting", "connect_attempts": 0, "last_connect_utc": None,
                      "last_disconnect_utc": None, "errors": 0, "last_error": None, "flush_errors": 0,
                      "last_message_utc": None, "last_write_utc": None, "flushes": 0, "distinct_mmsi": 0,
                      "hours_written": {}}

    # -- ingest -------------------------------------------------------------------------------------------------
    def handle(self, raw: str | bytes) -> str:
        """Parse one websocket frame. Returns 'position', 'static', 'other', 'error' or 'bad'. Never raises."""
        try:
            return self._handle(raw)
        except Exception as exc:  # a message the normaliser did not foresee must not drop the connection
            self.stats["handler_errors"] += 1
            self.stats["last_error"] = redact(f"handler {type(exc).__name__}: {exc}")[:300]
            return "bad"

    def _handle(self, raw: str | bytes) -> str:
        now = pd.Timestamp.now(tz="UTC")
        text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            self.stats["bad_json"] += 1
            return "bad"
        if not isinstance(msg, dict):
            self.stats["bad_json"] += 1
            return "bad"
        if "error" in msg and "MessageType" not in msg:
            self.stats["errors"] += 1
            self.stats["last_error"] = redact(str(msg.get("error")))[:300]
            return "error"
        self.stats["messages"] += 1
        self.stats["last_message_utc"] = now.isoformat()
        t = msg.get("MessageType") or "unknown"
        self.stats["by_type"][t] = self.stats["by_type"].get(t, 0) + 1
        kind = classify(msg)
        meta_time = parse_time_utc((msg.get("MetaData") or {}).get("time_utc"))
        skewed = (self.max_clock_skew is not None and meta_time is not None
                  and abs(meta_time - now) > self.max_clock_skew)
        if self.keep_raw:
            key = hour_key(now if skewed or meta_time is None else meta_time)
            self.raw_buf[key].append(text if text.endswith("\n") else text + "\n")
        if skewed:
            self.stats["clock_skew_dropped"] += 1
            return "other"
        if kind == "position":
            row = normalise_position(msg, received=now)
            if row is None:
                self.stats["dropped"] += 1
                return "other"
            self.pos_buf[hour_key(row["timestamp"])].append(row)
            self.mmsi_seen.add(row["mmsi"])
            self.stats["positions"] += 1
            return "position"
        if kind == "static":
            row = normalise_static(msg, received=now)
            if row is None:
                self.stats["dropped"] += 1
                return "other"
            self.static_buf[hour_key(row["seen_utc"])[0]].append(row)
            self.stats["static"] += 1
            return "static"
        self.stats["other"] += 1
        return "other"

    # -- flush --------------------------------------------------------------------------------------------------
    def flush(self) -> dict:
        """Write every buffer. Safe to call often; each partition is rewritten atomically.

        A buffer is cleared only after its write succeeded, so a failed write (disk full, permissions) keeps the rows
        for the next flush. The status file is written in every case.
        """
        written = {"positions": 0, "static": 0, "raw": 0}
        failures = []
        for (day, hh), rows in list(self.pos_buf.items()):
            if not rows:
                continue
            path = self.root / "positions" / day / f"{hh}.parquet"
            try:
                n = append_parquet(path, positions_frame(rows), positions_frame)
            except Exception as exc:
                failures.append(f"positions {day}T{hh}: {type(exc).__name__}: {exc}")
                continue
            self.stats["hours_written"][f"{day}T{hh}"] = n
            written["positions"] += len(rows)
            self.pos_buf[(day, hh)] = []
        for day, rows in list(self.static_buf.items()):
            if not rows:
                continue
            try:
                append_parquet(self.root / "static" / f"{day}.parquet", static_frame(rows), static_frame)
            except Exception as exc:
                failures.append(f"static {day}: {type(exc).__name__}: {exc}")
                continue
            written["static"] += len(rows)
            self.static_buf[day] = []
        if self.keep_raw:
            for (day, hh), lines in list(self.raw_buf.items()):
                if not lines:
                    continue
                path = self.root / "raw" / day / f"{hh}.jsonl.gz"
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with gzip.open(path, "ab") as fh:  # gzip members concatenate; a crash loses at most one member
                        fh.write("".join(lines).encode("utf-8"))
                except Exception as exc:
                    failures.append(f"raw {day}T{hh}: {type(exc).__name__}: {exc}")
                    continue
                written["raw"] += len(lines)
                self.raw_buf[(day, hh)] = []
        # drop empty buffers of past hours so the dicts do not grow forever
        for d in (self.pos_buf, self.raw_buf, self.static_buf):
            for k in [k for k, v in d.items() if not v]:
                del d[k]
        self.stats["flushes"] += 1
        self.stats["distinct_mmsi"] = len(self.mmsi_seen)
        if failures:
            self.stats["flush_errors"] += len(failures)
            self.stats["last_error"] = redact("flush " + "; ".join(failures))[:300]
        if sum(written.values()):
            self.stats["last_write_utc"] = pd.Timestamp.now(tz="UTC").isoformat()
        try:
            self.write_status()
        except Exception as exc:
            failures.append(f"status: {type(exc).__name__}: {exc}")
        written["failures"] = [redact(f)[:300] for f in failures]
        return written

    def write_status(self) -> None:
        """status.json (atomic): counters, connection state, last message and write times, pid, caveat."""
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f"status.json.tmp{os.getpid()}"
        status = dict(self.stats, updated_utc=pd.Timestamp.now(tz="UTC").isoformat(), pid=os.getpid(),
                      caveat=AIS_REACH_CAVEAT)
        hours = status.pop("hours_written")
        status["hours_written"] = dict(sorted(hours.items())[-48:])
        tmp.write_text(redact(json.dumps(status, indent=1, default=str)))
        os.replace(tmp, self.root / "status.json")


# ---------------------------------------------------------------------------------------------------------------
# Websocket client
# ---------------------------------------------------------------------------------------------------------------

def subscription(api_key: str, boxes=AOI_BOXES, message_types: list[str] | None = None) -> str:
    """The aisstream subscription JSON. Never log the result: it carries the key."""
    sub = {"APIKey": api_key, "BoundingBoxes": boxes}
    if message_types:
        sub["FilterMessageTypes"] = message_types
    return json.dumps(sub)


def backoff_wait(attempt: int, base_s: float = 1.0, max_s: float = 60.0, rng=random.random) -> float:
    """Seconds to wait after the `attempt`-th consecutive failure (1-based): base * 2^(attempt-1), capped, jitter +-30 %."""
    return min(base_s * 2 ** max(attempt - 1, 0), max_s) * (0.7 + 0.6 * rng())


def is_key_rejection(text: str) -> bool:
    low = str(text).lower()
    return "api key" in low and ("invalid" in low or "not valid" in low)


async def stream(api_key: str, on_frame, *, stop: asyncio.Event | None = None, boxes=AOI_BOXES,
                 url: str = AISSTREAM_URL, idle_timeout_s: float = 120.0, max_backoff_s: float = 60.0,
                 log=print, on_connect=None, on_disconnect=None, connect=None) -> None:
    """Read frames from aisstream until `stop` is set. One connection; retries forever with capped backoff.

    `on_frame(raw)` is called for every frame (text or bytes). `on_connect()` after each subscription is sent,
    `on_disconnect(reason)` after each failure. Every failed attempt is logged with its number and the wait.
    Any exception (network, proxy refusal, handshake, silent socket, service error frame) leads to a retry; the
    only exit is `stop` being set, or the service rejecting the key (then `stop` is set, retrying cannot help).
    `connect` replaces websockets.connect (tests).
    """
    if connect is None:
        import websockets

        def connect():
            return websockets.connect(url, compression="deflate", ping_interval=30, ping_timeout=30, close_timeout=5,
                                      open_timeout=30, max_size=2 ** 22, user_agent_header="darkvessel-ais-recorder/0.2")

    register_secret(api_key)
    stop = stop or asyncio.Event()
    failures = 0
    while not stop.is_set():
        try:
            if failures:
                log(f"[aisstream] connecting, attempt {failures + 1}")
            async with connect() as ws:
                await ws.send(subscription(api_key, boxes))
                if on_connect:
                    on_connect()
                log("[aisstream] connected and subscribed")
                good = False
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=idle_timeout_s)
                    except asyncio.TimeoutError:
                        raise ConnectionError(f"no frame for {idle_timeout_s:.0f} s")
                    kind = on_frame(raw)
                    if kind == "error":
                        text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
                        if is_key_rejection(text):
                            log("[aisstream] the service rejected the API key; stopping. Check AISSTREAM_API_KEY in .env")
                            if on_disconnect:
                                on_disconnect("service rejected the API key (not valid)")
                            stop.set()
                            return
                        raise ConnectionError("service error frame: " + redact(text)[:200])
                    if not good:
                        good = True
                        failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # network errors, proxy refusals, handshake failures, service errors
            if stop.is_set():
                break
            failures += 1
            wait = backoff_wait(failures, max_s=max_backoff_s)
            reason = redact(f"{type(exc).__name__}: {exc}")[:200]
            if on_disconnect:
                on_disconnect(reason)
            log(f"[aisstream] {reason} -> attempt {failures} failed, retry in {wait:.0f} s")
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass


async def record(api_key: str, recorder: Recorder, *, hours: float = 0, flush_s: float = 60.0,
                 stop: asyncio.Event | None = None, log=print, **kw) -> None:
    """Run the stream into `recorder`, flushing every `flush_s` seconds, for `hours` (0 = until `stop`).

    A failing flush is logged and retried at the next tick (the rows stay buffered); the flusher never dies.
    """
    stop = stop or asyncio.Event()
    deadline = time.monotonic() + hours * 3600 if hours and hours > 0 else None
    st = recorder.stats

    def on_connect():
        st["connects"] += 1
        st["connection"] = "connected"
        st["last_connect_utc"] = pd.Timestamp.now(tz="UTC").isoformat()

    def on_disconnect(reason: str):
        st["connection"] = "retrying"
        st["connect_attempts"] += 1
        st["last_disconnect_utc"] = pd.Timestamp.now(tz="UTC").isoformat()
        st["last_error"] = reason

    async def flusher():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=flush_s)
            except asyncio.TimeoutError:
                pass
            try:
                w = recorder.flush()
                log(f"[aisstream] flush: +{w['positions']} pos +{w['static']} static | total {st['messages']} msgs, "
                    f"{st['positions']} pos, {st['static']} static, {st['distinct_mmsi']} MMSI, {st['connects']} connects")
                for f in w.get("failures", []):
                    log(f"[aisstream] flush failed, rows kept for the next flush: {f}")
            except Exception as exc:
                log(f"[aisstream] flush error {redact(f'{type(exc).__name__}: {exc}')[:200]}; retrying next tick")
            if deadline and time.monotonic() >= deadline:
                log(f"[aisstream] {hours} h reached; stopping")
                stop.set()

    async def reader():  # stream() only returns once `stop` is set; a crash in it is logged and the reader restarted
        while not stop.is_set():
            try:
                await stream(api_key, recorder.handle, stop=stop, log=log, on_connect=on_connect,
                             on_disconnect=on_disconnect, **kw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"[aisstream] reader crashed {redact(f'{type(exc).__name__}: {exc}')[:200]}; restarting in 5 s")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass

    tasks = [asyncio.create_task(reader()), asyncio.create_task(flusher())]
    try:
        await stop.wait()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        st["connection"] = "stopped"
        try:
            recorder.flush()
        except Exception as exc:
            log(f"[aisstream] final flush error {redact(f'{type(exc).__name__}: {exc}')[:200]}")


# ---------------------------------------------------------------------------------------------------------------
# Reading back and reach statistics
# ---------------------------------------------------------------------------------------------------------------

def list_partitions(root: Path = AIS_CACHE) -> list[Path]:
    return sorted((Path(root) / "positions").glob("*/??.parquet"))


def _ts_utc(t) -> pd.Timestamp | None:
    if t is None:
        return None
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def load_positions(root: Path = AIS_CACHE, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> pd.DataFrame:
    """All recorded positions (optionally within [start, end], naive times read as UTC) as one typed DataFrame.

    Every partition, old or new, goes through `positions_frame`, so text columns come back as object with None.
    """
    start, end = _ts_utc(start), _ts_utc(end)
    parts = []
    for p in list_partitions(root):
        day, hh = p.parent.name, p.stem
        try:
            t0 = pd.Timestamp(f"{day[:4]}-{day[4:6]}-{day[6:]} {hh}:00", tz="UTC")
        except ValueError:
            continue
        if (start is not None and t0 + pd.Timedelta(hours=1) <= start) or (end is not None and t0 > end):
            continue
        try:
            parts.append(pd.read_parquet(p))
        except Exception as exc:
            print(f"[aisstream] skipping unreadable {p}: {type(exc).__name__}", flush=True)
    if not parts:
        return positions_frame([])
    df = positions_frame(pd.concat(parts, ignore_index=True))
    if start is not None:
        df = df[df.timestamp >= start]
    if end is not None:
        df = df[df.timestamp <= end]
    return df.reset_index(drop=True)


def load_static(root: Path = AIS_CACHE) -> pd.DataFrame:
    """Every static row recorded (all days), typed and content-deduplicated by `static_frame`."""
    parts = []
    for p in sorted((Path(root) / "static").glob("*.parquet")):
        try:
            parts.append(pd.read_parquet(p))
        except Exception as exc:
            print(f"[aisstream] skipping unreadable {p}: {type(exc).__name__}", flush=True)
    if not parts:
        return static_frame([])
    return static_frame(pd.concat(parts, ignore_index=True))


def latest_static(static: pd.DataFrame) -> pd.DataFrame:
    """One row per MMSI: the latest non-null value of each static field, plus static_seen_utc (latest message)."""
    cols = [c for c in STATIC_COLUMNS if c not in ("mmsi", "msg_type", "seen_utc")]
    if static.empty:
        out = pd.DataFrame(columns=["mmsi"] + cols + ["static_seen_utc"])
        for c in STATIC_TEXT:
            if c in out:
                out[c] = out[c].astype(object)
        return out
    s = static.sort_values("seen_utc", kind="stable")
    out = s.groupby("mmsi")[cols].last()  # last() skips nulls per column
    out["static_seen_utc"] = s.groupby("mmsi").seen_utc.max()
    out = out.reset_index()
    for c in STATIC_TEXT:
        if c in out:
            out[c] = as_text(out[c])
    return out


def recorded_hours(positions: pd.DataFrame) -> pd.DatetimeIndex:
    """The UTC hours with at least one position anywhere (the denominator of the reach share)."""
    if positions.empty:
        return pd.DatetimeIndex([], tz="UTC")
    return pd.DatetimeIndex(sorted(positions.timestamp.dt.floor("h").unique()))


def reach_grids(positions: pd.DataFrame, transform, shape: tuple[int, int], hours: pd.DatetimeIndex | None = None):
    """(share, mmsi_count, hours) per cell of a lon/lat grid.

    share      = hours with at least one position in the cell / number of recorded hours (0 where silent)
    mmsi_count = distinct MMSI heard in the cell over the period
    """
    from darkvessel.ocean.grid import cell_index

    h, w = shape
    share = np.zeros(shape, np.float32)
    mmsi_count = np.zeros(shape, np.float32)
    if positions.empty:
        return share, mmsi_count, hours if hours is not None else recorded_hours(positions)
    hours = recorded_hours(positions) if hours is None else hours
    n_hours = max(len(hours), 1)
    row, col, inside = cell_index(transform, shape, positions.lon.values, positions.lat.values)
    p = positions.loc[inside, ["mmsi", "timestamp"]].copy()
    p["cell"] = row[inside] * w + col[inside]
    p["hour"] = p.timestamp.dt.floor("h")
    hours_per_cell = p.drop_duplicates(["cell", "hour"]).groupby("cell").size()
    mmsi_per_cell = p.drop_duplicates(["cell", "mmsi"]).groupby("cell").size()
    share.flat[hours_per_cell.index.values] = (hours_per_cell.values / n_hours).astype(np.float32)
    mmsi_count.flat[mmsi_per_cell.index.values] = mmsi_per_cell.values.astype(np.float32)
    return share, mmsi_count, hours


_GEAR_NAME_RE = re.compile(r"(\d{1,3}\s*%\s*$)|(^|[^A-Z])(NET|BUOY|BOUY)([^A-Z]|$)", re.IGNORECASE)


def gear_beacon_like(name, mmsi) -> bool:
    """Heuristic: True when the AIS sender looks like a fishing-gear or net beacon rather than a vessel.

    Signs: a name ending in a percentage (e.g. 'NET-82542-84%', 'BUOY_MERAH_10-99%'; probably a battery level,
    UNVERIFIED), the words NET or BUOY in the name, or an MMSI that is not nine digits (ITU MMSIs of ships are nine
    digits). Such positions mark gear, not a vessel; a radar contact next to one may be the boat tending it.
    """
    try:
        m = int(mmsi)
    except (TypeError, ValueError):
        m = None
    if m is not None and not 100_000_000 <= m <= 999_999_999:
        return True
    return isinstance(name, str) and bool(_GEAR_NAME_RE.search(name))


def summarise(positions: pd.DataFrame, static: pd.DataFrame) -> dict:
    """Counts for data/ais_live_summary.json: period, messages, MMSI, class split, top ship types."""
    out = {"positions": int(len(positions)), "static_rows": int(len(static)), "mmsi_count": int(positions.mmsi.nunique()) if len(positions) else 0}
    if len(positions):
        out["period_start_utc"] = positions.timestamp.min().isoformat()
        out["period_end_utc"] = positions.timestamp.max().isoformat()
        out["hours_recorded"] = int(len(recorded_hours(positions)))
        by_class = positions.drop_duplicates("mmsi").ais_class.value_counts()
        out["mmsi_by_class"] = {k: int(v) for k, v in by_class.items()}
        out["positions_by_type"] = {k: int(v) for k, v in positions.msg_type.value_counts().items()}
        moving = positions.sog_kn.dropna()
        out["share_positions_underway_over_1kn"] = round(float((moving > 1).mean()), 3) if len(moving) else None
        names = positions.dropna(subset=["ship_name"]).drop_duplicates("mmsi", keep="last").set_index("mmsi").ship_name
        mm = positions.mmsi.drop_duplicates()
        out["mmsi_gear_beacon_like"] = int(sum(gear_beacon_like(names.get(m), m) for m in mm))
    if len(static):
        ls = latest_static(static)
        out["mmsi_with_static"] = int(len(ls))
        labels = ls.ship_type_label.dropna()
        out["top_ship_types"] = {str(k): int(v) for k, v in labels.value_counts().head(12).items()}
        ln = ls.length_m.dropna()
        if len(ln):
            out["length_m_quartiles"] = [round(float(q), 1) for q in np.percentile(ln, [25, 50, 75])]
            out["mmsi_with_length_under_15m"] = int((ln < 15).sum())
    return out
