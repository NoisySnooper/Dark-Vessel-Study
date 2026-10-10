"""Contract 1.3.0 fields of the backend (board D5 and D6, task R3-T10), offline on synthetic files: object context
(D5.3), expected activity (D5.4), live identification evidence with field provenance, AIS-only vessels and the
azimuth check on passes, L7 evidence lights, the leads detail file (D6.3), codes (D5.1) and research_only per row (D5.5).
"""

from __future__ import annotations

import json
import os
import shutil

import geopandas as gpd
import pandas as pd
import pyogrio
import pytest

from conftest import make_client
from synthetic import AISSTREAM_LABEL, IDS, LIVE_PASS, LIVE_SCENES, REG_SCENES

A = "/api/v1"
D53_FIELDS = ["depth_m", "dist_coast_km", "dist_port_km", "ship_presence_all", "ship_presence_commercial",
              "ship_presence_fishing", "ship_presence_oilgas", "ship_presence_passenger", "ship_presence_leisure", "sst_c",
              "sst_grad", "dist_front_km", "chl_log10", "current_speed_ms", "mld_m", "wave_hs_m"]
D54_ROW = ["unit_id", "night", "time_start_utc", "time_end_utc", "tested", "observed", "expected", "z", "q_bh", "flag",
           "flag_robust", "calm", "exposure_km2"]
LIVE_ID = ["az_time_utc", "match_dist_uncorr_m", "az_shift_m", "velocity_source", "match_ambiguous", "ambiguous_mmsi",
           "match_alt_dist_m", "review_note", "review_grade", "identity_label"]


def item(c, path):
    r = c.get(f"{A}{path}")
    assert r.status_code == 200, (path, r.text[:300])
    return r.json()["item"]


@pytest.fixture(scope="module", params=["open", "research"])
def client(request, shared_clients):
    return request.param, shared_clients[request.param]


# ---------------------------------------------------------------------------------------------------- D5.3
def check_context(ctx):
    from darkvessel.ocean.grid import OCEAN_CAVEAT

    from scs_api.models import ObjectContext

    ObjectContext.model_validate(ctx)
    assert list(ctx) == ["time_utc", "cell_id", "region", "fields", "caveat"]
    assert list(ctx["fields"]) == D53_FIELDS
    for name, f in ctx["fields"].items():
        assert list(f) == ["value", "unit", "time", "src"], name
    assert ctx["caveat"].startswith(OCEAN_CAVEAT)
    for name in D53_FIELDS:
        if name.startswith("ship_presence_"):
            f = ctx["fields"][name]
            assert isinstance(f["value"], bool) and f["unit"] == "presence as published, not a count"
            assert f["src"] == "worldbank_density" and f["time"] is None


def test_object_context_on_contacts(client):
    build, c = client
    rec = item(c, f"/contacts/{IDS['reg'][0]}")
    ctx = rec["object_context"]
    check_context(ctx)
    f = ctx["fields"]
    assert ctx["time_utc"] == "2026-09-20T10:48:16Z" and ctx["region"] == "Gulf of Tonkin"
    assert f["depth_m"] == {"value": 60.3, "unit": "m", "time": None, "src": "gebco_2026"}
    assert f["sst_c"]["src"] == "mur" and f["sst_c"]["time"] == "2026-09-20T09:00:00Z" and f["sst_c"]["unit"] == "degC"
    assert f["dist_front_km"]["src"] == "mur" and f["sst_grad"]["unit"] == "degC/km"
    assert f["chl_log10"] == {"value": -0.754, "unit": "log10 mg m-3", "time": "2026-09-20", "src": "noaacwNPPN20S3ASCIDINEOF2kmDaily"}
    assert f["current_speed_ms"]["src"] == "rtofs" and f["current_speed_ms"]["time"] == "2026-09-20T11:00:00Z"
    assert f["wave_hs_m"]["src"] == "gfs_wave" and f["ship_presence_all"]["value"] is True and f["ship_presence_fishing"]["value"] is False
    assert rec["prov"]["object_context"] == "ocean_context"
    # a structure with missing fields: the field stays with value null; its time is the table's (null when the day had
    # no layer, the layer's time when the layer had no value at the object)
    s = item(c, f"/contacts/{IDS['struct'][0]}")["object_context"]
    check_context(s)
    assert s["fields"]["wave_hs_m"] == {"value": None, "unit": "m", "time": None, "src": "gfs_wave"}
    assert s["fields"]["sst_grad"] == {"value": None, "unit": "degC/km", "time": "2026-09-20T09:00:00Z", "src": "mur"}
    # Ca Mau objects are radar_detail rows
    cm = item(c, f"/contacts/{IDS['camau'][1]}?")
    if build == "open":
        check_context(cm["object_context"])
    # null: no row in the table (a September contact, and every live contact until the table is rebuilt)
    assert item(c, f"/contacts/{IDS['reg'][1]}")["object_context"] is None
    assert item(c, f"/contacts/{IDS['live_matched']}")["object_context"] is None
    # summaries never carry it
    lst = c.get(f"{A}/contacts?limit=1000").json()["items"]
    assert lst and not any("object_context" in r for r in lst)


