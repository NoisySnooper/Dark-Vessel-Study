"""The bundle builder on the backend's synthetic data (offline, no chips read): layout, identity by reference, the open
build guard, content checks, the drop rules and the budget failures."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO / "app" / "build", _REPO / "app" / "backend", _REPO / "tests" / "app"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))  # appended: tests/app's conftest keeps the name `conftest`

import base64  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402

import pandas as pd
import pytest

from scs_bundle import budget as B
from scs_bundle import checks as C
from scs_bundle import encode as E
from scs_bundle import parts as P
from scs_bundle.bundle import Bundle, Options, Plan

SHELL = "<!doctype html><html><head><title>t</title></head><body><div id=root></div></body></html>"
FAKE_SECRET = "FAKEVALUE_not_a_key_0123456789"  # neutral: no real vendor key format


@pytest.fixture(scope="module")
def data_src(tmp_path_factory):
    from synthetic import write_all

    return write_all(tmp_path_factory.mktemp("bundle_data") / "data")


def run(data_dir: Path, tmp: Path, build: str, **kw) -> tuple[dict, Bundle]:
    tmp.mkdir(parents=True, exist_ok=True)
    shell = tmp / "shell.html"
    shell.write_text(SHELL)
    env = tmp / ".env"
    env.write_text(f"GFW_API_TOKEN={FAKE_SECRET}\nAISSTREAM_API_KEY=zz_another_long_value_1234\n")
    opt = Options(build=build, data_dir=data_dir, frontend=shell, out_dir=tmp / "out", no_chips=True, cache=False,
                  env_path=env, spot_n=10, log=lambda *a: None, **kw)
    b = Bundle(opt)
    return b.run(), b


def parts_of(html: str) -> dict:
    out = {}
    for m in re.finditer(r'<script type="application/json" id="scs-part-([a-z]+)">(.*?)</script>', html, re.S):
        out[m.group(1)] = json.loads(m.group(2))
    return out


@pytest.fixture(scope="module")
def built(data_src, tmp_path_factory):
    res = {}
    for b in ("open", "research"):
        tmp = tmp_path_factory.mktemp(f"out_{b}")
        rep, bundle = run(data_src, tmp, b)
        html = Path(rep["out"]).read_text(encoding="utf-8")
        res[b] = {"rep": rep, "html": html, "parts": parts_of(html), "bundle": bundle}
    return res


def test_layout_parts_before_body_and_meta_first(built):
    for b, r in built.items():
        html = r["html"]
        ids = re.findall(r'id="scs-part-([a-z]+)"', html)
        assert ids[0] == "meta"
        assert ids == [p for p in B.PART_ORDER if p in ids]
        assert html.rindex("</script>") < html.rindex("</body>")
        assert set(ids) >= {"meta", "contacts", "vessels", "lights", "passes", "geo", "cells"}
        meta = r["parts"]["meta"]
        for k in ("contract_version", "build", "build_label", "caveat", "caveat_short", "sources", "parts", "dropped",
                  "build_time_utc", "ais_recording", "live_rules", "product_caveat"):
            assert k in meta, k
        assert meta["parts"]["contacts"] == E.element_bytes("contacts", r["parts"]["contacts"])
        assert len(html.encode()) == r["rep"]["bytes"]


def test_research_labels_and_open_clean(built):
    meta = built["research"]["parts"]["meta"]
    assert meta["research_label"] == "Research build, noncommercial, CC BY-NC 4.0"
    assert meta["attribution"] == "Powered by Global Fishing Watch."
    assert any(s["research_only"] for s in meta["sources"])
    open_parts = built["open"]["parts"]
    assert C.open_build_problems(open_parts) == []
    text = json.dumps(open_parts).lower()
    assert "global fishing watch" not in text and "gfw" not in text
    assert "eez" not in open_parts["geo"]["layers"] and open_parts["geo"]["layers"]["eez_boundaries"]["kind"] == "line"


def test_identity_by_reference_every_matched_contact(built):
    for b, r in built.items():
        c = r["parts"]["contacts"]
        v = r["parts"]["vessels"]
        dc, dv = E.decode_part(c), E.decode_part(v)
        bundle = r["bundle"]
        api = bundle.rows.set_index("det_id")
        matched = [i for i, s in enumerate(dc["ais_status"]) if s == "matched"]
        assert len(matched) == int((api["ais_status"] == "matched").sum()) > 0
        for i in matched:
            d = dc["det_id"][i]
            ref = dc["vessel_ref"][i]
            assert ref is not None
            rec = c["records"].get(d, {})
            for cf, vf in P.IDENTITY_PAIRS:
                page = rec[cf] if cf in rec else dv[vf][ref]
                assert P.same(page, api.at[d, cf]), (b, d, cf)
        # identity strings stored once per vessel: no matched contact repeats them unless they differ (rule 6)
        own = r["rep"]["info"]["identity"]["own_strings"]
        idf = {cf for cf, _ in P.IDENTITY_PAIRS}
        assert sum(1 for d in c["records"] if idf & set(c["records"][d])) == own


def test_spot_records_are_the_api_records(built):
    for b, r in built.items():
        spot = r["rep"]["spot"]
        assert spot["d1_fields"][0] == "det_id" and spot["d1_fields"][-1] == "caveat"
        assert ("gfw_vessel_id" in spot["d1_fields"]) == (b == "research")
        dc = E.decode_part(r["parts"]["contacts"])
        idx = {d: i for i, d in enumerate(dc["det_id"])}
        for d, rec in spot["records"].items():
            i = idx[d]
            for f in ("det_id", "run_id", "acq_utc", "confidence", "ais_status", "research_only", "caveat"):
                assert dc[f][i] == rec[f], (b, d, f)
            for f in ("lon", "lat", "length_est_m", "cnn_score"):
                tol = E.tolerance(r["parts"]["contacts"]["columns"][f])
                assert (rec[f] is None and dc[f][i] is None) or abs(dc[f][i] - rec[f]) <= tol + 1e-9, (b, d, f)


def test_no_credentials_and_no_vendor_name(built, tmp_path):
    for r in built.values():
        assert FAKE_SECRET[:12] not in r["html"]
        assert r["rep"]["checks"]["credential_scan"] == "clean"
    env = tmp_path / ".env"
    env.write_text(f"X_KEY={FAKE_SECRET}\n# comment\nSHORT=abc\n")
    pre = C.env_prefixes(env)
    assert set(pre) == {"X_KEY"} and pre["X_KEY"] == FAKE_SECRET[:12]
    assert C.credential_hits("page " + FAKE_SECRET[:14], pre) == ["X_KEY"]


def test_open_build_problems_detects_each_case():
    assert C.open_build_problems({"contacts": {"columns": {"gfw_vessel_id": {"t": "const", "v": None}}}})
    assert C.open_build_problems({"meta": {"note": "Powered by Global Fishing Watch."}})
    assert C.open_build_problems({"contacts": {"columns": {"research_only": {"t": "const", "v": True}}}})
    b = base64.b64encode(bytes([0, 1, 255])).decode()
    assert C.open_build_problems({"contacts": {"columns": {"research_only": {"t": "bool8", "b": b}}}})
    assert C.open_build_problems({"geo": {"layers": {"eez": {"kind": "polygon"}}}})
    assert C.open_build_problems({"geo": {"layers": {"eez_boundaries": {"kind": "line"}}}}) == []


def test_open_build_reads_nothing_under_research(data_src, tmp_path, monkeypatch):
    """Every file reader the builder and the backend use is wrapped; the open build touches no path under research/."""
    import builtins
    import io

    import geopandas
    import pyarrow.parquet as pq
    import pyogrio
    import rasterio

    seen: list[str] = []

    def wrap(mod, name):
        fn = getattr(mod, name)

        def inner(*a, **k):
            for x in list(a[:1]) + [k.get("path"), k.get("filename"), k.get("source")]:
                if isinstance(x, (str, Path)):
                    seen.append(str(x))
            return fn(*a, **k)
        monkeypatch.setattr(mod, name, inner)

    for mod, names in ((pq, ["read_table", "read_metadata", "read_schema", "ParquetFile"]),
                       (pyogrio, ["read_dataframe", "read_info", "list_layers", "read_arrow"]),
                       (rasterio, ["open"]), (geopandas, ["read_file", "read_parquet"]), (builtins, ["open"]), (io, ["open"])):
        for n in names:
            if hasattr(mod, n):
                wrap(mod, n)
    research = str((data_src / "research").resolve())
    assert (data_src / "research").is_dir() and any((data_src / "research").iterdir())
    run(data_src, tmp_path / "o", "open")
    hits = [p for p in seen if str(Path(p).resolve()).startswith(research)]
    assert seen and hits == []
    seen.clear()
    run(data_src, tmp_path / "r", "research")
    assert any(str(Path(p).resolve()).startswith(research) for p in seen)  # the wrapper does see research reads


def test_catalog_guard_refuses_research_paths_in_open(data_src):
    from scs_api.catalog import Catalog, ResearchPathError
    from scs_api.config import Settings

    cat = Catalog(Settings(build="open", data_dir=data_src))
    with pytest.raises(ResearchPathError):
        cat.guard(data_src / "research" / "gfw_vessels.parquet")
    assert not any(s.rel.startswith("research") for s in cat.specs.values())


def test_part_over_budget_fails(data_src, tmp_path):
    with pytest.raises(B.BudgetError):
        run(data_src, tmp_path, "open", budgets={"passes": 100})


def test_total_cap_applies_rules_in_order_then_fails(data_src, tmp_path):
    with pytest.raises(B.BudgetError) as ei:
        run(data_src, tmp_path, "research", cap=20_000)
    assert "after every drop rule" in str(ei.value)


def test_total_cap_rule_order(data_src, tmp_path):
    shell = tmp_path / "shell.html"
    shell.write_text(SHELL)
    opt = Options(build="open", data_dir=data_src, frontend=shell, out_dir=tmp_path / "o2", no_chips=True, cache=False,
                  env_path=tmp_path / "none.env", cap=20_000, log=lambda *a: None)
    b = Bundle(opt)
    with pytest.raises(B.BudgetError):
        b.run()
    rules = [d["rule"] for d in b.dropped if d.get("kind") == "rule" and d.get("rule")]
    assert rules == sorted(rules)
    assert 2 in rules and 4 in rules  # lights, September medium (open build)
    assert 5 not in rules and 6 not in rules  # research-only rules
    # the only live pass holds a matched contact: rule 7 never drops it, so the build fails instead
    assert 7 not in rules
    assert b.next_live_pass(Plan()) is None


def test_live_pass_drop_order():
    ps = [{"pass_id": "m_old", "start_utc": "2026-10-01", "n_matched": 3, "n_unmatched": 0},
          {"pass_id": "u_new", "start_utc": "2026-10-09", "n_matched": 0, "n_unmatched": 2},
          {"pass_id": "none_new", "start_utc": "2026-10-09", "n_matched": 0, "n_unmatched": 0},
          {"pass_id": "none_old", "start_utc": "2026-10-02", "n_matched": 0, "n_unmatched": 0}]
    # a pass with a matched contact is never dropped (its identifications are the P0 demo)
    assert B.live_pass_drop_order(ps) == ["none_old", "none_new", "u_new"]


def test_leads_trimmed_in_priority_order(built):
    ctx = built["research"]["bundle"].ctx
    leads = []
    for k in range(400):
        leads.append({"lead_id": f"L1-S1C_20260920T104816_{k:05d}", "lead_type": "L1", "primary_type": "contact",
                      "primary_id": f"S1C_20260920T104816_{k:05d}", "priority": 100 - k // 4, "state": "new", "lon": 105.0 + k / 1000,
                      "lat": 10.0, "time_utc": "2026-09-20T10:48:16Z", "next_look_utc": None, "region_box": "other",
                      "factors": [{"factor": "evidence_quality", "points": 10, "max_points": 30, "source": "cnn_v0"}],
                      "evidence": [{"type": "contact", "id": f"S1C_20260920T104816_{k:05d}", "role": "primary"}],
                      "lawful_explanations": ["vms_fleet"], "change_indicators": ["late_ais_match"], "history": [],
                      "prov": {}, "priority_model_id": "m", "calibrated": False, "research_only": True})
    row_of = {"contacts": {L["primary_id"]: i for i, L in enumerate(leads)}}
    full, info_full = P.leads_part(ctx, leads, row_of, 10_000_000)
    assert info_full["kept"] == 400 and info_full["records"] == 400
    small, info = P.leads_part(ctx, leads, row_of, E.element_bytes("leads", {**full, "records": {}}) // 2)
    assert 0 < info["kept"] < 400 and info["dropped_budget"] == 400 - info["kept"]
    pr = E.decode_part(small)["priority"]
    assert pr == sorted(pr, reverse=True) and pr[0] == 100
    assert info["kept_ids"] == [L["lead_id"] for L in leads[: info["kept"]]]


def test_identity_resolution_records_own_strings():
    fr = pd.DataFrame({"det_id": ["a", "b", "c"], "ais_status": ["matched", "matched", "unmatched"],
                       "vessel_key": ["mmsi:1", "mmsi:2", None], "nearest_vessel_key": [None, None, "mmsi:2"],
                       "mmsi": ["1", "2", None], "imo": [None] * 3, "vessel_name": ["ONE", "TWO NEW", None],
                       "call_sign": [None] * 3, "flag": [None] * 3, "ship_type": [None] * 3, "length_ais_m": [None] * 3,
                       "identity_source": ["s", "s", None], "nearest_ais_mmsi": [None, None, "3"]})
    vdf = pd.DataFrame({"vessel_key": ["mmsi:1", "mmsi:2"], "mmsi": ["1", "2"], "imo": [None, None], "name": ["ONE", "TWO"],
                        "call_sign": [None, None], "flag": [None, None], "ship_type": [None, None],
                        "length_ais_m": [None, None], "identity_source": ["s", "s"]})
    vref, nref, own, st = P.identity_resolution(fr, {"mmsi:1": 0, "mmsi:2": 1}, vdf)
    assert list(vref) == [0, 1, -1] and list(nref) == [-1, -1, 1]
    assert own == {"b": {"vessel_name": "TWO NEW"}, "c": {"nearest_ais_mmsi": "3"}}
    assert st["by_reference"] == 1 and st["own_strings"] == 1 and st["nearest_overrides"] == 1


def test_live_identification_records_always_carried(built):
    """Every matched, hand-checked or ambiguous live contact has its full record (README reading 16): the hand-check
    note, the aisstream label and the azimuth evidence reach the page whatever the record budget."""
    from synthetic import IDS

    for b, r in built.items():
        c = r["parts"]["contacts"]
        recs = c["records"]
        d = IDS["live_matched"]
        assert d in recs, b
        rec = recs[d]
        assert rec.get("review_note", "").startswith("confirmed"), (b, rec)
        assert rec.get("identity_label") == "live AIS relayed by aisstream.io; terms UNVERIFIED"
        assert "field_prov" not in rec  # contract 1.3.0 field provenance stays in the local app
        # a record's prov holds only what differs from the row's prov set (the adapter's provSetName)
        base = c["prov_sets"]["live"]
        assert all(base.get(k) != v for k, v in (rec.get("prov") or {}).items())
        stats = r["rep"]["info"]["contact_records"]
        assert stats["live_matched"] >= 1 and stats["left_out"] >= 0
        drops = [x for x in r["parts"]["meta"]["dropped"] if x.get("columns") == ["field_prov"]]
        assert drops and drops[0]["part"] == "contacts"


def test_must_records_and_minimal_own_strings(built):
    bundle = built["open"]["bundle"]
    rows = bundle.rows
    part = built["open"]["parts"]["contacts"]
    live = rows[rows["view"] == "live"]
    must = bundle.must_records(rows, part, {"X_not_live": {"vessel_name": "NEW"}})
    assert must["X_not_live"] == ("identity_or_nearest_overrides", {"vessel_name": "NEW"})  # strings only
    for d in live.loc[live["ais_status"] == "matched", "det_id"]:
        assert must[d][0] == "live_matched" and "review_note" in must[d][1]


def test_passes_part_is_compact_and_lossless_for_the_adapter(built):
    for b, r in built.items():
        feats = r["parts"]["passes"]["features"]
        assert feats
        for f in feats:
            pr = f["properties"]
            assert all(v is not None for k, v in pr.items() if k not in P.PASS_KEEP_NULL), (b, pr["pass_id"])
            assert pr.get("research_only") is not False
            assert {"pass_id", "mission", "start_utc", "status"} <= set(pr)
        live = [f["properties"] for f in feats if str(f["properties"]["pass_id"]).startswith("live_")]
        assert live and all(p.get("contacts_in_bundle") is True for p in live)
    assert P.compact_pass({"pass_id": "p", "note": None, "research_only": False, "mission": None, "field_prov": {}}) == \
        {"pass_id": "p", "mission": None}


def test_ais_only_vessels_fill_the_passes_budget(built):
    """README reading 15: the AIS-only list may be a subset; tested sea first; n_ais_only stays the pass total."""
    from synthetic import IDS, LIVE_PASS

    for b, r in built.items():
        live = next(f["properties"] for f in r["parts"]["passes"]["features"] if f["properties"]["pass_id"] == LIVE_PASS)
        assert live["n_ais_only"] == 2
        got = live["ais_only"]
        assert {v["mmsi"] for v in got} == set(IDS["ais_only"])
        assert all(set(v) <= set(P.AIS_ONLY_KEEP) for v in got)
        assert [bool(v.get("on_tested_sea")) for v in got] == sorted([bool(v.get("on_tested_sea")) for v in got], reverse=True)
        info = r["rep"]["info"]["passes"]
        assert info["ais_only_in_bundle"] == info["ais_only_total"] == 2
    # a tight budget keeps a subset and the builder lists the rest
    ctx = built["open"]["bundle"].ctx
    part, _ = P.passes_part(ctx, set(), {})
    base = E.element_bytes("passes", part)
    one = len(E.dumps(P.slim_ais_only({"mmsi": IDS["ais_only"][0], "on_tested_sea": True, "length_ais_m": 300.0}))) + 40
    part2, info2 = P.passes_part(ctx, set(), {}, budget=base + one)
    assert info2["ais_only_total"] == 2 and info2["ais_only_in_bundle"] <= 1
    assert E.element_bytes("passes", part2) <= base + one
    assert P.slim_ais_only({"mmsi": "1", "vessel_key": "mmsi:1", "identity_label": "x", "lon": None}) == {"mmsi": "1"}


def test_spot_records_put_live_matched_first(built):
    from synthetic import IDS

    for b, r in built.items():
        spot = r["rep"]["spot"]
        assert spot["live_matched"] == [IDS["live_matched"]]
        lf = spot["live_fields"][IDS["live_matched"]]
        assert lf["review_note"].startswith("confirmed") and lf["identity_label"].startswith("live AIS relayed")
        assert all(v not in (None, False) for x in spot["live_fields"].values() for v in x.values())


def test_record_cache_deps_cover_every_record_input(built):
    ctx = built["open"]["bundle"].ctx
    deps = P.record_deps(ctx)
    for k in ("live_contacts", "live_review", "live_weather", "object_context", "leads_open", "regional_cnn"):
        assert k in deps, k
    assert "ais_positions" not in deps and "chips" not in deps
    assert "ais_positions" in P.record_deps(ctx, vessels=True)
    assert not any(k.startswith("gfw") or k.startswith("leads_research") for k in deps)  # open catalog only


def test_rule_drops_carry_row_counts(data_src, tmp_path):
    """Rule 4 (open) and rule 7 entries in meta.dropped say how many contacts left the bulk columns."""
    from synthetic import IDS

    shell = tmp_path / "shell.html"
    shell.write_text(SHELL)
    opt = Options(build="open", data_dir=data_src, frontend=shell, out_dir=tmp_path / "o3", no_chips=True, cache=False,
                  env_path=tmp_path / "none.env", log=lambda *a: None, budgets={"contacts": 1})
    b = Bundle(opt)
    with pytest.raises(B.BudgetError):
        b.run()
    r4 = [d for d in b.dropped if d.get("rule") == 4]
    assert r4 and r4[0]["rows"] == int(b.rule_masks()[4].sum())
    # the only live pass holds a matched contact, so rule 7 never names it
    assert not any(d.get("rule") == 7 and d.get("pass_id") == "live_S1D_20261008T2258" for d in b.dropped)
    assert IDS["live_matched"] in set(b.cfr_all["det_id"])


# ----------------------------------------------------------------------------------------------- object context
def decode_context(part: dict, i: int) -> dict | None:
    """Python mirror of the embedded adapter's contextOf (README reading 13): row i of the part's block, or None."""
    blk = part.get("object_context")
    if not blk:
        return None
    g = E.decode_part({"type": "object_context", "n": blk["n"], "columns": blk["columns"]})
    t = g["time_utc"][i]
    if t is None:
        return None
    fields = {}
    for name, spec in blk["fields"].items():
        if name not in g:
            continue
        fields[name] = {"value": g[name][i], "unit": spec.get("unit"),
                        "time": g[spec["time_col"]][i] if spec.get("time_col") in g else spec.get("time"),
                        "src": g[spec["src_col"]][i] if spec.get("src_col") in g else spec.get("src")}
    return {"time_utc": t, "cell_id": g["cell_id"][i] if "cell_id" in g else None,
            "region": g["region"][i] if "region" in g else None, "fields": fields, "caveat": blk["caveat"]}


def assert_context_equal(page: dict | None, api: dict | None, what: str, cell: bool = True):
    if api is None:
        assert page is None, what
        return
    assert page is not None, f"{what}: the API has context, the page none"
    for k in ("time_utc", "region") + (("cell_id",) if cell else ()):
        assert page[k] == api[k], (what, k)
    assert page["caveat"].startswith(api["caveat"]), what
    for f, pv in page["fields"].items():
        av = api["fields"][f]
        tol = 0.5 / P.CONTEXT_PAGE_SCALE[f] if f in P.CONTEXT_PAGE_SCALE else 0
        if isinstance(av["value"], bool) or av["value"] is None:
            assert pv["value"] == av["value"], (what, f)
        else:
            assert abs(pv["value"] - av["value"]) <= tol + 1e-9, (what, f, pv["value"], av["value"])
        for k in ("unit", "time", "src"):
            assert pv[k] == av[k], (what, f, k, pv[k], av[k])


def test_object_context_block_equals_the_api(built):
    """Reading 13: every contact and light row with a context row in the table carries it in the page, equal to the
    API's object_context; rows without one decode to null; records never repeat it."""
    from synthetic import CONTEXT_ROWS

    for b, r in built.items():
        bundle = r["bundle"]
        st = bundle.store
        c = r["parts"]["contacts"]
        dc = E.decode_part(c)
        rows = bundle.rows
        api = st.object_contexts("contact", rows["det_id"].tolist(), rows["_source"].tolist())
        assert sum(x is not None for x in api) >= 2, b  # the synthetic table holds September, structure and Ca Mau rows
        assert c["object_context"]["n"] == c["n"] and len(c["object_context"]["fields"]) == 16
        for i, d in enumerate(dc["det_id"]):
            assert_context_equal(decode_context(c, i), api[i], f"{b} contact {d}")
        assert not any("object_context" in rec for rec in c["records"].values())
        lp = r["parts"]["lights"]
        dl = E.decode_part(lp)
        lapi = st.object_contexts("light", dl["light_id"])
        assert any(x is not None for x in lapi)
        for i, lid in enumerate(dl["light_id"]):
            assert_context_equal(decode_context(lp, i), lapi[i], f"{b} light {lid}")
        info = r["parts"]["meta"]["object_context"]
        assert info["contacts"]["with_context"] == sum(x is not None for x in api)
        assert info["contacts"]["left_out"] == [] and info["lights"]["left_out"] == []
        assert not any(d.get("kind") == "object_context" for d in r["parts"]["meta"]["dropped"])
        assert r["parts"]["meta"]["parts_object_context"]["contacts"] == P.context_bytes(c["object_context"])
        assert {k for k, _ in CONTEXT_ROWS} and "This page carries" not in c["object_context"]["caveat"]
        # the spot records hold the API context of the sampled contacts and lights for check_bundle.mjs
        spot = r["rep"]["spot"]
        assert set(spot["object_context"]["contacts"]) == set(spot["det_ids"])
        assert spot["object_context"]["lights"] and spot["context_fields"]["contacts"] == list(c["object_context"]["fields"])


