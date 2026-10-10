"""Radar chips: serve cached `data/cache/chips/<det_id>.webp` (130 x 64 px: VV, 2 px gap, VH). Building a missing chip
from Sentinel-1 GRD (`fetch=1`) is not in this round's backend; it answers 404 with a clear message.
"""

from __future__ import annotations

import re

from fastapi.responses import Response

from .envelope import ApiError

SAFE = re.compile(r"^[A-Za-z0-9_\-]+$")


def chip_response(store, det_id: str, fetch: bool = False) -> Response:
    if not SAFE.match(det_id):
        raise ApiError(422, "bad_id", "det_id may hold only letters, digits, '_' and '-'")
    p = store.cat.guard(store.settings.chips_dir / f"{det_id}.webp")
    if p.exists():
        return Response(p.read_bytes(), media_type="image/webp", headers={"Cache-Control": "max-age=3600"})
    if fetch:
        raise ApiError(404, "chip_builder_unavailable",
                       f"No cached chip for {det_id}, and this backend cannot build one yet: the chip builder (Sentinel-1 "
                       "GRD window from the AWS mirror) is planned for a later round. The contact page shows its data "
                       "without the chip.")
    raise ApiError(404, "chip_not_cached", f"No cached chip for {det_id}. fetch=1 asks the chip builder (not available yet).")