def test_object_context_on_lights(client):
    build, c = client
    rec = item(c, f"/lights/{IDS['light'][0]}")
    check_context(rec["object_context"])
    assert rec["object_context"]["time_utc"] == "2026-09-10T18:30:12Z" and rec["prov"]["object_context"] == "ocean_context"
    assert item(c, f"/lights/{IDS['light'][1]}")["object_context"] is None
    assert not any("object_context" in r for r in c.get(f"{A}/lights").json()["items"])


def test_object_context_null_when_table_missing(data_dir):
    (data_dir / "ocean_context_objects.parquet").unlink()
    (data_dir / "expected_activity.parquet").unlink()
    c = make_client(data_dir, "open")
    assert item(c, f"/contacts/{IDS['reg'][0]}")["object_context"] is None
    assert item(c, f"/lights/{IDS['light'][0]}")["object_context"] is None
    cell = item(c, f"/cells/{IDS['cell']}")
    assert cell["expected_activity"] is None and "expected_activity" not in cell["field_prov"]
    files = {f["key"]: f for f in c.get(f"{A}/meta").json()["item"]["files"]}
    assert files["object_context"]["status"] == "missing" and files["expected_activity"]["status"] == "missing"


# ---------------------------------------------------------------------------------------------------- D5.4
def test_expected_activity_on_cells(client):
    from darkvessel.ocean.grid import OCEAN_CAVEAT

    from scs_api.models import ExpectedActivity

    build, c = client
    cell = item(c, f"/cells/{IDS['cell']}")
    ea = cell["expected_activity"]
    ExpectedActivity.model_validate(ea)
    assert list(ea) == ["model_id", "caveat", "targets"] and ea["model_id"] == "expected_activity_v1_fixture"
    assert ea["caveat"].startswith(OCEAN_CAVEAT) and "anomaly" in ea["caveat"]
    assert sorted(ea["targets"]) == ["radar", "viirs"]
    v = ea["targets"]["viirs"]
    assert [r["night"] for r in v] == ["2026-09-11", "2026-09-10"]  # tested rows only, newest first
    for r in v + ea["targets"]["radar"]:
        assert list(r) == D54_ROW and r["tested"] is True
    assert v[0]["flag"] == "high" and v[0]["flag_robust"] == "high" and v[0]["time_start_utc"] == "2026-09-11T18:30:00Z"
    assert v[0]["exposure_km2"] == 612.35 and v[0]["calm"] is True and v[0]["q_bh"] == 0.004
    assert ea["targets"]["radar"][0]["unit_id"] == REG_SCENES[0]
    assert cell["prov"]["expected_activity"] == "expected_activity"
    assert cell["field_prov"]["expected_activity"]["src"] == "expected_activity"
    # radar rows only: the other target is an empty list; no tested row at all: null
    other = item(c, "/cells/r24c39")["expected_activity"]
    assert other["targets"]["viirs"] == [] and len(other["targets"]["radar"]) == 1
    assert item(c, "/cells/r45c11")["expected_activity"] is None


