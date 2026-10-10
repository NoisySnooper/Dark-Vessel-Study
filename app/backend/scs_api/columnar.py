"""Encoder of the contract 6.2 columnar format, shared by `/api/v1/layers/{name}.cols` and the round 3 bundle builder.

A column is `{"t": type, ...}`: typed little-endian arrays in base64 (`i32`, `u32`, `i16`, `u16`, `u8`, `f32`), `bool8`,
`dict8`/`dict16`, `time` (u32 seconds since `e`), `detid`, `lightid`, `ref16`, `const`, `str`. The encoder picks the
narrowest integer type that holds a scaled column and records it in `t`; the frontend reads `t`, never assumes it.
"""

from __future__ import annotations

import base64
import re

import numpy as np
import pandas as pd

from .records import clean

_INT_TYPES = [("u8", np.uint8, 255), ("i16", np.int16, None), ("u16", np.uint16, 65535), ("i32", np.int32, None),
              ("u32", np.uint32, 4294967295)]
DETID_RE = re.compile(r"^(.*)_(\d+)$")
LIGHTID_RE = re.compile(r"^(SPP|N20|N21)_(\d{8}T\d{6})_(\d+)$")
SAT_CODE = {"S-NPP": "SPP", "NOAA-20": "N20", "NOAA-21": "N21"}


def b64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr).astype(arr.dtype.newbyteorder("<"), copy=False).tobytes()).decode()


def b64_decode(b: str, dtype) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b), dtype=np.dtype(dtype).newbyteorder("<"))


def const(v) -> dict:
    return {"t": "const", "v": clean(v)}


def num(values, scale: float = 1, kind: str | None = None) -> dict:
    """Scaled integer column (narrowest type that holds it, with an `na` code for nulls) or f32 when kind='f32'."""
    x = pd.to_numeric(pd.Series(values), errors="coerce").to_numpy(dtype=float)
    if kind == "f32":
        return {"t": "f32", "b": b64(x.astype(np.float32))}
    ok = np.isfinite(x)
    raw = np.round(x * scale)
    lo, hi = (raw[ok].min(), raw[ok].max()) if ok.any() else (0, 0)
    for t, dt, na in _INT_TYPES:
        info = np.iinfo(dt)
        if na is None:  # signed: the minimum is the null code
            na_code, bottom, top = int(info.min), int(info.min) + 1, int(info.max)
        else:  # unsigned: the maximum is the null code
            na_code, bottom, top = int(na), int(info.min), int(na) - 1
        if lo >= bottom and hi <= top:
            out = np.where(ok, raw, na_code).astype(dt)
            spec = {"t": t, "b": b64(out), "na": int(na_code)}
            if scale != 1:
                spec["s"] = scale
            return spec
    return {"t": "f32", "b": b64(x.astype(np.float32))}


def bool8(values) -> dict:
    s = pd.Series(values, dtype=object)
    out = np.full(len(s), 255, np.uint8)
    for i, v in enumerate(s):
        v = clean(v)
        if v is not None:
            out[i] = 1 if bool(v) else 0
    return {"t": "bool8", "b": b64(out)}


def dict_col(values) -> dict:
    s = [clean(v) for v in values]  # (pandas map turns a returned None back into NaN)
    cats = sorted({v for v in s if v is not None}, key=str)
    t, dt, na = ("dict8", np.uint8, 255) if len(cats) < 255 else ("dict16", np.uint16, 65535)
    if len(cats) >= 65535:
        return {"t": "str", "v": s}
    idx = {v: i for i, v in enumerate(cats)}
    out = np.array([na if v is None else idx[v] for v in s], dtype=dt)
    return {"t": t, "dict": cats, "b": b64(out)}


def time_col(values) -> dict:
    t = pd.Series(values)
    t = t if pd.api.types.is_datetime64_any_dtype(t) else pd.to_datetime(t, utc=True, errors="coerce", format="ISO8601")
    if t.dt.tz is not None:
        t = t.dt.tz_convert("UTC").dt.tz_localize(None)
    ok = t.notna().to_numpy()
    # pandas 3 keeps the parsed resolution (s, ms, us or ns): convert to seconds explicitly
    secs = np.where(ok, t.to_numpy().astype("datetime64[s]").astype("int64"), 0)
    e = int(secs[ok].min()) if ok.any() else 0
    e = e - e % 86400  # whole UTC day
    out = np.where(ok, secs - e, 4294967295).astype(np.uint32)
    epoch = pd.Timestamp(e, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"t": "time", "e": epoch, "b": b64(out)}


