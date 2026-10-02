"""Cell-averaging CFAR (CA-CFAR) for bright point targets in SAR intensity images.

Model: sea clutter intensity is gamma distributed with shape L (the equivalent number
of looks, ENL) and local mean mu. A pixel is a detection if

    I > alpha * mu_bg,   alpha = Gamma^-1(1 - PFA; shape=L, scale=1/L)

where mu_bg is the mean of valid pixels in a square ring: inside a `background` x
`background` box but outside a `guard` x `guard` box centred on the test pixel. The guard
box must exceed the largest target so the target does not inflate its own background.
Default guard 81 px = 810 m at 10 m spacing, which covers a 400 m ship from end to end.

Input should be calibrated intensity WITHOUT thermal-noise subtraction: subtracting
noise leaves a near-zero, partly negative background in VH that breaks a mean-based
threshold. The noise floor then behaves as part of the local clutter.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import ndimage
from scipy.stats import gamma


def gamma_alpha(pfa: float, enl: float) -> float:
    """Threshold multiplier on the local mean for a gamma(L, mu/L) clutter model."""
    return float(gamma.isf(pfa, a=enl, scale=1.0 / enl))


def estimate_enl(img: np.ndarray, valid: np.ndarray, win: int = 15, sample: int = 200_000,
                 seed: int = 0) -> float:
    """Robust ENL estimate: median of mean^2/var over homogeneous valid windows."""
    x = np.where(valid, img, 0.0).astype(np.float64)
    m = valid.astype(np.float64)
    n = ndimage.uniform_filter(m, win)
    with np.errstate(invalid="ignore", divide="ignore"):
        mu = ndimage.uniform_filter(x, win) / n
        mu2 = ndimage.uniform_filter(x * x, win) / n
        var = mu2 - mu * mu
        enl = mu * mu / var
    full = (n > 0.999) & np.isfinite(enl) & (enl > 0)
    vals = enl[full]
    if vals.size == 0:
        return float("nan")
    rng = np.random.default_rng(seed)
    if vals.size > sample:
        vals = rng.choice(vals, sample, replace=False)
    # homogeneous areas sit at the top of the ENL distribution; edges and targets drag it down
    return float(np.median(vals[vals >= np.percentile(vals, 50)]))


def _box_sum(a: np.ndarray, size: int) -> np.ndarray:
    return ndimage.uniform_filter(a, size, mode="constant", cval=0.0) * (size * size)


def ca_cfar(img: np.ndarray, valid: np.ndarray, guard: int = 81, background: int = 161,
            pfa: float = 1e-6, enl: float = 4.4, min_bg_frac: float = 0.5) -> dict:
    """CA-CFAR on one array. Returns dict with detect, threshold, bg_mean, alpha.

    Pixels whose ring holds fewer than `min_bg_frac` valid cells are not tested.
    """
    if guard % 2 == 0 or background % 2 == 0 or background <= guard:
        raise ValueError("guard and background must be odd with background > guard")
    x = np.where(valid, img, 0.0).astype(np.float64)
    m = valid.astype(np.float64)
    ring_sum = _box_sum(x, background) - _box_sum(x, guard)
    ring_n = _box_sum(m, background) - _box_sum(m, guard)
    ring_full = background * background - guard * guard
    with np.errstate(invalid="ignore", divide="ignore"):
        bg = ring_sum / ring_n
    alpha = gamma_alpha(pfa, enl)
    thr = alpha * bg
    testable = valid & (ring_n >= min_bg_frac * ring_full) & np.isfinite(bg) & (bg > 0)
    detect = testable & (img > thr)
    return {"detect": detect, "threshold": thr.astype(np.float32), "bg_mean": bg.astype(np.float32),
            "alpha": alpha, "enl": enl, "testable": testable}


def ca_cfar_tiled(img: np.ndarray, valid: np.ndarray, tile: int = 2048, **kw) -> dict:
    """Run ca_cfar block by block with overlap so results match a single full-array run."""
    background = kw.get("background", 161)
    margin = background // 2 + 1
    H, W = img.shape
    detect = np.zeros((H, W), bool)
    testable = np.zeros((H, W), bool)
    bg = np.full((H, W), np.nan, np.float32)
    alpha = enl = None
    for r in range(0, H, tile):
        for c in range(0, W, tile):
            r0, c0 = max(0, r - margin), max(0, c - margin)
            r1, c1 = min(H, r + tile + margin), min(W, c + tile + margin)
            res = ca_cfar(img[r0:r1, c0:c1], valid[r0:r1, c0:c1], **kw)
            sr = slice(r - r0, r - r0 + min(tile, H - r))
            sc = slice(c - c0, c - c0 + min(tile, W - c))
            detect[r : r + tile, c : c + tile] = res["detect"][sr, sc]
            testable[r : r + tile, c : c + tile] = res["testable"][sr, sc]
            bg[r : r + tile, c : c + tile] = res["bg_mean"][sr, sc]
            alpha, enl = res["alpha"], res["enl"]
    return {"detect": detect, "bg_mean": bg, "testable": testable, "alpha": alpha, "enl": enl}


def _principal_extent(rr: np.ndarray, cc: np.ndarray) -> tuple[float, float, float]:
    """Pixel extent along the object's principal axis and across it (inclusive, >= 1), and
    the major-axis angle from the image row (azimuth) axis in degrees, range [-90, 90].

    The equal-moments ellipse (skimage axis_major_length) overstates a rectangle's
    length by 2/sqrt(3), about 15 %, so project the pixels instead.
    """
    if len(rr) < 2:
        return 1.0, 1.0, 0.0
    xy = np.column_stack([cc, rr]).astype(float)
    xy -= xy.mean(axis=0)
    _, vecs = np.linalg.eigh(np.cov(xy.T))
    major, minor = xy @ vecs[:, 1], xy @ vecs[:, 0]
    angle = np.degrees(np.arctan2(vecs[0, 1], vecs[1, 1]))
    angle = (angle + 90.0) % 180.0 - 90.0
    return float(np.ptp(major) + 1), float(np.ptp(minor) + 1), float(angle)


def extract_detections(detect: np.ndarray, img: np.ndarray, bg_mean: np.ndarray,
                       min_pixels: int = 2, max_pixels: int = 4000, pixel_spacing_m: float = 10.0,
                       merge_px: int = 1, row_off: int = 0, col_off: int = 0) -> pd.DataFrame:
    """Group detected pixels into objects and measure them.

    Pixels within `merge_px` of each other join one object (fragmented ship returns).
    Length is the extent of the detected pixels along their principal axis times pixel
    spacing: a crude, upward-biased estimate (sidelobes and blooming widen bright
    targets, and a 2-pixel object reads as 20 m whatever its true size).
    Row/col are in full-scene pixel coordinates when offsets are given.
    """
    if merge_px > 0:
        grouped = ndimage.binary_dilation(detect, structure=np.ones((2 * merge_px + 1,) * 2))
    else:
        grouped = detect
    labels, n = ndimage.label(grouped, structure=np.ones((3, 3)))
    del grouped
    rows = []
    # Work per object bounding box: no full-size copies of the image or label array.
    for k, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None:
            continue
        member = (labels[sl] == k) & detect[sl]
        area = int(member.sum())
        if area < min_pixels or area > max_pixels:
            continue
        lr, lc = np.nonzero(member)
        rr, cc = lr + sl[0].start, lc + sl[1].start
        vals = img[rr, cc]
        w = vals / vals.sum()
        k = int(np.argmax(vals))
        bgv = float(np.nanmedian(bg_mean[rr, cc]))
        length_px, width_px, angle = _principal_extent(rr, cc)
        rows.append(
            {
                "row": float((rr * w).sum()) + row_off,
                "col": float((cc * w).sum()) + col_off,
                "n_pixels": area,
                "peak_sigma0_db": float(10 * np.log10(vals[k])),
                "mean_sigma0_db": float(10 * np.log10(vals.mean())),
                "bg_sigma0_db": float(10 * np.log10(bgv)) if bgv > 0 else np.nan,
                "peak_to_bg_db": float(10 * np.log10(vals[k] / bgv)) if bgv > 0 else np.nan,
                "length_est_m": float(length_px * pixel_spacing_m),
                "width_est_m": float(width_px * pixel_spacing_m),
                "orientation_deg": angle,
            }
        )
    return pd.DataFrame(rows)