# ---------------------------------------------------------------------------------------------------- live identity
def test_live_identity_fields_of_a_matched_contact(client):
    build, c = client
    r = item(c, f"/contacts/{IDS['live_matched']}")
    assert r["ais_status"] == "matched" and r["match_quality"] == "high" and r["mmsi"] == IDS["vessel_mmsi"]
    assert r["az_shift_m"] == 95.5 and r["match_dist_uncorr_m"] == 180.0 and r["az_time_utc"] == "2026-10-08T23:00:45Z"
    assert r["velocity_source"] == "sog_cog" and r["match_ambiguous"] is False and r["ambiguous_mmsi"] is None
    assert r["review_note"].startswith("confirmed:") and r["review_grade"] == "confirmed"
    assert r["identity_label"] == AISSTREAM_LABEL
    # weather from the pass's sidecar (data/live/<run_id>_weather.parquet), with its sources
    assert r["wind_ms"] == 7.5 and r["deep_convection"] is False and r["ctt_k"] is None
    assert r["prov"]["wind_ms"] == "gfs_wind" and r["prov"]["deep_convection"] == "himawari_ctt"
    fp = r["field_prov"]
    assert fp["wind_ms"] == {"src": "gfs_wind", "time": "2026-10-08T23:00:00Z", "text": "GFS 2026-10-08 18Z f005"}
    assert fp["deep_convection"]["src"] == "himawari_ctt" and fp["deep_convection"]["time"] == "2026-10-08T22:50:20Z"
    assert "ctt_k" not in fp  # no value, no source
    assert fp["az_shift_m"]["src"] == "det_live" and fp["az_shift_m"]["time"] == "2026-10-08T23:00:45Z"
    assert fp["match_dist_uncorr_m"]["src"] == "aisstream" and fp["review_note"] == {
        "src": "analyst", "time": "2026-10-10T15:10:00Z", "text": f"hand check, data/live/{LIVE_PASS}_review_matched.csv (reviewed_utc)"}
    assert r["prov"]["review_note"] == "analyst" and r["prov"]["identity_label"] == "aisstream"
    for f, v in fp.items():
        assert r.get(f) is not None, f  # provenance only for fields that hold a value
        assert set(v) == {"src", "time", "text"}


def test_live_identity_fields_of_unmatched_and_no_coverage(client):
    build, c = client
    u = item(c, f"/contacts/{IDS['live_unmatched']}")
    assert u["ais_status"] == "unmatched" and u["match_ambiguous"] is True
    assert u["ambiguous_mmsi"] == "412000002;413000005" and u["match_alt_dist_m"] == 160.0
    assert u["review_note"] is None and u["review_grade"] is None and "review_note" not in u["field_prov"]
    assert u["identity_label"] == AISSTREAM_LABEL  # the nearest AIS vessel is an aisstream identity
    assert u["wind_ms"] == 13.2 and u["deep_convection"] is True and u["ctt_k"] == 205.0
    n = item(c, f"/contacts/{IDS['live_nocov']}")
    assert n["wind_ms"] is None and n["deep_convection"] is None and n["az_shift_m"] is None
    assert not {"wind_ms", "deep_convection", "az_shift_m"} & set(n["field_prov"])
    # outside live passes every live identity field is null
    reg = item(c, f"/contacts/{IDS['reg'][0]}")
    for f in LIVE_ID:
        assert reg[f] is None, f
    # the regional table writes the Himawari scan start as 13 digits (yyyymmddHHMM and the tens of seconds)
    assert reg["field_prov"]["ctt_k"]["time"] == "2026-09-20T10:40:20Z" and reg["field_prov"]["wind_ms"]["time"] is None
    assert reg["field_prov"]["deep_convection"]["time"] == "2026-09-20T10:40:20Z"
    assert reg["extra"]["himawari_start"] == "2026-09-20T10:40:20Z"
    assert reg["field_prov"]["ctt_k"]["text"].startswith("data/weather_context.parquet (Himawari-9")
    assert reg["field_prov"]["wind_ms"]["text"].startswith("data/weather_context.parquet (GFS")


def test_live_weather_sidecar_reloads(data_dir):
    c = make_client(data_dir, "open")
    assert item(c, f"/contacts/{IDS['live_matched']}")["wind_ms"] == 7.5
    p = data_dir / "live" / f"{LIVE_PASS}_weather.parquet"
    w = pd.read_parquet(p)
    w["wind_ms"] = [3.25, 4.0]
    w.to_parquet(p, index=False)
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert item(c, f"/contacts/{IDS['live_matched']}")["wind_ms"] == 3.25


