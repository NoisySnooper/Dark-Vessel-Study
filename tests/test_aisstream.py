"""Offline tests for the aisstream client: message normalisation, storage, dtype round trips, the reconnect loop and
reach statistics. Sentinel-1 pass tests are in tests/test_s1_passes.py.

No network. Messages are synthetic copies of the aisstream.io JSON layout (MessageType, MetaData, Message); the
websocket is a fake object.
"""

from __future__ import annotations

import asyncio
import gzip
import json

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from rasterio.transform import from_origin

import darkvessel  # noqa: F401  (sets PROJ_DATA before rasterio/pyogrio)
from darkvessel.ais import aisstream as ais

T0 = "2026-10-08 03:04:05.123456789 +0000 UTC"


def pos_a(mmsi=574001234, lat=10.5, lon=108.2, sog=8.2, cog=123.4, heading=120, nav=0, t=T0, name="TEST@@"):
    return {"MessageType": "PositionReport",
            "MetaData": {"MMSI": mmsi, "MMSI_String": mmsi, "ShipName": name, "latitude": lat, "longitude": lon, "time_utc": t},
            "Message": {"PositionReport": {"Cog": cog, "CommunicationState": 0, "Latitude": lat, "Longitude": lon, "MessageID": 1,
                                           "NavigationalStatus": nav, "PositionAccuracy": True, "Raim": False, "RateOfTurn": 0,
                                           "RepeatIndicator": 0, "Sog": sog, "Spare": 0, "SpecialManoeuvreIndicator": 0,
                                           "Timestamp": 5, "TrueHeading": heading, "UserID": mmsi, "Valid": True}}}


def pos_b(mmsi=412000001, lat=22.2, lon=114.1, sog=3.0, cog=45.0, heading=511, t=T0):
    return {"MessageType": "StandardClassBPositionReport",
            "MetaData": {"MMSI": mmsi, "ShipName": "", "latitude": lat, "longitude": lon, "time_utc": t},
            "Message": {"StandardClassBPositionReport": {"Cog": cog, "Latitude": lat, "Longitude": lon, "MessageID": 18, "Sog": sog,
                                                         "TrueHeading": heading, "UserID": mmsi, "Valid": True}}}


def pos_b_ext(mmsi=412000002, t=T0):
    return {"MessageType": "ExtendedClassBPositionReport",
            "MetaData": {"MMSI": mmsi, "ShipName": "SMALL BOAT", "latitude": 9.9, "longitude": 105.1, "time_utc": t},
            "Message": {"ExtendedClassBPositionReport": {"Cog": 10.0, "Latitude": 9.9, "Longitude": 105.1, "MessageID": 19, "Name": "SMALL BOAT",
                                                         "Sog": 4.5, "TrueHeading": 12, "Type": 30, "UserID": mmsi,
                                                         "Dimension": {"A": 8, "B": 4, "C": 2, "D": 2}}}}


def pos_long(mmsi=574000009, t=T0):
    return {"MessageType": "LongRangeAisBroadcastMessage",
            "MetaData": {"MMSI": mmsi, "ShipName": "FAR SHIP", "latitude": 12.1, "longitude": 112.3, "time_utc": t},
            "Message": {"LongRangeAisBroadcastMessage": {"Cog": 511, "Latitude": 12.1, "Longitude": 112.3, "MessageID": 27,
                                                         "NavigationalStatus": 0, "Sog": 63, "UserID": mmsi, "PositionLatency": 0}}}


def static_a(mmsi=574001234, t=T0):
    return {"MessageType": "ShipStaticData", "MetaData": {"MMSI": mmsi, "ShipName": "TEST VESSEL", "time_utc": t},
            "Message": {"ShipStaticData": {"AisVersion": 1, "CallSign": "XVAB", "Destination": "HO CHI MINH", "Dimension": {"A": 50, "B": 20, "C": 5, "D": 6},
                                           "Dte": False, "Eta": {"Month": 10, "Day": 9, "Hour": 6, "Minute": 30}, "FixType": 1,
                                           "ImoNumber": 9123456, "MaximumStaticDraught": 5.2, "MessageID": 5, "Name": "TEST VESSEL@",
                                           "Type": 70, "UserID": mmsi, "Valid": True}}}


