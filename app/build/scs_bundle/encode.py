"""Encoders of the contract 6.2 bundle format and a Python decoder that mirrors app/frontend/src/adapters/columns.ts.

Columns are `{"t": type, ...}`. Typed columns are little-endian arrays in base64 `b` with an explicit `na` (README
reading 4); integers carry the scale `s` (value = raw / s; omitted when 1). The encoder picks the narrowest storage
that holds a column: `const` when every row has the same value, else the narrowest of u8, u16 or i16, u32 or i32 at the
column's scale, or `dict8` (or `dict16`) over the distinct values when that is smaller (a low-cardinality numeric
column such as a time offset in whole hours decodes exactly that way). The frontend reads `t`, never assumes it.

Geometry (`geom`): lon, lat x 10,000 as interleaved i32, `ring` = first vertex of each line or ring, `feat` = first
ring of each feature; polygon rings drop the closing vertex (README reading 8). Points carry one vertex per feature.
"""

from __future__ import annotations

import base64
import json
import math
import re
from typing import Any, Iterable

import numpy as np
import pandas as pd

TIME_NA = 4294967295
REF_NA = 65535
# (type, numpy dtype, null code, lowest value, highest value); the null code is never a value
INT_TYPES = {
    "u8": ("<u1", 255, 0, 254),
    "u16": ("<u2", 65535, 0, 65534),
    "i16": ("<i2", -32768, -32767, 32767),
    "u32": ("<u4", 4294967295, 0, 4294967294),
    "i32": ("<i4", -2147483648, -2147483647, 2147483647),
}
WIDTH = {"u8": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4, "f32": 4, "dict8": 1, "dict16": 2}
DETID_RE = re.compile(r"^(.*)_(\d+)$")
LIGHTID_RE = re.compile(r"^(SPP|N20|N21)_(\d{8}T\d{6})_(\d{6})$")
SAT_CODE = {"S-NPP": "SPP", "NOAA-20": "N20", "NOAA-21": "N21"}


class EncodeError(ValueError):
    pass


# ----------------------------------------------------------------------------------------------- values
def is_null(v) -> bool:
    if v is None or v is pd.NA or v is pd.NaT:
        return True
    if isinstance(v, (float, np.floating)):
        return not math.isfinite(float(v))
    return False


def plain(v):
    """JSON value of one cell: None for nulls, Python int for integral numbers, float otherwise."""
    if is_null(v):
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return int(f) if f.is_integer() and abs(f) < 2**53 else f
    if isinstance(v, (pd.Timestamp,)):
        return iso(v)
    return v


def iso(v) -> str | None:
    if is_null(v):
        return None
    t = pd.Timestamp(v)
    if pd.isna(t):
        return None
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def b64(arr: np.ndarray, dtype: str) -> str:
    return base64.b64encode(np.ascontiguousarray(np.asarray(arr).astype(dtype)).tobytes()).decode("ascii")


def unb64(b: str, dtype: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b), dtype=np.dtype(dtype))


# ----------------------------------------------------------------------------------------------- column encoders
def const(v) -> dict:
    return {"t": "const", "v": plain(v)}


def strs(values: Iterable) -> dict:
    return {"t": "str", "v": [None if is_null(v) else str(v) for v in values]}


def int_type(lo: float, hi: float) -> str:
    """Narrowest integer type that holds [lo, hi] with a reserved null code."""
    for t in ("u8", "u16", "i16", "u32", "i32"):
        _, _, a, b = INT_TYPES[t]
        if lo >= a and hi <= b:
            return t
    raise EncodeError(f"range {lo}..{hi} does not fit a 32-bit integer")


def dict_col(values: Iterable, order: list | None = None) -> dict:
    """dict8 (under 255 categories) or dict16 (under 65,535); `order` fixes the category order (others are appended
    sorted). Values may be strings or numbers; the dict holds them as JSON values."""
    vals = [plain(v) for v in values]
    present = {v for v in vals if v is not None}
    cats = list(dict.fromkeys(order or []))
    known = set(cats)
    cats += sorted((v for v in present if v not in known), key=lambda x: (isinstance(x, str), x))
    if len(cats) < 255:
        t, dt, na = "dict8", "<u1", 255
    elif len(cats) < 65535:
        t, dt, na = "dict16", "<u2", 65535
    else:
        return strs(vals)
    idx = {c: i for i, c in enumerate(cats)}
    arr = np.fromiter((na if v is None else idx[v] for v in vals), dtype=np.int64, count=len(vals))
    return {"t": t, "dict": cats, "na": na, "b": b64(arr, dt)}


