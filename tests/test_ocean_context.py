"""Ocean context per object on synthetic grids and tables (no network, no real data)."""

import datetime as dt
import inspect

import numpy as np
import pandas as pd
import pytest
from rasterio.transform import from_origin

from darkvessel.ocean import context as cx


def _objects():
    t = pd.to_datetime(["2026-09-20T10:48:16Z", "2026-09-20T10:48:16Z", "2026-09-21T22:30:00Z", "2026-09-20T18:05:00Z"], utc=True)
    return pd.DataFrame({"object_type": ["radar", "radar", "radar", "viirs"], "object_id": ["a", "b", "c", "L1"],
                         "group": ["high", "clutter_zone", "medium", "lit_vessel_candidate_clear"], "time_utc": t,
                         "lat": [10.05, 10.15, 10.25, 10.35], "lon": [105.05, 105.15, 105.25, 105.35],
                         "length_est_m": [40.0, 20.0, 80.0, np.nan], "mission": ["S1D", "S1D", "S1C", "NOAA-20"],
                         "day": t.strftime("%Y-%m-%d")})


def _write_cog(path, arr, transform):
    import rasterio

    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1, dtype="float32",
                       crs="EPSG:4326", transform=transform, nodata=-9999.0) as ds:
        ds.write(arr.astype(np.float32), 1)
        ds.update_tags(version="test", licence="none", access_date="2026-10-08")


TR = from_origin(105.0, 10.5, 0.1, 0.1)    # 5 x 5 cells, 0.1 degree


def _static_rasters(tmp_path, dens):
    depth = np.full((5, 5), 50.0)
    depth[0, :] = np.nan                        # row 0 (10.4 to 10.5 N) is land
    coast = np.full((5, 5), 12.5)
    layers = {"depth_m": depth, "dist_coast_km": coast, "dist_port_km": coast * 10}
    for t in cx.SHIP_TYPES:
        layers[f"ship_density_{t}"] = dens if t in ("all", "fishing") else np.zeros((5, 5))
    for name, a in layers.items():
        _write_cog(tmp_path / f"{name}_4326.tif", np.where(np.isfinite(a), a, -9999.0), TR)


def _density():
    dens = np.zeros((5, 5))
    dens[0, :] = np.nan                         # the shipping layers are NaN on land
    dens[4, 0] = 10_711_823.0                   # object a: an implausible published value (docs/ocean_context.md 3.4)
    dens[3, 1] = 1e-3                           # object b: a tiny value is still presence
    return dens


def test_static_context_reads_presence_only(tmp_path):
    _static_rasters(tmp_path, _density())
    obj = _objects()
    obj.loc[3, ["lat", "lon"]] = [10.45, 105.05]           # the light on the land row: unknown everywhere
    obj.loc[2, ["lat", "lon"]] = [12.0, 120.0]             # outside the grid
    static, meta = cx.static_context(obj, tmp_path)
    assert static.depth_m.tolist()[:2] == [50.0, 50.0] and np.isnan(static.depth_m[3]) and np.isnan(static.depth_m[2])
    assert static.in_aoi_grid.tolist() == [True, True, False, True]
    assert static.ship_presence_all.tolist()[:2] == [True, True]
    assert pd.isna(static.ship_presence_all[2]) and pd.isna(static.ship_presence_all[3])
    assert static.ship_presence_commercial.tolist()[:2] == [False, False]
    assert str(static.ship_presence_fishing.dtype) == "boolean"
    obj.loc[1, ["lat", "lon"]] = [10.25, 105.35]           # a sea cell holding 0: no presence
    static2, _ = cx.static_context(obj, tmp_path)
    assert static2.ship_presence_all[1] == False  # noqa: E712
    assert meta["layers"]["depth_m"]["version"] == "test" and meta["presence_rule"] == cx.PRESENCE_RULE
    assert meta["layers"]["ship_presence_all"]["read_as"] == "presence (value > 0) only"


