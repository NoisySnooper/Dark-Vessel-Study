"""Daily ocean layers and the front detector on small synthetic arrays (no network)."""

import datetime as dt

import numpy as np
import pytest
from rasterio.transform import from_origin

from darkvessel.ocean import daily, fronts

KM = fronts.KM_PER_DEG


def test_mur_to_fine_is_the_mean_of_four_corner_cells_and_masks_land():
    sst = np.arange(12, dtype=float).reshape(3, 4)  # (H + 1, W + 1) MUR cells north-up
    mask = np.ones((3, 4), np.int8)  # open sea everywhere
    mask[0, 0] = 2  # land in one corner cell
    mask[2, 3] = 9  # open sea with ice (bits 1 + 8)
    fine, sea, ice = daily.mur_to_fine(sst, mask)
    assert fine.shape == (2, 3)
    assert fine[1, 1] == pytest.approx(np.mean([5, 6, 9, 10]))
    assert not sea[0, 0] and np.isnan(fine[0, 0])  # touches the land cell
    assert sea[1, 1] and sea[1, 2] and ice[1, 2] and not ice[0, 0]


def test_axis_transform_handles_descending_and_ascending_latitude():
    lat_desc, lon = np.array([10.5, 9.5, 8.5]), np.array([100.5, 101.5])
    tr, flip = daily.axis_transform(lat_desc, lon)
    assert not flip and (tr.c, tr.f, tr.a, tr.e) == pytest.approx((100.0, 11.0, 1.0, -1.0))
    tr2, flip2 = daily.axis_transform(lat_desc[::-1], lon)
    assert flip2 and tr2 == tr


def test_regrid_averages_finer_sources_and_samples_coarser_ones():
    src_tr = from_origin(100.0, 10.0, 0.5, 0.5)  # 0.5 degree source, 4 x 4
    src = np.arange(16, dtype=float).reshape(4, 4)
    dst_tr = from_origin(100.0, 10.0, 1.0, 1.0)  # 1 degree destination, 2 x 2
    out = daily.regrid(src, src_tr, dst_tr, (2, 2))
    assert out[0, 0] == pytest.approx(np.mean([0, 1, 4, 5])) and out[1, 1] == pytest.approx(np.mean([10, 11, 14, 15]))
    back = daily.regrid(out, dst_tr, src_tr, (4, 4))  # coarse to fine: containing cell
    assert back[0, 1] == out[0, 0] and back[3, 3] == out[1, 1]


def test_rtofs_nowcast_lives_in_the_next_days_folder():
    t = dt.datetime(2026, 9, 20, 18, tzinfo=dt.timezone.utc)
    assert daily.rtofs_url(t).endswith("rtofs.20260921/rtofs_glo_2ds_n018_diag.nc")
    assert daily.rtofs_path(t).name == "rtofs_20260920T18.npz"
    assert daily.night_time("2026-09-20") == t


def test_wave_key_uses_the_nearest_gfs_cycle_and_step():
    key, path = daily.wave_key(dt.datetime(2026, 9, 29, 22, 52, tzinfo=dt.timezone.utc))
    assert key == "gfs.20260929/18/wave/gridded/gfswave.t18z.global.0p25.f005.grib2"
    assert path.name == "gfswave_2026092918_f005_htsgw.grib2"
    key18, _ = daily.wave_key(dt.datetime(2026, 9, 29, 18, tzinfo=dt.timezone.utc))
    assert key18.endswith("gfswave.t18z.global.0p25.f000.grib2")


def test_metric_gradient_on_a_2d_lonlat_grid():
    lon, lat = np.meshgrid(np.arange(100, 101, 0.1), np.arange(10, 9, -0.1))
    field = 2.0 * lon  # 2 units per degree of longitude
    g = daily.metric_gradient(field, lon, lat)
    expect = 2.0 / (KM * np.cos(np.radians(lat)))
    assert np.allclose(g, expect, rtol=1e-3)


def test_sst_gradient_uses_metric_spacing_with_cos_latitude():
    tr = from_origin(105.0, 20.0, 0.01, 0.01)
    lon = tr.c + (np.arange(60) + 0.5) * tr.a
    sst = np.tile(0.5 * (lon - lon[0]), (40, 1)) + 28.0  # 0.5 degC per degree of longitude, no meridional change
    g = fronts.gradient_magnitude(sst, tr)
    lat_mid = tr.f + 20.5 * tr.e
    assert g[20, 30] == pytest.approx(0.5 / (KM * np.cos(np.radians(lat_mid))), rel=1e-3)
    assert np.all(np.isfinite(g))
    sst[10:15, 10:15] = np.nan  # land: no gradient on or next to it, no NaN leak elsewhere
    g2 = fronts.gradient_magnitude(sst, tr)
    assert np.isnan(g2[9:16, 9:16]).all() and np.isfinite(g2[20, 30])


def test_hysteresis_front_mask_and_thresholds():
    g = np.zeros((20, 20), np.float32)
    g[10, 2:18] = 0.06  # a real front, well above the high threshold
    g[11, 2:18] = 0.03  # its weak flank, kept because it touches the strong line
    g[3, 3] = 0.03  # an isolated weak pixel, dropped
    g[5, 5:8] = 0.09  # strong but inside the exclusion zone
    exclude = np.zeros_like(g, bool)
    exclude[5, :] = True
    m = fronts.front_mask(g, low=0.02, high=0.05, exclude=exclude)
    assert m[10, 2:18].all() and m[11, 2:18].all()
    assert not m[3, 3] and not m[5].any()
    lo, hi = fronts.thresholds(np.r_[np.linspace(0, 1, 1001), np.nan], 0.9, 0.97)
    assert lo == pytest.approx(0.9, abs=1e-3) and hi == pytest.approx(0.97, abs=1e-3)


