"""Leads: queue, one lead with history and evidence previews, and the decision POST (append-only log)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Query

from ..decisions import validate
from ..envelope import item_response, list_response
from ..models import DecisionIn, ItemEnvelope, ListEnvelope
from .common import window


def router(store) -> APIRouter:
    M = store.models
    r = APIRouter(tags=["leads"])

    @r.get("/leads", response_model=ListEnvelope[M["lead"]])
    def leads(state: Optional[str] = None, lead_type: Optional[str] = None, min_priority: Optional[float] = None,
              region_box: Optional[str] = None, pass_id: Optional[str] = None, t0: Optional[str] = None,
              t1: Optional[str] = None, sort: Optional[str] = None, limit: int = Query(100, ge=0, le=20000),
              offset: int = Query(0, ge=0)):
        p = {**window(t0, t1), "state": state, "lead_type": lead_type, "min_priority": min_priority,
             "region_box": region_box, "pass_id": pass_id, "sort": sort}
        pos, total = store.leads_query(p)
        more = {}
        if store.data["leads"].source_key is None:
            more["note"] = "The leads file of this build is missing (not built yet); see /api/v1/meta files."
        sel = pos[offset: offset + limit]  # the queue loads every lead at once; summaries leave out `extra`
        return list_response(store.settings, store.lead_rows(sel, summary=True), total, limit, offset, **more)

    @r.get("/leads/{lead_id}", response_model=ItemEnvelope[M["lead"]])
    def lead(lead_id: str):
        return item_response(store.settings, store.lead(lead_id))

    @r.post("/leads/{lead_id}/decision", response_model=ItemEnvelope[M["lead"]],
            responses={409: {"description": "transition not allowed"}, 422: {"description": "reason or note missing"}})
    def decide(lead_id: str, body: DecisionIn = Body(...)):
        with store.decision_lock:
            cur = store.lead(lead_id, previews=False)
            reason = validate(cur["state"], body.to_state, body.reason, body.note)
            user = (body.user or "").strip() or "owner"
            store.decisions.append(lead_id, cur["state"], body.to_state, reason, body.note, user)
        log = "data/" + store.settings.decisions_path.relative_to(store.settings.data_dir).as_posix()
        return item_response(store.settings, store.lead(lead_id), log_path=log)

    return r