def static_b_part_a(mmsi=412000001, t=T0):
    return {"MessageType": "StaticDataReport", "MetaData": {"MMSI": mmsi, "ShipName": "", "time_utc": t},
            "Message": {"StaticDataReport": {"MessageID": 24, "PartNumber": False, "Reserved": 0,
                                             "ReportA": {"Name": "BAY FISHER", "Valid": True},
                                             "ReportB": {"CallSign": "", "Dimension": {"A": 0, "B": 0, "C": 0, "D": 0}, "FixType": 0,
                                                         "ShipType": 0, "Valid": False}, "UserID": mmsi}}}


def static_b_part_b(mmsi=412000001, t=T0):
    return {"MessageType": "StaticDataReport", "MetaData": {"MMSI": mmsi, "ShipName": "BAY FISHER", "time_utc": t},
            "Message": {"StaticDataReport": {"MessageID": 24, "PartNumber": True, "Reserved": 0,
                                             "ReportA": {"Name": "", "Valid": False},
                                             "ReportB": {"CallSign": "BF1", "Dimension": {"A": 6, "B": 6, "C": 2, "D": 2}, "FixType": 1,
                                                         "ShipType": 30, "Valid": True}, "UserID": mmsi}}}


# ---------------------------------------------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------------------------------------------

def test_parse_time_utc_nanoseconds_and_fallback():
    t = ais.parse_time_utc(T0)
    assert t == pd.Timestamp("2026-10-08 03:04:05.123456", tz="UTC")
    assert ais.parse_time_utc("2026-10-08 03:04:05 +0000 UTC") == pd.Timestamp("2026-10-08 03:04:05", tz="UTC")
    assert ais.parse_time_utc("") is None and ais.parse_time_utc("garbage") is None


def test_normalise_class_a_position():
    r = ais.normalise_position(pos_a())
    assert r["mmsi"] == 574001234 and r["ais_class"] == "A" and r["msg_id"] == 1
    assert r["timestamp"].tzinfo is not None and r["timestamp"].year == 2026
    assert (r["lon"], r["lat"]) == (108.2, 10.5)
    assert r["sog_kn"] == pytest.approx(8.2) and r["cog_deg"] == pytest.approx(123.4) and r["heading"] == 120
    assert r["nav_status"] == 0 and r["ship_name"] == "TEST"  # '@' padding stripped


def test_not_available_codes_become_null():
    r = ais.normalise_position(pos_a(sog=102.3, cog=360.0, heading=511, nav=15))
    assert np.isnan(r["sog_kn"]) and np.isnan(r["cog_deg"]) and np.isnan(r["heading"]) and r["nav_status"] == 15
    r = ais.normalise_position(pos_long())  # long-range codes: SOG 63, COG 511
    assert np.isnan(r["sog_kn"]) and np.isnan(r["cog_deg"]) and np.isnan(r["heading"]) and r["msg_id"] == 27


def test_bad_positions_are_dropped():
    assert ais.normalise_position(pos_a(lat=91.0)) is None  # lat not available
    assert ais.normalise_position(pos_a(lon=181.0)) is None  # lon not available
    assert ais.normalise_position(pos_a(lat=95.0)) is None   # out of range
    assert ais.normalise_position(pos_a(lat=0.0, lon=0.0)) is None  # null island
    m = pos_a()
    m["MetaData"]["MMSI"] = 0
    m["MetaData"]["MMSI_String"] = 0
    m["Message"]["PositionReport"]["UserID"] = 0
    assert ais.normalise_position(m) is None
    assert ais.normalise_position({"MessageType": "BaseStationReport", "MetaData": {}, "Message": {}}) is None


def test_class_b_positions():
    r = ais.normalise_position(pos_b())
    assert r["ais_class"] == "B" and r["msg_id"] == 18 and np.isnan(r["heading"]) and r["nav_status"] is None
    assert r["ship_name"] is None
    r = ais.normalise_position(pos_b_ext())
    assert r["ais_class"] == "B" and r["msg_id"] == 19 and r["ship_name"] == "SMALL BOAT"


