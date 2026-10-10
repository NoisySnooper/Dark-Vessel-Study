"""Passes: list and one pass."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ..envelope import item_response, list_response
from ..models import ItemEnvelope, ListEnvelope
from .common import window


def router(store) -> APIRouter:
    M = store.models
    r = APIRouter(tags=["passes"])

    @r.get("/passes", response_model=ListEnvelope[M["pass"]])
    def passes(status: Optional[str] = None, t0: Optional[str] = None, t1: Optional[str] = None,
               mission: Optional[str] = None, footprint: bool = True,
               limit: int = Query(1000, ge=0, le=5000), offset: int = Query(0, ge=0)):
        pos, total = store.passes_query({**window(t0, t1), "status": status, "mission": mission})
        sel = pos[offset: offset + limit]
        return list_response(store.settings, store.pass_rows(sel, footprint=footprint), total, limit, offset)

    @r.get("/passes/{pass_id}", response_model=ItemEnvelope[M["pass"]])
    def pass_(pass_id: str):
        return item_response(store.settings, store.pass_(pass_id))

    return r