def test_meta_live_rules_and_counts(client):
    build, c = client
    c.app.state.store.wait_background()  # counts never wait for a background load (lights_evidence is one)
    m = c.get(f"{A}/meta").json()["item"]
    assert m["contract_version"] == "1.3.0" and m["loading"] == []
    assert m["live_rules"]["ais_window"].startswith("AIS positions from 30 min") and m["live_rules"]["ambiguity"]
    assert m["counts"]["lights_evidence"] == 1 and m["counts"]["context_objects"] == 5 and m["counts"]["expected_activity_rows"] == 4
    keys = {s["key"] for s in m["sources"]}
    assert {"ocean_context", "expected_activity"} <= keys
    files = {f["key"]: f for f in m["files"]}
    for k in ("live_ais_only", "live_weather", "live_review", "lights_all", "object_context", "expected_activity"):
        assert files[k]["status"] == "existing", k
    assert ("leads_open_detail" in files) == (build == "open")


# ---------------------------------------------------------------------------------------------------- passes
def test_pass_ais_only_and_azimuth_check(client):
    from scs_api.models import AisOnlyVessel, build_models

    build, c = client
    p = item(c, f"/passes/{LIVE_PASS}")
    build_models(build)["pass"].model_validate(p)
    assert p["n_ais_only"] == 2 and len(p["ais_only"]) == 2 and p["identity_label"] == AISSTREAM_LABEL
    first, second = p["ais_only"]
    assert list(first) == list(AisOnlyVessel.model_fields)
    assert first["mmsi"] == IDS["ais_only"][0] and first["vessel_key"] == f"mmsi:{IDS['ais_only'][0]}"
    assert first["on_tested_sea"] is True and first["ambiguous_det_id"] == IDS["live_unmatched"]
    assert first["vessel_name"] == "SECOND" and first["ship_type"] == "fishing" and first["length_ais_m"] == 14.0 and first["sog_kn"] == 2.0
    assert first["identity_label"] == AISSTREAM_LABEL and second["on_tested_sea"] is False and second["vessel_name"] is None
    az = p["azimuth_check"]
    assert az["note"].startswith("per scene") and len(az["by_scene"]) == 2
    assert [s["scene_id"] for s in az["by_scene"]] == LIVE_SCENES and az["by_scene"][0]["corrected_median_nearest_m"] == 141.7
    assert p["prov"]["ais_only"] == "aisstream" and p["prov"]["azimuth_check"] == "det_live"
    assert p["field_prov"]["ais_only"]["time"] == "2026-10-08T23:00:43Z"
    assert p["field_prov"]["azimuth_check"]["time"] == "2026-10-09T07:37:06Z"
    # lists carry the count, not the vessels; plan passes carry none of it
    lst = {r["pass_id"]: r for r in c.get(f"{A}/passes").json()["items"]}
    assert lst[LIVE_PASS]["ais_only"] is None and lst[LIVE_PASS]["n_ais_only"] == 2 and lst[LIVE_PASS]["azimuth_check"]
    plan = lst[IDS["plan_up"]]
    assert plan["n_ais_only"] is None and plan["ais_only"] is None and plan["azimuth_check"] is None and plan["identity_label"] is None


def test_pass_without_ais_only_layer(data_dir):
    from scs_api.catalog import Catalog
    from scs_api.config import Settings

    p = data_dir / "live" / "live_contacts.gpkg"
    layers = [str(n) for n, _ in pyogrio.list_layers(p)]
    tmp = data_dir / "live" / "copy.gpkg"
    for name in layers:
        if name == "ais_only_4326":
            continue
        df = pyogrio.read_dataframe(p, layer=name)
        if isinstance(df, gpd.GeoDataFrame) and df.geometry.notna().any():
            df.to_file(tmp, layer=name, driver="GPKG", engine="pyogrio")
        else:
            pyogrio.write_dataframe(pd.DataFrame(df.drop(columns="geometry", errors="ignore")), tmp, layer=name, driver="GPKG")
    shutil.move(tmp, p)
    assert not Catalog(Settings(build="open", data_dir=data_dir)).exists("live_ais_only")
    pr = item(make_client(data_dir, "open"), f"/passes/{LIVE_PASS}")
    assert pr["ais_only"] is None and pr["n_ais_only"] is None and "ais_only" not in pr["field_prov"]
    assert pr["azimuth_check"] is not None


