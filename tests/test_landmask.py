"""Sea mask logic on a synthetic WorldCover grid (no network)."""

import numpy as np
from rasterio.transform import from_origin

import darkvessel.landmask as lm


def _fake_worldcover(monkeypatch, wc):
    tr = from_origin(105.0, 10.0, 0.01, 0.01)  # 0.01 degree cells, north-up
    monkeypatch.setattr(lm, "read_worldcover", lambda bounds, factor=8: (wc, tr))
    rows, cols = np.mgrid[0:wc.shape[0], 0:wc.shape[1]]
    return 105.0 + (cols + 0.5) * 0.01, 10.0 - (rows + 0.5) * 0.01


def test_nearshore_sea_coded_80_needs_a_seed(monkeypatch):
    # Land (10) on the left third, the rest is water coded 80 with no code-0 cell: WorldCover's
    # coding of nearshore sea inside a land tile.
    wc = np.full((30, 30), 80, np.uint8)
    wc[:, :10] = 10
    lon, lat = _fake_worldcover(monkeypatch, wc)
    sea, land = lm.sea_mask_on_grid(lon, lat, cell_m=1000.0, buffer_m=1500.0)
    assert not sea.any() and land.all()  # old rule: no open-sea cell, so everything is masked

    seed = np.zeros(wc.shape, bool)
    seed[:, -3:] = True  # cells known to be sea, far from the coast
    sea, land = lm.sea_mask_on_grid(lon, lat, cell_m=1000.0, buffer_m=1500.0, seed=seed)
    assert sea[:, 11:].all()  # connected water kept beyond the 1.5 km shore buffer (2 cells)
    assert not sea[:, :11].any()  # land and the buffer stay out
    assert land[:, :10].all() and not land[:, 10:].any()


def test_seed_does_not_pull_in_a_separate_lake(monkeypatch):
    wc = np.full((30, 30), 10, np.uint8)
    wc[:, 20:] = 0  # open sea on the right
    wc[10:15, 3:8] = 80  # an inland lake, not connected
    lon, lat = _fake_worldcover(monkeypatch, wc)
    seed = np.zeros(wc.shape, bool)
    seed[:, -2:] = True
    sea, _ = lm.sea_mask_on_grid(lon, lat, cell_m=1000.0, buffer_m=0.0, seed=seed)
    assert sea[:, 20:].all()
    assert not sea[10:15, 3:8].any()