def test_normalise_static_class_a():
    r = ais.normalise_static(static_a())
    assert r["imo"] == 9123456 and r["name"] == "TEST VESSEL" and r["callsign"] == "XVAB"
    assert r["ship_type"] == 70 and r["ship_type_label"] == "cargo"
    assert r["length_m"] == 70 and r["width_m"] == 11 and r["destination"] == "HO CHI MINH" and r["eta"] == "10-09 06:30"
    assert r["seen_utc"] == pd.Timestamp("2026-10-08 03:04:05.123456", tz="UTC")


def test_normalise_static_class_b_parts():
    a = ais.normalise_static(static_b_part_a())
    assert a["name"] == "BAY FISHER" and a["ship_type"] is None and np.isnan(a["length_m"]) and a["msg_type"] == "StaticDataReportA"
    b = ais.normalise_static(static_b_part_b())
    assert b["callsign"] == "BF1" and b["ship_type"] == 30 and b["ship_type_label"] == "fishing"
    assert b["length_m"] == 12 and b["width_m"] == 4 and b["msg_type"] == "StaticDataReportB"


def test_ship_type_labels():
    assert ais.ship_type_label(30) == "fishing" and ais.ship_type_label(52) == "tug" and ais.ship_type_label(89) == "tanker"
    assert ais.ship_type_label(0) is None and ais.ship_type_label(None) is None and ais.ship_type_label(float("nan")) is None
    assert ais.ship_type_label(100) is None and ais.ship_type_label(10) == "reserved"


def test_latest_static_merges_parts():
    st = ais.static_frame([ais.normalise_static(static_b_part_a()), ais.normalise_static(static_b_part_b(t="2026-10-08 03:05:00 +0000 UTC"))])
    ls = ais.latest_static(st)
    assert len(ls) == 1
    row = ls.iloc[0]
    assert row["name"] == "BAY FISHER" and row["callsign"] == "BF1" and row["ship_type"] == 30 and row["length_m"] == 12


def test_frames_dedupe_and_types():
    rows = [ais.normalise_position(pos_a()), ais.normalise_position(pos_a()), ais.normalise_position(pos_b())]
    df = ais.positions_frame(rows)
    assert len(df) == 2
    assert str(df.mmsi.dtype) == "int64" and str(df.timestamp.dtype).startswith("datetime64") and df.timestamp.dt.tz is not None
    assert str(df.nav_status.dtype) == "Int16" and df.nav_status.isna().sum() == 1
    empty = ais.positions_frame([])
    assert list(empty.columns) == ais.POSITION_COLUMNS and len(empty) == 0


def test_redact_hides_keys():
    s = ais.redact("Authorization: Bearer abcdef0123456789abcdef0123456789abcdef and APIKey=\"0123456789abcdef0123456789abcdef\"")
    assert "abcdef0123456789" not in s and "<redacted>" in s


# ---------------------------------------------------------------------------------------------------------------
# Recorder storage
# ---------------------------------------------------------------------------------------------------------------

