# Chess Engine

A bitboard chess engine in Rust with an NNUE neural-network evaluation, plus a
web visualizer that shows the search as it happens.

## Quick start

```bash
cargo build --release

./target/release/chess serve          # visualizer at http://127.0.0.1:8080
./target/release/chess                # UCI mode (for GUIs / Lichess)
./target/release/chess perft 6        # move generator verification
```

The engine loads `net.nnue` automatically if it sits next to the binary, or
pass `--net <path>`.

## What it does

| | |
|---|---|
| Move generation | 160 M nodes/sec, 33/33 standard perft positions exact |
| Search | alpha-beta with IIR, futility pruning, LMR, aspiration windows |
| Threading | Lazy SMP — depth 19 → 22 in 3 s going from 1 to 8 threads |
| Evaluation | NNUE, 768×4 buckets → 1024×2 → 1, **+127 Elo** over handcrafted |
| Protocol | UCI — runs in any chess GUI |

### Measured changes

Every change goes through an engine-vs-engine match, because node counts and
intuition both mislead:

| Change | Result |
|---|---|
| IIR + futility + recapture extensions | **+191 Elo** |
| NNUE v2 (king buckets, 1024 wide, 11.2M positions) | **+127 Elo** (v1 was +60) |
| Lazy SMP (8 threads) | depth 19 → 22 in 3 s |
| SEE pruning in the main search | **−191 Elo — reverted** |
| Logarithmic LMR table + history adjustment | ±0 Elo, shallower search — reverted |
| King-capture fix | ±0 Elo, but fixed a hard crash |

Network numbers are the *isolated* contribution: the same binary with and
without the net, so they exclude search gains and are comparable across
versions.

**These figures do not add up.** The whole engine measures **+53 Elo** against
the previous complete version (old search + v1 net), not +191 plus +127. Two
reasons: Elo gains compound sub-linearly rather than summing, and the search
improvements were measured against the *handcrafted* evaluation — a strong
network already finds good moves, so sharper pruning buys less. The
end-to-end number against the previous engine is the honest headline; the
per-change numbers show which direction each change moved.

## Architecture

**Move generation** (`src/movegen.rs`, `src/attacks.rs`) — magic bitboards for
sliding pieces, fully legal generation with pins and checks resolved up front,
so no move needs a make/unmake legality check.

**Search** (`src/search.rs`) — iterative deepening with aspiration windows,
alpha-beta, transposition table, quiescence with SEE pruning, null-move
pruning, late move reductions, killers and history heuristics.

**Evaluation** (`src/nnue.rs`, `src/eval.rs`) — an incrementally-updated
neural network. When a piece moves only two feature columns change, so the
first layer is never recomputed; that is what makes it fast enough to call at
every node. Falls back to tapered piece-square tables when no net is loaded.

**Visualizer** (`src/server.rs`, `web/index.html`) — a dependency-free HTTP +
SSE server. Shows the board with the engine's intended move, a live eval bar,
each completed depth, and a search tree of the candidate moves ranked by
score, with refuted branches dimmed and the principal variation as a chain.

## Training a network

```bash
# 1. Generate positions by self-play
./target/release/chess datagen 60000 6 data/positions.txt

# 2. Label them with a stronger engine (locally, or on Modal)
python3 trainer/label.py --in data/positions.txt --out data/labeled.txt --depth 8
modal run trainer/modal_label.py --data data/positions.txt --shards 50

# 3. Train
python3 trainer/train.py --data data/labeled.txt --out net.nnue --epochs 30
modal run trainer/modal_train.py --data data/labeled.txt --epochs 30

# 4. Measure — never assume a net is better
./target/release/chess match 200 10000 --net net.nnue
```

### What actually mattered

Four nets lost badly (−241 to −417 Elo) before one won. The lessons, all found
by measurement rather than guesswork:

**Label your positions with a *different* engine.** Training on our own search
scores looked reasonable but was circular: that search is driven by the
handcrafted eval, so the net could only ever approximate the baseline it was
being measured against. Stockfish labels disagree on 37% of positions by more
than 100 cp — that disagreement is the signal. This single change took the net
from −241 Elo to +42.

**Anchor the loss to centipawns.** Training only against a win/draw/loss target
lets the network shrink its output into the sigmoid's flat region. Mean output
weight decayed from 6.3 to 1.0 out of 64, destroying int16 precision after
quantization.

**Clip weights every step.** Without it 15% of hidden activations saturated
above the clipped-ReLU bound and 48% below zero — only 37% of the layer carried
information.

**Deduplicate.** 13.3 M generated positions were only 5.4 M unique.

**Filter the openings.** Uniformly random opening moves hang pieces constantly;
23% of positions had a >3 pawn imbalance, so the net learned material counting
instead of positional judgment.

## Tests

```bash
cargo test --release              # 13 tests
cargo test --release -- --ignored # deep perft (~3 min)
```

Perft is the important one: it verifies the move generator against published
node counts, including the positions that catch en-passant discovered check,
promotion and castling edge cases. The NNUE tests verify that incremental
accumulator updates are exactly equal to a full refresh, and that the
fixed-point arithmetic has not drifted.

### Known weaknesses

- **No endgame tablebases.** The network misjudges theoretically drawn
  endings badly (a dead-drawn K+P vs K reads as −400), though search usually
  corrects it. It does not understand *opposition*: a drawn K+P vs K with the
  defending king holding the opposition evaluates the same as a winning one.
  Only 1.26% of self-play positions have ≤5 pieces, so this is low priority.

### Comparing candidates

Rank two networks by playing them **against each other**, not by comparing
each against a third party. Measuring both against a common opponent adds
that opponent's variance to both numbers: on this project, config 1 and
config 3 were measured at +132 vs +83 against the handcrafted evaluation but
+53 vs +124 against the v1 champion — opposite conclusions from the same two
nets. `scripts/duel_two_nets.py` plays them head to head in the same binary,
and promotion requires a margin whose confidence interval excludes zero.

### Hyperparameter sweep

Measured against the handcrafted eval, same binary, so the numbers are
comparable:

| Config | vs handcrafted | head-to-head vs config 1 |
|---|---|---|
| 50 epochs, λ=0.9, lr 1e-3 | +132 Elo | — |
| 80 epochs, λ=0.8, lr 1e-3 | +89 Elo | not tested |
| 30 epochs, λ=1.0, lr 7e-4 | +83 Elo | **+16 ±48 (a tie)** |

The head-to-head is the number that settles it: configs 1 and 3 are
statistically identical over 200 games, despite third-party measurements
disagreeing by 70 Elo about which was better. None of the hyperparameter
changes moved the needle, which points at training data as the binding
constraint rather than optimisation.

### Measurements that said "don't bother"

Two optimisations looked obviously right and turned out not to be:

- **Deeper self-play search for position generation.** Depth 6 and depth 10
  labels correlate at 0.98 with zero sign flips, so a deeper teacher buys
  almost nothing.
- **Stockfish depth 12 instead of depth 10.** Correlation 0.982, median
  difference 22 cp, 7% disagreeing by more than 100 cp — for 2.7x the compute.
  More positions is the better use of the same budget.