def test_no_shipping_magnitude_threshold_can_return(tmp_path):
    """Guard for board decision D4.3: the output must not change under any rescaling of the positive shipping values.

    A lane rule, a percentile, a rank or a log of the published magnitudes would change when the positive values are
    multiplied by random factors; presence (value > 0) does not. The field names must not imply counts or intensity."""
    rng = np.random.default_rng(0)
    obj = pd.concat([_objects()] * 30, ignore_index=True)
    obj["lat"] = rng.uniform(10.0, 10.4, len(obj))
    obj["lon"] = rng.uniform(105.0, 105.5, len(obj))
    dens = np.where(rng.uniform(size=(5, 5)) < 0.5, 0.0, rng.lognormal(3, 4, (5, 5)))
    dens[0, :] = np.nan
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    _static_rasters(a, dens)
    _static_rasters(b, np.where(dens > 0, dens * rng.uniform(1e-4, 1e4, dens.shape), dens))
    sa, ma = cx.static_context(obj, a)
    sb, _ = cx.static_context(obj, b)
    pd.testing.assert_frame_equal(sa, sb)
    assert sa.ship_presence_all.any() and not sa.ship_presence_all.dropna().all()
    banned = ("density", "lane", "intensity", "count", "busy", "traffic")
    assert not [c for c in sa.columns if any(w in c for w in banned)]
    assert not [c for c in sa.columns if c.startswith("ship_") and str(sa[c].dtype) != "boolean"]
    assert not hasattr(cx, "LANE_RULE") and not hasattr(cx, "lane_threshold") and not hasattr(cx, "LANE_QUANTILE")
    src = inspect.getsource(cx.static_context) + inspect.getsource(cx.presence)
    assert "quantile" not in src and "percentile" not in src and "argsort" not in src
    assert "above 0" in cx.PRESENCE_RULE and "no magnitude" in cx.PRESENCE_RULE
    assert set(cx.PRESENCE_FIELDS) == {f"ship_presence_{t}" for t in cx.SHIP_TYPES}
    assert "density" not in " ".join(ma["layers"])  # metadata keyed by the presence field names
    assert cx.presence([0.0, 1e-9, 5.0, np.nan]).tolist() == [False, True, True, pd.NA]


def test_cell_keys_follow_the_model_grid():
    tr = from_origin(99.0, 24.0, 0.25, 0.25)
    k = cx.cell_keys(np.array([99.1, 105.3, 130.0]), np.array([23.9, 10.1, 10.0]), tr, (94, 109))
    assert k.cell_id.tolist()[:2] == ["r0c0", "r55c25"] and pd.isna(k.cell_id[2])
    assert k.region.tolist() == ["other", "South Vietnam shelf", "other"]


def test_nearest_time_within_window_only():
    av = [pd.Timestamp("2026-09-20T06:00Z"), pd.Timestamp("2026-09-20T18:00Z")]
    assert cx.nearest_time(pd.Timestamp("2026-09-20T11:00Z"), av, 12) == av[0]
    assert cx.nearest_time(pd.Timestamp("2026-09-20T13:00Z"), av, 12) == av[1]
    assert cx.nearest_time(pd.Timestamp("2026-09-22T13:00Z"), av, 12) is None
    assert cx.nearest_time(pd.Timestamp("2026-09-20T13:00Z"), [], 12) is None
    # the RTOFS window is about the hourly nowcasts: a 10:48 radar pass does not take the 18 UTC field
    assert cx.nearest_time(pd.Timestamp("2026-09-20T11:00Z"), av[1:], cx.MAX_HOURS_RTOFS) is None
    assert cx.MAX_HOURS_RTOFS <= 3 and cx.MAX_HOURS_WAVE <= 3


def test_cached_times_parse_file_names(tmp_path):
    for n in ("rtofs_20260929T18.npz", "rtofs_20260930T06.npz", "rtofs_grid.npz", "junk.txt"):
        (tmp_path / n).write_bytes(b"")
    ts = cx.cached_times(tmp_path, r"rtofs_(\d{8}T\d{2})\.npz", "%Y%m%dT%H")
    assert ts == [pd.Timestamp("2026-09-29T18:00Z"), pd.Timestamp("2026-09-30T06:00Z")]


