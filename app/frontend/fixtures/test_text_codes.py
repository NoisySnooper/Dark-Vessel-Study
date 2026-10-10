"""Board D5.1: every lead code the lead builder can emit has a display string in app/frontend/src/app/text.ts.

Codes: the factor names of darkvessel.leads.priority (every `"factor": "..."` literal, so "weather unknown" is
included), the explanation codes of darkvessel.leads.rules.LAWFUL_EXPLANATIONS and the indicator codes of
CHANGE_INDICATORS. The frontend maps codes to sentences (LAWFUL_TEXT, CHANGE_TEXT, FACTOR_LABEL); a code without an
entry would show humanised, which this test does not accept. The same check runs before every frontend build
(app/frontend/scripts/check_build.mjs). Offline; reads only repo files.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from darkvessel.leads import priority, rules

TEXT_TS = Path(__file__).resolve().parents[1] / "src" / "app" / "text.ts"


def ts_map_keys(name: str) -> set[str]:
    src = TEXT_TS.read_text(encoding="utf-8")
    m = re.search(rf"export const {name}: Record<string, string> = \{{(.*?)\n\}};", src, re.S)
    assert m, f"{name} not found in {TEXT_TS}"
    return {a or b for a, b in re.findall(r'^\s+(?:"([^"]+)"|([a-z0-9_]+)):', m.group(1), re.M)}


def emitted_factors() -> set[str]:
    return set(re.findall(r'"factor": "([^"]+)"', inspect.getsource(priority))) | set(priority.FACTORS)


def test_explanation_codes_have_display_strings():
    have = ts_map_keys("LAWFUL_TEXT")
    codes = {c for per_type in rules.LAWFUL_EXPLANATIONS.values() for c in per_type}
    assert codes, "no explanation codes in darkvessel.leads.rules"
    assert not codes - have, f"explanation codes without a display string: {sorted(codes - have)}"


def test_indicator_codes_have_display_strings():
    have = ts_map_keys("CHANGE_TEXT")
    codes = {c for per_type in rules.CHANGE_INDICATORS.values() for c in per_type}
    assert codes, "no indicator codes in darkvessel.leads.rules"
    assert not codes - have, f"indicator codes without a display string: {sorted(codes - have)}"


def test_factor_names_have_display_strings():
    have = ts_map_keys("FACTOR_LABEL")
    codes = emitted_factors()
    assert "weather unknown" in codes
    assert not codes - have, f"factor names without a display string: {sorted(codes - have)}"


def test_ui_text_has_no_em_or_en_dash():
    assert not re.search("[\u2013\u2014]", TEXT_TS.read_text(encoding="utf-8"))
