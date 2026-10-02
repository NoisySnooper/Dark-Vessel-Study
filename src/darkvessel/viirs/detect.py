"""Lit-boat detection in VIIRS Day/Night Band (DNB) radiance: a spike detector after Elvidge et al. (2015).

Elvidge, Zhizhin, Baugh, Hsu (2015), Automatic Boat Identification System for VIIRS Low Light Imaging
Data, Remote Sensing 7(3) (corpus anchor paper, docs/bibliometrics.md). Their VBD product is run by the
Earth Observation Group; this module is an independent, simplified re-implementation for the project:

  1. radiance in nW cm-2 sr-1; fill values (< 0) become NaN;
  2. background = median of a 7 x 7 pixel window (about 5 km);
  3. spike = radiance - background; local noise = 1.4826 x the median absolute spike in a 15 x 15
     window (textured, moonlit cloud fields get a higher bar than calm dark sea);
  4. a detection is a 3 x 3 local maximum over open sea with spike >= k x local noise, spike >= an
     absolute floor, peak >= `min_ratio` x background, and isolation >= `min_isolation` (the peak
     stands alone; cloud edges have bright neighbours along the edge);
  5. single-pixel spikes whose 8 neighbours carry almost none of the light are flagged as possible
     energetic-particle hits (`sharp`), not dropped.

Lights that recur on most nights (platforms, gas flares, island lights) are separated later by
persistence across nights. A detection is a light at sea, not a confirmed vessel.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import ndimage

NW = 1e9  # W cm-2 sr-1 -> nW cm-2 sr-1


def spike_detect(rad_w: np.ndarray, sea: np.ndarray, k: float = 5.0, floor_nw: float = 1.5,
                 min_ratio: float = 2.0, min_isolation: float = 2.0, win: int = 7, texture_win: int = 15) -> pd.DataFrame:
    """Detections in one DNB granule. rad_w in W cm-2 sr-1 (SDR units); sea: bool, True = open sea.

    noise is local: 1.4826 x the median absolute spike in a `texture_win` window, so textured
    moonlit cloud fields need a larger spike than calm dark sea. isolation = peak spike / largest
    spike in the ring between 3 x 3 and 5 x 5 around it: a point source stands alone, a cloud edge
    has bright neighbours along the edge.
    """
    rad = np.where(rad_w > -1, rad_w * NW, np.nan).astype(np.float32)
    filled = np.where(np.isfinite(rad), rad, np.nanmedian(rad)).astype(np.float32)
    bg = ndimage.median_filter(filled, size=win, mode="nearest")
    spike = filled - bg
    ok = sea & np.isfinite(rad)
    noise = 1.4826 * ndimage.median_filter(np.abs(spike), size=texture_win, mode="nearest")
    noise = np.maximum(noise, 0.05)  # nW; DNB noise floor guard on very dark, smooth sea

    peak = filled == ndimage.maximum_filter(filled, size=3, mode="nearest")
    ring = np.ones((5, 5), bool)
    ring[1:4, 1:4] = False
    ring_max = ndimage.maximum_filter(spike, footprint=ring, mode="nearest")
    isolation = spike / np.maximum(ring_max, 0.05)
    cand = (ok & peak & (spike >= k * noise) & (spike >= floor_nw) & (filled >= min_ratio * np.maximum(bg, 1e-3))
            & (isolation >= min_isolation))
    r, c = np.nonzero(cand)
    if len(r) == 0:
        return _empty()
    # light carried by the 8 neighbours, relative to the peak spike (0 = single-pixel spike)
    pos = np.maximum(spike, 0)
    nb_sum = ndimage.uniform_filter(pos, size=3, mode="nearest") * 9 - pos
    share = nb_sum[r, c] / np.maximum(spike[r, c], 1e-6) / 8.0
    return pd.DataFrame({
        "row": r, "col": c, "radiance_nw": rad[r, c], "background_nw": bg[r, c], "spike_nw": spike[r, c],
        "snr": spike[r, c] / noise[r, c], "isolation": isolation[r, c], "neighbour_share": share.astype(np.float32),
        "sharp": share < 0.02,
    })


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["row", "col", "radiance_nw", "background_nw", "spike_nw", "snr", "isolation",
                                 "neighbour_share", "sharp"])
