"""Reload behaviour of the backend store (review R2-T3): the aisstream hour files reload only the tracks, in the background
and debounced, and keep every other cache; a reload builds a new State on the side, so requests during a slow reload get
the old data with status 200; a request keeps the State it pinned; unexpected errors still carry the error envelope; /meta
counts never wait for the background events load. Also the empty CSV export and the shipping-density presence values.
Offline, synthetic files."""

from __future__ import annotations

import contextvars
import csv
import io
import os
import threading
import time

import geopandas as gpd
import pandas as pd
import pytest

from conftest import make_client
from synthetic import IDS, live_contacts

A = "/api/v1"


def _client(data_dir, build="open", **kw):
    from fastapi.testclient import TestClient

    from scs_api.app import create_app
    from scs_api.config import Settings

    s = Settings(build=build, data_dir=data_dir, check_interval_s=0.0, frontend_dist=data_dir / "no_frontend", **kw)
    return TestClient(create_app(s), raise_server_exceptions=False)


def _bump(p, seconds=5):
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 1_000_000_000))


def _add_live_contact(data_dir, det_id="S1D_20261008T230108_00099"):
    g = live_contacts()
    extra = g.iloc[[1]].copy()
    extra["det_id"] = det_id
    g = gpd.GeoDataFrame(pd.concat([g, extra], ignore_index=True), crs="EPSG:4326")
    p = data_dir / "live" / "live_contacts.gpkg"
    g.to_file(p, layer="contacts_4326", driver="GPKG", engine="pyogrio")
    _bump(p)


def _write_positions(data_dir, hour="23", ts=("2026-10-08T23:10:00Z", "2026-10-08T23:40:00Z")):
    p = data_dir / "cache" / "ais" / "aisstream" / "positions" / "20261008" / f"{hour}.parquet"
    pd.DataFrame({"mmsi": [int(IDS["vessel_mmsi"])] * len(ts), "timestamp": pd.to_datetime(list(ts), utc=True),
                  "lon": [101.86 + 0.01 * k for k in range(len(ts))], "lat": [12.63] * len(ts), "sog_kn": 8.0, "cog_deg": 90.0,
                  "heading": 90.0, "nav_status": 0, "msg_type": "PositionReport", "msg_id": 1, "ais_class": "A",
                  "ship_name": None}).to_parquet(p, index=False)
    _bump(p)


def test_recorder_flush_keeps_caches_and_debounces_tracks(data_dir):
    """A rewritten hour file (the recorder's 60 s flush) must not reload vessels or empty the lead summary cache; with
    the default interval the tracks wait, with interval 0 they reload in the background and swap in."""
    from scs_api.loaders.common import obj_cache

    c = _client(data_dir)  # default tracks_reload_s (300 s)
    store = c.app.state.store
    assert c.get(f"{A}/leads?limit=100").status_code == 200
    ld, vd, tr, cd = (store.data[k] for k in ("leads", "vessels", "tracks", "contacts"))
    summaries = obj_cache(ld, "lead_summaries")
    assert summaries
    c.get(f"{A}/search?q=S1D")
    prefix = obj_cache(cd, "prefix")
    assert "contacts" in prefix
    _write_positions(data_dir)
    for _ in range(3):
        assert c.get(f"{A}/leads?limit=100").status_code == 200
    store.wait_background()
    assert store.data["leads"] is ld and obj_cache(store.data["leads"], "lead_summaries") is summaries
    assert store.data["vessels"] is vd and store.data["contacts"] is cd and "contacts" in obj_cache(cd, "prefix")
    assert store.data["tracks"] is tr, "tracks reloaded inside the debounce interval"
    assert len(c.get(f"{A}/vessels/mmsi:{IDS['vessel_mmsi']}/track").json()["features"]) == 3

    c2 = _client(data_dir, tracks_reload_s=0.0)
    s2 = c2.app.state.store
    c2.get(f"{A}/leads?limit=100")
    ld2, vd2, tr2 = s2.data["leads"], s2.data["vessels"], s2.data["tracks"]
    _write_positions(data_dir, ts=("2026-10-08T23:10:00Z", "2026-10-08T23:40:00Z", "2026-10-08T23:50:00Z"))
    assert c2.get(f"{A}/meta").status_code == 200  # submits the background tracks reload, never waits for it
    s2.wait_background()
    assert s2.data["tracks"] is not tr2 and s2.data["leads"] is ld2 and s2.data["vessels"] is vd2
    assert obj_cache(ld2, "lead_summaries")
    tr_new = s2.data["tracks"]
    assert len(c2.get(f"{A}/vessels/mmsi:{IDS['vessel_mmsi']}/track").json()["features"]) == 5
    # only the changed hour file was re-read; the 14 h file frame is the same object
    old14 = [v for p, v in tr2.parts.items() if p.name == "14.parquet"][0]
    new14 = [v for p, v in tr_new.parts.items() if p.name == "14.parquet"][0]
    assert old14[1] is new14[1]
    assert c2.get(f"{A}/meta").json()["item"]["counts"]["track_positions"] == 5


