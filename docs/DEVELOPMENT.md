# Khatib

A bitboard chess engine in Rust with an NNUE neural-network evaluation,
plus a web visualizer that shows the search as it happens.

Named after its author, Nassim Khatib.

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
| Evaluation | NNUE, 768×8 buckets → 1536×2 → 8 output buckets, **+313 Elo** over the previous net |
| Protocol | UCI — runs in any chess GUI |

### Measured changes

Every change goes through an engine-vs-engine match, because node counts and
intuition both mislead:

| Change | Result |
|---|---|
| NNUE v7 (1536 wide, Stockfish-labelled, 8 output buckets) | **+313 Elo** over v4 |
| IIR + futility + recapture extensions | **+191 Elo** |
| Countermove history | **+117 ± 51 Elo** (200 games) |
| NNUE v3 (24.3M positions) | +179 Elo, and +117 head-to-head vs v2 |
| NNUE v2 (king buckets, 1024 wide, 11.2M positions) | +127 Elo (v1 was +60) |
| Logarithmic LMR table | **+58 ± 35 Elo** (380 games, 50 openings) |
| Lazy SMP (8 threads) | depth 19 → 22 in 3 s |
| SEE pruning in the main search | −191 Elo — reverted, **unverified** |
| King-capture fix | ±0 Elo, but fixed a hard crash |

Countermove history and logarithmic LMR stack to **+56 Elo**, not +177: better
move ordering is what makes late-move reductions safe, so the two claim
overlapping ground.

### A measurement bug that invalidated earlier results

Every match script generated its opening positions with a *one-node* search,
which is deterministic — so all "200 games" were the same game played 200
times. It surfaced when a net played against itself and scored +10 =10 −10
instead of something near even.

Three changes had been rejected on that broken harness. Re-measured with 50
distinct openings, countermove history was **+117** (recorded as −191) and
logarithmic LMR was **+60** (recorded as ±0) — both are now in the engine. The
SEE-pruning result above has not been re-measured and should not be trusted.
A single lost game replayed produces exactly −191 Elo, which is what those
numbers were.

Network numbers are the *isolated* contribution: the same binary with and
without the net, so they exclude search gains and are comparable across
versions.

**These figures do not add up, and that is expected.** Elo gains compound
sub-linearly rather than summing, and search improvements measured against the
*handcrafted* evaluation buy less once a strong network is in place — it
already finds good moves. Per-change numbers show which direction a change
moved; only the end-to-end number against a previous complete version is a
headline.

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

## Current state

`net.nnue` is NNUE v7: **+313 Elo** over v4, trained on Stockfish-labelled
positions.

**Measured strength: ~2544 Elo** — 42% against Stockfish limited to 2600
(50 games, colour-reversed opening pairs). Strong club level: it will beat
most club players and lose to a titled one.

Every candidate network is kept under `nets/`; training data lives in `data/`
and is not committed.

Cloud training is paused — the Modal workspace hit its spend limit. Everything
below still works locally (slower); raising the limit resumes the cloud path
unchanged.

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

### Correctness fixes that are not Elo

The network evaluated dead-drawn king-and-pawn endings at +600 to +825 — five of
seven test positions disagreed with Stockfish about whether the position was even
a draw. Opposition is a rule, not a pattern, so exact rules now handle K+P vs K
before the network is consulted, and all five now read 0.

**This is very unlikely to be worth measurable Elo.** K+P vs K arises in 0.083%
of positions (331 of 400,000 sampled), so the effect is far below what a few
hundred games can resolve. It is in because it is *right*, not because it is
worth rating points — and it is listed here rather than in the results table for
that reason.

### Known weaknesses

- **No endgame tablebases.** The network misjudges theoretically drawn
  endings badly (a dead-drawn K+P vs K reads as −400), though search usually
  corrects it. It does not understand *opposition*: a drawn K+P vs K with the
  defending king holding the opposition evaluates the same as a winning one.
  Only 1.26% of self-play positions have ≤5 pieces, so this is low priority.

### Where more data stopped paying