def test_front_lines_and_distance():
    tr = from_origin(110.0, 10.0, 0.01, 0.01)
    m = np.zeros((50, 80), bool)
    m[25, 5:65] = True  # 60 pixels east-west at about 9.75 N: about 65 km
    m[40, 10:13] = True  # 3 pixels: too short
    lines = fronts.front_lines(m, tr, grad=np.full(m.shape, 0.07, np.float32), min_length_km=10.0)
    assert len(lines) == 1
    line, length_km, g = lines[0]
    assert length_km == pytest.approx(59 * 0.01 * KM * np.cos(np.radians(9.745)), rel=0.02)
    assert g == pytest.approx(0.07)
    d = fronts.distance_to_front_km(m, tr, [110.3], [9.645])  # 10 rows south of the line (centre 9.745 N): about 11 km
    assert d[0] == pytest.approx(10 * 0.01 * KM, rel=0.03)
    assert np.isnan(fronts.distance_to_front_km(np.zeros_like(m), tr, [110.3], [9.6])[0])


def test_front_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(fronts, "FRONT_CACHE", tmp_path)
    monkeypatch.setattr(fronts, "fine_grid", lambda: (from_origin(99.16, 23.76, 0.01, 0.01), (6, 7)))
    m = np.zeros((6, 7), bool)
    m[2, 1:5] = True
    fronts.save_mask("2026-09-29", m, 0.044, 0.062)
    m2, tr, th = fronts.load_mask("2026-09-29")
    assert (m2 == m).all() and th == {"low": 0.044, "high": 0.062} and tr.c == 99.16
    g = np.random.default_rng(0).random((6, 7)).astype(np.float32) * 0.1
    fronts.save_grad("2026-09-29", g)
    g2, _ = fronts.load_grad("2026-09-29")
    assert np.allclose(g2, g, atol=1e-4)


def test_coast_buffer_mask_marks_cells_near_land():
    from shapely.geometry import box

    tr = from_origin(105.0, 10.0, 0.01, 0.01)
    land = [box(105.0, 9.5, 105.2, 10.0)]  # land in the north-west corner
    near = fronts.coast_buffer_mask(tr, (100, 100), land, buffer_m=2000.0)
    assert near[10, 10] and near[51, 10]  # on land, and 1.7 km south of its edge at 9.5 N (row 51 centre 9.485 N)
    assert not near[53, 10] and not near[90, 90] and not near[10, 90]  # 3.9 km south, and far away


def test_mur_lake_cells_are_not_sea():
    sst = np.full((2, 3), 29.0)
    mask = np.array([[1, 1, 5], [1, 1, 5]], np.int8)  # 5 = open_sea bit 1 + open_lake bit 4 in the MUR bit field
    fine, sea, _ = daily.mur_to_fine(sst, mask)
    assert sea[0, 0] and not sea[0, 1] and np.isnan(fine[0, 1])


def test_front_distance_is_great_circle_at_any_latitude():
    tr = from_origin(100.0, 25.0, 0.01, 0.01)
    m = np.zeros((10, 10), bool)
    m[5, 5] = True  # front pixel centre 100.055 E, 24.945 N
    # a point one degree of longitude east at the same latitude, and one far south (the old single-plane method
    # scaled both with the cosine of their mean latitude)
    d = fronts.distance_to_front_km(m, tr, [101.055, 100.055], [24.945, 0.0])
    lat = np.radians(24.945)
    hav = 2 * fronts.R_EARTH_KM * np.arcsin(np.cos(lat) * np.sin(np.radians(0.5)))
    assert d[0] == pytest.approx(hav, rel=1e-4)
    assert d[1] == pytest.approx(np.radians(24.945) * fronts.R_EARTH_KM, rel=1e-4)


def test_bilinear_is_the_mean_of_four_cells_at_a_shared_corner_and_skips_nan():
    from darkvessel.ocean.grid import bilinear

    tr = from_origin(-0.125, 90.125, 0.25, 0.25)  # global 0.25 degree grid, centres on whole quarter degrees, lon 0..360
    arr = np.zeros((721, 1440))
    r, c = int((90 - 10.0) / 0.25), int(100.0 / 0.25)  # cell centred 100.0 E, 10.0 N
    arr[r, c], arr[r, c + 1], arr[r + 1, c], arr[r + 1, c + 1] = 1.0, 2.0, 3.0, 4.0
    v = bilinear(arr, tr, [100.125], [9.875])
    assert v[0] == pytest.approx(2.5)
    arr[r, c] = np.nan  # land: the other three share the weight
    assert bilinear(arr, tr, [100.125], [9.875])[0] == pytest.approx(3.0)
    assert bilinear(arr, tr, [100.25], [10.0])[0] == pytest.approx(2.0)  # on a valid cell centre: that cell's value
    # longitudes given as -180..180 wrap onto the 0..360 grid
    arr2 = np.zeros((721, 1440))
    arr2[:, int(250.0 / 0.25)] = 7.0
    assert bilinear(arr2, tr, [-110.0], [0.0], wrap_lon=True)[0] == pytest.approx(7.0)


def test_region_of_uses_reporting_boxes_and_other():
    from darkvessel.ocean.grid import region_of

    out = region_of([107.0, 115.0, 100.0, 121.0], [20.0, 10.0, 10.0, 20.0])
    assert out.tolist() == ["Gulf of Tonkin", "Central sea", "Gulf of Thailand", "other"]