def test_requests_during_a_slow_reload_get_the_old_data(data_dir, monkeypatch):
    """The reviewer's race: a contacts reload with slow vessels and passes loads. Requests meanwhile get 200 and the old
    State (no 500, no mixed frame and mask); afterwards every request sees the new row."""
    from scs_api.loaders import passes as L_passes

    c = _client(data_dir)
    store = c.app.state.store
    n0 = c.get(f"{A}/contacts?limit=1").json()["total"]
    started, release = threading.Event(), threading.Event()
    real = L_passes.load

    def slow(*a, **k):
        started.set()
        release.wait(10)
        return real(*a, **k)

    monkeypatch.setattr(L_passes, "load", slow)
    _add_live_contact(data_dir)
    out = {}
    t = threading.Thread(target=lambda: out.update(r=c.get(f"{A}/contacts?limit=1")))
    t.start()
    assert started.wait(10), "the reload did not start"
    during = [c.get(f"{A}/contacts?limit=1"), c.get(f"{A}/timeline"), c.get(f"{A}/contacts?limit=1000"), c.get(f"{A}/meta")]
    release.set()
    t.join(20)
    for r in during:
        assert r.status_code == 200, r.text[:300]
    assert during[0].json()["total"] == n0 and len(during[2].json()["items"]) == n0
    assert out["r"].status_code == 200 and out["r"].json()["total"] == n0 + 1
    assert c.get(f"{A}/contacts?limit=1").json()["total"] == n0 + 1
    assert c.get(f"{A}/timeline").status_code == 200
    assert len(store.product_mask) == len(store.data["contacts"].df)


def test_a_request_keeps_the_state_it_pinned(data_dir, monkeypatch):
    from scs_api import store as S

    c = make_client(data_dir, "open")
    store = c.app.state.store
    seen = {}
    real = store.counts

    def spy():
        seen["pin"] = S._PINNED.get()
        return real()

    monkeypatch.setattr(store, "counts", spy)
    assert c.get(f"{A}/meta").status_code == 200
    assert seen["pin"] is not None and seen["pin"][0] is store and seen["pin"][1] is store._state  # pinned in the endpoint
    assert S._PINNED.get() is None  # and not leaked to the caller's context
    ctx = contextvars.copy_context()
    pinned = ctx.run(store.pin)
    old_df = pinned.data["contacts"].df
    _add_live_contact(data_dir)
    store.ensure_fresh()  # swaps a new State in, outside the pinned context
    assert len(store.data["contacts"].df) == len(old_df) + 1
    assert ctx.run(lambda: store.data["contacts"].df) is old_df
    assert ctx.run(lambda: len(store.product_mask)) == len(old_df)
    assert ctx.run(lambda: store.contacts_query({})[1]) == c.get(f"{A}/contacts?limit=1").json()["total"] - 1


def test_unexpected_errors_carry_the_envelope(data_dir, monkeypatch):
    from scs_api.config import PRODUCT_CAVEAT

    c = _client(data_dir)
    store = c.app.state.store

    def boom(p):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(store, "contacts_query", boom)
    r = c.get(f"{A}/contacts")
    assert r.status_code == 500
    b = r.json()
    assert b["error"]["code"] == "internal_error" and "RuntimeError" in b["error"]["message"]
    assert b["caveat"].startswith(PRODUCT_CAVEAT) and b["build"] == "open" and b["contract_version"]


def test_meta_counts_do_not_wait_for_background_events(data_dir, monkeypatch):
    from scs_api.loaders import events as L_events

    release = threading.Event()
    real = L_events.load

    def slow(*a, **k):
        release.wait(20)
        return real(*a, **k)

    monkeypatch.setattr(L_events, "load", slow)
    try:
        t = time.time()
        c = _client(data_dir, build="research")
        m = c.get(f"{A}/meta").json()["item"]
        assert time.time() - t < 15
        assert m["loading"] == ["events"] and m["counts"]["events"] == 4  # catalog rows of the four GFW event files
    finally:
        release.set()
    store = c.app.state.store
    store.wait_background()
    m = c.get(f"{A}/meta").json()["item"]
    assert m["loading"] == [] and m["counts"]["events"] == 4