| Training positions | Result |
|---|---|
| 2.5M → 11.2M | +67 Elo |
| 11.2M → 24.3M | +117 Elo |
| **24.3M → 36.4M** | **±0 — three checkpoints, none beat the 24.3M net** |

The 36.4M run reached the best validation loss of any net here (0.0275 against
0.0301) and its checkpoints measured −111, −3 and −7 Elo against the champion.
More data has stopped buying strength at this network size, which points at
capacity rather than data as the next constraint — a wider layer or more
feature buckets.

That is also the third time on this project validation loss pointed the wrong
way, so the conclusion above is stated from the games, not the loss curve.

### Where the next gain is

Learning curves, comparing 11.2M against 24.3M training positions:

| | val loss @40 | train/val gap | slope at the end |
|---|---|---|---|
| 11.2M positions | 0.0334 | +0.0150 | plateaued |
| 24.3M positions | 0.0291 | +0.0089 | plateaued |

Both runs plateau on validation loss, which looked like the architecture had
become the constraint. **That inference was wrong.** Played head to head, the
24.3M-position net beats the 11.2M one by **+117 Elo** (±51 over 200 games).

A plateau in validation loss does not mean the extra data was wasted: the loss
measures agreement with Stockfish scores, while playing strength depends on
getting the *ordering* of candidate moves right. Only games measure that. This
is the same lesson as the SEE-pruning and LMR reverts, from the opposite
direction — proxies mislead in both directions, so measure in games.

### Which checkpoint to keep

The trainer's best-validation-loss checkpoint is not necessarily the strongest
net. On the 24.3M-position run, the final epoch-50 net (best val loss 0.02884)
measured **−44 Elo** against an epoch-43 checkpoint — the last seven epochs
improved the loss and did not improve play. Use `--checkpoint-every` to export
intermediate nets and rank them by games.

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

### Node counts are not a proxy for strength

Three separate changes on this project reduced node counts and did not improve
play:

| Change | Nodes | Elo |
|---|---|---|
| SEE pruning in main search | fewer | −191 |
| Logarithmic LMR table | fewer at some depths | ±0, shallower |
| Countermove + continuation history | **−40%** | **−191** |

Fewer nodes to reach a given depth means the *ordering* improved, but pruning
harder on that ordering can cut lines that mattered. The same holds in reverse
for validation loss (see above). Only games measure strength.

### Measurements that said "don't bother"

Two optimisations looked obviously right and turned out not to be:

- **Deeper self-play search for position generation.** Depth 6 and depth 10
  labels correlate at 0.98 with zero sign flips, so a deeper teacher buys
  almost nothing.
- **Stockfish depth 12 instead of depth 10.** Correlation 0.982, median
  difference 22 cp, 7% disagreeing by more than 100 cp — for 2.7x the compute.
  More positions is the better use of the same budget.

## Benchmarks

Two suites, both with answers verified by deep search:

```bash
python3 scripts/benchmark.py --all --ms 1000                      # standard tactics
python3 scripts/benchmark.py --all --ms 500 --suite benchmarks/hard.epd
```

| Suite | Ours | Stockfish 18 |
|---|---|---|
| Tactics (25 positions, 1 s) | 22/25 — 88% | 25/25 — 100% |
| Hard (30 positions, 0.5 s) | 8/30 — 27% | 15/30 — 50% |
| Hard (30 positions, 2 s) | 10/30 — 33% | 15/30 — 50% |

The hard suite is generated, not hand-written. `scripts/make_suite.py` samples
real positions, settles each with a long multi-threaded search, then keeps only
those a short search gets *wrong* — so the answer is correct by construction and
the position is hard by construction.

This matters: of twenty hard positions written from memory, **thirteen had
answer keys that deep search disagreed with**, including one labelled a forced
mate that wasn't even best. A hand-written suite measures the author's memory
before it measures any engine.

Stockfish plateaus at 50% on the hard suite while extra time still helps this
engine, which is the useful signal — the remaining positions need evaluation
quality rather than more search.