def test_nearest_valid_index_skips_land_cells_and_far_points():
    glon = np.array([[105.0, 105.0833, 105.1667]])
    glat = np.array([[10.0, 10.0, 10.0]])
    sea = np.array([False, True, True])                       # the first native cell is land
    j = cx.nearest_valid_index(glon, glat, sea, [105.0, 105.17, 106.0], [10.0, 10.0, 10.0], max_km=10.0)
    assert j.tolist() == [1, 2, -1]                          # 9.1 km to the sea cell; 90 km is too far
    assert cx.nearest_valid_index(glon, glat, np.zeros(3, bool), [105.0], [10.0]).tolist() == [-1]


def test_daily_context_with_injected_loaders():
    obj = _objects()
    tr = TR
    sst = np.full((5, 5), 28.0, np.float32)
    sst[4, 0] = 29.5                                       # object a's cell
    grad = np.zeros((5, 5), np.float32)
    grad[3, 1] = 0.2                                       # object b's cell
    mask = np.zeros((5, 5), bool)
    mask[4, 0] = True                                      # the front pixel is object a's cell centre (0 km away)
    chl = np.full((5, 5), 0.5, np.float32)                 # log10 mg m-3
    rt = {"speed": np.array([[0.1, 0.4, np.nan]], np.float32), "mixed_layer_thickness": np.array([[12.0, 30.0, np.nan]], np.float32),
          "valid_time": "2026-09-20T11:00:00+00:00"}
    wave_tr = from_origin(-0.125, 90.125, 0.25, 0.25)
    hs = np.full((721, 1440), 1.5, np.float32)
    hs[319, 421] = np.nan                                   # one of the four cells around objects a and b is land
    hs[320, 421] = 2.5
    calls = {"fetch": 0, "sea": None}

    def rtofs_index(lon, lat, sea):
        calls["sea"] = sea.tolist()
        return np.array([0, 1, 1, -1])                      # the light has no RTOFS sea cell within reach

    loaders = {
        "sst_meta": lambda day: ({"sst": sst, "valid_time": f"{day}T09:00:00Z", "source": "mur", "dataset": "jplMURSST41"}
                                 if day == "2026-09-20" else (_ for _ in ()).throw(FileNotFoundError(day))),
        "sst_transform": lambda: tr,
        "grad": lambda day: (grad, tr), "mask": lambda day: (mask, tr, {"low": 0.05, "high": 0.1}),
        "chl": lambda day: (chl, tr, "testchl") if day == "2026-09-20" else None,
        "rtofs_times": lambda: [pd.Timestamp("2026-09-20T11:00Z"), pd.Timestamp("2026-09-20T18:00Z")], "rtofs": lambda t: rt,
        "rtofs_index": rtofs_index,
        "wave_times": lambda: {pd.Timestamp("2026-09-20T11:00Z"): "w1", pd.Timestamp("2026-09-20T18:00Z"): "w2"},
        "wave": lambda p: (hs, wave_tr, dt.datetime(2026, 9, 20, 11 if p == "w1" else 18, tzinfo=dt.timezone.utc)),
        "fetch_rtofs": lambda t: calls.__setitem__("fetch", calls["fetch"] + 1), "fetch_wave": lambda t: None,
    }
    out, meta = cx.daily_context(obj, fetch=False, log=lambda m: None, loaders=loaders)
    assert out.sst_c.tolist()[:2] == [29.5, 28.0] and np.isnan(out.sst_c[2])          # day 21 has no SST cache
    assert out.sst_time[0] == "2026-09-20T09:00:00Z" and out.sst_source[0] == "mur"
    assert out.sst_grad[1] == pytest.approx(0.2) and out.sst_grad[0] == 0.0
    assert out.dist_front_km[0] == pytest.approx(0.0, abs=1e-3) and out.dist_front_km[1] > 10
    assert out.chl_log10[0] == pytest.approx(0.5) and out.chl_dataset[0] == "testchl" and pd.isna(out.chl_log10[2])
    assert out.current_speed_ms.tolist()[:2] == [pytest.approx(0.1), pytest.approx(0.4)] and out.mld_m[1] == 30.0
    assert calls["sea"] == [True, True, False]                                         # the land mask comes from the field
    assert np.isnan(out.current_speed_ms[2])                                            # 2026-09-21 22 UTC: nothing within 2 h, no fetch
    assert np.isnan(out.current_speed_ms[3]) and out.current_time[3] is None            # no sea cell near the light
    assert out.current_time[0] == "2026-09-20T11:00:00+00:00" and calls["fetch"] == 0
    # bilinear with the land neighbour dropped (as in the daily cell table), not the containing cell
    ref = cx.bilinear(hs, wave_tr, obj.lon[:2].to_numpy(), obj.lat[:2].to_numpy(), wrap_lon=True)
    assert np.isfinite(ref).all() and out.wave_hs_m[:2].tolist() == pytest.approx(ref.tolist())
    assert 1.5 < out.wave_hs_m[0] < 2.5 and out.wave_time[0].startswith("2026-09-20T11")
    assert out.wave_time[3].startswith("2026-09-20T18") and np.isnan(out.wave_hs_m[2])
    assert meta["front_thresholds"]["2026-09-20"] == {"low": 0.05, "high": 0.1}
    assert any(m.startswith("sst 2026-09-21") for m in meta["missing"])
    assert meta["rtofs_hours_used"] == ["2026-09-20T11:00:00+00:00"]
    # with fetch the missing hour is pulled
    out2, meta2 = cx.daily_context(obj, fetch=True, log=lambda m: None, loaders=loaders)
    assert calls["fetch"] == 1 and meta2["rtofs_hours_fetched"] == ["2026-09-21T22:00:00+00:00"]