def bool8(values: Iterable) -> dict:
    out = []
    for v in values:
        if is_null(v):
            out.append(255)
        else:
            out.append(1 if bool(v) else 0)
    return {"t": "bool8", "na": 255, "b": b64(np.array(out, dtype=np.int64), "<u1")}


def _json_len(v) -> int:
    return len(json.dumps(v, separators=(",", ":"), ensure_ascii=False))


def num(values: Iterable, s: int = 1, allow_dict: bool = True, allow_const: bool = True, f32: bool = False) -> dict:
    """A numeric column: const, the narrowest integer type at scale `s`, or dict8/dict16 over its exact values when
    that is smaller. `f32` forces float32 (NaN = null)."""
    x = pd.to_numeric(pd.Series(list(values), dtype=object), errors="coerce").to_numpy(dtype=float)
    ok = np.isfinite(x)
    if f32:
        return {"t": "f32", "b": b64(np.where(ok, x, np.nan), "<f4")}
    n = len(x)
    if not ok.any():
        return const(None)
    if allow_const and ok.all() and np.all(x == x[0]):
        return const(x[0])
    if not float(s).is_integer() or s < 1:
        raise EncodeError("the scale must be a positive integer (value = raw / s)")
    s = int(s)
    raw = np.round(x[ok] * s)
    t = int_type(float(raw.min()), float(raw.max()))
    dt, na, _, _ = INT_TYPES[t]
    int_cost = n * WIDTH[t]
    if allow_dict:
        uniq = np.unique(x[ok])
        if len(uniq) < 65535:
            dw = 1 if len(uniq) < 255 else 2
            dict_cost = n * dw + sum(_json_len(plain(u)) + 1 for u in uniq)
            if dict_cost < int_cost:
                return dict_col([plain(v) if k else None for v, k in zip(x, ok)])
    full = np.full(n, na, dtype=np.int64)
    full[ok] = raw.astype(np.int64)
    out = {"t": t, "na": int(na), "b": b64(full, dt)}
    if s != 1:
        out["s"] = s
    return out