def test_recorder_flush_round_trip(tmp_path):
    rec = ais.Recorder(root=tmp_path, keep_raw=True)
    frames = [pos_a(), pos_a(), pos_b(), pos_b_ext(t="2026-10-08 04:00:01 +0000 UTC"), pos_long(), static_a(), static_b_part_a(),
              {"MessageType": "SubscriptionConfirmation", "Message": {"CompressionEnabled": True}}]
    kinds = [rec.handle(json.dumps(f).encode("utf-8")) for f in frames]  # binary frames, as the service sends them
    assert kinds == ["position", "position", "position", "position", "position", "static", "static", "other"]
    assert rec.handle("not json") == "bad" and rec.handle(json.dumps({"error": "Api Key Is Not Valid"})) == "error"
    assert rec.stats["last_error"] == "Api Key Is Not Valid"
    w = rec.flush()
    assert w["positions"] == 5 and w["static"] == 2 and w["raw"] == 8
    p03 = pd.read_parquet(tmp_path / "positions" / "20261008" / "03.parquet")
    p04 = pd.read_parquet(tmp_path / "positions" / "20261008" / "04.parquet")
    assert len(p03) == 3 and len(p04) == 1  # duplicate dropped, partitions by hour of time_utc
    st = pd.read_parquet(tmp_path / "static" / "20261008.parquet")
    assert len(st) == 2 and set(st.mmsi) == {574001234, 412000001}
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["positions"] == 5 and status["distinct_mmsi"] == 4 and "caveat" in status
    # a second flush with a later message appends to the same hour partition and keeps the earlier rows
    rec.handle(json.dumps(pos_a(t="2026-10-08 03:30:00 +0000 UTC")))
    rec.flush()
    p03b = pd.read_parquet(tmp_path / "positions" / "20261008" / "03.parquet")
    assert len(p03b) == 4 and p03b.timestamp.is_monotonic_increasing
    # load helpers
    allpos = ais.load_positions(tmp_path)
    assert len(allpos) == 5 and allpos.mmsi.nunique() == 4  # 3 + 1 in hour 03, 1 in hour 04
    row = allpos[allpos.mmsi == 412000001].iloc[0]
    assert row.ais_class == "B" and row.ship_name is None and pd.isna(row.nav_status)  # rows stay intact through typing
    assert len(ais.list_partitions(tmp_path)) == 2
    summ = ais.summarise(allpos, ais.load_static(tmp_path))
    assert summ["mmsi_count"] == 4 and summ["mmsi_by_class"] == {"A": 2, "B": 2} and summ["hours_recorded"] == 2
    assert summ["top_ship_types"] == {"cargo": 1}


def test_append_parquet_recovers_from_torn_file(tmp_path):
    path = tmp_path / "positions" / "20261008" / "03.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"PAR1 this is not a parquet file")
    n = ais.append_parquet(path, ais.positions_frame([ais.normalise_position(pos_a())]), ais.positions_frame)
    assert n == 1 and len(pd.read_parquet(path)) == 1


# ---------------------------------------------------------------------------------------------------------------
# Reach statistics
# ---------------------------------------------------------------------------------------------------------------

def test_reach_grids_share_and_mmsi():
    # 1 degree grid, 4 columns x 3 rows, origin 100E 13N
    transform = from_origin(100.0, 13.0, 1.0, 1.0)
    shape = (3, 4)
    hours = pd.date_range("2026-10-08 00:00", periods=4, freq="h", tz="UTC")
    rows = []
    # cell (row 0, col 0): two MMSI, heard in 4 of 4 hours
    for h in hours:
        rows.append({"mmsi": 1, "timestamp": h + pd.Timedelta(minutes=5), "lon": 100.5, "lat": 12.5})
        rows.append({"mmsi": 2, "timestamp": h + pd.Timedelta(minutes=7), "lon": 100.6, "lat": 12.4})
    # cell (row 2, col 3): one MMSI, heard in 1 of 4 hours (three messages in the same hour count once)
    for m in range(3):
        rows.append({"mmsi": 3, "timestamp": hours[1] + pd.Timedelta(minutes=m), "lon": 103.5, "lat": 10.5})
    # outside the grid: ignored
    rows.append({"mmsi": 4, "timestamp": hours[0], "lon": 120.0, "lat": 0.0})
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df.timestamp, utc=True)
    share, mmsi, hrs = ais.reach_grids(df, transform, shape)
    assert len(hrs) == 4
    assert share[0, 0] == pytest.approx(1.0) and mmsi[0, 0] == 2
    assert share[2, 3] == pytest.approx(0.25) and mmsi[2, 3] == 1
    assert share.sum() == pytest.approx(1.25) and mmsi.sum() == 3
    # empty input gives zero grids, no crash
    share0, mmsi0, hrs0 = ais.reach_grids(ais.positions_frame([]), transform, shape)
    assert share0.shape == shape and share0.sum() == 0 and len(hrs0) == 0


