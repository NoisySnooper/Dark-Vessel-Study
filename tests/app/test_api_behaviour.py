"""Backend behaviour beyond the lettered contract tests: envelope, models, paging, reload, missing files, decision log,
labels, chips, exports, docs off, static frontend, tracks, search, passes, rasters. Offline, synthetic files."""

from __future__ import annotations

import csv
import io
import json
import os
import time
from pathlib import Path

import pytest

from conftest import make_client
from synthetic import IDS, LIVE_PASS

A = "/api/v1"
ENVELOPE = {"contract_version", "build", "build_label", "caveat", "generated_utc"}


def test_envelope_shape(shared_clients):
    for build, c in shared_clients.items():
        lst = c.get(f"{A}/contacts?limit=2").json()
        assert ENVELOPE <= set(lst) and {"items", "total", "limit", "offset"} <= set(lst)
        assert lst["contract_version"] == "1.2.0" and lst["build"] == build
        one = c.get(f"{A}/contacts/{IDS['live_matched']}").json()
        assert ENVELOPE <= set(one) and "item" in one and "items" not in one
        err = c.get(f"{A}/contacts/NOPE_1").json()
        assert err["error"]["code"] == "not_found" and err["caveat"] and err["build"] == build
        if build == "research":
            assert lst["research_label"] == "Research build, noncommercial, CC BY-NC 4.0"
            assert lst["attribution"] == "Powered by Global Fishing Watch."
        else:
            assert "research_label" not in lst and "attribution" not in lst


def test_records_validate_against_models(shared_clients):
    from scs_api.models import build_models

    for build, c in shared_clients.items():
        M = build_models(build)
        checks = [(f"{A}/contacts?limit=1000&view=camau,live,regional&confidence=high,medium,fixed,low", "contact_summary", True),
                  (f"{A}/contacts/{IDS['live_matched']}", "contact", False), (f"{A}/contacts/{IDS['camau'][0]}", "contact", False),
                  (f"{A}/vessels", "vessel_summary", True), (f"{A}/vessels/mmsi:{IDS['vessel_mmsi']}", "vessel", False),
                  (f"{A}/lights", "light_summary", True), (f"{A}/lights/{IDS['light'][0]}", "light", False),
                  (f"{A}/sites/{IDS['site']}", "site", False), (f"{A}/events", "event", True),
                  (f"{A}/leads?state=new,reviewing,closed_explained", "lead", True), (f"{A}/leads/{IDS['lead_l7']}", "lead", False),
                  (f"{A}/passes", "pass", True), (f"{A}/passes/{LIVE_PASS}", "pass", False), (f"{A}/cells/{IDS['cell']}", "cell", False)]
        if build == "research":
            checks += [(f"{A}/contacts/{IDS['reg'][0]}", "contact", False), (f"{A}/events/{IDS['gap']}", "event", False),
                       (f"{A}/vessels/gfw:{IDS['gfw_stub']}", "vessel", False), (f"{A}/leads/{IDS['lead_r1']}", "lead", False)]
        for path, model, many in checks:
            body = c.get(path).json()
            recs = body["items"] if many else [body["item"]]
            for r in recs:
                M[model].model_validate(r)
                assert list(r)[: len(M[model].model_fields)] == [f.alias or n for n, f in M[model].model_fields.items()][: len(r)]


def test_paging(shared_clients):
    c = shared_clients["open"]
    allp = c.get(f"{A}/contacts?limit=1000").json()
    a = c.get(f"{A}/contacts?limit=2&offset=0").json()
    b = c.get(f"{A}/contacts?limit=2&offset=2").json()
    assert a["total"] == b["total"] == allp["total"] >= 4
    assert len(a["items"]) == 2 and a["limit"] == 2 and b["offset"] == 2
    ids = [r["det_id"] for r in allp["items"]]
    assert [r["det_id"] for r in a["items"] + b["items"]] == ids[:4]
    times = [r["acq_utc"] for r in allp["items"]]
    assert times == sorted(times, reverse=True)  # default sort -acq_utc
    r = c.get(f"{A}/contacts?limit=5000")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = c.get(f"{A}/contacts?sort=colour")
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_sort"
    asc = c.get(f"{A}/contacts?sort=length_est_m&limit=1000").json()["items"]
    lens = [x["length_est_m"] for x in asc if x["length_est_m"] is not None]
    assert lens == sorted(lens)