def detid(values) -> dict:
    """`pfx[p] + '_' + pad(q, w[p])`; falls back to `str` when an id does not end in `_<digits>`."""
    pfx, widths, p, q, index = [], [], [], [], {}
    for v in values:
        m = DETID_RE.match(str(v))
        if not m:
            return {"t": "str", "v": [clean(x) for x in values]}
        head, tail = m.group(1), m.group(2)
        key = (head, len(tail))
        if key not in index:
            index[key] = len(pfx)
            pfx.append(head)
            widths.append(len(tail))
        p.append(index[key])
        q.append(int(tail))
    if len(pfx) > 65535 or (q and max(q) > 4294967295):
        return {"t": "str", "v": [clean(x) for x in values]}
    return {"t": "detid", "pfx": pfx, "w": widths, "p": b64(np.array(p, np.uint16)), "q": b64(np.array(q, np.uint32))}


def lightid(values, satellite, time_utc) -> dict:
    """Light ids rebuilt from satellite, time and a 6-digit index; `str` when any id does not follow the rule."""
    stamps = pd.to_datetime(pd.Series(time_utc), utc=True, errors="coerce", format="ISO8601").dt.strftime("%Y%m%dT%H%M%S")
    q = []
    for v, sat, st in zip(values, satellite, stamps):
        m = LIGHTID_RE.match(str(v))
        if not m or m.group(1) != SAT_CODE.get(sat) or m.group(2) != st or len(m.group(3)) != 6:
            return {"t": "str", "v": [clean(x) for x in values]}
        q.append(int(m.group(3)))
    return {"t": "lightid", "w": 6, "q": b64(np.array(q, np.uint32))}


def strs(values) -> dict:
    return {"t": "str", "v": [clean(x) for x in values]}


def auto(series: pd.Series, scale: float | None = None) -> dict:
    """A reasonable encoding for any column: const, bool8, scaled integer, time, dict or str."""
    s = series
    vals = s.map(clean) if s.dtype == object else s
    nonnull = pd.Series(vals).dropna()
    if len(nonnull) == 0:
        return const(None)
    if pd.Series(vals).nunique(dropna=False) == 1:
        return const(nonnull.iloc[0])
    if s.dtype == bool or str(s.dtype) == "boolean":
        return bool8(s)
    if pd.api.types.is_numeric_dtype(s):
        return num(s, scale or 1)
    if pd.api.types.is_datetime64_any_dtype(s):
        return time_col(s)
    if nonnull.map(lambda v: isinstance(v, bool)).all():
        return bool8(s)
    if nonnull.nunique() <= max(255, len(nonnull) // 4):
        return dict_col(s)
    return strs(s)


def part(kind: str, columns: dict, n: int, records: dict | None = None, **more) -> dict:
    out = {"type": kind, "n": int(n), "columns": columns}
    if records:
        out["records"] = records
    out.update(more)
    return out


def decode(spec: dict, n: int) -> list:
    """Python decoder of the same encodings (tests and the round 3 builder's checks)."""
    t = spec["t"]
    if t == "const":
        return [spec.get("v")] * n
    if t == "str":
        return list(spec["v"])
    if t == "detid":
        p, q = b64_decode(spec["p"], np.uint16), b64_decode(spec["q"], np.uint32)
        w = spec["w"]
        return [f"{spec['pfx'][a]}_{int(b):0{w[a] if len(w) > 1 else w[0]}d}" for a, b in zip(p, q)]
    dt = {"i32": np.int32, "u32": np.uint32, "i16": np.int16, "u16": np.uint16, "u8": np.uint8, "bool8": np.uint8,
          "dict8": np.uint8, "dict16": np.uint16, "f32": np.float32, "time": np.uint32, "ref16": np.uint16}[t]
    a = b64_decode(spec["b"], dt)
    if t == "f32":
        return [None if not np.isfinite(v) else float(v) for v in a]
    if t == "bool8":
        return [None if v == 255 else bool(v) for v in a]
    if t in ("dict8", "dict16"):
        na = 255 if t == "dict8" else 65535
        return [None if v == na else spec["dict"][v] for v in a]
    if t == "time":
        e = pd.Timestamp(spec["e"]).value // 10**9
        return [None if v == 4294967295 else pd.Timestamp(int(e) + int(v), unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
                for v in a]
    s, na = spec.get("s", 1), spec.get("na")
    return [None if na is not None and v == na else float(v) / s if s != 1 else int(v) for v in a]
