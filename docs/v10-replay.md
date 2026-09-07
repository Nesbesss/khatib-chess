# V10: continue v7 with self-play replay

The preselected candidate is **QAT epoch 5**, judged by **800 fixed games at
20,000 nodes**, 400 distinct `8moves_v3` openings with colours reversed.
The candidate and baseline use byte-identical copies of `target/testing/v7-engine`.
The experiment does not change the rated bot or promote a network.

## Decision

Keep v7's 1536-wide, 8 king-bucket, 8 output-bucket direct architecture.
Start from its deployed integer weights, divided by their respective scales.
This creates a new float initialization that re-exports byte-identically to v7;
it does **not** recover or verify v7's unavailable historical float checkpoint.

The earlier Lichess runs lost 266–308 Elo despite better agreement with Lichess
labels. The user's domain panel instead found a large self-play tactical
regression. This motivates retaining the known successful population and
adding newer self-play experience. It does not establish that domain alone
caused the earlier losses. A mature v7 initialization also makes five epochs a
more plausible improvement budget than random initialization.

There is a plausible chance of a modest gain, not a basis to predict a win.
V7 is already trained extensively on the replay data. This single candidate
tests the complete continuation recipe, not individual causal effects.

## Fixed recipe

| Item | Choice |
|---|---|
| Original source | Exact `data/train_30M.txt`, SHA-256 `cc1fbf4bffc0f1e1bebb46cae434fd5714422e927407533da8b27040e23e9856` |
| Fresh source | All mini `data/selfplay/shard_*.txt` available at preparation |
| Selection | Require real WDL (0, 0.5, 1); retain labelled `abs(cp) < 1000` |
| Position filtering | Preserve generator selection; no additional no-capture filter |
| Epoch | Original training rows once; fresh training rows twice; shuffle together |
| Split | 2% deterministic position hash, first four FEN fields, shared across sources |
| Loss | Existing sigmoid probability MSE with lambda 0.7, plus 0.5 clipped logit MSE |
| Optimizer | Fresh AdamW state, weight decay 1e-8 |
| Training forward | Weight rounding to deployment grids, straight-through gradients; float optimizer master weights |
| Schedule | Five complete epochs; LR 5e-6 → 5e-5 over 200 steps, cosine decay to 5e-6 |
| Batch | 8192, include final partial batch |
| CPU | Six compute threads, no loader workers, process nice 15 |
| Seed | 20260907; epoch shuffle generator independent of model RNG |
| Checkpoints | Every epoch: integer net, float weights, complete optimizer/scheduler/RNG state |
| Selection | Epoch 5 regardless of validation loss; parity failure stops the run |

Both sources carry real WDL. There are no Lichess/score-only rows whose loss
would silently ignore lambda. No new labels or game results are fabricated.
The original generator's score filter preceded Stockfish relabelling, so the
extra bound is explicitly on the final teacher score.

Repeated positions are retained but always share a split, even across sources
or with different clocks. The split is **not game-disjoint**, and original-data
diagnostics are **not an unseen holdout for v7**. They measure drift during
continuation; the independent playing match is the decision experiment.

## Implementation and checks

`trainer/pack_replay.rs` streams text into 140-byte little-endian feature rows,
with four source/split index files. It fails on missing/invalid WDL or malformed
retained FENs. No `python-chess` installation is required for training. It
preserves source counts, tail rejection counts, WDL counts, and whole-source
reservoir samples. The Python preparation step records source/cache hashes and
checks the packed samples against an independent feature decoder.

`trainer/replay_v10.py` uses the bounded memory-mapped batch loader and writes
sample-weighted diagnostics separately for each source. Resume checks source,
cache, network, binary, configuration, optimizer, scheduler and RNG provenance.
The small regression test compares uninterrupted and interrupted/resumed
trajectories exactly, including optimizer tensors and exported network bytes.