# ---------------------------------------------------------------------------------------------------------------
# dtype round trips, old partitions, atomic storage
# ---------------------------------------------------------------------------------------------------------------

def test_text_columns_are_none_not_nan_everywhere(tmp_path):
    rows = [ais.normalise_position(pos_b()), ais.normalise_position(pos_a())]
    df = ais.positions_frame(rows)
    assert df.ship_name.dtype == object and df.ship_name.iloc[0] is None or df.ship_name.iloc[1] is None
    assert [type(v) for v in df.ship_name] in ([type(None), str], [str, type(None)])
    st = ais.static_frame([ais.normalise_static(static_b_part_a())])
    assert st.callsign.iloc[0] is None and st.destination.iloc[0] is None and st["name"].iloc[0] == "BAY FISHER"
    ls = ais.latest_static(st)
    assert ls.callsign.iloc[0] is None and ls.ship_type_label.iloc[0] is None
    assert ais.as_text(pd.Series(["x", np.nan, None, pd.NA, ""])).tolist() == ["x", None, None, None, None]


def test_old_partitions_with_pandas_str_dtype_read_back_as_none(tmp_path):
    # a partition as the first recorder wrote it: pandas str columns (NaN for missing), large_string on disk
    old = pd.DataFrame({"mmsi": [1, 2], "timestamp": pd.to_datetime(["2026-10-08 14:31:00", "2026-10-08 14:32:00"], utc=True),
                        "lon": [110.0, 111.0], "lat": [10.0, 11.0], "sog_kn": np.array([1.0, np.nan], "float32"),
                        "cog_deg": np.array([5.0, np.nan], "float32"), "heading": np.array([np.nan, np.nan], "float32"),
                        "nav_status": pd.array([0, None], dtype="Int16"), "msg_type": ["PositionReport", "StandardClassBPositionReport"],
                        "msg_id": pd.array([1, 18], dtype="Int16"), "ais_class": ["A", "B"], "ship_name": pd.Series(["OLD", None], dtype="str")})
    path = tmp_path / "positions" / "20261008" / "14.parquet"
    path.parent.mkdir(parents=True)
    old.to_parquet(path, index=False)
    back = ais.load_positions(tmp_path)
    assert back.ship_name.tolist() == ["OLD", None] and back.ship_name.dtype == object
    assert str(back.timestamp.dtype) == "datetime64[us, UTC]" and str(back.nav_status.dtype) == "Int16"
    # appending new rows to the old partition keeps both and writes the fixed schema
    n = ais.append_parquet(path, ais.positions_frame([ais.normalise_position(pos_a(t="2026-10-08 14:40:00 +0000 UTC"))]),
                           ais.positions_frame)
    assert n == 3 and pq.read_schema(path).equals(ais.POSITION_SCHEMA, check_metadata=False)


def test_partition_schema_is_identical_across_flushes(tmp_path):
    rec = ais.Recorder(root=tmp_path, keep_raw=False)
    rec.handle(json.dumps(pos_b()))  # hour 03: only a nameless class B row (all ship_name None)
    rec.handle(json.dumps(pos_a(t="2026-10-08 04:10:00 +0000 UTC")))
    rec.handle(json.dumps(static_b_part_a()))
    rec.flush()
    s03 = pq.read_schema(tmp_path / "positions" / "20261008" / "03.parquet")
    s04 = pq.read_schema(tmp_path / "positions" / "20261008" / "04.parquet")
    assert s03.equals(s04, check_metadata=False) and s03.equals(ais.POSITION_SCHEMA, check_metadata=False)
    assert pq.read_schema(tmp_path / "static" / "20261008.parquet").equals(ais.STATIC_SCHEMA, check_metadata=False)
    assert not list(tmp_path.rglob("*.tmp*"))  # no temp files left behind


def test_torn_partition_is_moved_aside_not_lost(tmp_path):
    path = tmp_path / "positions" / "20261008" / "03.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"PAR1 torn")
    ais.append_parquet(path, ais.positions_frame([ais.normalise_position(pos_a())]), ais.positions_frame)
    aside = list(path.parent.glob("03.parquet.corrupt-*"))
    assert len(aside) == 1 and aside[0].read_bytes() == b"PAR1 torn"
    assert ais.list_partitions(tmp_path) == [path]  # the quarantined file is not read as a partition


