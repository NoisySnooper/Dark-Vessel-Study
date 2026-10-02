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