def time_col(values: Iterable, epoch: str | None = None) -> dict:
    """u32 seconds since `e` (the column's first UTC midnight unless given); 4294967295 means null."""
    t = pd.to_datetime(pd.Series(list(values), dtype=object), utc=True, errors="coerce", format="ISO8601")
    ok = t.notna().to_numpy()
    secs = np.zeros(len(t), dtype=np.int64)
    if ok.any():
        secs[ok] = t[ok].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().astype("datetime64[s]").astype(np.int64)
    if epoch is None:
        e0 = int(secs[ok].min()) if ok.any() else 0
        e0 -= e0 % 86400
    else:
        e0 = int(pd.Timestamp(epoch).value // 10**9)
    rel = secs - e0
    if ok.any() and (rel[ok].min() < 0 or rel[ok].max() >= TIME_NA):
        raise EncodeError("a time falls outside the u32 range of the epoch")
    out = np.where(ok, rel, TIME_NA)
    return {"t": "time", "e": pd.Timestamp(e0, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"), "na": TIME_NA,
            "b": b64(out, "<u4")}


def time_or_dict(values: Iterable) -> dict:
    """`time`, or `dict8`/`dict16` over the ISO strings when that is smaller (both decode to the same ISO text).
    Only for columns the frontend reads as text, never for a column it reads with timeMs (acq_utc, time_utc)."""
    iso_vals = [iso(v) for v in values]
    present = sorted({v for v in iso_vals if v is not None})
    n = len(iso_vals)
    if len(present) < 65535:
        dw = 1 if len(present) < 255 else 2
        if n * dw + sum(len(p) + 3 for p in present) < n * 4:
            return dict_col(iso_vals, present)
    return time_col(iso_vals)


def detid(values: Iterable) -> dict:
    """`pfx[p] + "_" + pad(q, w[p])`, w parallel to pfx (README reading 2); `str` when an id does not fit."""
    vals = [str(v) for v in values]
    pfx, widths, p, q, index = [], [], [], [], {}
    for v in vals:
        m = DETID_RE.match(v)
        if not m:
            return strs(vals)
        head, tail = m.group(1), m.group(2)
        key = (head, len(tail))
        if key not in index:
            index[key] = len(pfx)
            pfx.append(head)
            widths.append(len(tail))
        p.append(index[key])
        q.append(int(tail))
    if len(pfx) > 65535 or (q and max(q) > 4294967295):
        return strs(vals)
    return {"t": "detid", "pfx": pfx, "w": widths, "p": b64(np.array(p, np.int64), "<u2"), "q": b64(np.array(q, np.int64), "<u4")}


def lightid(ids: Iterable, satellites: Iterable, times: Iterable) -> dict:
    """`<SPP|N20|N21>_<time yyyymmddThhmmss>_<pad(q, 6)>`, rebuilt from the satellite and time_utc columns of the
    same part (README reading 3); `str` when any id does not follow the rule."""
    ids, sats = [str(v) for v in ids], list(satellites)
    stamps = [None if iso(t) is None else iso(t).replace("-", "").replace(":", "")[:15] for t in times]
    q = []
    for v, sat, st in zip(ids, sats, stamps):
        m = LIGHTID_RE.match(v)
        if not m or m.group(1) != SAT_CODE.get(sat) or m.group(2) != st:
            return strs(ids)
        q.append(int(m.group(3)))
    return {"t": "lightid", "w": 6, "q": b64(np.array(q, np.int64), "<u4")}


def ref16(indices: Iterable, to: str, allow_const: bool = True) -> dict:
    """u16 row index into the part `to`; a column with no reference at all is `const` null."""
    arr = np.array([REF_NA if (i is None or is_null(i) or int(i) < 0) else int(i) for i in indices], dtype=np.int64)
    if len(arr) and arr[arr != REF_NA].max(initial=0) >= REF_NA:
        raise EncodeError(f"ref16 index into {to} over 65,534")
    if allow_const and len(arr) and np.all(arr == REF_NA):
        return const(None)
    return {"t": "ref16", "to": to, "na": REF_NA, "b": b64(arr, "<u2")}


def u32_index(indices: Iterable) -> dict:
    """Row indices (leads `primary`): the narrowest unsigned type, null as its maximum."""
    return num([None if i is None or int(i) < 0 else int(i) for i in indices], 1, allow_dict=False, allow_const=False)


# ----------------------------------------------------------------------------------------------- geometry
def geom(geoms, kind: str, props: dict | None = None, note: str | None = None) -> dict:
    """`geom` block of shapely geometries (None or empty geometries become features with no rings)."""
    xy: list[float] = []
    ring: list[int] = []
    feat: list[int] = []
    n = 0
    for g in geoms:
        n += 1
        feat.append(len(ring))
        if kind == "point":
            if g is None or g.is_empty:
                raise EncodeError("a point layer cannot hold an empty geometry")
            xy.extend([g.x, g.y])
            continue
        if g is None or g.is_empty:
            continue
        parts = list(g.geoms) if hasattr(g, "geoms") else [g]
        for part in parts:
            if part.is_empty:
                continue
            if kind == "polygon":
                if part.geom_type != "Polygon":
                    continue
                rings = [part.exterior, *part.interiors]
            else:
                if part.geom_type != "LineString":
                    continue
                rings = [part]
            for r in rings:
                c = np.asarray(r.coords)[:, :2]
                if kind == "polygon" and len(c) > 1 and np.allclose(c[0], c[-1]):
                    c = c[:-1]
                if len(c) == 0:
                    continue
                ring.append(len(xy) // 2)
                xy.extend(c.ravel().tolist())
    out = {"kind": kind, "n": n, "xy": b64(np.round(np.array(xy, dtype=float) * 10000), "<i4")}
    if kind != "point":
        out["ring"] = b64(np.array(ring, np.int64), "<u4")
        out["feat"] = b64(np.array(feat, np.int64), "<u4")
    if props:
        out["props"] = props
    if note:
        out["note"] = note
    return out


# ----------------------------------------------------------------------------------------------- part
def part(kind: str, n: int, columns: dict, records: dict | None = None, prov: dict | None = None,
         note: str | None = None, **more) -> dict:
    out: dict[str, Any] = {"type": kind, "n": int(n), "columns": columns}
    if records is not None:
        out["records"] = records
    if prov is not None:
        out["prov"] = prov
    if note:
        out["note"] = note
    out.update(more)
    return out


def dumps(obj) -> str:
    """Compact JSON for an embedded part; '<' becomes \\u003c so no '</script' or '<!--' can end or bend the element."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")


def element(name: str, obj) -> str:
    return f'<script type="application/json" id="scs-part-{name}">{dumps(obj)}</script>\n'


def element_bytes(name: str, obj) -> int:
    return len(element(name, obj).encode("utf-8"))


def column_bytes(spec: dict) -> int:
    return len(json.dumps(spec, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


# ----------------------------------------------------------------------------------------------- decoder
# Mirrors app/frontend/src/adapters/columns.ts (decodeColumn, decodePart, decodeGeom); the tests round-trip through it.
_TYPED = {"i32": "<i4", "u32": "<u4", "i16": "<i2", "u16": "<u2", "u8": "<u1", "bool8": "<u1", "dict8": "<u1",
          "dict16": "<u2", "ref16": "<u2", "f32": "<f4", "time": "<u4"}


def _pad(n: int, w: int) -> str:
    s = str(int(n))
    return s.rjust(w, "0")


def decode_column(spec: dict, n: int, ctx: dict | None = None) -> list:
    ctx = ctx or {}
    t = spec["t"]
    if t == "const":
        return [spec.get("v")] * n
    if t == "str":
        v = spec.get("v") or []
        return [v[i] if i < len(v) else None for i in range(n)]
    if t == "detid":
        p, q = unb64(spec.get("p", ""), "<u2"), unb64(spec.get("q", ""), "<u4")
        pfx, w = spec.get("pfx") or [], spec.get("w")
        out = []
        for i in range(n):
            k = int(p[i])
            width = (w[0] if len(w) == 1 else w[k]) if isinstance(w, list) else (w or 5)
            out.append(pfx[k] + "_" + _pad(q[i], width))
        return out
    if t == "lightid":
        q = unb64(spec.get("q", ""), "<u4")
        w = spec["w"] if isinstance(spec.get("w"), int) else 6
        sat, tim = ctx.get("satellite"), ctx.get("time_utc")
        out = []
        for i in range(n):
            s = "" if sat is None else str(sat[i])
            ts = "" if tim is None else str(tim[i])
            stamp = ts.replace("-", "").replace(":", "")[:15]
            out.append(SAT_CODE.get(s, s) + "_" + stamp + "_" + _pad(q[i], w))
        return out
    if not spec.get("b"):
        return [None] * n
    if t not in _TYPED:
        raise EncodeError("unknown column type " + t)
    arr = unb64(spec["b"], _TYPED[t])
    s = spec.get("s") or 1
    na = spec.get("na")
    if t in ("dict8", "dict16"):
        d = spec.get("dict") or []
        nav = na if na is not None else (255 if t == "dict8" else 65535)
        return [None if int(a) == nav else (d[int(a)] if int(a) < len(d) else None) for a in arr[:n]]
    if t == "bool8":
        nav = 255 if na is None else na
        return [None if int(a) == nav else int(a) == 1 for a in arr[:n]]
    if t == "time":
        e = pd.Timestamp(spec.get("e") or "1970-01-01T00:00:00Z").value // 10**9
        nav = TIME_NA if na is None else na
        return [None if int(a) == nav else pd.Timestamp(int(e) + int(a), unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
                for a in arr[:n]]
    if t == "ref16":
        nav = REF_NA if na is None else na
        return [None if int(a) == nav else int(a) for a in arr[:n]]
    if t == "f32":
        return [None if not np.isfinite(a) else float(a) for a in arr[:n]]
    return [None if (na is not None and int(a) == na) else int(a) / s for a in arr[:n]]


def decode_part(p: dict) -> dict[str, list]:
    cols = p.get("columns") or {}
    n = int(p.get("n", 0))
    out: dict[str, list] = {}
    for name in ("satellite", "time_utc"):
        if name in cols:
            out[name] = decode_column(cols[name], n)
    for name, spec in cols.items():
        if name not in out:
            out[name] = decode_column(spec, n, out)
    return out


def decode_geom(g: dict) -> list:
    """Features as lists of rings of (lon, lat) tuples (points: one ring of one vertex)."""
    xy = unb64(g["xy"], "<i4").astype(float) / 10000
    nv = len(xy) // 2
    if g["kind"] == "point":
        return [[[(xy[2 * j], xy[2 * j + 1])]] for j in range(g["n"])]
    ring = unb64(g.get("ring", ""), "<u4")
    feat = unb64(g.get("feat", ""), "<u4")
    out = []
    for j in range(g["n"]):
        r0 = int(feat[j])
        r1 = int(feat[j + 1]) if j + 1 < len(feat) else len(ring)
        rings = []
        for r in range(r0, r1):
            v0 = int(ring[r])
            v1 = int(ring[r + 1]) if r + 1 < len(ring) else nv
            rings.append([(xy[2 * v], xy[2 * v + 1]) for v in range(v0, v1)])
        out.append(rings)
    return out


def tolerance(spec: dict) -> float:
    """Largest decode error of a column: half a scale step for scaled integers, float32 rounding for f32."""
    t = spec.get("t")
    if t in INT_TYPES:
        return 0.5 / (spec.get("s") or 1) + 1e-9
    if t == "f32":
        return 1e-6
    return 0.0
