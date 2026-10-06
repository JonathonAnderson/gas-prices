"""Shared chart styling and helpers for the four replication notebooks.

One series per chart, so one hue (#2a78d6, validated against the light surface).
Text uses ink tokens, never the series color. Bars are capped at 24 px thick with
4 px rounded data-ends; lines are 2 px with an 8 px end marker ringed in the surface color.
"""

from __future__ import annotations

import datetime as dt
import textwrap
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import polars as pl
from matplotlib.patches import FancyBboxPatch, Rectangle

OUT = Path(__file__).resolve().parent / "output"

# ------------------------------------------------------------------ tokens
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"
SERIES = "#2a78d6"

mpl.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 150,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": "sans-serif", "font.size": 10,
    "axes.edgecolor": BASELINE, "axes.labelcolor": INK2,
    "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 1, "grid.linestyle": "-",
    "axes.axisbelow": True, "ytick.left": False, "xtick.bottom": False,
})

# ------------------------------------------------------------------ administration windows
# January of the inauguration year through December of the last full year; Trump 2.0 ends in
# August 2026, the last month with CPI data when the article was written.
ADMINS: dict[str, tuple[dt.date, dt.date]] = {
    "Obama": (dt.date(2009, 1, 1), dt.date(2016, 12, 1)),
    "Trump 1.0": (dt.date(2017, 1, 1), dt.date(2020, 12, 1)),
    "Biden": (dt.date(2021, 1, 1), dt.date(2024, 12, 1)),
    "Trump 2.0": (dt.date(2025, 1, 1), dt.date(2026, 8, 1)),
}


def admin_labels(admins: dict[str, tuple[dt.date, dt.date]] = ADMINS) -> list[str]:
    return [f"{k}\n({a.month}/{a.year} - {b.month}/{b.year})" for k, (a, b) in admins.items()]


def period_mean(df: pl.DataFrame, col: str, admins: dict[str, tuple[dt.date, dt.date]] = ADMINS) -> list[float]:
    """Mean of `col` over each window, using the DataFrame's `date` column."""
    return [df.filter(pl.col("date").is_between(a, b))[col].mean() for a, b in admins.values()]


def compare(names, recomputed, article, digits: int = 2) -> pl.DataFrame:
    """Small table of recomputed versus article values."""
    r = [round(v, digits) if digits else round(v) for v in recomputed]
    return pl.DataFrame({"administration": list(names), "recomputed": r, "article": article})


# ------------------------------------------------------------------ layout helpers


def px_to_data(ax, px: float) -> tuple[float, float]:
    """(dx, dy) in data units for a px-long step along each axis; call after limits are set."""
    inv = ax.transData.inverted()
    x0, y0 = inv.transform((0, 0))
    x1, y1 = inv.transform((px, px))
    return abs(x1 - x0), abs(y1 - y0)


def titled(fig, title: str, subtitle: str | None = None, source: str | None = None) -> tuple[float, float]:
    """Title and subtitle at the top, source at the bottom, each wrapped to the figure width.

    Returns (top, bottom) fractions for fig.subplots_adjust so the plot never overlaps the text.
    """
    w_in = fig.get_figwidth()
    t_lines = textwrap.wrap(title, width=int(w_in * 8.5))
    s_lines = textwrap.wrap(subtitle, width=int(w_in * 11.5)) if subtitle else []
    src_lines = textwrap.wrap(source, width=int(w_in * 14)) if source else []
    fig.text(0.02, 0.97, "\n".join(t_lines), ha="left", va="top", fontsize=13, fontweight="bold", color=INK, linespacing=1.25)
    y = 0.97 - 0.058 * len(t_lines) - 0.012
    if s_lines:
        fig.text(0.02, y, "\n".join(s_lines), ha="left", va="top", fontsize=10, color=INK2, linespacing=1.3)
        y -= 0.042 * len(s_lines)
    if src_lines:
        fig.text(0.02, 0.015, "\n".join(src_lines), ha="left", va="bottom", fontsize=8, color=MUTED, linespacing=1.3)
    return y - 0.05, 0.04 + 0.033 * len(src_lines)


def rounded_bar(ax, x: float, height: float, width: float, radius_px: float = 4, color: str = SERIES) -> None:
    """Square at the baseline, rounded at the data end; a rectangle plus a rounded cap."""
    rx, ry = px_to_data(ax, radius_px)
    ry = min(ry, height / 2)
    ax.add_patch(Rectangle((x - width / 2, 0), width, max(height - ry, 0), color=color, lw=0))
    ax.add_patch(
        FancyBboxPatch(
            (x - width / 2, max(height - 2 * ry, 0)),
            width,
            2 * ry if height > 2 * ry else height,
            boxstyle=f"round,pad=0,rounding_size={rx}",
            mutation_aspect=ry / rx,
            color=color,
            lw=0,
        )
    )


# ------------------------------------------------------------------ chart forms


