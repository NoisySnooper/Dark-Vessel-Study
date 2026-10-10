"""Lights and light sites."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ..envelope import item_response
from ..models import ItemEnvelope, ListEnvelope
from .common import listed, window


def router(store) -> APIRouter:
    M = store.models
    r = APIRouter(tags=["lights"])

    @r.get("/lights", response_model=ListEnvelope[M["light_summary"]])
    def lights(t0: Optional[str] = None, t1: Optional[str] = None, bbox: Optional[str] = None, night: Optional[str] = None,
               quality: Optional[str] = None, limit: int = Query(100, ge=0, le=1000), offset: int = Query(0, ge=0)):
        pos, total = store.lights_query({**window(t0, t1, bbox), "night": night, "quality": quality})
        return listed(store, pos, total, limit, offset, lambda s: store.light_rows(s, full=False))

    @r.get("/lights/{light_id}", response_model=ItemEnvelope[M["light"]])
    def light(light_id: str):
        return item_response(store.settings, store.light(light_id))

    @r.get("/sites/{site_id}", response_model=ItemEnvelope[M["site"]])
    def site(site_id: str):
        return item_response(store.settings, store.site(site_id))

    return r
