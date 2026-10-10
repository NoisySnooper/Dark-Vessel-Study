"""Raster registry, colour-mapped WebP overlays and point values (contract 3.7, Raster layers)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response

from ..envelope import ApiError, item_response, list_response
from ..models import ItemEnvelope, ListEnvelope, RasterEntry, RasterValue


def router(store) -> APIRouter:
    r = APIRouter(tags=["rasters"])

    def rs():
        return store.data["rasters"]

    def known(name: str):
        if name not in rs().paths:
            raise ApiError(404, "not_found", f"no raster {name} in this build")

    @r.get("/rasters", response_model=ListEnvelope[RasterEntry])
    def rasters():
        items = [rs().entry(n, store.cav()) for n in rs().names()]
        return list_response(store.settings, items, len(items), len(items), 0)

    @r.get("/rasters/{name}.webp", responses={200: {"content": {"image/webp": {}}}})
    def webp(name: str, theme: Literal["dark", "light"] = "dark"):
        known(name)
        e = rs().entry(name, store.cav())
        return Response(rs().webp(name, theme), media_type="image/webp",
                        headers={"X-Bounds": ",".join(str(x) for x in e["bounds"]), "Cache-Control": "max-age=600"})

    @r.get("/rasters/{name}/value", response_model=ItemEnvelope[RasterValue])
    def value(name: str, lon: float = Query(..., ge=-180, le=180), lat: float = Query(..., ge=-90, le=90)):
        known(name)
        e = rs().entry(name, store.cav())
        rec = {"name": name, "lon": lon, "lat": lat, **rs().presence_value(name, lon, lat), "unit": e["unit"],
               "valid_period": e["valid_period"], "src": e["src"], "note": e["note"], "research_only": e["research_only"],
               "caveat": store.cav()}
        return item_response(store.settings, rec)

    return r
