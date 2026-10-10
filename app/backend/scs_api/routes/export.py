"""GET /api/v1/export/{format}: gpkg, geojson, csv or html of one object type, by ids or by that type's list filters."""

from __future__ import annotations

from typing import Optional

import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import Response

from .. import export as X
from ..envelope import ApiError
from ..loaders.common import csv_list
from .common import window

FILTERS = {
    "contacts": ("run_id", "view", "ais_status", "confidence", "cnn_min", "cnn_vessel", "mmsi", "pass_id", "sort"),
    "vessels": ("q", "in_aoi", "ais_class"),
    "lights": ("night", "quality"),
    "events": ("code", "event_type", "vessel_key", "det_id"),
    "leads": ("state", "lead_type", "min_priority", "region_box", "pass_id", "sort"),
    "passes": ("status", "mission"),
}


def router(store) -> APIRouter:
    r = APIRouter(tags=["export"])

    def records(typ: str, ids: list[str] | None, q: dict) -> list[dict]:
        if ids:
            one = {"contacts": store.contact, "vessels": store.vessel, "lights": store.light, "events": store.event,
                   "leads": store.lead, "passes": store.pass_}[typ]
            return [one(i) for i in ids]
        p = {**window(q.get("t0"), q.get("t1"), q.get("bbox")), **{k: q.get(k) for k in FILTERS[typ]}}
        if typ == "contacts" and q.get("confidence") is None and q.get("view") is None:
            p["confidence"] = "high,medium,fixed,low"  # exports include low objects (contract 4.4)
        query = {"contacts": store.contacts_query, "vessels": store.vessels_query, "lights": store.lights_query,
                 "events": store.events_query, "leads": store.leads_query, "passes": store.passes_query}[typ]
        pos, total = query(p)
        if total > X.MAX_ROWS:
            raise ApiError(413, "too_many_rows", f"{total:,} rows; narrow the filters (at most {X.MAX_ROWS:,} per export)")
        pos = np.asarray(pos)
        rows = {"contacts": store.contact_rows, "vessels": store.vessel_rows, "lights": store.light_rows,
                "events": store.event_rows, "leads": store.lead_rows, "passes": store.pass_rows}[typ]
        return rows(pos)

    @r.get("/export/{format}", responses={200: {"description": "file download"}})
    def export(format: str, request: Request, type: str = "contacts", ids: Optional[str] = None):
        X.check_type(type)
        if format not in X.FORMATS:
            raise ApiError(422, "bad_format", f"format must be one of {', '.join(X.FORMATS)}")
        recs = records(type, csv_list(ids), dict(request.query_params))
        body, media, name = X.export(store.settings, store.git_hash, format, type, recs, store)
        return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                          "X-Caveat": "Dark = no AIS match. Not evidence of illegal activity.",
                                                          "X-Build": store.settings.build})

    return r