def test_filters_and_low_objects(shared_clients):
    c = shared_clients["open"]
    default = c.get(f"{A}/contacts?limit=1000").json()["items"]
    assert not [r for r in default if r["confidence"] == "low"]  # contract 4.4
    cam = c.get(f"{A}/contacts?view=camau").json()["items"]
    assert {r["confidence"] for r in cam} == {"low", "medium", "high"} and all(r["view"] == "camau" for r in cam)
    m = c.get(f"{A}/contacts?ais_status=matched").json()["items"]
    assert [r["det_id"] for r in m] == [IDS["live_matched"]]
    assert c.get(f"{A}/contacts?mmsi={IDS['vessel_mmsi']}").json()["total"] == 1
    assert c.get(f"{A}/contacts?bbox=101,12,102,13").json()["total"] >= 2
    assert c.get(f"{A}/contacts?t0=2026-10-01T00:00:00Z").json()["total"] == 4
    assert c.get(f"{A}/contacts?cnn_min=0.85").json()["total"] >= 1
    assert c.get(f"{A}/contacts?bbox=1,2,3").status_code == 422
    reg = c.get(f"{A}/contacts/{IDS['reg'][0]}").json()["item"]
    assert reg["ais_status"] == "not_checked" and reg["cnn_score"] is not None and reg["cnn_model_id"] == "verifier_v0_356af0ca"
    assert reg["pass_id"] == "S1C_20260920T1048" and reg["scene_id"].startswith("S1C_IW_GRDH") and reg["wind_ms"] == 5.0
    assert reg["optical_object"] is True and reg["cell_id"] == "r24c39"
    assert c.get(f"{A}/contacts/{IDS['live_unmatched']}").json()["item"]["lead_ids"] == [IDS["lead_l1"]]


def test_cnn_join_reads_only_loaded_contacts(data_dir):
    """regional_cnn.parquet also scores the low objects of the September run; the loader converts only the rows of the
    contacts it loads, and the joined values are unchanged."""
    import pandas as pd

    from scs_api.catalog import Catalog
    from scs_api.config import Settings
    from scs_api.loaders.contacts import read_cnn

    p = data_dir / "ml" / "regional_cnn.parquet"
    df = pd.read_parquet(p)
    low = df.iloc[[0, 1]].assign(det_id=["S1C_20260920T104816_90001", "S1C_20260920T104816_90002"], confidence="low",
                                 cnn_score=0.01)
    pd.concat([df, low], ignore_index=True).to_parquet(p, index=False)
    cat = Catalog(Settings(build="open", data_dir=data_dir))
    got = read_cnn(cat, [pd.Series(IDS["reg"][:2]), pd.Series([IDS["struct"][0], "not_a_contact"])])
    assert sorted(got.index) == sorted(IDS["reg"][:2] + [IDS["struct"][0]]) and "det_id" not in got.columns
    assert got.loc[IDS["reg"][0], "cnn_score"] == pytest.approx(float(df.set_index("det_id").loc[IDS["reg"][0], "cnn_score"]))
    assert read_cnn(cat, []).empty
    c = make_client(data_dir, "open")
    rec = c.get(f"{A}/contacts/{IDS['reg'][0]}").json()["item"]
    assert rec["cnn_score"] == pytest.approx(float(df.set_index("det_id").loc[IDS["reg"][0], "cnn_score"]))
    assert c.get(f"{A}/contacts/S1C_20260920T104816_90001").status_code == 404


