"""Charts for docs/bibliometrics.md, drawn with matplotlib from the CSV tables in data/biblio.

Design follows the dataviz skill: one blue for a single series, blue and orange for two (slots 1
and 2 of the validated categorical palette, light mode), thin bars with a 4 px rounded data end and a
square baseline end, solid hairline gridlines, a 2 px surface gap between stacked segments, a legend
whenever there are two series, direct labels only at the peak or tip, and text in ink tokens
never in the series colour. Because the figures are meant for print, the second series also
carries a 45 degree hatch so it survives grayscale. Every figure has a title, labelled axes and a
source line. The CSV tables in data/biblio are the table view of each chart.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import PathPatch  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

from . import themes as T  # noqa: E402

# palette (reference instance, light mode)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BLUE = "#2a78d6"  # slot 1
ORANGE = "#eb6834"  # slot 2
BLUE_DARK = "#184f95"  # sequential step 600 of the same blue, for the "all papers" total
HATCH_ORANGE = "#a8451f"  # darker tone of the orange for the hatch lines
PARTIAL_ALPHA = 0.5  # the partial last year is drawn as a tint of the same colour

DPI = 200
FONT = ["Liberation Sans", "DejaVu Sans"]


def _px(css_px: float) -> float:
    """CSS pixels (96 per inch) to figure pixels at DPI."""
    return css_px * DPI / 96.0


def _style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": FONT,
            "font.size": 8.5,
            "axes.edgecolor": AXIS,
            "axes.labelcolor": INK2,
            "xtick.color": INK2,
            "ytick.color": INK2,
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "hatch.linewidth": 0.9,
            "axes.unicode_minus": False,
        }
    )


def _thousands(x, _pos=None) -> str:
    return f"{int(round(x)):,}"


# ---------------------------------------------------------------------------
# rounded bars: 4 px rounded data end, square baseline end
# ---------------------------------------------------------------------------
def _scale(ax) -> tuple[float, float]:
    """Figure pixels per data unit along x and y."""
    p0 = ax.transData.transform((0, 0))
    p1 = ax.transData.transform((1, 1))
    return abs(p1[0] - p0[0]), abs(p1[1] - p0[1])


def _bar_path(ax, x0, y0, x1, y1, end: str, radius_px: float) -> MPath:
    """Rectangle from (x0, y0) to (x1, y1) with the two corners at `end` ('right' or 'top') rounded."""
    sx, sy = _scale(ax)
    rx, ry = radius_px / sx, radius_px / sy
    if end == "right":
        rx = min(rx, (x1 - x0) / 2)
        ry = min(ry, (y1 - y0) / 2)
        v = [
            (x0, y0), (x1 - rx, y0), (x1, y0), (x1, y0 + ry), (x1, y1 - ry), (x1, y1), (x1 - rx, y1), (x0, y1), (x0, y0),
        ]
        c = [MPath.MOVETO, MPath.LINETO, MPath.CURVE3, MPath.CURVE3, MPath.LINETO, MPath.CURVE3, MPath.CURVE3, MPath.LINETO, MPath.CLOSEPOLY]
    else:  # top
        rx = min(rx, (x1 - x0) / 2)
        ry = min(ry, (y1 - y0) / 2)
        v = [
            (x0, y0), (x0, y1 - ry), (x0, y1), (x0 + rx, y1), (x1 - rx, y1), (x1, y1), (x1, y1 - ry), (x1, y0), (x0, y0),
        ]
        c = [MPath.MOVETO, MPath.LINETO, MPath.CURVE3, MPath.CURVE3, MPath.LINETO, MPath.CURVE3, MPath.CURVE3, MPath.LINETO, MPath.CLOSEPOLY]
    return MPath(v, c)


def _add_bar(ax, x0, y0, x1, y1, color, *, end: str | None, alpha=1.0, hatch=None, hatch_color=None, radius_css=4):
    if x1 <= x0 or y1 <= y0:
        return
    if end is None:
        path = MPath([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], [MPath.MOVETO, MPath.LINETO, MPath.LINETO, MPath.LINETO, MPath.CLOSEPOLY])
    else:
        path = _bar_path(ax, x0, y0, x1, y1, end, _px(radius_css))
    patch = PathPatch(path, facecolor=color, edgecolor="none", linewidth=0, alpha=alpha, zorder=3)
    ax.add_patch(patch)
    if hatch:
        overlay = PathPatch(path, facecolor="none", edgecolor=hatch_color or INK2, linewidth=0, hatch=hatch, alpha=alpha, zorder=3.1)
        ax.add_patch(overlay)


def _frame(fig, title: str, subtitle: str, source: str, *, title_y=0.975) -> None:
    """Title, subtitle and source line in ink tokens, left-aligned to the figure margin."""
    fig.text(0.035, title_y, title, ha="left", va="top", fontsize=13, fontweight="bold", color=INK)
    if subtitle:
        fig.text(0.035, title_y - 0.045 * (6.0 / fig.get_figheight()), subtitle, ha="left", va="top", fontsize=8.8, color=INK2, linespacing=1.35)
    fig.text(0.035, 0.012, source, ha="left", va="bottom", fontsize=7.8, color=INK2, linespacing=1.35)


def _style_axes(ax, *, grid_axis="y", keep_left=True, keep_bottom=True) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_visible(keep_left and grid_axis == "x")
    ax.spines["bottom"].set_visible(keep_bottom)
    ax.spines["bottom"].set_color(AXIS)
    ax.spines["left"].set_color(AXIS)
    ax.spines["bottom"].set_linewidth(0.9)
    ax.spines["left"].set_linewidth(0.9)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8, linestyle="-")
    ax.set_axisbelow(True)
    ax.tick_params(length=0, pad=3)


def _source_line(snapshot: str, n: int, extra: str = "") -> str:
    base = f"Source: OpenAlex snapshot ({snapshot}), n = {n:,}"
    return base + (". " + extra if extra else "")


# ---------------------------------------------------------------------------
# figure 1: papers per year by theme (small multiples, one panel per theme)
# ---------------------------------------------------------------------------
def fig_papers_per_year(per_year: pd.DataFrame, out: Path, snapshot: str, n: int, last_year_partial: bool = True) -> Path:
    _style()
    panels = [(t, T.THEME_LABELS[t], BLUE) for t in T.THEMES] + [("all_papers", "All papers (unique)", BLUE_DARK)]
    years = per_year["year"].tolist()
    fig = plt.figure(figsize=(7.2, 9.4), dpi=DPI)
    ncols, nrows = 2, 4
    left, right, top, bottom = 0.095, 0.03, 0.125, 0.075
    gx, gy = 0.07, 0.065
    pw = (1 - left - right - gx * (ncols - 1)) / ncols
    ph = (1 - top - bottom - gy * (nrows - 1)) / nrows
    for k, (col, label, color) in enumerate(panels):
        r, c = divmod(k, ncols)
        x0 = left + c * (pw + gx)
        y0 = 1 - top - (r + 1) * ph - r * gy
        ax = fig.add_axes([x0, y0, pw, ph])
        vals = per_year[col].to_numpy()
        ymax = max(vals.max(), 1)
        ax.set_xlim(years[0] - 0.7, years[-1] + 0.7)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=3, integer=True))
        ax.set_ylim(0, ymax * 1.28)
        ax.yaxis.set_major_formatter(FuncFormatter(_thousands))
        _style_axes(ax, grid_axis="y")
        bar_w = 0.62
        for yr, v in zip(years, vals):
            partial = last_year_partial and yr == years[-1]
            _add_bar(ax, yr - bar_w / 2, 0, yr + bar_w / 2, v, color, end="top", alpha=PARTIAL_ALPHA if partial else 1.0, radius_css=3)
        ax.set_xticks([2015, 2020, 2025])
        ax.tick_params(axis="x", labelsize=8.5)
        ax.tick_params(axis="y", labelsize=8.5)
        # direct label: the peak bar (a star marks the partial year)
        peak = int(np.argmax(vals))
        star = "*" if (last_year_partial and years[peak] == years[-1]) else ""
        ax.text(years[peak], vals[peak] + ymax * 0.04, f"{vals[peak]:,}{star}", ha="center", va="bottom", fontsize=8.5, color=INK, fontweight="bold")
        ax.set_title(label, loc="left", fontsize=10, fontweight="bold", color=INK, pad=14)
        ax.text(0.0, 1.035, f"{int(vals.sum()):,} papers" if col != "all_papers" else f"{int(vals.sum()):,} papers, each counted once", transform=ax.transAxes, ha="left", va="bottom", fontsize=8.3, color=INK2)
    fig.text(0.5 + (left - right) / 2, 0.045, "Publication year", ha="center", va="center", fontsize=9, color=INK2)
    fig.text(0.012, 0.5 + (bottom - top) / 2, "Papers per year", rotation=90, ha="center", va="center", fontsize=9, color=INK2)
    _frame(
        fig,
        "Papers per year by theme, 2015 to 2026",
        "A paper matching several themes counts in each theme panel. Each panel has its own vertical scale.",
        _source_line(snapshot, n, "* 2026 is a partial year (snapshot of 23 Sep 2026); its bars are drawn as a lighter tint."),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# figure 2: top 15 venues (journals and conference series)
# ---------------------------------------------------------------------------
def fig_top_venues(venues: pd.DataFrame, out: Path, snapshot: str, n: int, note: str, k: int = 15) -> Path:
    _style()
    top = venues.head(k).reset_index(drop=True)
    labels = [textwrap.fill(v, width=46, break_long_words=False) for v in top["venue"]]
    row = 0.43
    fig_h = 1.35 + row * k + 0.55
    fig = plt.figure(figsize=(7.2, fig_h), dpi=DPI)
    left, right = 3.3 / 7.2, 0.5 / 7.2
    top_in, bottom_in = 1.15, 0.78
    ax = fig.add_axes([left, bottom_in / fig_h, 1 - left - right, (fig_h - top_in - bottom_in) / fig_h])
    vmax = top["n_papers"].max()
    ax.set_xlim(0, vmax * 1.16)
    ax.set_ylim(k - 0.5, -0.5)
    ax.xaxis.set_major_formatter(FuncFormatter(_thousands))
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
    _style_axes(ax, grid_axis="x")
    thickness = 0.5
    for i, r in top.iterrows():
        conference = r["venue_group"] == "conference"
        _add_bar(
            ax, 0, i - thickness / 2, r["n_papers"], i + thickness / 2, ORANGE if conference else BLUE, end="right",
            hatch="////" if conference else None, hatch_color=HATCH_ORANGE,
        )
        ax.text(r["n_papers"] + vmax * 0.012, i, f"{int(r['n_papers']):,}", ha="left", va="center", fontsize=8.5, color=INK, fontweight="bold")
    ax.set_yticks(range(k))
    ax.set_yticklabels(labels, fontsize=8.5, color=INK, linespacing=1.15)
    ax.set_xlabel("Papers in the corpus (each paper counted once, for its primary venue)", fontsize=9, color=INK2, labelpad=6)
    # legend: two series, so always present
    handles = [
        plt.Rectangle((0, 0), 1, 1, facecolor=BLUE, edgecolor="none"),
        plt.Rectangle((0, 0), 1, 1, facecolor=ORANGE, edgecolor=HATCH_ORANGE, hatch="////", linewidth=0),
    ]
    leg = fig.legend(handles, ["Journal", "Conference series (all years together)"], loc="upper left", bbox_to_anchor=(0.035, 1 - 0.78 / fig_h), ncol=2, frameon=False, fontsize=8.8, handlelength=1.4, handleheight=0.9, columnspacing=1.6)
    for t in leg.get_texts():
        t.set_color(INK2)
    _frame(
        fig,
        "Top 15 venues: journals and conference series",
        "Ranked by number of papers in the corpus.",
        _source_line(snapshot, n, note),
        title_y=1 - 0.12 / fig_h,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# figure 3: top 15 countries (full counting)
# ---------------------------------------------------------------------------
def fig_top_countries(countries: pd.DataFrame, out: Path, snapshot: str, n: int, note: str, k: int = 15) -> Path:
    _style()
    top = countries.head(k).reset_index(drop=True)
    row = 0.34
    fig_h = 1.3 + row * k + 0.7
    fig = plt.figure(figsize=(7.2, fig_h), dpi=DPI)
    left, right = 1.45 / 7.2, 0.55 / 7.2
    top_in, bottom_in = 1.05, 0.85
    ax = fig.add_axes([left, bottom_in / fig_h, 1 - left - right, (fig_h - top_in - bottom_in) / fig_h])
    vmax = top["n_papers"].max()
    ax.set_xlim(0, vmax * 1.2)
    ax.set_ylim(k - 0.5, -0.5)
    ax.xaxis.set_major_formatter(FuncFormatter(_thousands))
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
    _style_axes(ax, grid_axis="x")
    thickness = 0.52
    for i, r in top.iterrows():
        _add_bar(ax, 0, i - thickness / 2, r["n_papers"], i + thickness / 2, BLUE, end="right")
        ax.text(r["n_papers"] + vmax * 0.012, i, f"{int(r['n_papers']):,} ({r['pct_of_corpus']:.0f}%)", ha="left", va="center", fontsize=8.5, color=INK)
    ax.set_yticks(range(k))
    ax.set_yticklabels(list(top["country"]), fontsize=9, color=INK)
    ax.set_xlabel("Papers with at least one author affiliated in the country", fontsize=9, color=INK2, labelpad=6)
    _frame(
        fig,
        "Top 15 countries by author affiliation",
        "Full counting: a paper counts once for each country among its authors, so shares add to more than 100%.\nPercentages are of all papers in the corpus.",
        _source_line(snapshot, n, note),
        title_y=1 - 0.12 / fig_h,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# figure 4: Southeast Asia and Vietnam papers per year, split by relevance judged on reading
# ---------------------------------------------------------------------------
NEUTRAL = "#8f8e86"  # muted neutral for the works judged not on topic (not a categorical hue)
NEUTRAL_HATCH = "#5d5c56"


def _stacked_year_panel(ax, years, on_topic, off_topic, *, last_year_partial=True, label_peak=True) -> None:
    """Stacked bars per year: judged on topic (blue) on the baseline, not on topic (neutral, hatched) on top."""
    ymax = int(max((on_topic + off_topic).max(), 1))
    ax.set_xlim(years[0] - 0.7, years[-1] + 0.7)
    ax.set_ylim(0, ymax * 1.22)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
    ax.yaxis.set_major_formatter(FuncFormatter(_thousands))
    _style_axes(ax, grid_axis="y")
    sx, sy = _scale(ax)
    gap = _px(2) / sy  # 2 css px of surface between the stacked segments
    w = 0.62
    for yr in years:
        a = PARTIAL_ALPHA if (last_year_partial and yr == years[-1]) else 1.0
        v_on, v_off = int(on_topic[yr]), int(off_topic[yr])
        if v_on:
            _add_bar(ax, yr - w / 2, 0, yr + w / 2, v_on, BLUE, end=None if v_off else "top", alpha=a, radius_css=3)
        if v_off:
            _add_bar(
                ax, yr - w / 2, v_on + (gap if v_on else 0), yr + w / 2, v_on + v_off, NEUTRAL, end="top", alpha=a,
                hatch="////", hatch_color=NEUTRAL_HATCH, radius_css=3,
            )
    total = on_topic + off_topic
    if label_peak:
        peak = int(total.idxmax())
        star = "*" if (last_year_partial and peak == years[-1]) else ""
        ax.text(peak, total[peak] + ymax * 0.04, f"{int(total[peak]):,}{star}", ha="center", va="bottom", fontsize=8.8, color=INK, fontweight="bold")
        last_full = years[-2]
        if last_full != peak and total[last_full] > 0:
            ax.text(last_full, total[last_full] + ymax * 0.04, f"{int(total[last_full]):,}", ha="center", va="bottom", fontsize=8.8, color=INK, fontweight="bold")
    ax.set_xticks(years)
    ax.set_xticklabels([str(y) if y != years[-1] else f"{y}*" for y in years], fontsize=8.5)


def fig_sea_vietnam(sea: pd.DataFrame, out: Path, snapshot: str, n: int, note: str = "") -> Path:
    """Two panels: all Southeast Asia works, and the Vietnam subset, each split by the on-topic judgment."""
    _style()
    years = list(range(T.YEAR_MIN, T.YEAR_MAX + 1))
    on_flag = sea["judged_on_topic"].fillna(False).astype(bool)
    vn_flag = sea["vn_flag"].astype(bool)

    def per_year(mask) -> pd.Series:
        return sea[mask].groupby("year").size().reindex(years, fill_value=0)

    sea_on, sea_off = per_year(on_flag), per_year(~on_flag)
    vn_on, vn_off = per_year(on_flag & vn_flag), per_year(~on_flag & vn_flag)
    n_sea, n_vn = len(sea), int(vn_flag.sum())
    h_sea, h_vn, gap_in = 3.0, 1.55, 1.1
    top_in, bottom_in = 1.9, 0.95
    fig_h = top_in + h_sea + gap_in + h_vn + bottom_in
    fig = plt.figure(figsize=(7.2, fig_h), dpi=DPI)
    left, right = 0.75 / 7.2, 0.35 / 7.2
    ax1 = fig.add_axes([left, (fig_h - top_in - h_sea) / fig_h, 1 - left - right, h_sea / fig_h])
    ax2 = fig.add_axes([left, bottom_in / fig_h, 1 - left - right, h_vn / fig_h])
    _stacked_year_panel(ax1, years, sea_on, sea_off)
    _stacked_year_panel(ax2, years, vn_on, vn_off)
    for ax, title, on, off in (
        (ax1, "All Southeast Asia", int(on_flag.sum()), int((~on_flag).sum())),
        (ax2, "Vietnam subset", int((on_flag & vn_flag).sum()), int((~on_flag & vn_flag).sum())),
    ):
        ax.set_title(title, loc="left", fontsize=10, fontweight="bold", color=INK, pad=16)
        ax.annotate(
            f"{on + off:,} papers: {on:,} judged on topic, {off:,} not", xy=(0, 1), xycoords="axes fraction",
            xytext=(0, 3), textcoords="offset points", ha="left", va="bottom", fontsize=8.3, color=INK2,
        )
    ax2.set_xlabel("Publication year", fontsize=9, color=INK2, labelpad=6)
    fig.text(0.012, (bottom_in + (h_vn + gap_in + h_sea) / 2) / fig_h, "Papers per year", rotation=90, ha="center", va="center", fontsize=9, color=INK2)
    handles = [
        plt.Rectangle((0, 0), 1, 1, facecolor=BLUE, edgecolor="none"),
        plt.Rectangle((0, 0), 1, 1, facecolor=NEUTRAL, edgecolor=NEUTRAL_HATCH, hatch="////", linewidth=0),
    ]
    leg = fig.legend(
        handles, ["Judged on topic (read title and abstract)", "Judged not on topic"], loc="upper left",
        bbox_to_anchor=(0.035, 1 - 1.12 / fig_h), ncol=2, frameon=False, fontsize=8.8, handlelength=1.4, handleheight=0.9, columnspacing=1.6,
    )
    for t in leg.get_texts():
        t.set_color(INK2)
    subtitle = textwrap.fill(
        "Papers whose title or abstract names a Southeast Asian place, or with an author affiliated in VN, TH, MY, ID, PH, SG, KH, LA, "
        "MM, BN or TL. The Vietnam subset names Vietnam, Tonkin, Ca Mau, Mekong Delta or the East Sea of Vietnam, or has a VN affiliation. "
        "Each panel has its own vertical scale.",
        width=104,
    )
    _frame(
        fig,
        "Southeast Asia and Vietnam in the corpus, 2015 to 2026",
        subtitle,
        _source_line(snapshot, n, "* 2026 is a partial year (to 23 Sep 2026), drawn as a lighter tint." + (" " + note if note else "")),
        title_y=1 - 0.12 / fig_h,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------
def make_all(repo: Path, snapshot: str, summary: dict) -> list[Path]:
    repo = Path(repo)
    data = repo / "data" / "biblio"
    figs = repo / "docs" / "figures"
    n = summary["corpus_size"]
    venue_cov = summary["venues"]["by_group"]
    note_venues = (
        f"Repositories ({venue_cov.get('repository', 0):,} works) and works with no recoverable venue "
        f"({venue_cov.get('unattributed', 0):,}) are not ranked."
    )
    no_country = summary["papers_without_country_data"]
    note_countries = f"{no_country:,} works ({100 * no_country / n:.0f}%) have no author country in OpenAlex."
    outs = [
        fig_papers_per_year(pd.read_csv(data / "papers_per_year.csv"), figs / "biblio_papers_per_year.png", snapshot, n),
        fig_top_venues(pd.read_csv(data / "top_venues.csv"), figs / "biblio_top_venues.png", snapshot, n, note_venues),
        fig_top_countries(pd.read_csv(data / "top_countries.csv"), figs / "biblio_top_countries.png", snapshot, n, note_countries),
        fig_sea_vietnam(pd.read_csv(data / "sea_vietnam.csv"), figs / "biblio_sea_vietnam_per_year.png", snapshot, n),
    ]
    return outs
