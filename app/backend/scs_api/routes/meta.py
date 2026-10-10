"""GET /api/v1/meta: build, labels, caveat, source registry, file status, git hash, priority model, counts."""

from __future__ import annotations

from fastapi import APIRouter

from ..envelope import item_response
from ..models import ItemEnvelope, MetaRecord


def router(store) -> APIRouter:
    r = APIRouter(tags=["meta"])

    @r.get("/meta", response_model=ItemEnvelope[MetaRecord])
    def meta():
        return item_response(store.settings, store.meta())

    return r