def _no_context_bytes(r: dict) -> int:
    meta = r["parts"]["meta"]
    return r["rep"]["bytes"] - sum(meta["parts_object_context"].values())


def test_object_context_fields_fill_the_room_and_left_out_are_listed(built, data_src, tmp_path):
    """Under the cap, every row keeps its context and fields join in priority order; the fields left out are in
    meta.dropped with the rows that have context, and the block's caveat says the page carries a subset."""
    from scs_bundle.bundle import CONTEXT_MARGIN

    r = built["open"]
    bundle = r["bundle"]
    fc, _ = P.context_block(bundle.ctx, "contact", bundle.rows["det_id"], bundle.rows["_source"])
    fl, _ = P.context_block(bundle.ctx, "light", bundle.light_ids)
    base = P.context_bytes(P.context_subset(fc, [])) + P.context_bytes(P.context_subset(fl, []))
    full = P.context_bytes(P.context_subset(fc, P.CONTEXT_PRIORITY)) + P.context_bytes(P.context_subset(fl, P.CONTEXT_PRIORITY))
    cap = _no_context_bytes(r) + CONTEXT_MARGIN + base + (full - base) // 2
    rep, b2 = run(data_src, tmp_path / "half", "open", cap=cap)
    assert rep["bytes"] <= cap
    parts = parts_of(Path(rep["out"]).read_text(encoding="utf-8"))
    assert not any(d.get("kind") == "rule" for d in parts["meta"]["dropped"])  # context never makes a part drop
    kept = list(parts["contacts"]["object_context"]["fields"])
    assert 0 < len(kept) < 16
    assert kept == [f for f in P.CONTEXT_PRIORITY if f in kept]
    assert list(parts["lights"]["object_context"]["fields"]) == kept
    assert f"This page carries {len(kept)} of the 16 context fields" in parts["contacts"]["object_context"]["caveat"]
    drops = {d["part"]: d for d in parts["meta"]["dropped"] if d.get("kind") == "object_context"}
    assert set(drops) == {"contacts", "lights"}
    info = parts["meta"]["object_context"]
    for k in ("contacts", "lights"):
        assert drops[k]["rows"] == info[k]["with_context"] > 0
        assert set(drops[k]["columns"]) >= set(info[k]["left_out"]) and info[k]["left_out"]
        assert not set(drops[k]["columns"]) & set(kept)
    # every row with context still decodes to its context (the subset of fields), never to null
    dc = E.decode_part(parts["contacts"])
    api = b2.store.object_contexts("contact", list(dc["det_id"]), b2.rows["_source"].tolist())
    for i, d in enumerate(dc["det_id"]):
        assert_context_equal(decode_context(parts["contacts"], i), api[i], d, cell="cell_id" in kept)


