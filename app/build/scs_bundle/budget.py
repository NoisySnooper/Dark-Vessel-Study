"""Size budget of the single-file page (contract 6.3): part budgets per build, the hard cap and the drop rules.

MB is 1,000,000 bytes. A part's size is the bytes of its serialised `<script type="application/json">` element.
"""

from __future__ import annotations

MB = 1_000_000
CAP = 15_000_000  # hard cap of the build (the artifact limit is 16 MB)
SHELL_BUDGET = 2_000_000  # frontend JS and CSS (owned by the frontend task; reported, not enforced here)

BUDGETS = {
    "open": {"meta": 0.05, "contacts": 4.4, "chips": 2.0, "lights": 1.4, "vessels": 0.6, "events": 0.3, "leads": 0.4,
             "passes": 0.1, "cells": 0.6, "geo": 0.6, "rasters": 0.5, "camau": 0.6},
    "research": {"meta": 0.05, "contacts": 5.4, "chips": 1.0, "lights": 1.4, "vessels": 1.6, "events": 0.6, "leads": 0.4,
                 "passes": 0.1, "cells": 0.6, "geo": 0.6, "rasters": 0.6, "camau": 0.0},
}
PART_ORDER = ["meta", "contacts", "chips", "lights", "vessels", "events", "leads", "passes", "cells", "geo", "rasters", "camau"]

# Contract 6.3 drop rules, in the order the total cap applies them.
RULES = {
    1: ("chips", "chip budget lowered in 0.25 MB steps, from the end of the selection order"),
    2: ("lights", "lights under cloud become positions only"),
    3: ("camau", "Ca Mau backdrop at half resolution"),
    4: ("contacts", "September medium contacts with cnn_vessel false leave the bulk columns (open); their counts stay in the cells part"),
    5: ("contacts", "September medium contacts with cnn_vessel false that are not matched leave the bulk columns (research); every matched contact stays"),
    6: ("vessels", "stub vessel rows no longer referenced by a remaining contact"),
    7: ("contacts", "whole live passes leave the bulk columns, oldest first, passes with no unmatched contact first; a pass "
                    "with a matched contact never leaves (its identifications are the P0 demo)"),
}
CHIP_STEP = 250_000


def budget_bytes(build: str, part: str) -> int:
    return int(round(BUDGETS[build].get(part, 0.0) * MB))


class BudgetError(RuntimeError):
    """A part still over its budget after its drop rules, or the page over the cap after every rule."""


def live_pass_drop_order(passes: list[dict]) -> list[str]:
    """Rule 7 order: passes with no matched or unmatched contact first, then passes with unmatched only; oldest first
    within each group. A pass with a matched contact is never in the order: matched live contacts with their identity
    are never dropped (the contract 6.3 rule 7 puts them last; this build keeps them, so a page that does not fit
    without dropping one fails instead). passes: [{pass_id, start_utc, n_matched, n_unmatched}]."""
    keep = [p for p in passes if p.get("n_matched", 0) <= 0]
    return [p["pass_id"] for p in sorted(keep, key=lambda p: (int(p.get("n_unmatched", 0) > 0), str(p.get("start_utc")),
                                                              p["pass_id"]))]
