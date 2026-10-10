"""Record building: JSON-safe values, UTC times with a Z suffix, contract field order, unknown columns under `extra`."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from typing import Any, Iterable

import numpy as np
import pandas as pd


NULL_STRINGS = {"", "nan", "NaN", "None", "<NA>", "NaT"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_z(v) -> str | None:
    """Any time value to ISO 8601 UTC with a Z suffix (contract 1, Types); None for nulls."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, str) and v.strip() in NULL_STRINGS:
        return None
    try:
        t = pd.Timestamp(v)
    except (ValueError, TypeError):
        return str(v)
    if pd.isna(t):
        return None
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_utc_series(s: pd.Series) -> pd.Series:
    """A column of mixed time strings or timestamps as tz-aware UTC timestamps (NaT for nulls)."""
    return pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")


def iso_series(s: pd.Series) -> pd.Series:
    """ISO 8601 UTC strings with a Z suffix (vectorised; None for nulls)."""
    t = s if pd.api.types.is_datetime64_any_dtype(s) else to_utc_series(s)
    vals = t.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().astype("datetime64[s]") if t.dt.tz is not None \
        else t.to_numpy().astype("datetime64[s]")
    out = np.char.add(np.datetime_as_string(vals, unit="s"), "Z").astype(object)
    out[pd.isna(t).to_numpy()] = None
    return pd.Series(out, index=s.index, dtype=object)


def clean(v: Any) -> Any:
    """Plain JSON value: NaN, NA and NaT to None, numpy scalars to Python, timestamps to ISO Z, containers recursively."""
    t = type(v)  # fast path for the plain types that JSON-parsed evidence and history hold (called ~1e6 times per queue)
    if v is None or t is str or t is int or t is bool:
        return v
    if t is float:
        return v if math.isfinite(v) else None
    if t is list:
        return [clean(x) for x in v]
    if t is dict:
        return {k if type(k) is str else str(k): clean(x) for k, x in v.items()}
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return None if not math.isfinite(f) else f
    if isinstance(v, str):
        return v
    if isinstance(v, (pd.Timestamp, datetime)):
        return iso_z(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, np.ndarray, pd.Series)):
        return [clean(x) for x in list(v)]
    if v is pd.NA or v is pd.NaT:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def json_list(v) -> list:
    """A list column that a producer stored as JSON text (leads: factors, evidence, history, ...)."""
    if v is None:
        return []
    if isinstance(v, (list, tuple, np.ndarray)):
        return [clean(x) for x in v]
    if isinstance(v, float) and math.isnan(v):
        return []
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            out = json.loads(s)
        except json.JSONDecodeError:
            return [s]
        return out if isinstance(out, list) else [out]
    return [clean(v)]


def json_map(v) -> dict:
    if isinstance(v, dict):
        return clean(v)
    if isinstance(v, str) and v.strip():
        try:
            out = json.loads(v)
            return out if isinstance(out, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def digits(v) -> str | None:
    """MMSI or IMO as a digit string (files hold int64, float or text; the API always returns a string)."""
    v = clean(v)
    if v is None:
        return None
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else None
    s = str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s or None


def build_record(fields: Iterable[str], row: dict, /, *, extra_cols: Iterable[str] = (), extra: dict | None = None,
                 defaults: dict | None = None, ints: set | None = None, **fixed) -> dict:
    """One record in field order: `fixed` values win, then the row's own value, then None or the list default.

    `extra_cols` are source columns outside the contract; their non-null values go under `extra` unchanged.
    """
    rec = {}
    for f in fields:
        if f in fixed:
            v = fixed[f]
        elif f == "extra":
            continue
        else:
            v = row.get(f)
        v = clean(v)
        if v is None and defaults and f in defaults:
            v = defaults[f]()
        elif ints and f in ints and isinstance(v, float) and v.is_integer():
            v = int(v)
        rec[f] = v
    if "extra" in fields or extra is not None or extra_cols:
        ex = dict(extra or {})
        for c in extra_cols:
            val = clean(row.get(c))
            if val is not None and val != "":
                ex[c] = val
        if "extra" in fields or ex:
            rec["extra"] = ex
    return rec


def subset(rec: dict, fields: Iterable[str]) -> dict:
    return {f: rec.get(f) for f in fields}


def dumps(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