def test_object_context_without_room_is_listed_whole(built, data_src, tmp_path):
    r = built["research"]
    cap = _no_context_bytes(r) + 3000  # under the margin: no block fits
    rep, _ = run(data_src, tmp_path / "none", "research", cap=cap)
    parts = parts_of(Path(rep["out"]).read_text(encoding="utf-8"))
    assert "object_context" not in parts["contacts"] and "object_context" not in parts["lights"]
    drops = {d["part"]: d for d in parts["meta"]["dropped"] if d.get("kind") == "object_context"}
    assert set(drops) == {"contacts", "lights"}
    assert len(drops["contacts"]["columns"]) == 16 and drops["contacts"]["rows"] > 0
    assert "no room under the cap" in drops["lights"]["reason"]


def test_context_block_rules():
    """const where every row with context agrees (rows without context are never read), page scales, presence bool8."""
    class _Ctx:  # the store's ContextData through P.context_block needs only data["context"]
        pass

    import numpy as np

    from scs_api.loaders.context import ContextData

    objs = pd.DataFrame({"object_type": ["radar", "radar", "viirs"], "object_id": ["a", "b", "L"],
                         "time_utc": pd.to_datetime(["2026-09-20T10:48:16Z"] * 2 + ["2026-09-20T18:30:00Z"], utc=True),
                         "cell_id": ["r1c1", "r1c2", "r9c9"], "region": pd.Categorical(["other", "other", "other"]),
                         "depth_m": [60.34, np.nan, 5.0], "dist_port_km": [73.46, 800.123, 1.0],
                         "ship_presence_all": pd.array([True, None, False], dtype="boolean"),
                         "sst_c": [28.4752, 27.0, 29.0], "sst_time": ["2026-09-20T09:00:00Z"] * 3, "sst_source": ["mur"] * 3,
                         "caveat": ["x"] * 3})
    cd = ContextData(objs, "x", None, None, None)
    ctx = _Ctx()
    ctx.store = type("S", (), {"data": {"context": cd}})()
    blk, info = P.context_block(ctx, "contact", ["a", "zz", "b"], ["regional"] * 3)
    assert info == {"rows": 3, "with_context": 2, "caveats": 1}
    g = E.decode_part({"type": "object_context", "n": 3, "columns": blk["columns"]})
    assert g["time_utc"] == ["2026-09-20T10:48:16Z", None, "2026-09-20T10:48:16Z"]
    assert blk["columns"]["region"]["t"] == "const" and blk["columns"]["sst_src"]["t"] == "const"
    assert blk["columns"]["dist_port_km"]["t"] == "u16" and blk["columns"]["dist_port_km"]["s"] == 10  # not u32 at 0.01 km
    assert g["depth_m"][0] == pytest.approx(60.3) and g["depth_m"][2] is None
    assert g["ship_presence_all"] == [True, None, None] and blk["columns"]["ship_presence_all"]["t"] == "bool8"
    assert blk["fields"]["sst_c"] == {"unit": "degC", "src_col": "sst_src", "time_col": "sst_time"}
    assert blk["fields"]["depth_m"] == {"unit": "m", "src": "gebco_2026"}
    sub = P.context_subset(blk, ["sst_c", "cell_id"])
    assert set(sub["columns"]) == {"time_utc", "region", "sst_c", "sst_src", "sst_time", "cell_id"}
    assert list(sub["fields"]) == ["sst_c"] and "carries 1 of the 16" in sub["caveat"] and "_aux" not in sub
    lb, linfo = P.context_block(ctx, "light", ["L", "M"])
    assert linfo["with_context"] == 1
    assert P.context_block(ctx, "contact", ["q"], ["regional"])[0] is None