def test_shrink_summarise_and_fill_rates_report_denominators():
    obj = _objects()
    obj["depth_m"] = [20.0, 60.0, np.nan, 300.0]
    obj["dist_coast_km"] = [5.0, 30.0, 10.0, 200.0]
    obj["dist_front_km"] = [2.0, 50.0, np.nan, 8.0]
    obj["ship_presence_all"] = pd.array([True, False, None, False], dtype="boolean")
    obj["sst_source"] = ["mur", "mur", None, "mur"]
    obj["cell_id"] = ["r1c1", "r1c1", "r1c2", "r2c2"]
    small = cx.shrink(obj)
    assert small.depth_m.dtype == np.float32 and small.lat.dtype == np.float32
    assert str(small.group.dtype) == "category" and not isinstance(small.object_id.dtype, pd.CategoricalDtype)
    assert not isinstance(small.cell_id.dtype, pd.CategoricalDtype)
    rows = {(r["object_type"], r["group"]): r for r in cx.summarise(small)}
    hi = rows[("radar", "high")]
    assert hi["n"] == 1 and hi["depth_m_median"] == 20.0 and hi["depth_m_n"] == 1
    assert hi["ship_presence_all_share"] == 1.0 and hi["ship_presence_all_n"] == 1 and hi["within_10km_of_front_share"] == 1.0
    med = rows[("radar", "medium")]
    assert med["depth_m_median"] is None and med["depth_m_n"] == 0 and med["ship_presence_all_n"] == 0
    assert med["ship_presence_all_share"] is None
    assert rows[("viirs", "lit_vessel_candidate_clear")]["shallower_than_200m_share"] == 0.0
    fill = cx.fill_rates(small, ["depth_m", "ship_presence_all", "absent"])
    assert fill["depth_m"]["all"] == {"n": 4, "filled": 3, "share": 0.75}
    assert fill["depth_m"]["radar"] == {"n": 3, "filled": 2, "share": 0.6667} and fill["ship_presence_all"]["viirs"]["filled"] == 1
    assert "absent" not in fill