def test_research_identity_and_vessels(shared_clients):
    c = shared_clients["research"]
    rec = c.get(f"{A}/contacts/{IDS['reg'][0]}").json()["item"]
    assert rec["ais_status"] == "matched" and rec["ais_source"] == "gfw" and rec["vessel_key"] == f"gfw:{IDS['gfw_vessel']}"
    assert rec["research_only"] is True and rec["prov"]["vessel_name"] == "gfw_vessels" and rec["inc_angle_deg"] == 31.2
    filled = c.get(f"{A}/contacts/{IDS['reg'][3]}").json()["item"]
    assert filled["cnn_score"] is not None  # the backend's join fills a null research score from regional_cnn.parquet
    assert filled["nearest_vessel_key"] == f"gfw:{IDS['gfw_stub']}"
    stub = c.get(f"{A}/vessels/gfw:{IDS['gfw_stub']}").json()["item"]
    assert stub["stub"] is True and stub["name"] == "NEAR ONE"
    gv = c.get(f"{A}/vessels/gfw:{IDS['gfw_vessel']}").json()["item"]
    assert IDS["reg"][0] in gv["contacts_matched"] and gv["extra"]["aisstream_vessel_key"] == f"mmsi:{IDS['vessel_mmsi']}"
    assert "Global Fishing Watch" in gv["identity_note"]
    ev = c.get(f"{A}/events?det_id={IDS['reg'][1]}").json()["items"]
    assert [e["event_id"] for e in ev] == [IDS["enc"]] and ev[0]["code"] == "E10"
    assert "look identical" in ev[0]["caveat"] and "eez" not in ev[0]["extra"]
    gap = c.get(f"{A}/events/{IDS['gap']}").json()["item"]
    assert gap["code"] == "E8" and gap["grade"] == "extended" and gap["geometry"]["type"] == "LineString"
    assert "An AIS gap is not proof of intent." in gap["caveat"] and gap["params"]["gap_duration_h"] == 36.0
    assert c.get(f"{A}/events?code=E11").json()["total"] == 1
    assert c.get(f"{A}/events?vessel_key=gfw:{IDS['gfw_near']}").json()["total"] == 1
    cell = c.get(f"{A}/cells/r24c39").json()["item"]
    assert cell["gfw_comparison"]["n_ours"] == 3 and cell["research_only"] is True
    assert "gfw_comparison" not in shared_clients["open"].get(f"{A}/cells/r24c39").json()["item"]


def test_mtime_reload(data_dir):
    c = make_client(data_dir, "open")
    n0 = c.get(f"{A}/meta").json()["item"]["counts"]["contacts_live"]
    import geopandas as gpd

    from synthetic import live_contacts

    g = live_contacts()
    extra = g.iloc[[1]].copy()
    extra["det_id"] = "S1D_20261008T230108_00099"
    g = gpd.GeoDataFrame(__import__("pandas").concat([g, extra], ignore_index=True), crs="EPSG:4326")
    p = data_dir / "live" / "live_contacts.gpkg"
    g.to_file(p, layer="contacts_4326", driver="GPKG", engine="pyogrio")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    m = c.get(f"{A}/meta").json()["item"]
    assert m["counts"]["contacts_live"] == n0 + 1
    assert c.get(f"{A}/contacts/S1D_20261008T230108_00099").status_code == 200


