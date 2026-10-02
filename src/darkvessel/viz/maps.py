"""Map figures for detection products: overview map and detection chip gallery."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

from darkvessel.config import CRS_UTM, DARK_CAVEAT_SHORT
from darkvessel.viz.style import INK, INK_2, LAND_FILL, MUTED, SERIES_DARK, apply_matplotlib_style

# Fixed class -> slot mapping (colour follows the class, never its count).
CLASS_STYLE = {
    "high": {"color": SERIES_DARK[0], "label": "Vessel candidate, both channels"},
    "medium": {"color": SERIES_DARK[1], "label": "Vessel candidate, one channel"},
    "fixed": {"color": SERIES_DARK[2], "label": "Fixed structure (recurs on other dates)"},
    "low": {"color": MUTED, "label": "Low confidence (weak VV-only clutter, or longer than 450 m)"},
}


def _scale_bar(ax, x0, y0, length_km, color="white"):
    ax.plot([x0, x0 + length_km * 1000], [y0, y0], color=color, lw=2.5, solid_capstyle="butt")
    ax.text(x0 + length_km * 500, y0 + 900, f"{length_km:g} km", color=color, ha="center", va="bottom", fontsize=9)


def detection_map(db_utm, transform, sea_utm, dets_utm, title, subtitle, footer, out_png,
                  vmin=None, vmax=None, aoi_utm=None, utm_label="WGS 84 / UTM 48N"):
    """North-up UTM map: sigma0 (dB) backdrop, land in flat fill, detections by class."""
    apply_matplotlib_style()
    h, w = db_utm.shape
    left, top = transform.c, transform.f
    right, bottom = left + w * transform.a, top + h * transform.e
    extent = (left, right, bottom, top)
    finite = db_utm[np.isfinite(db_utm) & sea_utm]
    vmin = np.percentile(finite, 2) if vmin is None else vmin
    vmax = np.percentile(finite, 99.7) if vmax is None else vmax

    fig = plt.figure(figsize=(10, 11.2))
    ax = fig.add_axes([0.07, 0.19, 0.88, 0.7])
    ax.set_facecolor("#0d0d0d")
    ax.imshow(np.ma.masked_invalid(np.where(sea_utm, db_utm, np.nan)), cmap="gray", vmin=vmin, vmax=vmax,
              extent=extent, interpolation="nearest")
    land = np.ma.masked_where(sea_utm | ~np.isfinite(db_utm), np.ones_like(db_utm))
    ax.imshow(land, cmap=matplotlib.colors.ListedColormap([LAND_FILL]), extent=extent, interpolation="nearest")
    if aoi_utm is not None:
        ax.plot(*aoi_utm.exterior.xy, color=MUTED, lw=1)
    handles = []
    for cls in ("low", "fixed", "medium", "high"):
        sty = CLASS_STYLE[cls]
        q = dets_utm[dets_utm["confidence"] == cls]
        if cls == "low":
            ax.scatter(q.geometry.x, q.geometry.y, s=6, c=sty["color"], lw=0, alpha=0.8, zorder=3)
            handles.append(Line2D([], [], marker="o", ls="", ms=3, color=sty["color"], label=f"{sty['label']}: {len(q)}"))
        else:
            ax.scatter(q.geometry.x, q.geometry.y, s=70, facecolors="none", edgecolors=sty["color"], lw=1.6, zorder=4)
            handles.append(Line2D([], [], marker="o", ls="", ms=8, mfc="none", mec=sty["color"], mew=1.6,
                                  label=f"{sty['label']}: {len(q)}"))
    ax.set_xlim(left, right)
    ax.set_ylim(bottom, top)
    ax.ticklabel_format(useOffset=False, style="plain")
    ax.set_xticks(ax.get_xticks()[1:-1])
    ax.set_xticklabels([f"{x/1000:.0f}" for x in ax.get_xticks()], fontsize=8)
    ax.set_yticklabels([f"{y/1000:.0f}" for y in ax.get_yticks()], fontsize=8)
    ax.set_xlabel(f"Easting, km ({utm_label})", fontsize=9)
    ax.set_ylabel("Northing, km", fontsize=9)
    span = (right - left) / 1000
    bar = 10 if span > 60 else 5
    _scale_bar(ax, left + 0.05 * (right - left), bottom + 0.05 * (top - bottom), bar)
    ax.annotate("N", xy=(0.95, 0.95), xycoords="axes fraction", ha="center", va="center", color="white",
                fontsize=12, fontweight="bold")
    ax.annotate("", xy=(0.95, 0.935), xytext=(0.95, 0.87), xycoords="axes fraction",
                arrowprops={"arrowstyle": "-|>", "color": "white", "lw": 1.5})
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(0, -0.075), ncol=2, fontsize=9,
              labelcolor=INK_2, handletextpad=0.4, columnspacing=1.5)
    fig.text(0.07, 0.965, title, fontsize=15, color=INK, fontweight="bold", va="top")
    fig.text(0.07, 0.932, subtitle, fontsize=10, color=INK_2, va="top")
    fig.text(0.07, 0.012, footer, fontsize=7.5, color=INK_2, va="bottom", wrap=True)
    fig.savefig(out_png, dpi=160)
    plt.close(fig)
    return out_png


def chip_gallery(scene, dets, classes, out_png, per_class=6, half=40, title="", footer=""):
    """Grid of VV|VH chips (2*half px square, 10 m pixels) around detections, by class."""
    apply_matplotlib_style()
    rows = [c for c in classes if (dets["confidence"] == c).any()]
    fig, axs = plt.subplots(len(rows), per_class, figsize=(per_class * 2.3, len(rows) * 1.55 + 0.9), squeeze=False)
    from rasterio.windows import Window

    for i, cls in enumerate(rows):
        q = dets[dets["confidence"] == cls]
        key = "scr_vh_db" if cls in ("high", "fixed") else ("scr_vv_db" if cls == "low" else "scr_vh_db")
        q = q.sort_values(key, ascending=False, na_position="last").head(per_class)
        for j in range(per_class):
            ax = axs[i, j]
            ax.set_xticks([]), ax.set_yticks([])
            for s in ax.spines.values():
                s.set_visible(False)
            if j >= len(q):
                continue
            d = q.iloc[j]
            win = Window(int(d.col) - half, int(d.row) - half, 2 * half, 2 * half)
            pair = []
            for pol in ("VV", "VH"):
                s0 = scene.read_sigma0(pol, win, denoise=False)
                db = 10 * np.log10(s0)
                lo, hi = np.nanpercentile(db, 5), np.nanmax(db)
                pair.append(np.clip((db - lo) / max(hi - lo, 1e-3), 0, 1))
            gap = np.ones((2 * half, 3))
            ax.imshow(np.hstack([pair[0], gap, pair[1]]), cmap="gray", vmin=0, vmax=1, interpolation="nearest")
            ax.set_title(f"{d.length_est_m:.0f} m est.", fontsize=7.5, color=INK_2, pad=2)
        axs[i, 0].set_ylabel(CLASS_STYLE[cls]["label"].replace(" (", "\n("), fontsize=7.5, color=CLASS_STYLE[cls]["color"]
                             if cls != "low" else MUTED, rotation=0, ha="right", va="center", labelpad=8)
    fig.suptitle(title, x=0.02, ha="left", fontsize=12, color=INK, fontweight="bold")
    fig.text(0.02, 0.005, footer, fontsize=7, color=INK_2, va="bottom")
    fig.tight_layout(rect=(0.08, 0.04, 1, 0.95))
    fig.savefig(out_png, dpi=150)
    plt.close(fig)
    return out_png