def test_flush_failure_keeps_rows_for_the_next_flush(tmp_path):
    rec = ais.Recorder(root=tmp_path, keep_raw=False)
    rec.handle(json.dumps(pos_a()))
    (tmp_path / "positions").write_text("not a directory")  # makes the partition write fail
    w = rec.flush()
    assert w["positions"] == 0 and w["failures"] and rec.stats["flush_errors"] == 1
    assert json.loads((tmp_path / "status.json").read_text())["flush_errors"] == 1
    (tmp_path / "positions").unlink()
    w = rec.flush()
    assert w["positions"] == 1 and len(ais.load_positions(tmp_path)) == 1


def test_read_raw_stops_at_a_truncated_member(tmp_path):
    path = tmp_path / "03.jsonl.gz"
    with gzip.open(path, "ab") as fh:
        fh.write(b'{"a": 1}\n{"a": 2}\n')
    with gzip.open(path, "ab") as fh:
        fh.write(b'{"a": 3}\n')
    full = path.read_bytes()
    assert ais.read_raw(path) == ['{"a": 1}', '{"a": 2}', '{"a": 3}']
    member = gzip.compress(b'{"a": 4}\n{"a": 5}\n' * 50)
    path.write_bytes(full + member[: len(member) // 2])  # a kill in the middle of the third flush
    lines = ais.read_raw(path)
    assert lines[:3] == ['{"a": 1}', '{"a": 2}', '{"a": 3}'] and all(json.loads(x) for x in lines)


# ---------------------------------------------------------------------------------------------------------------
# Time, redaction, handler robustness
# ---------------------------------------------------------------------------------------------------------------

def test_parse_time_utc_applies_offsets():
    assert ais.parse_time_utc("2026-10-08 10:00:00 +0700") == pd.Timestamp("2026-10-08 03:00:00", tz="UTC")
    assert ais.parse_time_utc("2026-10-08T03:00:00Z") == pd.Timestamp("2026-10-08 03:00:00", tz="UTC")
    assert ais.parse_time_utc("2026-10-08 03:00:00 -05:30") == pd.Timestamp("2026-10-08 08:30:00", tz="UTC")
    assert ais.hour_key(pd.Timestamp("2026-10-08 23:59:59", tz="Asia/Ho_Chi_Minh")) == ("20261008", "16")


def test_registered_secret_is_redacted_everywhere():
    secret = "Zq9-not-hex-but-secret-value"
    ais.register_secret(secret)
    assert secret not in ais.redact(f"failed with key {secret} in the frame")
    assert "<redacted>" in ais.redact(f"x{secret}y")
    sub = ais.subscription(secret)
    assert secret in sub and secret not in ais.redact(sub)  # the subscription carries the key; logs never do


def test_handler_never_raises_and_skewed_clocks_stay_raw_only(tmp_path):
    rec = ais.Recorder(root=tmp_path, keep_raw=True, max_clock_skew=pd.Timedelta(hours=1))
    now = pd.Timestamp.now(tz="UTC")
    bad = pos_a(t=now.strftime("%Y-%m-%d %H:%M:%S.%f +0000 UTC"))
    bad["Message"]["PositionReport"] = "not a dict"
    assert rec.handle(json.dumps(bad)) == "bad" and rec.stats["handler_errors"] == 1
    old = pos_a(t="2001-01-01 00:00:00 +0000 UTC")
    assert rec.handle(json.dumps(old)) == "other" and rec.stats["clock_skew_dropped"] == 1
    fresh = pos_a(t=now.strftime("%Y-%m-%d %H:%M:%S.%f +0000 UTC"))
    assert rec.handle(json.dumps(fresh)) == "position"
    rec.flush()
    assert len(ais.load_positions(tmp_path)) == 1 and not (tmp_path / "positions" / "20010101").exists()
    raw = [line for p in sorted((tmp_path / "raw").rglob("*.jsonl.gz")) for line in ais.read_raw(p)]
    assert len(raw) == 3  # every frame is in the raw log, filed under the receive hour


# ---------------------------------------------------------------------------------------------------------------
# Reconnect loop (fake websocket)
# ---------------------------------------------------------------------------------------------------------------

class _FakeWS:
    def __init__(self, frames, stop, then_raise=None):
        self.frames, self.stop, self.then_raise, self.sent = list(frames), stop, then_raise, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, text):
        self.sent.append(text)

    async def recv(self):
        if self.frames:
            return self.frames.pop(0)
        if self.then_raise:
            raise self.then_raise
        self.stop.set()  # the test is over: stop, then drop the socket
        raise ConnectionError("closed by the test")


def test_stream_retries_forever_and_logs_each_attempt(monkeypatch):
    monkeypatch.setattr(ais, "backoff_wait", lambda attempt, base_s=1.0, max_s=60.0, rng=None: 0.0)
    stop = asyncio.Event()
    secret = "0123456789abcdef0123456789abcdef01234567"
    attempts = []

    def connect():
        attempts.append(len(attempts) + 1)
        n = len(attempts)
        if n <= 3:  # proxy refusing, then a dropped socket
            raise ConnectionRefusedError(111, f"Connect call failed ('127.0.0.1', 39767) key={secret}")
        if n == 4:
            return _FakeWS([json.dumps({"MessageType": "SubscriptionConfirmation"})], stop,
                           then_raise=ConnectionError("no close frame received or sent"))
        return _FakeWS([json.dumps(pos_a())], stop)

    logs, frames, downs = [], [], []
    asyncio.run(asyncio.wait_for(ais.stream(secret, lambda raw: frames.append(raw) or "position", stop=stop,
                                            log=logs.append, on_disconnect=downs.append, connect=connect), timeout=10))
    assert len(attempts) == 5 and len(frames) == 2 and len(downs) == 4
    failed = [m for m in logs if "failed, retry" in m]
    # three refusals count up; the good frame on connection 4 resets the count, so its drop is attempt 1 again
    assert [m.split("attempt ")[1].split(" ")[0] for m in failed] == ["1", "2", "3", "1"]
    assert any("connecting, attempt 4" in m for m in logs) and any("connecting, attempt 2" in m for m in logs)
    assert not any(secret in m for m in logs + downs)  # the key never reaches a log line


def test_stream_stops_on_key_rejection():
    stop = asyncio.Event()
    err = json.dumps({"error": "Api Key Is Not Valid"})
    downs = []
    asyncio.run(asyncio.wait_for(ais.stream("k" * 40, lambda raw: "error", stop=stop, log=lambda m: None,
                                            on_disconnect=downs.append, connect=lambda: _FakeWS([err], stop)), timeout=5))
    assert stop.is_set() and ais.is_key_rejection(downs[-1])


def test_backoff_wait_is_capped_with_jitter():
    vals = [ais.backoff_wait(k, rng=lambda: 0.5) for k in (1, 2, 3, 6, 7, 20)]
    assert vals == [1.0, 2.0, 4.0, 32.0, 60.0, 60.0]
    assert 0.7 <= ais.backoff_wait(1, rng=lambda: 0.0) <= 1.3 and ais.backoff_wait(30, rng=lambda: 1.0) == pytest.approx(78.0)


def test_gear_beacon_heuristic():
    assert ais.gear_beacon_like("NET-82542-84%", 412345678) and ais.gear_beacon_like("BUOY_MERAH_10-99%", 412345678)
    assert ais.gear_beacon_like("P6LXUNOIQDAT A18-82%", 412345678) and ais.gear_beacon_like("SOMETHING", 5631101)
    assert not ais.gear_beacon_like("THE VENETIAN", 477000001) and not ais.gear_beacon_like("NETHERLANDS STAR", 244000001)
    assert not ais.gear_beacon_like(None, 574001234) and not ais.gear_beacon_like("TEST VESSEL", 574001234)