def test_group_labels_use_low_reason_for_low_objects():
    conf = pd.Series(["high", "low", "low", "fixed"])
    reason = pd.Series(["", "clutter_zone", None, ""])
    assert cx._group_from_classes(conf, reason).tolist() == ["high", "clutter_zone", "low", "fixed"]
    assert cx._group_from_classes(conf, None).tolist() == ["high", "low", "low", "fixed"]


def test_gfw_table_normalisation_and_fishing_bins():
    df = pd.DataFrame({"Lat": [10.0, 10.1], "Lon": [105.0, 105.1], "timestamp": ["2026-09-20 10:48:00 UTC", "2026-09-20T10:49:00Z"],
                       "fishing_score": [0.95, 0.05], "matched_category": ["matched_fishing", "unmatched"], "length_m": [45.0, 18.0]})
    g = cx.normalise_gfw_table(df)
    assert list(g.lat) == [10.0, 10.1] and g.time_utc.dt.tz is not None
    assert g.fishing_class.tolist() == ["likely_fishing", "likely_non_fishing"]
    assert g.matched.tolist() == [True, False] and (g.detections == 1).all()
    assert cx.gfw_fishing_class(pd.Series([0.1, 0.5, 0.9, np.nan])).tolist() == ["likely_non_fishing", "unknown", "likely_fishing", None]
    with pytest.raises(ValueError):
        cx.normalise_gfw_table(pd.DataFrame({"x": [1]}))


def test_gfw_point_comparison_matches_same_pass_within_radius():
    ours = _objects()
    ours = ours[ours.object_type == "radar"].reset_index(drop=True)
    gfw = cx.normalise_gfw_table(pd.DataFrame({
        "lat": [10.05 + 0.001, 10.15, 10.25 + 0.02, 11.0], "lon": [105.05, 105.15, 105.25, 106.0],
        "timestamp": ["2026-09-20T10:48:00Z", "2026-09-22T10:48:00Z", "2026-09-21T22:31:00Z", "2026-09-20T10:48:00Z"],
        "fishing_score": [0.95, 0.5, 0.02, 0.5], "matched": ["false", "true", "true", "true"], "length_m": [50.0, 20.0, 80.0, 90.0]}))
    per, s = cx.gfw_compare_points(ours, gfw, radius_m=500.0, max_hours=1.0)
    # a: GFW detection 111 m north on the same pass -> hit; b: GFW point two days later -> no hit;
    # c: GFW point 2.2 km away -> too far
    assert per.gfw_within_radius.tolist() == [True, False, False]
    assert per.gfw_dist_m[0] == pytest.approx(111.2, rel=0.02) and per.gfw_fishing_class[0] == "likely_fishing"
    assert per.gfw_matched[0] is np.False_ or per.gfw_matched[0] == False  # noqa: E712
    assert s["gfw_detections"] == 4 and s["gfw_detections_with_one_of_ours_within_radius"] == 1
    hi = next(r for r in s["by_group"] if r["group"] == "high")
    assert hi["with_gfw_detection_share"] == 1.0 and hi["length_ratio_ours_over_gfw_median"] == pytest.approx(0.8)
    assert s["gfw_with_one_of_ours_by_fishing_class"]["likely_fishing"]["share_with_ours"] == 1.0


def test_gfw_cell_comparison_counts_by_cell_and_day():
    ours = _objects()
    ours = ours[ours.object_type == "radar"].reset_index(drop=True)
    gfw = pd.DataFrame({"lat": [10.05, 10.05, 10.25, 12.0], "lon": [105.05, 105.05, 105.25, 110.0],
                        "time_utc": pd.to_datetime(["2026-09-20T10:00Z"] * 2 + ["2026-09-21T22:00Z", "2026-09-20T10:00Z"], utc=True),
                        "detections": [1, 1, 3, 2]})
    r = cx.gfw_compare_cells(ours, gfw, res_deg=0.1, by_day=True)
    assert r["cells_both"] == 2 and r["cells_ours_only"] == 1 and r["cells_gfw_only"] == 1
    assert r["ours_total"] == 3 and r["gfw_total"] == 7
    r2 = cx.gfw_compare_cells(ours, gfw, res_deg=1.0, by_day=False)
    assert r2["cells"] == 2 and r2["cells_both"] == 1