# ---------------------------------------------------------------------------------------------------- L7 lights
def test_l7_evidence_lights_resolve(client):
    build, c = client
    lid = IDS["lead_l7"]
    lead = item(c, f"/leads/{lid}")
    lights = [e for e in lead["evidence"] if e["type"] == "light"]
    assert {e["id"] for e in lights} == {IDS["light"][0], IDS["light_all"]}
    assert all(e["preview"] and e["preview"]["label"].startswith("VIIRS light") for e in lights)
    sites = [e for e in lead["evidence"] if e["type"] in ("site", "light_site")]
    assert sites and all(e["preview"] and e["preview"]["label"] for e in sites)
    rec = item(c, f"/lights/{IDS['light_all']}")
    assert rec["light_id"] == IDS["light_all"] and rec["satellite"] == "NOAA-20" and rec["night"] == "2026-09-20"
    assert rec["extra"]["light_file"] == "data/viirs_lights_all.gpkg" and "lean" in rec["extra"]["light_file_note"]
    assert rec["extra"]["snr"] == 9.0 and rec["cell_id"] == "r61c24"
    check_context(rec["object_context"])
    # the light list and layer stay the lean file
    assert c.get(f"{A}/lights").json()["total"] == 3
    assert c.get(f"{A}/lights/N20_20260920T183000_999999").status_code == 404


def test_lights_all_read_only_for_cited_lights(data_dir):
    """viirs_lights_all.gpkg is read only when a lead cites a light outside the lean file."""
    p = data_dir / "leads_open.gpkg"
    g = pyogrio.read_dataframe(p, layer="leads_4326")
    ev = json.loads(g.loc[g["lead_id"] == IDS["lead_l7"], "evidence"].iloc[0])
    g.loc[g["lead_id"] == IDS["lead_l7"], "evidence"] = json.dumps([e for e in ev if e["id"] != IDS["light_all"]])
    g.to_file(p, layer="leads_4326", driver="GPKG", engine="pyogrio")
    c = make_client(data_dir, "open")
    assert c.get(f"{A}/lights/{IDS['light_all']}").status_code == 404
    assert not [x for x in c.app.state.store.cat.opened if x.endswith("viirs_lights_all.gpkg")]


# ---------------------------------------------------------------------------------------------------- D6.3
def test_leads_detail_file_fills_missing_evidence(data_dir):
    p = data_dir / "leads_open.gpkg"
    g = pyogrio.read_dataframe(p, layer="leads_4326")
    l1 = g["lead_id"] == IDS["lead_l1"]
    ev = json.loads(g.loc[l1, "evidence"].iloc[0])
    g.loc[l1, "evidence"] = "[]"
    g.to_file(p, layer="leads_4326", driver="GPKG", engine="pyogrio")
    rows = [{"lead_id": IDS["lead_l1"], **e, "caveat": "x", "research_only": False} for e in ev]
    pyogrio.write_dataframe(pd.DataFrame(rows), data_dir / "leads_open_detail.gpkg", layer="lead_evidence", driver="GPKG")
    c = make_client(data_dir, "open")
    lead = item(c, f"/leads/{IDS['lead_l1']}")
    assert [(e["type"], e["id"], e["role"]) for e in lead["evidence"]] == [(e["type"], e["id"], e["role"]) for e in ev]
    assert lead["extra"]["evidence_source"] == "data/leads_open_detail.gpkg lead_evidence"
    assert IDS["lead_l1"] in item(c, f"/contacts/{IDS['live_unmatched']}")["lead_ids"]
    summ = {r["lead_id"]: r for r in c.get(f"{A}/leads").json()["items"]}
    assert len(summ[IDS["lead_l1"]]["evidence"]) == len(ev)
    # the research build never reads the open detail file
    rc = make_client(data_dir, "research")
    assert "leads_open_detail" not in rc.app.state.store.cat.specs


def test_leads_detail_file_ignored_when_evidence_is_present(data_dir):
    pyogrio.write_dataframe(pd.DataFrame([{"lead_id": IDS["lead_l1"], "type": "contact", "id": "nope", "role": "primary"}]),
                            data_dir / "leads_open_detail.gpkg", layer="lead_evidence", driver="GPKG")
    c = make_client(data_dir, "open")
    lead = item(c, f"/leads/{IDS['lead_l1']}")
    assert "nope" not in {e["id"] for e in lead["evidence"]} and "evidence_source" not in lead["extra"]
    assert not [x for x in c.app.state.store.cat.opened if x.endswith("leads_open_detail.gpkg")]


