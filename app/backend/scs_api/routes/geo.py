"""Vector context layers as GeoJSON (contract 5, /geo/{name}.geojson). EEZ layers are labelled as published."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from ..envelope import ApiError, raw_response
from ..loaders.geo import NAMES
from .common import window


def router(store) -> APIRouter:
    r = APIRouter(tags=["geo"])

    @r.get("/geo/{name}.geojson", responses={200: {"description": "GeoJSON FeatureCollection with note, src and the envelope fields"}})
    def geo(name: str, t0: Optional[str] = None, t1: Optional[str] = None):
        if name not in NAMES:
            raise ApiError(404, "not_found", f"geo layer must be one of {', '.join(NAMES)}")
        w = window(t0, t1)
        fc = store.geo.layer(name, w["t0"], w["t1"])
        if fc is None:
            raise ApiError(404, "missing", f"the file of geo layer {name} is missing (not built yet)")
        cav = store.cav()
        out = {**fc, "features": [{**f, "properties": {**f["properties"], "caveat": cav}} for f in fc["features"]],
               "name": name, "default_on": False if name.startswith("eez") else None,
               "exportable": not name.startswith("eez")}
        return raw_response(store.settings, out)

    return r
