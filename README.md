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
| Search | ~2.3 M nodes/sec, depth 16 from the start position in 1.6 s |
| Evaluation | NNUE (768→512×2→1), **+60 Elo** over the handcrafted eval |
| Protocol | UCI — runs in any chess GUI |

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