def test_background_work_is_held_until_the_server_listens(data_dir, monkeypatch):
    """serve.py sets background_delay_s: the research events load and the warm-up wait until the lifespan startup
    releases them (a moment after the server listens), and a request that needs the events releases them at once."""
    from scs_api.loaders import events as L_events

    calls = []
    real = L_events.load
    monkeypatch.setattr(L_events, "load", lambda *a, **k: calls.append(time.time()) or real(*a, **k))
    c = _client(data_dir, build="research", background_delay_s=0.2)
    store = c.app.state.store
    time.sleep(0.5)
    m = c.get(f"{A}/meta").json()["item"]
    assert not calls and m["loading"] == ["events"] and m["counts"]["events"] == 4  # held, counted from the catalog
    r = c.get(f"{A}/events?limit=1")  # needs the events: releases the hold and waits for the load
    assert r.status_code == 200 and r.json()["total"] == 4 and len(calls) == 1
    c2 = _client(data_dir, build="research", background_delay_s=0.2)
    with c2:  # runs the lifespan: the hold is released 0.2 s after startup
        deadline = time.time() + 15
        while c2.app.state.store.loading() and time.time() < deadline:
            time.sleep(0.05)
        assert c2.get(f"{A}/meta").json()["item"]["loading"] == []
    store.wait_background()


def test_empty_csv_export_carries_caveat_build_and_licences(shared_clients):
    from scs_api.config import PRODUCT_CAVEAT

    for build, c in shared_clients.items():
        r = c.get(f"{A}/export/csv?type=lights&night=2001-01-01")
        assert r.status_code == 200
        rows = list(csv.DictReader(io.StringIO(r.text)))
        assert len(rows) == 1 and rows[0]["note"].startswith("No lights matched")
        assert rows[0]["caveat"].startswith(PRODUCT_CAVEAT) and rows[0]["build"] == build and rows[0]["licence"]
        assert ("research_use" in rows[0]) == (build == "research")


def test_shipping_density_served_as_presence(shared_clients):
    from scs_api.config import SHIPPING_LABEL

    c = shared_clients["open"]
    reg = {r["name"]: r for r in c.get(f"{A}/rasters").json()["items"]}
    e = reg["ship_density_all"]
    assert (e["vmin"], e["vmax"]) == (0.0, 1.0) and e["unit"].startswith("presence") and e["note"].startswith(SHIPPING_LABEL)
    hit = c.get(f"{A}/rasters/ship_density_all/value?lon=104.15&lat=9.85").json()["item"]
    assert hit["value"] == 1.0 and hit["presence"] is True and hit["value_as_published"] == 1e6
    miss = c.get(f"{A}/rasters/ship_density_all/value?lon=104.35&lat=9.65").json()["item"]
    assert miss["value"] == 0.0 and miss["presence"] is False
    depth = c.get(f"{A}/rasters/depth_m/value?lon=104.2&lat=9.9").json()["item"]
    assert depth["value"] == 40.0 and depth.get("presence") is None and depth.get("value_as_published") is None


@pytest.mark.parametrize("build", ["open", "research"])
def test_meta_reports_expected_activity_file(data_dir, build):
    pd.DataFrame({"target": ["viirs"], "cell_id": ["r62c24"]}).to_parquet(data_dir / "expected_activity.parquet", index=False)
    c = _client(data_dir, build=build)
    files = {f["key"]: f for f in c.get(f"{A}/meta").json()["item"]["files"]}
    assert files["expected_activity"]["path"] == "data/expected_activity.parquet"
    assert files["expected_activity"]["status"] == "existing" and files["expected_activity"]["rows"] == 1


def test_a_failed_reload_keeps_serving_the_old_data(data_dir):
    """A file caught mid-write (here: not a GeoPackage at all) fails the reload; requests keep the old State with 200,
    /meta names the failure, and the next change of the file reloads."""
    c = _client(data_dir)
    n0 = c.get(f"{A}/contacts?limit=1").json()["total"]
    p = data_dir / "live" / "live_contacts.gpkg"
    good = p.read_bytes()
    p.write_bytes(b"partial write")
    _bump(p)
    r = c.get(f"{A}/contacts?limit=1")
    assert r.status_code == 200 and r.json()["total"] == n0
    m = c.get(f"{A}/meta").json()["item"]
    assert m["reload_error"] and "contacts" in m["reload_error"]
    p.write_bytes(good)
    _add_live_contact(data_dir)
    assert c.get(f"{A}/contacts?limit=1").json()["total"] == n0 + 1
    assert c.get(f"{A}/meta").json()["item"]["reload_error"] is None
