"""VIIRS light detector on synthetic Day/Night Band radiance (no network)."""

import numpy as np

from darkvessel.viirs.detect import spike_detect

NW = 1e-9  # nW cm-2 sr-1 in SDR units (W cm-2 sr-1)


def _scene(seed=0):
    rng = np.random.default_rng(seed)
    rad = (1.0 + 0.05 * rng.standard_normal((200, 300))) * NW  # dark sea, small noise
    sea = np.ones(rad.shape, bool)
    return rad, sea


def test_point_lights_are_found_and_land_is_ignored():
    rad, sea = _scene()
    rad[50, 60] += 40 * NW           # bright boat
    rad[120, 200] += 4 * NW          # dim boat
    rad[150, 250] += 80 * NW         # light on land
    sea[140:160, 240:260] = False
    det = spike_detect(rad, sea)
    found = set(zip(det.row, det.col))
    assert (50, 60) in found and (120, 200) in found
    assert (150, 250) not in found
    assert len(det) == 2


def test_cloud_edge_ridge_is_not_a_light():
    rad, sea = _scene(1)
    # a moonlit cloud: bright block with a textured edge (ridge of similar values along the edge)
    rad[:, 150:] += 6 * NW
    rad[:, 150] += 3 * NW
    det = spike_detect(rad, sea)
    assert det.empty or not ((det.col >= 148) & (det.col <= 152)).any()


def test_fill_values_are_ignored():
    rad, sea = _scene(2)
    rad[:, :40] = -999.3              # bow-tie deletion / fill
    rad[100, 100] += 20 * NW
    det = spike_detect(rad, sea)
    assert list(zip(det.row, det.col)) == [(100, 100)]


def test_weather_grid_sampling_picks_the_containing_cell():
    from rasterio.transform import from_origin

    from darkvessel.weather import sample_grid

    arr = np.arange(721 * 1440, dtype=float).reshape(721, 1440)
    for origin in (-180.125, -0.125):  # GDAL may present GFS as -180..180 or 0..360
        tr = from_origin(origin, 90.125, 0.25, 0.25)
        v = sample_grid(arr, tr, np.array([114.5, -70.0]), np.array([6.4, -10.1]))
        col = int(np.floor((np.mod(114.5 - origin, 360)) / 0.25))
        assert v[0] == arr[int(np.floor((90.125 - 6.4) / 0.25)), col]
        col2 = int(np.floor((np.mod(-70.0 - origin, 360)) / 0.25))
        assert v[1] == arr[int(np.floor((90.125 + 10.1) / 0.25)), col2]