def test_unrecorded_fields_are_counted():
    rows = pd.DataFrame({"det_id": ["a", "b", "c"], "view": ["regional"] * 3, "confidence": ["high"] * 3,
                         "research_only": [False] * 3, "lon": [1.0, 2.0, 3.0], "s2_item": ["S2A_x", None, "S2B_y"],
                         "scene_id": ["s1", "s2", "s3"],  # an extension column: listed by its own entry, not here
                         "extra": [json.dumps({"source_caveat": "c", "himawari_start": "t"}), None, json.dumps({"source_caveat": "c"})],
                         "prov": [json.dumps({"lon": "det_regional"}), json.dumps({"lon": "other"}), None],
                         "object_context": [{"time_utc": "t"}, None, None], "field_prov": [{"lon": {}}, None, None]})
    n, counts = P.unrecorded_fields(rows, {"det_id", "lon", "view", "confidence", "research_only"}, {"c": {}},
                                    lambda i: {"lon": "det_regional"})
    assert n == 2
    assert counts == {"extra.himawari_start": 1, "extra.source_caveat": 1, "prov": 1, "s2_item": 1}
    # what the page holds another way is not counted: lead_ids of a lead primary (leads part), the aisstream label of
    # an aisstream row (identityLabel), match_ambiguous false (reading 16)
    from scs_api.config import AISSTREAM_LABEL

    live = pd.DataFrame({"det_id": ["p", "q", "r"], "ais_source": ["aisstream", "aisstream", None],
                         "lead_ids": [json.dumps(["L1-p"]), json.dumps(["L1-x"]), None],
                         "identity_label": [AISSTREAM_LABEL, AISSTREAM_LABEL, "other label"],
                         "match_ambiguous": [False, True, None]})
    n, counts = P.unrecorded_fields(live, {"det_id", "ais_source"}, {}, lambda i: {}, lead_primary={"p", "q"})
    assert n == 3 and counts == {"identity_label": 1, "lead_ids": 1, "match_ambiguous": 1}
    near = pd.DataFrame({"det_id": ["a", "b"], "nearest_ais_name": ["ONE", "TWO"]})
    n, counts = P.unrecorded_fields(near, {"det_id"}, {}, lambda i: {}, derived={"nearest_ais_name": ["ONE", "OTHER"]})
    assert counts == {"nearest_ais_name": 1}  # the adapter shows ONE from the nearest_ref row; TWO differs
    rec = P.contact_record({"det_id": "z", "match_ambiguous": False, "review_note": "x"}, {"det_id"}, {})
    assert rec == {"review_note": "x"}


def test_unrecorded_fields_entry_in_meta(built):
    for b, r in built.items():
        info = r["parts"]["meta"]["contacts_without_record"]
        c = r["parts"]["contacts"]
        assert info["rows"] == c["n"] - len([d for d in c["records"] if d in set(E.decode_part(c)["det_id"])])
        drops = [d for d in r["parts"]["meta"]["dropped"] if d.get("kind") == "fields" and isinstance(d.get("columns"), dict)]
        if info["fields"]:
            assert drops and drops[0]["columns"] == info["fields"] and drops[0]["rows"] == info["rows"]
