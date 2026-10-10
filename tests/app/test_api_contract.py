"""Contract tests (a) to (i) of app/CONTRACT.md section 4, offline on synthetic files."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from synthetic import IDS, LIVE_PASS

REPO = Path(__file__).resolve().parents[2]


def board_d1() -> list[str]:
    """The D1 column list of docs/PROJECT_BOARD.md, in table order (first cell of each row of the D1 table)."""
    text = (REPO / "docs" / "PROJECT_BOARD.md").read_text(encoding="utf-8")
    sec = text.split("### D1.", 1)[1].split("###", 1)[0]
    names = []
    for line in sec.splitlines():
        if not line.startswith("| `"):
            continue
        first = line.split("|")[1]
        names += re.findall(r"`([a-z0-9_]+)`", first)
    return names


def endpoints(build: str) -> list[str]:
    """Every section 5 GET endpoint, with fixture ids (chip and raster images are checked separately)."""
    a = "/api/v1"
    eps = [f"{a}/meta", *[f"{a}/layers/{n}.cols" for n in ("contacts", "structures", "lights", "sites", "vessels")],
           f"{a}/contacts", f"{a}/contacts?view=camau", f"{a}/contacts?limit=1000&confidence=high,medium,fixed,low&view=camau,live,regional",
           f"{a}/contacts/{IDS['live_matched']}", f"{a}/contacts/{IDS['camau'][0]}", f"{a}/contacts/{IDS['reg'][0]}",
           f"{a}/contacts/{IDS['struct'][0]}", f"{a}/vessels", f"{a}/vessels/mmsi:{IDS['vessel_mmsi']}",
           f"{a}/vessels/mmsi:{IDS['vessel_mmsi']}/track", f"{a}/lights", f"{a}/lights/{IDS['light'][0]}", f"{a}/sites/{IDS['site']}",
           f"{a}/events", f"{a}/leads", f"{a}/leads?state=new,reviewing,closed_explained,closed_unexplained,closed_false_alarm",
           f"{a}/leads/{IDS['lead_l7']}", f"{a}/passes", f"{a}/passes/{LIVE_PASS}", f"{a}/passes/{IDS['plan_up']}",
           f"{a}/cells/at?lon=105.1&lat=8.4", f"{a}/cells/{IDS['cell']}", f"{a}/cells/{IDS['cell']}?night=2026-09-10",
           f"{a}/rasters", f"{a}/rasters/depth_m/value?lon=104.2&lat=9.9",
           *[f"{a}/geo/{n}.geojson" for n in ("land", "aoi", "reporting_boxes", "eez", "eez_boundaries", "depth_contours", "ports", "fronts", "footprints")],
           f"{a}/search?q=TEST", f"{a}/search?q={IDS['live_matched']}", f"{a}/search?q=10.25, 107.5", f"{a}/search?q=L7-r62",
           f"{a}/timeline", f"{a}/timeline?bin=day", f"{a}/timeline?bin=hour"]
    if build == "open":
        eps += [f"{a}/leads/{IDS['lead_l1']}"]
    else:
        eps += [f"{a}/leads/{IDS['lead_r1']}", f"{a}/events/{IDS['gap']}", f"{a}/events/{IDS['enc']}", f"{a}/events?det_id={IDS['reg'][1]}",
                f"{a}/vessels/gfw:{IDS['gfw_vessel']}", f"{a}/vessels/gfw:{IDS['gfw_vessel']}/track", f"{a}/vessels/gfw:{IDS['gfw_stub']}",
                f"{a}/contacts/{IDS['reg'][1]}"]
    return eps


def keys(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, path
            yield from keys(v, path + "." + k)
    elif isinstance(obj, list):
        for v in obj:
            yield from keys(v, path + "[]")


def records(body) -> list[dict]:
    """The records of a response: item, items, features (properties), timeline lists."""
    out = []
    if "item" in body and isinstance(body["item"], dict):
        out.append(body["item"])
        for k in ("passes", "contactsByPass", "events", "upcoming"):
            if isinstance(body["item"].get(k), list):
                out += body["item"][k]
    out += body.get("items") or []
    out += [f["properties"] for f in body.get("features") or []]
    return out


@pytest.fixture(scope="module")
def crawl(shared_clients):
    """{build: [(path, status, body)]} for every endpoint."""
    out = {}
    for build, c in shared_clients.items():
        rows = []
        for p in endpoints(build):
            r = c.get(p)
            rows.append((p, r.status_code, r.json()))
        out[build] = rows
    return out


# (a) ------------------------------------------------------------------------------------------------------------------
def test_a_contact_fields_equal_d1():
    from darkvessel.ais.gfw_identity import D1_COLUMNS as D1_GFW
    from darkvessel.live.schema import D1_COLUMNS as D1_LIVE
    from scs_api.models import build_models, field_names

    d1 = board_d1()
    assert len(d1) == 32 and d1[0] == "det_id" and d1[-1] == "caveat"
    research = field_names(build_models("research")["contact"])
    open_ = field_names(build_models("open")["contact"])
    assert research[:32] == d1 == list(D1_GFW)
    assert open_[:31] == [c for c in d1 if c != "gfw_vessel_id"] == list(D1_LIVE)
    for b in ("open", "research"):
        summ = field_names(build_models(b)["contact_summary"])
        assert summ[: 32 if b == "research" else 31] == (research[:32] if b == "research" else open_[:31])


def test_a_api_contact_record_order(shared_clients):
    from darkvessel.live.schema import D1_COLUMNS as D1_LIVE

    rec = shared_clients["open"].get(f"/api/v1/contacts/{IDS['live_matched']}").json()["item"]
    assert list(rec)[:31] == list(D1_LIVE)
    assert rec["mmsi"] == IDS["vessel_mmsi"] and rec["vessel_key"] == f"mmsi:{IDS['vessel_mmsi']}"


# (b) ------------------------------------------------------------------------------------------------------------------
def test_b_open_catalog_refuses_research_paths(data_dir):
    from scs_api.catalog import Catalog, FileSpec, ResearchPathError
    from scs_api.config import Settings

    cat = Catalog(Settings(build="open", data_dir=data_dir))
    for p in ("research/regional_identity.parquet", data_dir / "research" / "gfw_vessels.parquet", "live/../research/x.parquet",
              "research", data_dir / "research"):
        with pytest.raises(ResearchPathError):
            cat.guard(p)
    with pytest.raises(ResearchPathError):
        cat.read_parquet("x", path=data_dir / "research" / "regional_identity.parquet")
    with pytest.raises(ResearchPathError):
        Catalog(Settings(build="open", data_dir=data_dir), [FileSpec("bad", "research/regional_identity.parquet", kind="parquet")])
    assert not any(k for k in cat.specs if "research" in cat.specs[k].rel)
    rcat = Catalog(Settings(build="research", data_dir=data_dir))
    assert rcat.guard("research/regional_identity.parquet").name == "regional_identity.parquet"


def test_b_open_glob_listing_guards_symlinks_into_research(data_dir):
    """A glob or dir spec lists plain files without resolving each one, but a symlink (or a nested pattern) into
    data/research/ is still refused."""
    from scs_api.catalog import Catalog, ResearchPathError
    from scs_api.config import Settings

    cat = Catalog(Settings(build="open", data_dir=data_dir))
    assert [p.name for p in cat.paths("chips")] == [f"{IDS['chip']}.webp"]
    (data_dir / "research" / "x.webp").write_bytes(b"RIFF")
    (data_dir / "cache" / "chips" / "S1D_20261008T230108_99999.webp").symlink_to(data_dir / "research" / "x.webp")
    with pytest.raises(ResearchPathError):
        cat.paths("chips")
    day = data_dir / "cache" / "ais" / "aisstream" / "positions" / "20261009"
    day.symlink_to(data_dir / "research", target_is_directory=True)
    (data_dir / "research" / "05.parquet").write_bytes(b"PAR1")
    with pytest.raises(ResearchPathError):
        cat.paths("ais_positions")


def test_b_open_build_never_opens_research(data_dir, monkeypatch):
    """Run the open app over every endpoint with research files present; no path under data/research/ is opened."""
    import builtins

    import pyarrow.parquet as pq
    import pyogrio
    import rasterio

    from conftest import make_client

    research = str((data_dir / "research").resolve())
    seen = []

    def spy(fn):
        def wrapper(path, *a, **k):
            seen.append(str(path))
            return fn(path, *a, **k)
        return wrapper

    monkeypatch.setattr(builtins, "open", spy(builtins.open))
    for mod, names in ((pq, ("read_table", "read_metadata", "read_schema")), (pyogrio, ("read_dataframe", "read_info", "list_layers")),
                       (rasterio, ("open",))):
        for n in names:
            monkeypatch.setattr(mod, n, spy(getattr(mod, n)))
    c = make_client(data_dir, "open")
    for p in endpoints("open"):
        assert c.get(p).status_code in (200, 404), p
    c.get("/api/v1/export/geojson?type=contacts")
    c.get(f"/api/v1/contacts/{IDS['chip']}/chip.webp")
    store = c.app.state.store
    opened = seen + store.cat.opened
    assert opened, "the spy saw no file reads"
    assert not [p for p in opened if str(Path(p).resolve()).startswith(research)]


def test_b_open_responses_have_no_gfw_field(crawl, shared_clients):
    for path, status, body in crawl["open"]:
        bad = [(k, where) for k, where in keys(body) if k.lower().startswith("gfw")]
        assert not bad, (path, bad[:5])
        text = json.dumps(body)
        assert "Global Fishing Watch" not in text and '"gfw:' not in text, path
    spec = shared_clients["open"].get("/openapi.json").text
    assert "gfw_" not in spec and "Global Fishing Watch" not in spec
    rspec = shared_clients["research"].get("/openapi.json").text
    assert "gfw_vessel_id" in rspec  # the research model does carry it


# (c) ------------------------------------------------------------------------------------------------------------------
def test_c_every_record_has_the_caveat(crawl):
    from scs_api.config import PRODUCT_CAVEAT

    for build, rows in crawl.items():
        for path, status, body in rows:
            assert status == 200, (build, path, body)
            assert body["caveat"] == PRODUCT_CAVEAT, path
            recs = records(body)
            if "columns" in body:  # columnar layer: the caveat is a const column
                assert body["columns"]["caveat"]["t"] == "const" and body["columns"]["caveat"]["v"].startswith(PRODUCT_CAVEAT), path
                continue
            if path.endswith("/meta") or "/search?" in path and not recs:
                continue
            if path.endswith("/track"):  # one Track record: caveat on the collection, gap note on every gap
                assert body["caveat"].startswith(PRODUCT_CAVEAT) and all(g["note"] for g in body["gaps"]), path
                continue
            assert recs or body.get("total") == 0 or "features" in body, path
            for r in recs:
                assert isinstance(r.get("caveat"), str) and r["caveat"].startswith(PRODUCT_CAVEAT), (build, path, r.get("caveat"))
                if build == "research":
                    assert "Research build, noncommercial, CC BY-NC 4.0" in r["caveat"], path


def test_c_product_caveat_equals_config():
    import darkvessel.config as cfg

    from scs_api import config

    assert config.PRODUCT_CAVEAT == config._PRODUCT_CAVEAT_LOCAL or config.PRODUCT_CAVEAT == getattr(cfg, "PRODUCT_CAVEAT", None)
    if hasattr(cfg, "PRODUCT_CAVEAT"):
        assert config.PRODUCT_CAVEAT == cfg.PRODUCT_CAVEAT == config._PRODUCT_CAVEAT_LOCAL
    contract = (REPO / "app" / "CONTRACT.md").read_text(encoding="utf-8")
    assert f'`PRODUCT_CAVEAT` = "{config.PRODUCT_CAVEAT}"' in contract


def test_c_source_caveat_kept_in_extra(shared_clients):
    from darkvessel.config import DARK_CAVEAT

    rec = shared_clients["open"].get(f"/api/v1/contacts/{IDS['live_matched']}").json()["item"]
    assert rec["extra"]["source_caveat"] == DARK_CAVEAT


# (d) ------------------------------------------------------------------------------------------------------------------
def test_d_ais_status_values(crawl, shared_clients):
    allowed = {"matched", "unmatched", "no_coverage", "not_checked"}
    for build, rows in crawl.items():
        for path, status, body in rows:
            for r in records(body):
                if "ais_status" in r and r["ais_status"] is not None:
                    assert r["ais_status"] in allowed, (path, r["ais_status"])
    cm = shared_clients["open"].get(f"/api/v1/contacts/{IDS['camau'][1]}").json()["item"]
    assert cm["ais_status"] == "not_checked" and cm["extra"]["source_ais_status"].startswith("not_checked:")
    r = shared_clients["open"].get("/api/v1/contacts?ais_status=dark")
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_ais_status"


# (e) ------------------------------------------------------------------------------------------------------------------
def test_e_decision_appends_one_line_and_needs_reason(open_client, data_dir):
    log = data_dir / "labels" / "lead_decisions.jsonl"
    assert not log.exists()
    lid = IDS["lead_l1"]
    r = open_client.post(f"/api/v1/leads/{lid}/decision", json={"to_state": "closed_explained", "user": "owner"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "reason_required"
    assert not log.exists()
    r = open_client.post(f"/api/v1/leads/{lid}/decision", json={"to_state": "closed_explained", "reason": "VMS fleet", "user": "owner"})
    assert r.status_code == 200, r.text
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    d = json.loads(lines[0])
    assert set(d) == {"lead_id", "time_utc", "user", "from_state", "to_state", "reason", "note", "build", "app_version"}
    assert d["from_state"] == "new" and d["to_state"] == "closed_explained" and d["reason"] == "VMS fleet" and d["build"] == "open"
    item = r.json()["item"]
    assert item["state"] == "closed_explained" and item["reason"] == "VMS fleet" and len(item["history"]) == 1


# (f) ------------------------------------------------------------------------------------------------------------------
def test_f_bundle_drop_rules():
    pytest.skip("contract test (f) belongs to the single-file bundle builder (app/build/, round 3); the backend's "
                "columnar encoder it will reuse is tested in test_api_behaviour.py::test_columnar_round_trip")


# (g) ------------------------------------------------------------------------------------------------------------------
def test_g_open_meta_has_no_research_sources(shared_clients):
    m = shared_clients["open"].get("/api/v1/meta").json()["item"]
    assert m["sources"] and not [s for s in m["sources"] if s["research_only"]]
    assert not [s for s in m["sources"] if s["key"].startswith("gfw")]
    assert not [f for f in m["files"] if "research/" in f["path"]]
    rm = shared_clients["research"].get("/api/v1/meta").json()["item"]
    assert {"gfw_4wings", "gfw_vessels", "gfw_events"} <= {s["key"] for s in rm["sources"]}


# (h) ------------------------------------------------------------------------------------------------------------------
def test_h_cells_at_route_order(shared_clients):
    c = shared_clients["open"]
    r = c.get("/api/v1/cells/at?lon=105.1&lat=8.4")
    assert r.status_code == 200, r.text
    assert r.json()["item"]["cell_id"] == IDS["cell"]
    paths = list(c.get("/openapi.json").json()["paths"])  # in route declaration order
    assert paths.index("/api/v1/cells/at") < paths.index("/api/v1/cells/{cell_id}")
    assert c.get("/api/v1/cells/at").status_code == 422  # lon and lat required, never read as a cell id


# (i) ------------------------------------------------------------------------------------------------------------------
def test_i_marineregions_only_inside_eez(crawl):
    for build, rows in crawl.items():
        for path, status, body in rows:
            if "/cells/" not in path:
                continue
            cell = body["item"]
            assert not [k for k in cell if k.startswith("marineregions_")], path
            assert not [k for k in cell.get("extra", {}) if k.startswith("marineregions_")], path
            assert cell["eez"]["marineregions_mrgid"] == 8484 and cell["eez"]["heading"] == "As published by Marine Regions"
            assert "takes no position" in cell["eez"]["statement"]
