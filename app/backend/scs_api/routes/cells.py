"""Cell context. /cells/at is declared before /cells/{cell_id} (contract 5, route order; contract test h)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ..envelope import item_response
from ..models import ItemEnvelope


def router(store) -> APIRouter:
    M = store.models
    r = APIRouter(tags=["cells"])

    @r.get("/cells/at", response_model=ItemEnvelope[M["cell"]])
    def cell_at(lon: float = Query(..., ge=-180, le=180), lat: float = Query(..., ge=-90, le=90)):
        return item_response(store.settings, store.cell_at(lon, lat))

    @r.get("/cells/{cell_id}", response_model=ItemEnvelope[M["cell"]])
    def cell(cell_id: str, night: Optional[str] = None, scene_id: Optional[str] = None):
        return item_response(store.settings, store.cell(cell_id, night, scene_id))

    return r
