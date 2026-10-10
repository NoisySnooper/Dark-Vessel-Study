"""Vessels: search list, one vessel, its track."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ..envelope import item_response, raw_response
from ..models import ItemEnvelope, ListEnvelope, TrackGap
from .common import listed, window


def router(store) -> APIRouter:
    M = store.models
    r = APIRouter(tags=["vessels"])

    @r.get("/vessels", response_model=ListEnvelope[M["vessel_summary"]])
    def vessels(q: Optional[str] = None, in_aoi: Optional[str] = None, ais_class: Optional[str] = None,
                limit: int = Query(100, ge=0, le=1000), offset: int = Query(0, ge=0)):
        pos, total = store.vessels_query({"q": q, "in_aoi": in_aoi, "ais_class": ais_class})
        return listed(store, pos, total, limit, offset, lambda s: store.vessel_rows(s, full=False))

    @r.get("/vessels/{vessel_key}/track", responses={200: {"description": "GeoJSON FeatureCollection of positions with t, plus gaps"}})
    def track(vessel_key: str, t0: Optional[str] = None, t1: Optional[str] = None,
              max_points: int = Query(2000, ge=2, le=20000)):
        w = window(t0, t1)
        fc = store.track(vessel_key, w["t0"], w["t1"], max_points)
        for g in fc.get("gaps", []):
            TrackGap.model_validate(g)
        fc["vessel_key"] = vessel_key
        fc["track_caveat"] = store.cav()  # the envelope caveat is PRODUCT_CAVEAT; this one adds the build line
        return raw_response(store.settings, fc)

    @r.get("/vessels/{vessel_key}", response_model=ItemEnvelope[M["vessel"]])
    def vessel(vessel_key: str):
        return item_response(store.settings, store.vessel(vessel_key))

    return r
