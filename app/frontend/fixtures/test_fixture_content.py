"""Content checks of the committed development fixture (fixtures/bundle_small.json), offline.

Board D6.2 and the review of R3-T11: every L1 lead of the fixture stands on an unmatched, unambiguous contact (a lead
whose contact is now ambiguous, matched or without AIS coverage is stale and stays out); the Pearl River pass carries
matched contacts of every quality with their hand-check notes, ambiguous contacts with candidates (both cases: one
candidate MMSI and two or more), no_coverage
contacts including hand-checked ones, and AIS-only vessels; the file stays under about 1 MB.

Usage: /home/user/.mamba/envs/darkvessel/bin/python -m pytest -q app/frontend/fixtures/test_fixture_content.py
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_encoders import decode  # noqa: E402

BUNDLE = Path(__file__).resolve().parent / "bundle_small.json"
PR = "live_S1D_20261010T1032"


@pytest.fixture(scope="module")
def parts():
    return json.loads(BUNDLE.read_text(encoding="utf-8"))["parts"]


def det_ids(col: dict, n: int) -> list[str]:
    p = np.frombuffer(base64.b64decode(col["p"]), dtype="<u2")
    q = np.frombuffer(base64.b64decode(col["q"]), dtype="<u4")
    w = col["w"]
    width = (lambda k: w[0] if len(w) == 1 else w[k]) if isinstance(w, list) else (lambda k: w)
    return [f"{col['pfx'][p[i]]}_{str(int(q[i])).zfill(width(p[i]))}" for i in range(n)]


@pytest.fixture(scope="module")
def contacts(parts):
    c = parts["contacts"]
    ids = det_ids(c["columns"]["det_id"], c["n"])
    status = decode(c["columns"]["ais_status"])
    run = decode(c["columns"]["run_id"])
    return {d: {"ais_status": status[i], "run_id": run[i], **c.get("records", {}).get(d, {})} for i, d in enumerate(ids)}, ids


def test_size_under_about_1_mb():
    assert BUNDLE.stat().st_size < 1_050_000


def test_every_l1_lead_stands_on_an_unmatched_unambiguous_contact(parts, contacts):
    by_id, ids = contacts
    L = parts["leads"]
    types = decode(L["columns"]["lead_type"])
    ptypes = decode(L["columns"]["primary_type"])
    prim = decode(L["columns"]["primary"])
    n_l1 = 0
    for t, pt, row in zip(types, ptypes, prim):
        if t != "L1":
            continue
        n_l1 += 1
        assert pt == "contacts" and row is not None
        c = by_id[ids[int(row)]]
        assert c["ais_status"] == "unmatched", (ids[int(row)], c["ais_status"])
        assert not c.get("match_ambiguous") and not c.get("ambiguous_mmsi"), ids[int(row)]
        assert f"L1-{ids[int(row)]}" in c.get("lead_ids", [])
    assert n_l1 >= 10


def test_ambiguous_contacts_have_candidates_and_no_lead(contacts):
    by_id, _ = contacts
    amb = [d for d, c in by_id.items() if c.get("match_ambiguous")]
    assert len(amb) >= 5
    for d in amb:
        c = by_id[d]
        assert c["ais_status"] == "unmatched"
        assert c.get("ambiguous_mmsi") and all(len(m) == 9 for m in str(c["ambiguous_mmsi"]).split(";"))
        assert not c.get("lead_ids"), d
    # both cases of docs/live_pass.md item 11, which the page words differently: one candidate (another radar contact
    # fits the same vessel) and two or more (several vessels fit this return)
    n_cand = [len(str(by_id[d]["ambiguous_mmsi"]).split(";")) for d in amb]
    assert 1 in n_cand and any(n >= 2 for n in n_cand), n_cand


def test_pearl_river_pass_roles(parts, contacts):
    by_id, _ = contacts
    pr = {d: c for d, c in by_id.items() if c["run_id"] == PR}
    matched = [c for c in pr.values() if c["ais_status"] == "matched"]
    assert {c.get("review_note", "").split(":")[0] for c in matched} >= {"confirmed", "plausible", "doubtful"}
    assert all(c.get("identity_label") == "live AIS relayed by aisstream.io; terms UNVERIFIED" for c in matched)
    nocov = [c for c in pr.values() if c["ais_status"] == "no_coverage"]
    assert nocov and any(c.get("review_note") for c in nocov)
    feats = [f["properties"] for f in parts["passes"]["features"] if f["properties"].get("pass_id") == PR]
    assert len(feats) == 1 and feats[0]["ais_only"] and feats[0]["n_ais_only"] >= len(feats[0]["ais_only"])
