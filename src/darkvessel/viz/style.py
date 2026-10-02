"""Shared figure style (reference palette from the project's dataviz rules).

Categorical slots are assigned in fixed order and never cycled. Detection classes use
the first three slots, which validate all-pairs for colour-vision deficiency. On the
dark SAR background the dark-mode steps are used.
"""

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
LAND_FILL = "#383835"

# Categorical slots 1-3, light and dark steps.
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70"]
SERIES_EXTENDED = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]

FONT = ["system-ui", "DejaVu Sans", "Arial", "sans-serif"]


def apply_matplotlib_style():
    import matplotlib as mpl

    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": FONT,
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "axes.edgecolor": BASELINE,
            "axes.labelcolor": INK_2,
            "axes.titlecolor": INK,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "grid.color": GRID,
            "grid.linestyle": "-",
            "grid.linewidth": 0.5,
            "legend.frameon": False,
            "savefig.facecolor": SURFACE,
        }
    )
