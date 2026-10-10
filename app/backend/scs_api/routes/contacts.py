"""Contacts: list, one contact, its chip, and owner labels."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Query

from .. import chips
from ..decisions import append_label
from ..envelope import item_response
from ..models import ItemEnvelope, LabelIn, LabelRecord, ListEnvelope
from .common import listed, window


def router(store) -> APIRouter:
    M = store.models
    r = APIRouter(tags=["contacts"])

    @r.get("/contacts", response_model=ListEnvelope[M["contact_summary"]])
    def contacts(t0: Optional[str] = None, t1: Optional[str] = None, bbox: Optional[str] = None,
                 run_id: Optional[str] = None, view: Optional[str] = None, ais_status: Optional[str] = None,
                 confidence: Optional[str] = None, cnn_min: Optional[float] = None, cnn_vessel: Optional[str] = None,
                 mmsi: Optional[str] = None, pass_id: Optional[str] = None, sort: Optional[str] = None,
                 limit: int = Query(100, ge=0, le=1000), offset: int = Query(0, ge=0)):
        p = {**window(t0, t1, bbox), "run_id": run_id, "view": view, "ais_status": ais_status, "confidence": confidence,
             "cnn_min": cnn_min, "cnn_vessel": cnn_vessel, "mmsi": mmsi, "pass_id": pass_id, "sort": sort}
        pos, total = store.contacts_query(p)
        return listed(store, pos, total, limit, offset, lambda s: store.contact_rows(s, full=False))

    @r.get("/contacts/{det_id}/chip.webp", responses={200: {"content": {"image/webp": {}}}})
    def chip(det_id: str, fetch: Optional[int] = None):
        store.contact(det_id)  # 404 when the contact does not exist
        return chips.chip_response(store, det_id, fetch=bool(fetch))

    @r.get("/contacts/{det_id}", response_model=ItemEnvelope[M["contact"]])
    def contact(det_id: str):
        return item_response(store.settings, store.contact(det_id))

    @r.post("/contacts/{det_id}/label", response_model=ItemEnvelope[LabelRecord])
    def label(det_id: str, body: LabelIn = Body(...)):
        store.contact(det_id)
        user = (body.user or "").strip() or "owner"
        row = append_label(store.settings.contact_labels_path, det_id, body.label, user, body.note, store.settings.build)
        rec = {**row, "note": row["note"] or None, "caveat": store.cav(), "src": "analyst"}
        return item_response(store.settings, rec, path="data/labels/contact_labels.csv")

    return r