def test_missing_files_are_reported_not_errors(data_dir):
    for rel in ("leads_open.gpkg", "s1_next_passes.json", "ocean_daily_cells.parquet", "viirs_lights.gpkg", "ais_live.gpkg"):
        (data_dir / rel).unlink()
    c = make_client(data_dir, "open")
    m = c.get(f"{A}/meta")
    assert m.status_code == 200
    files = {f["key"]: f for f in m.json()["item"]["files"]}
    for k in ("leads_open", "pass_plan", "cells_daily", "lights", "ais_vessels", "events_open"):
        assert files[k]["status"] == "missing" and files[k]["rows"] is None, k
    leads = c.get(f"{A}/leads").json()
    assert leads["items"] == [] and leads["total"] == 0 and "missing" in leads["note"] and ENVELOPE <= set(leads)
    assert c.get(f"{A}/lights").json()["total"] == 0
    assert c.get(f"{A}/vessels").json()["total"] == 0
    assert c.get(f"{A}/events").json()["note"].startswith("Open-build events")
    assert c.get(f"{A}/passes").status_code == 200
    assert c.get(f"{A}/cells/{IDS['cell']}").json()["item"]["nightly"] is None
    assert c.get(f"{A}/timeline").status_code == 200
    assert c.get(f"{A}/leads/{IDS['lead_l1']}").status_code == 404


def test_decision_transitions(open_client, data_dir):
    c, lid = open_client, IDS["lead_l1"]
    log = data_dir / "labels" / "lead_decisions.jsonl"

    def post(**body):
        return c.post(f"{A}/leads/{lid}/decision", json={"user": "owner", **body})

    assert post(to_state="new").status_code == 409
    assert post(to_state="dark").status_code == 422
    assert post(to_state="reviewing").status_code == 200
    r = post(to_state="reviewing")
    assert r.status_code == 409 and r.json()["error"]["code"] == "transition_not_allowed"
    assert post(to_state="closed_unexplained").json()["error"]["code"] == "note_required"
    assert post(to_state="closed_explained", reason="other").json()["error"]["code"] == "note_required"
    assert post(to_state="closed_explained", reason="aliens").json()["error"]["code"] == "reason_required"
    assert post(to_state="closed_unexplained", note="evidence reviewed").status_code == 200
    assert post(to_state="closed_false_alarm", reason="sea clutter").status_code == 409  # closed -> closed
    assert post(to_state="reviewing").json()["error"]["code"] == "note_required"  # reopen needs a note
    assert post(to_state="reviewing", note="second look").status_code == 200
    r = post(to_state="closed_false_alarm", reason="other (note required)", note="sidelobe of a tanker")
    assert r.status_code == 200
    lead = r.json()["item"]
    assert lead["state"] == "closed_false_alarm" and lead["reason"] == "other" and len(lead["history"]) == 4
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [d["to_state"] for d in lines] == ["reviewing", "closed_unexplained", "reviewing", "closed_false_alarm"]
    assert all(d["build"] == "open" and d["user"] == "owner" for d in lines)
    assert c.get(f"{A}/leads").json()["total"] == 1  # default state filter new,reviewing: only L7 left
    assert c.get(f"{A}/leads?state=closed_false_alarm").json()["items"][0]["lead_id"] == lid
    assert c.post(f"{A}/leads/L9-none/decision", json={"to_state": "reviewing"}).status_code == 404
    # a second app on the same files sees the log (append-only, re-read on mtime change)
    c2 = make_client(data_dir, "open")
    assert c2.get(f"{A}/leads/{lid}").json()["item"]["state"] == "closed_false_alarm"


def test_research_decisions_go_to_research_log(research_client, data_dir):
    r = research_client.post(f"{A}/leads/{IDS['lead_r1']}/decision", json={"to_state": "reviewing", "user": "owner"})
    assert r.status_code == 200
    assert (data_dir / "research" / "lead_decisions.jsonl").read_text().count("\n") == 1
    assert not (data_dir / "labels" / "lead_decisions.jsonl").exists()
    assert r.json()["log_path"] == "data/research/lead_decisions.jsonl"


