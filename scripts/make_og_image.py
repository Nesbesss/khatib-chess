"""Render the social preview card used by Discord/Twitter/Slack embeds.

1200x630 is the size those platforms crop to; anything else gets letterboxed.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import matplotlib.patches as patches

BG, CARD, INK, SUB, ACCENT, DIM = "#0d1117", "#161b22", "#e6edf3", "#8b949e", "#f0883e", "#3d444d"

fig = plt.figure(figsize=(12, 6.3), dpi=100)
fig.patch.set_facecolor(BG)

# Logo, top-left.
try:
    logo = mpimg.imread("docs/logo.png")
    ax = fig.add_axes([0.055, 0.60, 0.15, 0.30])
    ax.imshow(logo); ax.axis("off")
except Exception:
    pass

fig.text(0.225, 0.755, "Khatib", color=INK, fontsize=62, fontweight="bold",
         va="center", ha="left")
fig.text(0.225, 0.655, "a chess engine in Rust with an NNUE neural network",
         color=SUB, fontsize=20, va="center", ha="left")

# Three stat tiles.
stats = [("5-0", "vs human players"), ("95M", "training positions"),
         ("163M", "moves / second")]
for i, (big, small) in enumerate(stats):
    x = 0.055 + i * 0.315
    ax = fig.add_axes([x, 0.30, 0.28, 0.22])
    ax.set_facecolor(CARD)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks([]); ax.set_yticks([])
    ax.text(0.5, 0.62, big, color=ACCENT, fontsize=40, fontweight="bold",
            ha="center", va="center", transform=ax.transAxes)
    ax.text(0.5, 0.24, small, color=SUB, fontsize=15,
            ha="center", va="center", transform=ax.transAxes)

# Benchmark strip along the bottom.
fig.text(0.055, 0.185, "30 hard positions, 1s each:", color=SUB, fontsize=15,
         va="center", ha="left")
bars = [("Stockfish 17", 46.7, DIM), ("Khatib", 40.0, ACCENT), ("Leela", 33.3, DIM)]
for i, (name, val, col) in enumerate(bars):
    x = 0.30 + i * 0.235
    fig.text(x, 0.185, f"{name}  {val:.0f}%", color=INK if col == ACCENT else SUB,
             fontsize=15, va="center", ha="left",
             fontweight="bold" if col == ACCENT else "normal")

fig.text(0.055, 0.075, "github.com/Nesbesss/khatib-chess", color=SUB, fontsize=14,
         va="center", ha="left")

fig.savefig("docs/img/social.png", facecolor=BG)
print("wrote docs/img/social.png")
