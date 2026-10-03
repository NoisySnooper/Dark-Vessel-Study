"""Sentinel-2 helpers (no network)."""

import numpy as np
import pytest

from darkvessel import s2


def test_tile_ids_and_bucket_paths():
    assert s2.tile_of(9.5, 104.5) == "48PVR"
    assert s2.tile_of(-2.5, 107.0) == "48MYC"          # south of the equator: southern latitude band
    assert s2.tile_prefix("48PVR") == "sentinel-s2-l2a-cogs/48/P/VR"
    with pytest.raises(ValueError):
        s2.tile_prefix("48PV")


def test_nir_contrast_peaks_at_the_centre_only():
    rng = np.random.default_rng(0)
    nir = 1100 + rng.normal(0, 10, (31, 31))           # open water with the L2A offset
    peak, med, con = s2.nir_contrast(nir)
    assert con < 60
    nir[15, 16] += 2500                                # bright object 10 m from the point
    peak, med, con = s2.nir_contrast(nir)
    assert con > 2400
    nir2 = 1100 + rng.normal(0, 10, (31, 31))
    nir2[2, 2] += 2500                                 # bright pixel at the window edge is not the point
    assert s2.nir_contrast(nir2)[2] < 60


def test_ndvi_at_peak_uses_the_offset():
    nir = np.full((31, 31), 1100.0)
    red = np.full((31, 31), 1100.0)
    nir[15, 15], red[15, 15] = 1000 + 4000, 1000 + 500    # vegetation: reflectance 0.40 NIR, 0.05 red
    assert abs(s2.ndvi_at_peak(nir, red, -1000) - (0.35 / 0.45)) < 1e-6
    nir[15, 15], red[15, 15] = 1000 + 3000, 1000 + 2800   # white hull or concrete: flat spectrum
    assert s2.ndvi_at_peak(nir, red, -1000) < 0.1


def test_satlas_distance_uses_given_points():
    import geopandas as gpd

    from darkvessel.satlas import distance_m

    pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy([110.0, 111.0], [10.0, 10.0]), crs="EPSG:4326")
    d = distance_m([110.0, 110.01, 111.0], [10.0, 10.0, 10.009], pts)
    assert d[0] < 1
    assert abs(d[1] - 1095) < 15          # 0.01 degree of longitude at 10 N
    assert abs(d[2] - 1001) < 15          # 0.009 degree of latitude
