"""Render the benchmark charts published in the README.

Numbers come from scripts/benchmark.py on benchmarks/hard.epd, every engine
at the same 1s per position on the same machine.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BG, CARD, INK, SUB = "#0d1117", "#161b22", "#e6edf3", "#8b949e"
GRID, ACCENT, DIM = "#21262d", "#f0883e", "#3d444d"


def _style(ax, fig):
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(CARD)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=SUB, labelsize=10)
    ax.grid(axis="x", color=GRID, linewidth=1)
    ax.set_axisbelow(True)


def tactics(path="docs/img/benchmark.png"):
    names = ["Stockfish 17", "Khatib", "Leela (lc0)"]
    vals = [46.7, 40.0, 33.3]
    raw = ["14/30", "12/30", "10/30"]
    colors = [DIM, ACCENT, DIM]

    fig, ax = plt.subplots(figsize=(8, 3.4), dpi=200)
    _style(ax, fig)
    y = range(len(names))
    ax.barh(y, vals, color=colors, height=0.55, zorder=3)
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, color=INK, fontsize=11)
    ax.invert_yaxis()
    ax.set_xlim(0, 60)
    ax.set_xlabel("positions solved  (%)", color=SUB, fontsize=10)
    for i, (v, r) in enumerate(zip(vals, raw)):
        ax.text(v + 1.2, i, f"{v:.1f}%  ({r})", va="center",
                color=INK if i == 1 else SUB, fontsize=10)
    ax.set_title("30 hard positions · 1 s each · same machine",
                 color=INK, fontsize=12, pad=14, loc="left")
    fig.tight_layout()
    fig.savefig(path, facecolor=BG)
    print("wrote", path)


def progress(path="docs/img/progress.png"):
    # Each point is that version's measured gain over the version before it,
    # not a cumulative rating: v7's +313 was measured against v4.
    labels = ["v1", "v2", "v3", "v4", "v7"]
    elo = [60, 127, 179, 241, 313]

    fig, ax = plt.subplots(figsize=(8, 3.2), dpi=200)
    _style(ax, fig)
    ax.grid(axis="x", color=BG, linewidth=0)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.plot(labels, elo, color=ACCENT, linewidth=2.5, marker="o",
            markersize=7, markerfacecolor=ACCENT, zorder=3)
    for x, v in zip(labels, elo):
        ax.annotate(f"+{v}", (x, v), textcoords="offset points",
                    xytext=(0, 10), ha="center", color=INK, fontsize=10)
    ax.set_ylim(0, 380)
    ax.set_ylabel("Elo gained over the previous version", color=SUB, fontsize=10)
    ax.set_title("each network beat the one before it · measured in games",
                 color=INK, fontsize=12, pad=14, loc="left")
    fig.tight_layout()
    fig.savefig(path, facecolor=BG)
    print("wrote", path)


if __name__ == "__main__":
    tactics()
    progress()
