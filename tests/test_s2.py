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