def test_contact_labels(open_client, data_dir):
    r = open_client.post(f"{A}/contacts/{IDS['live_unmatched']}/label", json={"label": "vessel", "user": "owner", "note": "clear hull"})
    assert r.status_code == 200 and r.json()["item"]["label"] == "vessel"
    open_client.post(f"{A}/contacts/{IDS['live_nocov']}/label", json={"label": "clutter"})
    rows = list(csv.DictReader((data_dir / "labels" / "contact_labels.csv").open()))
    assert [x["det_id"] for x in rows] == [IDS["live_unmatched"], IDS["live_nocov"]]
    assert list(rows[0])[:4] == ["det_id", "label", "user", "time_utc"] and rows[1]["user"] == "owner"
    assert open_client.post(f"{A}/contacts/{IDS['live_nocov']}/label", json={"label": "boat"}).status_code == 422
    assert open_client.post(f"{A}/contacts/NOPE_1/label", json={"label": "vessel"}).status_code == 404


def test_chips(shared_clients):
    c = shared_clients["open"]
    r = c.get(f"{A}/contacts/{IDS['chip']}/chip.webp")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp" and r.content[:4] == b"RIFF"
    assert c.get(f"{A}/contacts/{IDS['chip']}").json()["item"]["chip"] == f"{A}/contacts/{IDS['chip']}/chip.webp"
    r = c.get(f"{A}/contacts/{IDS['live_nocov']}/chip.webp")
    assert r.status_code == 404 and r.json()["error"]["code"] == "chip_not_cached"
    r = c.get(f"{A}/contacts/{IDS['live_nocov']}/chip.webp?fetch=1")
    assert r.status_code == 404 and r.json()["error"]["code"] == "chip_builder_unavailable"


def test_exports(shared_clients):
    import geopandas as gpd
    import pyogrio

    from scs_api.config import PRODUCT_CAVEAT

    c = shared_clients["open"]
    gj = c.get(f"{A}/export/geojson?type=contacts&view=live")
    assert gj.status_code == 200 and "attachment" in gj.headers["content-disposition"]
    fc = gj.json()
    assert fc["meta"]["caveat"].startswith(PRODUCT_CAVEAT) and fc["meta"]["build"] == "open"
    assert all(f["properties"]["caveat"].startswith(PRODUCT_CAVEAT) and f["properties"]["build"] == "open" and f["properties"]["licence"]
               for f in fc["features"])
    assert "gfw" not in json.dumps(fc["meta"]["sources"]).lower()
    cs = c.get(f"{A}/export/csv?type=leads&ids={IDS['lead_l1']},{IDS['lead_l7']}")
    rows = list(csv.DictReader(io.StringIO(cs.text)))
    assert len(rows) == 2 and {"caveat", "build", "licence"} <= set(rows[0]) and rows[0]["caveat"].startswith(PRODUCT_CAVEAT)
    gp = c.get(f"{A}/export/gpkg?type=contacts&confidence=high,medium,fixed,low")
    assert gp.status_code == 200
    p = Path(os.environ.get("TMPDIR", "/tmp")) / f"scs_export_test_{os.getpid()}.gpkg"
    p.write_bytes(gp.content)
    try:
        layers = {n for n, _ in pyogrio.list_layers(p)}
        assert {"contacts_4326", "contacts_utm49n", "contacts_utm48n", "about"} <= layers
        about = pyogrio.read_dataframe(p, layer="about")
        assert about["caveat"].iloc[0].startswith(PRODUCT_CAVEAT) and about["build"].iloc[0] == "open"
        assert gpd.read_file(p, layer="contacts_utm48n").crs.to_epsg() == 32648
        assert gpd.read_file(p, layer="contacts_utm49n").crs.to_epsg() == 32649
    finally:
        p.unlink(missing_ok=True)
    h = c.get(f"{A}/export/html?type=leads&ids={IDS['lead_l1']}").text
    assert h.count(PRODUCT_CAVEAT.replace("'", "&#x27;")) >= 2 and h.startswith("<!doctype html>")
    for typ in ("eez", "eez_boundaries"):
        r = c.get(f"{A}/export/geojson?type={typ}")
        assert r.status_code == 403 and r.json()["error"]["code"] == "not_exportable"
    assert c.get(f"{A}/export/shp?type=contacts").status_code == 422
    rj = shared_clients["research"].get(f"{A}/export/geojson?type=contacts&view=regional").json()
    assert rj["meta"]["research_use"].startswith("research build only") and rj["features"][0]["properties"]["research_licence_url"]