Initialization and every epoch use `trainer/parity.py` on layout probes plus
1024 samples per source: float export identity, independent integer inference
versus the actual match binary, clipping/overflow checks, and float-error gates
(MAE ≤10 cp, p95 ≤25 cp, max ≤100 cp; no sign reversals outside 100 cp).
No Rust inference change is made; this run uses the existing v7 binary.

QAT checkpoints explicitly declare `inference=quantized-forward-v1`. Their
float reference executes the actual QAT forward function; Rust still must
agree exactly with the independent integer evaluator. The checker also reports
the unrounded optimizer master model's error separately. Those master weights
are not the function used for training or validation. Standard historical
checkpoints continue to use the standard float reference and existing gates.

## Execution

The active isolated directory on the mini is:
`/Users/pandy/khatib-chess/logs/v10-qat-20260907`.
It contains private source, binary and network copies. The gzip transfer and
decompressed original file were verified against laptop SHA-256 hashes.

The runner is `scripts/run_replay_v10.sh`. It prepares, trains, then calls the
existing Fastchess harness with explicit book, binary, network, node, memory,
thread, concurrency and seed settings. The primary match uses six concurrent
games, one engine thread, Hash 64, OwnBook false, seed 20260907.

Progress: `status.json`, `run.log`, `candidate/progress.json`.
Saved epochs: `candidate/epochN.nnue`, `.nnue.pt`, `.state.pt`, `.json`,
`.parity.json`. Primary evidence: `match-epoch5/manifest.json`, `console.log`,
`games.pgn`, and Fastchess's paired statistics. The fixed game cap is not an
SPRT pass; positive Elo alone is not proof of improvement.

Results will be added after the complete epoch-5 match.

QAT epoch 1 completed in 827.7 seconds. Actual-forward diagnostic MAE improved
57.96 → 55.98 cp (original), 125.32 → 107.09 cp (fresh). The saved network
passed all 2,154 parity positions: zero mismatches/overflows, 0.505 cp
float/integer MAE and 0.995 cp maximum error.

## Preserved failed pilot

The first standard-float continuation stopped automatically at epoch 1 after
800 seconds of training. Original diagnostic MAE improved 57.96 → 55.33 cp;
fresh diagnostic MAE improved 125.32 → 106.59 cp. However, float/export MAE was
17.66 cp, p95 43.82 cp, max 100.96 cp, bias +10.45 cp. Integer/Rust mismatches
and overflows were both zero. This is a new rounding-error failure in this
continuation, not a reinterpretation of the earlier v8/v8b parity results.

The failed pilot is preserved at `logs/v10-replay-20260907`. Its weights were
not matched or promoted. The QAT run restarts from v7 with fresh optimizer and
schedule, retaining the same data and five-epoch endpoint. The immutable cache
is shared by symlink. A real gradient-update integration test of QAT passed
all 106 layout probes against Rust (max float/integer error 0.990 cp); the
uninterrupted/resumed trajectory regression also passed for QAT.

## Preparation measurements

The original file has 30,182,710 rows; 30,004,928 remain after rejecting
177,782 scores outside the bound. The 138 fresh shards have 3,289,118 rows;
3,269,305 remain after rejecting 19,813. All retained rows have real WDL.
One epoch has 29,403,668 original training rows and twice 3,203,749 fresh
training rows: 35,811,166 examples, 17.9% fresh. Diagnostics use 601,260
original and 65,556 fresh rows excluded from this continuation's training.

The 2,154-position initialization parity panel passed with zero integer/Rust
mismatches, zero observed overflows, 0.493 cp float/integer MAE, and 0.995 cp
maximum error. The deployed network SHA-256 is
`ed11fa0bd618ed70405c17adb5e0e0c75c28364cd62f02d02cc8c8540ad007ca`.

Before continuation, float diagnostic MAE is 57.96 cp on original rows and
125.32 cp on fresh rows (RMSE 79.95 / 169.56 cp). These populations have
different labels/positions, so this comparison alone is not a strength claim.