def bar_chart(labels, values, *, title, subtitle, source, fmt, ymax=None, ystep=None, fname=None,
              figsize=(7.2, 4.4), label_size=9.5, value_size=11):
    """Single-series column chart with value labels on the caps. Saves to output/<fname> if given."""
    fig, ax = plt.subplots(figsize=figsize)
    top, bottom = titled(fig, title, subtitle, source)
    fig.subplots_adjust(left=0.08, right=0.98, top=top, bottom=bottom + 0.09)  # room for two-line x labels
    n = len(values)
    ax.set_xlim(-0.6, n - 0.4)
    top_y = ymax if ymax is not None else max(values) * 1.18
    ax.set_ylim(0, top_y)
    if ystep:
        ax.set_yticks(np.arange(0, top_y + 1e-9, ystep))
    ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda v, _: fmt(v)))
    fig.canvas.draw()  # transforms need the final layout before px_to_data
    w, _ = px_to_data(ax, 24)
    _, dy = px_to_data(ax, 6)
    for i, v in enumerate(values):
        rounded_bar(ax, i, v, w)
        ax.text(i, v + dy, fmt(v), ha="center", va="bottom", fontsize=value_size, fontweight="bold", color=INK)
    ax.set_xticks(range(n), labels, fontsize=label_size, color=INK2)
    ax.tick_params(axis="x", length=0, pad=8)
    ax.spines["bottom"].set_color(BASELINE)
    if fname:
        OUT.mkdir(exist_ok=True)
        fig.savefig(OUT / fname)
    return fig


def line_chart(dates, values, *, title, subtitle, source, ylim, ystep, yfmt, end_label, fname=None,
               figsize=(8.9, 4.4), year_step=5, xpad_years=5, bands=None, marks=None, mark_fmt=None):
    """Single-series line with an end marker and end label. Saves to output/<fname> if given.

    `bands` is an optional {label: (start, end)} mapping (e.g. ADMINS); each window is marked by a
    hairline at its start, with its label set in muted ink along the top of the plot.
    `marks` is an optional {label: date} mapping; each date gets a hairline, a ringed marker on the
    last observation at or before it, and a label with the value (formatted by `mark_fmt`, else `yfmt`).
    """
    x = np.asarray(dates)
    y = np.asarray(values, dtype=float)
    fig, ax = plt.subplots(figsize=figsize)
    top, bottom = titled(fig, title, subtitle, source)
    fig.subplots_adjust(left=0.07, right=0.98, top=top, bottom=bottom + 0.05)
    ax.plot(x, y, color=SERIES, lw=2, solid_joinstyle="round", solid_capstyle="round")
    ax.plot(x[-1:], y[-1:], marker="o", ms=8, mfc=SERIES, mec=SURFACE, mew=2, lw=0)
    ax.annotate(end_label, (x[-1], y[-1]), xytext=(10, 0), textcoords="offset points", va="center",
                fontsize=9.5, fontweight="bold", color=INK, linespacing=1.3)
    ax.set_ylim(*ylim)
    ax.set_yticks(np.arange(ylim[0], ylim[1] + 1e-9, ystep))
    ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda v, _: yfmt(v)))
    first_year = x[0].astype("datetime64[Y]").astype(int) + 1970
    last_year = x[-1].astype("datetime64[Y]").astype(int) + 1970
    ax.set_xlim(np.datetime64(f"{first_year - 1}-07-01"), np.datetime64(f"{last_year + xpad_years}-01-01"))
    ax.xaxis.set_major_locator(mpl.dates.YearLocator(year_step))
    ax.xaxis.set_major_formatter(mpl.dates.DateFormatter("%Y"))
    ax.tick_params(axis="x", pad=6)
    ax.spines["bottom"].set_color(BASELINE)
    if bands:
        for label, (a, b) in bands.items():
            ax.axvline(np.datetime64(a), color=BASELINE, lw=1, ls=(0, (3, 3)), zorder=0.5)
            ax.text(np.datetime64(a), ylim[1], f" {label}", ha="left", va="top", fontsize=8.5, color=MUTED)
    for label, when in (marks or {}).items():
        i = int(np.searchsorted(x, np.datetime64(when), side="right")) - 1
        ax.vlines(np.datetime64(when), ylim[0], ylim[0] + 0.92 * (ylim[1] - ylim[0]), color=MUTED, lw=1, zorder=0.6)
        ax.plot(x[i:i + 1], y[i:i + 1], marker="o", ms=8, mfc=SERIES, mec=SURFACE, mew=2, lw=0)
        ax.annotate(f"{label}\n{(mark_fmt or yfmt)(y[i])}", (x[i], y[i]), xytext=(8, -9), textcoords="offset points",
                    ha="left", va="top", fontsize=9, color=INK, linespacing=1.3)
    if fname:
        OUT.mkdir(exist_ok=True)
        fig.savefig(OUT / fname)
    return fig
