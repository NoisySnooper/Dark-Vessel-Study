"""The section 5 envelope around every JSON response, and the error envelope."""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import Response

from . import CONTRACT_VERSION
from .config import ATTRIBUTION, PRODUCT_CAVEAT, RESEARCH_LABEL, Settings
from .records import clean, dumps, utc_now


class ApiError(HTTPException):
    """An HTTP error with a contract error code; rendered as the error envelope."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(status_code=status, detail=message)
        self.code = code
        self.message = message


def head(settings: Settings) -> dict:
    h = {"contract_version": CONTRACT_VERSION, "build": settings.build, "build_label": settings.build_label,
         "caveat": PRODUCT_CAVEAT, "generated_utc": utc_now()}
    if settings.research:
        h["research_label"] = RESEARCH_LABEL
        h["attribution"] = ATTRIBUTION
    return h


class JSONBytes(Response):
    media_type = "application/json"


def _encode(body: dict) -> bytes:
    """Records built by records.build_record are already plain JSON; anything else is cleaned on the way out."""
    try:
        return dumps(body)
    except (TypeError, ValueError):
        return dumps(clean(body))


def item_response(settings: Settings, item, **more) -> Response:
    body = head(settings)
    body["item"] = item
    body.update(more)
    return JSONBytes(_encode(body))


def list_response(settings: Settings, items: list, total: int, limit: int, offset: int, **more) -> Response:
    body = head(settings)
    body.update({"items": items, "total": int(total), "limit": int(limit), "offset": int(offset)})
    body.update(more)
    return JSONBytes(_encode(body))


def raw_response(settings: Settings, body: dict, headers: dict | None = None) -> Response:
    """A non-list object (GeoJSON FeatureCollection, columnar layer) with the envelope fields added at its top level."""
    out = dict(body)
    for k, v in head(settings).items():
        out.setdefault(k, v)
    return JSONBytes(_encode(out), headers=headers)


def error_body(settings: Settings, code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}, "caveat": PRODUCT_CAVEAT, "build": settings.build,
            "contract_version": CONTRACT_VERSION}


def error_response(settings: Settings, status: int, code: str, message: str) -> Response:
    return JSONBytes(dumps(error_body(settings, code, message)), status_code=status)


def page(limit: int | None, offset: int | None, default: int = 100, maximum: int = 1000) -> tuple[int, int]:
    lim = default if limit is None else int(limit)
    off = 0 if offset is None else int(offset)
    if lim < 0 or lim > maximum:
        raise ApiError(422, "bad_limit", f"limit must be between 0 and {maximum}")
    if off < 0:
        raise ApiError(422, "bad_offset", "offset must be 0 or more")
    return lim, off
