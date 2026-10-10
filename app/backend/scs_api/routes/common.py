"""Shared route helpers: query parsing and list responses."""

from __future__ import annotations

from ..envelope import list_response, page
from ..loaders.common import parse_bbox, parse_time


def window(t0=None, t1=None, bbox=None) -> dict:
    return {"t0": parse_time(t0, "t0"), "t1": parse_time(t1, "t1"), "bbox": parse_bbox(bbox)}


def listed(store, positions, total, limit, offset, rows_fn, **more):
    lim, off = page(limit, offset)
    sel = positions[off: off + lim]
    return list_response(store.settings, rows_fn(sel), total, lim, off, **more)