def test_gfw_request_carries_no_token_and_fetch_needs_env(monkeypatch):
    req = cx.gfw_report_request({"type": "Polygon", "coordinates": [[[105, 10], [106, 10], [106, 11], [105, 10]]]},
                                "2026-09-20", "2026-10-02", filters="matched='false'")
    assert req["url"].endswith("/4wings/report") and req["params"]["datasets[0]"] == cx.GFW_SAR_DATASET
    assert req["params"]["date-range"] == "2026-09-20,2026-10-02" and req["params"]["filters[0]"] == "matched='false'"
    assert "Authorization" not in str(req)
    monkeypatch.delenv("GFW_API_TOKEN", raising=False)
    assert cx.gfw_fetch_report(req) is None
    rows = cx.gfw_rows_from_report({"entries": [{cx.GFW_SAR_DATASET: [{"lat": 1.0, "lon": 2.0, "date": "2026-09-20", "detections": 3}]}]})
    assert rows.iloc[0].to_dict() == {"lat": 1.0, "lon": 2.0, "date": "2026-09-20", "detections": 3, "dataset": cx.GFW_SAR_DATASET}
    assert cx.normalise_gfw_table(rows).time_utc.iloc[0] == pd.Timestamp("2026-09-20", tz="UTC")


def test_gfw_cells_at_objects_joins_same_cell_date_and_hour():
    ours = _objects()
    ours = ours[ours.object_type == "radar"].reset_index(drop=True)
    # 4Wings HOURLY cells (centres) with a matched flag, and DAILY cells by neural type label
    hourly = cx.normalise_gfw_table(pd.DataFrame({
        "date": ["2026-09-20", "2026-09-20", "2026-09-20", "2026-09-21"], "hour": [10, 10, 15, 22],
        "lat": [10.055, 10.055, 10.155, 10.255], "lon": [105.055, 105.055, 105.155, 105.255],
        "detections": [2, 1, 4, 1], "matched": [False, True, False, False]}))
    assert hourly.time_utc.iloc[0] == pd.Timestamp("2026-09-20T10:00Z")
    neural = cx.normalise_gfw_table(pd.DataFrame({
        "date": ["2026-09-20", "2026-09-20", "2026-09-21"], "lat": [10.055, 10.055, 10.255], "lon": [105.055, 105.055, 105.255],
        "detections": [2, 1, 1], "neural_vessel_type": ["Likely Fishing", "Likely non-fishing", "Unknown"]}))
    assert neural.fishing_class.tolist() == ["likely_fishing", "likely_non_fishing", "unknown"]
    per, s = cx.gfw_cells_at_objects(ours, hourly, neural, res_deg=0.01, max_hours=1.0)
    # a (10:48, cell 105.05/10.05): 3 detections at 10 UTC, one matched -> hit, mixed classes
    # b (10:48, cell 105.15/10.15): GFW cell-hour is 15 UTC -> too far in time
    # c (22:30 on the 21st, cell 105.25/10.25): 1 unmatched detection at 22 UTC, class unknown
    assert per.gfw_cell_detections.tolist() == [3.0, 0.0, 1.0]
    assert per.gfw_cell_matched.tolist()[0] == True and per.gfw_cell_unmatched.tolist() == [2.0, 0.0, 1.0]  # noqa: E712
    assert per.gfw_cell_fishing_class.tolist() == ["mixed", None, "unknown"]
    assert per.gfw_in_cell_hour.tolist() == [True, False, True]
    assert s["gfw_cell_hours"] == 4 and s["gfw_cell_hours_with_one_of_ours"] == 3 and s["gfw_detections"] == 8.0
    assert s["gfw_cell_hours_matched_with_one_of_ours_share"] == 1.0
    hi = next(r for r in s["by_group"] if r["group"] == "high")
    assert hi["with_gfw_detection_same_cell_hour_share"] == 1.0 and hi["gfw_cell_has_ais_matched_share"] == 1.0
