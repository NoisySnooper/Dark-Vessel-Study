"""Dark-lead scoring: the ranked leads queue of SCS Vessel Watch (app/CONTRACT.md section 3.5, spec section 4.1).

A lead is one object or cell plus the evidence on it, a review priority (0 to 100, not a risk score) and the product
caveat. Two types are built here: L1, an unmatched radar contact in AIS reach, and L7, lit activity where radar does
not look (a coverage lead for tasking, not a vessel lead). "Dark" means only "no AIS match"; it never means illegal.

Modules: guard (the open build never opens data/research/), rules (lead gates and the per-type explanations),
priority (factors, points and bands), evidence (joins, pairing, next look, titles), build (assembly and outputs).
"""

from __future__ import annotations

# The product caveat of app/CONTRACT.md section 1.1. R2-T5 adds the constant to darkvessel.config (board D4.2); until
# then this module holds the identical text, and tests/test_leads.py asserts the two are equal once the constant exists.
_PRODUCT_CAVEAT_TEXT = (
    "'Dark' means only that no AIS position was matched to this radar contact. It does not mean illegal. Many vessels "
    "are not required to carry AIS, AIS can be off for lawful reasons, and both satellite and terrestrial AIS have "
    "blind spots: satellite AIS misses messages in busy coastal waters, and shore receivers cover only the waters "
    "within their radio range. Treat every unmatched contact as a lead for review, not as evidence of wrongdoing. "
    "An AIS gap is not proof of intent."
)
try:
    from darkvessel.config import PRODUCT_CAVEAT  # type: ignore[attr-defined]
except ImportError:
    PRODUCT_CAVEAT = _PRODUCT_CAVEAT_TEXT

# Appended to the caveat of every research-build record (contract 1.1).
RESEARCH_LINE = ("Research build, noncommercial, CC BY-NC 4.0. Contains Global Fishing Watch data. "
                 "Powered by Global Fishing Watch.")

PRIORITY_MODEL_ID = "lead_priority_v0_20261009"
BUILDS = ("open", "research")


def caveat_for(build: str) -> str:
    """The caveat string of a record in `build`: PRODUCT_CAVEAT, plus the research line in the research build."""
    if build not in BUILDS:
        raise ValueError(f"unknown build {build!r}; expected one of {BUILDS}")
    return PRODUCT_CAVEAT if build == "open" else f"{PRODUCT_CAVEAT} {RESEARCH_LINE}"


__all__ = ["PRODUCT_CAVEAT", "RESEARCH_LINE", "PRIORITY_MODEL_ID", "BUILDS", "caveat_for"]
