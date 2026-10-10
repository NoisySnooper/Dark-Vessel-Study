"""The product caveat in darkvessel.config matches app/CONTRACT.md section 1.1 exactly."""

import re

from darkvessel import config

CONTRACT = config.REPO_ROOT / "app" / "CONTRACT.md"


def _contract_caveat() -> str:
    text = CONTRACT.read_text(encoding="utf-8")
    section = text.split("### 1.1 The product caveat", 1)[1].split("\n## ", 1)[0]
    m = re.search(r'^`PRODUCT_CAVEAT` = "(.*)"$', section, re.M)
    assert m, "no backtick-quoted PRODUCT_CAVEAT line in app/CONTRACT.md section 1.1"
    return m.group(1)


def test_product_caveat_equals_contract():
    assert config.PRODUCT_CAVEAT == _contract_caveat()


def test_product_caveat_extends_dark_caveat():
    first = "'Dark' means only that no AIS position was matched to this radar"
    assert config.DARK_CAVEAT.startswith(first)
    assert config.PRODUCT_CAVEAT.startswith(first)
    assert "It does not mean illegal." in config.PRODUCT_CAVEAT
    assert "terrestrial" in config.PRODUCT_CAVEAT
    assert config.PRODUCT_CAVEAT.endswith("An AIS gap is not proof of intent.")


def test_caveats_have_no_long_dashes():
    for s in (config.PRODUCT_CAVEAT, config.DARK_CAVEAT, config.DARK_CAVEAT_SHORT):
        assert chr(0x2014) not in s and chr(0x2013) not in s  # no em or en dash
        assert chr(0x2018) not in s and chr(0x2019) not in s  # straight quotes only


def test_dark_caveat_unchanged():
    assert config.DARK_CAVEAT_SHORT == "Dark = no AIS match. Not evidence of illegal activity."
    assert config.DARK_CAVEAT.endswith("not as evidence of wrongdoing.")