# ---------------------------------------------------------------------------------------------------- D5.1, D5.5, guard
def test_lead_codes_pass_through_unchanged(client):
    build, c = client
    lid = IDS["lead_l1"] if build == "open" else IDS["lead_r1"]
    lead = item(c, f"/leads/{lid}")
    assert lead["lawful_explanations"] == ["no_carriage_requirement", "vms_fleet"]
    assert lead["change_indicators"] == ["late_ais_match"]
    assert [f["factor"] for f in lead["factors"]] == ["evidence_quality"]
    summ = {r["lead_id"]: r for r in c.get(f"{A}/leads").json()["items"]}[lid]
    assert summ["lawful_explanations"] == lead["lawful_explanations"] and summ["change_indicators"] == lead["change_indicators"]


def test_research_only_is_per_row(shared_clients):
    c = shared_clients["research"]
    live = item(c, f"/contacts/{IDS['live_matched']}")
    reg = item(c, f"/contacts/{IDS['reg'][0]}")
    assert live["research_only"] is False and reg["research_only"] is True
    for r in (live, reg, item(c, f"/lights/{IDS['light_all']}"), item(c, f"/passes/{LIVE_PASS}")):
        assert "Research build, noncommercial, CC BY-NC 4.0" in r["caveat"]
    o = shared_clients["open"]
    assert item(o, f"/contacts/{IDS['live_matched']}")["research_only"] is False


def test_open_build_new_specs_stay_outside_research(data_dir):
    from scs_api.catalog import Catalog
    from scs_api.config import Settings

    cat = Catalog(Settings(build="open", data_dir=data_dir))
    research = (data_dir / "research").resolve()
    for k in ("live_ais_only", "live_weather", "live_review", "lights_all", "leads_open_detail", "object_context", "expected_activity"):
        assert k in cat.specs
        for p in cat.paths(k):
            assert research not in p.resolve().parents, (k, p)


def test_held_context_is_released_by_the_request_that_needs_it(data_dir):
    """The object context loads in the background after the server listens; a Contact request that comes first releases
    the hold and waits for it, so it never answers a null context that the table holds."""
    from fastapi.testclient import TestClient

    from scs_api.app import create_app
    from scs_api.config import Settings

    s = Settings(build="open", data_dir=data_dir, check_interval_s=0.0, frontend_dist=data_dir / "x", background_delay_s=30.0)
    c = TestClient(create_app(s))
    m = c.get(f"{A}/meta").json()["item"]
    assert "context" in m["loading"] and "context_objects" not in m["counts"]
    rec = item(c, f"/contacts/{IDS['reg'][0]}")
    assert rec["object_context"] is not None and rec["object_context"]["fields"]["depth_m"]["value"] == 60.3
    assert item(c, f"/cells/{IDS['cell']}")["expected_activity"]["model_id"] == "expected_activity_v1_fixture"


def test_live_pass_vessels_missing_from_the_snapshot_get_stub_records(client):
    """Every vessel key a live record links to resolves: an MMSI the live pass references but the vessel snapshot
    (ais_live.gpkg vessels_latest_4326) lacks gets a stub Vessel record built from the live file's own strings."""
    build, c = client
    v = item(c, f"/vessels/mmsi:{IDS['ais_only'][1]}")
    assert v["stub"] is True and v["mmsi"] == IDS["ais_only"][1] and v["identity_source"] == "MMSI only"
    assert v["extra"]["stub_reason"] == "ais_only" and "vessels_latest_4326" in v["extra"]["stub_note"]
    assert v["flag"] and v["src"] == "aisstream" and v["research_only"] is False and v["identity_label"] == AISSTREAM_LABEL
    known = item(c, f"/vessels/mmsi:{IDS['vessel_mmsi']}")
    assert known["stub"] is False and known["identity_label"] == AISSTREAM_LABEL  # in the snapshot: never a stub
    assert all(x["identity_label"] == AISSTREAM_LABEL for x in c.get(f"{A}/vessels?q=mmsi").json()["items"] if x["src"] == "aisstream")
    if build == "research":
        g = item(c, f"/vessels/gfw:{IDS['gfw_vessel']}")
        assert g["identity_label"] is None and "Research build" in g["caveat"]
    for d in (IDS["live_matched"], IDS["live_unmatched"]):
        r = item(c, f"/contacts/{d}")
        for k in ("vessel_key", "nearest_vessel_key"):
            if r[k]:
                assert c.get(f"{A}/vessels/{r[k]}").status_code == 200, (d, k, r[k])
    for a in item(c, f"/passes/{LIVE_PASS}")["ais_only"]:
        assert c.get(f"{A}/vessels/{a['vessel_key']}").status_code == 200, a["vessel_key"]


