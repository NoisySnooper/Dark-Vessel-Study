"""Events: list and one event (open: aisstream events when built; research: GFW events)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ..envelope import item_response
from ..models import ItemEnvelope, ListEnvelope
from .common import listed, window


def router(store) -> APIRouter:
    M = store.models
    r = APIRouter(tags=["events"])

    @r.get("/events", response_model=ListEnvelope[M["event"]])
    def events(t0: Optional[str] = None, t1: Optional[str] = None, bbox: Optional[str] = None, code: Optional[str] = None,
               event_type: Optional[str] = None, vessel_key: Optional[str] = None, det_id: Optional[str] = None,
               limit: int = Query(100, ge=0, le=1000), offset: int = Query(0, ge=0)):
        p = {**window(t0, t1, bbox), "code": code, "event_type": event_type, "vessel_key": vessel_key, "det_id": det_id}
        pos, total = store.events_query(p)
        more = {} if store.cat.exists("events_open") or store.settings.research else \
            {"note": "Open-build events (data/events_open.gpkg) are not built yet."}
        return listed(store, pos, total, limit, offset, store.event_rows, **more)

    @r.get("/events/{event_id}", response_model=ItemEnvelope[M["event"]])
    def event(event_id: str):
        return item_response(store.settings, store.event(event_id))

    return r