def test_docs_off_and_static_frontend(data_dir, tmp_path):
    c = make_client(data_dir, "open")
    assert c.get("/docs").status_code == 404 and c.get("/redoc").status_code == 404
    assert c.get("/openapi.json").status_code == 200
    page = c.get("/")
    assert page.status_code == 200 and "frontend is not built" in page.text and "Not evidence of illegal activity" in page.text
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>SCS Vessel Watch</title>built")
    c2 = make_client(data_dir, "open", frontend=dist)
    assert c2.get("/").text.endswith("built") and c2.get(f"{A}/meta").status_code == 200
    assert c2.get(f"{A}/nothing").json()["error"]["code"] == "not_found"


def test_track_gaps(shared_clients):
    t = shared_clients["open"].get(f"{A}/vessels/mmsi:{IDS['vessel_mmsi']}/track").json()
    assert t["type"] == "FeatureCollection" and len(t["features"]) == 3 and t["features"][0]["properties"]["t"] == "2026-10-08T14:10:00Z"
    assert len(t["gaps"]) == 1 and t["gaps"][0]["note"] == "An AIS gap is not proof of intent." and t["gaps"][0]["overlaps_recorder_gap"]
    t2 = shared_clients["open"].get(f"{A}/vessels/mmsi:{IDS['vessel_mmsi']}/track?max_points=2&t0=2026-10-08T14:00:00Z").json()
    assert len(t2["features"]) == 2 and t2["simplified"] is True
    tr = shared_clients["research"].get(f"{A}/vessels/gfw:{IDS['gfw_vessel']}/track").json()
    assert tr["features"][0]["properties"]["msg_type"] == "gfw_presence_hour"


def test_search(shared_clients):
    c = shared_clients["open"]
    r = c.get(f"{A}/search?q=10.25, 107.5").json()
    assert r["items"][0]["type"] == "point" and r["items"][0]["lat"] == 10.25 and r["items"][0]["lon"] == 107.5
    assert r["interpretation"] == "read as 10.25 N, 107.5 E"
    assert c.get(f"{A}/search?q=107.5E 10.25N").json()["items"][0]["lat"] == 10.25
    d = c.get(f"{A}/search?q=10°15'00\"N 107°30'00\"E").json()["items"][0]
    assert abs(d["lat"] - 10.25) < 1e-6 and abs(d["lon"] - 107.5) < 1e-6
    v = c.get(f"{A}/search?q=test vessel").json()["items"]
    assert v[0]["type"] == "vessel" and v[0]["id"] == f"mmsi:{IDS['vessel_mmsi']}"
    assert c.get(f"{A}/search?q={IDS['vessel_mmsi']}").json()["items"][0]["score"] == 1.0
    ids = [x["id"] for x in c.get(f"{A}/search?q=S1D_20261008T2300").json()["items"]]
    assert IDS["live_matched"] in ids and IDS["live_unmatched"] in ids
    assert c.get(f"{A}/search?q={IDS['lead_l7']}").json()["items"][0]["type"] == "lead"
    assert c.get(f"{A}/search?q={IDS['cell']}").json()["items"][0]["type"] == "cell"
    re_ = shared_clients["research"].get(f"{A}/search?q={IDS['gap']}").json()["items"]
    assert re_[0]["type"] == "event"