# ---------------------------------------------------------------------------------------------------- provenance times
ISO_OR_DATE = r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?"


def prov_times(rec):
    """Every field_prov time and object_context field time of a record."""
    out = [(f"field_prov.{k}", v.get("time")) for k, v in (rec.get("field_prov") or {}).items()]
    ctx = rec.get("object_context")
    if ctx:
        out += [(f"object_context.{k}", v.get("time")) for k, v in ctx["fields"].items()] + [("object_context.time_utc", ctx["time_utc"])]
    return out


def test_every_provenance_time_is_iso_or_a_date(client):
    """Contract 1 and 2: a field_prov or object_context time is ISO 8601 UTC with Z, a YYYY-MM-DD date, or null; never a
    producer's compact string (the September weather table writes 2026092010402)."""
    import re

    build, c = client
    recs = [item(c, f"/contacts/{r['det_id']}") for r in c.get(f"{A}/contacts?limit=1000&view=live,regional,camau").json()["items"]]
    recs += [item(c, f"/lights/{r['light_id']}") for r in c.get(f"{A}/lights").json()["items"]]
    recs += [item(c, f"/lights/{IDS['light_all']}"), item(c, f"/passes/{LIVE_PASS}"), item(c, f"/cells/{IDS['cell']}")]
    seen = 0
    for rec in recs:
        for where, t in prov_times(rec):
            seen += t is not None
            assert t is None or re.fullmatch(ISO_OR_DATE, t), (where, t)
    assert seen > 20


def test_prov_time_and_himawari_start_text():
    from scs_api.loaders.contacts import himawari_start_text
    from scs_api.records import prov_time

    s = pd.Series(["2026092010402", "202609201040", "20260920104025", "2026-09-20T10:40:00+00:00", "x", None])
    assert himawari_start_text(s).tolist() == ["2026-09-20T10:40:20Z", "2026-09-20T10:40:00Z", "2026-09-20T10:40:25Z",
                                               "2026-09-20T10:40:00Z", None, None]
    assert prov_time("2026-09-20") == "2026-09-20" and prov_time("2026-09-20T10:40:00Z") == "2026-09-20T10:40:00Z"
    assert prov_time("2026-09-20 10:40:00+07:00") == "2026-09-20T03:40:00Z"
    for bad in ("2026092010402", "", "nan", None, float("nan"), "soon"):
        assert prov_time(bad) is None, bad


def test_review_note_without_a_review_table(data_dir):
    """A hand-check note with no row in a review table keeps its provenance source, with the time null and the file named."""
    for p in (data_dir / "live").glob("*_review_*.csv"):
        p.unlink()
    r = item(make_client(data_dir, "open"), f"/contacts/{IDS['live_matched']}")
    assert r["review_note"].startswith("confirmed:")
    assert r["field_prov"]["review_note"] == {"src": "analyst", "time": None, "text": "hand check, review_note of "
                                              "data/live/live_contacts.gpkg (no row in a hand-check table, time unknown)"}


def test_azimuth_check_scene_ids():
    """Entries name their scene only when the producer does or every scene has an entry; otherwise scene_id is null."""
    from scs_api.loaders.passes import azimuth_check

    ids = ["S1D_a", "S1D_b"]
    whole = azimuth_check({"azimuth_check_by_scene": [{"vessels": 3}, {"vessels": 1}], "scene_ids": ids, "azimuth_check_note": "n"})
    assert [r["scene_id"] for r in whole["by_scene"]] == ids and whole["note"] == "n"
    part = azimuth_check({"azimuth_check_by_scene": [{"vessels": 3}], "scene_ids": ids})
    assert part["by_scene"] == [{"vessels": 3, "scene_id": None}] and part["note"] is None
    named = azimuth_check({"azimuth_check_by_scene": [{"vessels": 3, "scene_id": "S1D_b"}], "scene_ids": ids})
    assert named["by_scene"][0]["scene_id"] == "S1D_b"
    assert azimuth_check({"scene_ids": ids}) is None and azimuth_check(None) is None