def test_passes_and_timeline(shared_clients):
    c = shared_clients["open"]
    p = c.get(f"{A}/passes/{LIVE_PASS}").json()["item"]
    assert p["processed"] and p["sources"] == ["processed"] and p["n_contacts"] == {"matched": 1, "no_coverage": 2, "unmatched": 1}
    assert p["ais_aoi_positions"] == 8812 + 9074 and p["ais_aoi_mmsi"] == 995 and p["footprint"]["type"] in ("Polygon", "MultiPolygon")
    assert isinstance(p["ais_aoi_positions"], int) and isinstance(p["relative_orbit"], int)
    assert p["extra"]["plan_pass_id"] == IDS["plan_past"]
    up = c.get(f"{A}/passes?status=upcoming").json()["items"]
    assert [x["pass_id"] for x in up] == [IDS["plan_up"]] and up[0]["sources"] == ["esa_plan"]
    reg = c.get(f"{A}/passes/S1C_20260920T1048").json()["item"]
    assert len(reg["scenes"]) == 2 and reg["n_contacts"] == {"not_checked": 7}
    tl = c.get(f"{A}/timeline").json()["item"]
    assert {"passes", "contactsByPass", "aisHours", "aisGaps", "viirsNights", "events", "upcoming"} <= set(tl)
    assert tl["aisGaps"] and tl["aisGaps"][0]["label"] == "recorder gap" and len(tl["aisHours"]) == 2
    assert {r["pass_id"] for r in tl["contactsByPass"]} >= {LIVE_PASS, "S1C_20260920T1048"}
    day = c.get(f"{A}/timeline?bin=day").json()["item"]["contactsByPass"]
    assert day and all(r["pass_id"] is None for r in day)


def test_coverage_note_on_no_coverage_pass():
    from scs_api.loaders.passes import coverage_note
    import pandas as pd

    sc = pd.DataFrame({"ais_aoi_positions": [8812, 9074], "ais_near_footprint_mmsi": [0, 0], "ais_footprint_positions": [0, 0]})
    s = coverage_note({"no_coverage": 3081}, sc)
    assert s.startswith("No AIS was heard inside or within 0.3 degree of any of the 2 scenes, so none of the 3,081 contacts")
    assert "8,812 to 9,074 positions elsewhere in the AOI" in s
    assert coverage_note({"matched": 3, "no_coverage": 1}, sc) is None


def test_rasters_and_geo(shared_clients):
    from scs_api.config import SHIPPING_LABEL

    c = shared_clients["open"]
    reg = {r["name"]: r for r in c.get(f"{A}/rasters").json()["items"]}
    assert {"ais_reach_share", "s1_look_prob_7d", "ship_density_all", "depth_m"} <= set(reg)
    assert reg["ship_density_all"]["note"].startswith(SHIPPING_LABEL) and reg["ship_density_all"]["src"] == "worldbank_density"
    assert not [n for n in reg if n.startswith("gfw_")] and all(r["default_on"] is False for r in reg.values())
    w = c.get(f"{A}/rasters/ship_density_all.webp?theme=light")
    assert w.status_code == 200 and w.headers["content-type"] == "image/webp" and len(w.headers["x-bounds"].split(",")) == 4
    v = c.get(f"{A}/rasters/depth_m/value?lon=104.2&lat=9.9").json()["item"]
    assert v["value"] == 40.0 and v["unit"] == "m"
    assert c.get(f"{A}/rasters/nope.webp").status_code == 404
    assert "gfw_ais_presence_hours" in {r["name"] for r in shared_clients["research"].get(f"{A}/rasters").json()["items"]}
    cell = c.get(f"{A}/cells/{IDS['cell']}").json()["item"]
    assert cell["ais_reach_share"] == 0.25 and cell["look_prob_7d"] == 40.0 and cell["shipping_note"] == SHIPPING_LABEL
    assert cell["nights_available"] == ["2026-09-10", "2026-09-11"] and cell["nightly"]["night"] == "2026-09-11"
    assert c.get(f"{A}/cells/{IDS['cell']}?night=2026-09-10").json()["item"]["nightly"]["sst_mean_c"] == 29.0
    assert c.get(f"{A}/cells/{IDS['cell']}?scene_id=nope").status_code == 404
    assert c.get(f"{A}/cells/r0c0").status_code == 404
    eez = c.get(f"{A}/geo/eez_boundaries.geojson").json()
    assert "takes no position" in eez["note"] and eez["default_on"] is False and eez["exportable"] is False
    assert c.get(f"{A}/geo/reporting_boxes.geojson").json()["note"] == "reporting box: for statistics only, not a boundary"
    assert len(c.get(f"{A}/geo/footprints.geojson?t0=2026-09-20T10:48:30Z").json()["features"]) == 1


def test_columnar_round_trip(shared_clients):
    import numpy as np
    import pandas as pd

    from scs_api import columnar as C

    ids = ["S1C_20260920T104816_00051", "S1C_20260920T104816_00052", "S1D_20260929T1110_0003"]
    spec = C.detid(ids)
    assert spec["t"] == "detid" and C.decode(spec, 3) == ids
    vals = [12.5, None, 103.0]
    s = C.num(vals, 10)
    assert C.decode(s, 3) == [12.5, None, 103.0] and s["t"] == "i16"  # narrowest type that holds 125 to 1030
    assert C.num([-5.5, 2.0], 100)["t"] == "i16"
    assert C.decode(C.dict_col(["a", None, "b"]), 3) == ["a", None, "b"]
    assert C.decode(C.bool8([True, None, False]), 3) == [True, None, False]
    t = ["2026-09-20T10:48:16Z", None, "2026-10-08T23:00:43Z"]
    assert C.decode(C.time_col(pd.Series(t)), 3) == t
    lid = C.lightid(["N21_20260910T183012_000001"], ["NOAA-21"], pd.Series(["2026-09-10T18:30:12Z"]))
    assert lid["t"] == "lightid"
    assert C.lightid(["weird"], ["NOAA-21"], pd.Series(["2026-09-10T18:30:12Z"]))["t"] == "str"
    part = shared_clients["open"].get(f"{A}/layers/contacts.cols").json()
    n = part["n"]
    got = C.decode(part["columns"]["det_id"], n)
    assert set(got) >= {IDS["live_matched"], IDS["reg"][0]} and not set(got) & set(IDS["struct"])
    assert set(C.decode(part["columns"]["ais_status"], n)) <= {"matched", "unmatched", "no_coverage", "not_checked"}
    lat = np.array([x for x in C.decode(part["columns"]["lat"], n)])
    assert np.all((lat > -4) & (lat < 25))
    st = shared_clients["open"].get(f"{A}/layers/structures.cols").json()
    assert set(C.decode(st["columns"]["confidence"], st["n"])) == {"fixed"}
    li = shared_clients["open"].get(f"{A}/layers/lights.cols").json()
    assert li["columns"]["light_id"]["t"] == "lightid" and li["n"] == 3
    assert shared_clients["open"].get(f"{A}/layers/nope.cols").status_code == 404


REAL = Path(__file__).resolve().parents[2] / "data"


@pytest.mark.skipif(not (REAL / "detections_regional.gpkg").exists() or not (REAL / "live" / "live_contacts.gpkg").exists(),
                    reason="real product files not present")
def test_real_data_smoke_open():
    """The open build on the repo's real data: starts, answers /meta and the core lists, never opens data/research/."""
    t = time.time()
    c = make_client(REAL, "open", frontend=REAL / "no_frontend")
    start = time.time() - t
    store = c.app.state.store
    m = c.get(f"{A}/meta").json()["item"]
    assert m["counts"]["contacts"] > 0 and not [s for s in m["sources"] if s["research_only"]]
    assert c.get(f"{A}/contacts?limit=5").status_code == 200
    assert c.get(f"{A}/leads?limit=5").status_code == 200
    assert c.get(f"{A}/search?q=S1C").status_code == 200
    research = str((REAL / "research").resolve())
    assert not [p for p in store.cat.opened if str(Path(p).resolve()).startswith(research)]
    assert start < 30
