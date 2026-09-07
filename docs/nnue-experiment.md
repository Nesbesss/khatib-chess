# Next NNUE experiment: parity, then architecture

Decision: first verify the actual v8b epoch-6 binary/net/float state used in
Fastchess. If it passes, run a fresh paired comparison of **1536/direct** and
**2048/16-unit clipped L2**, holding data, loss, schedule, split, batch order,
search source and test conditions fixed. Keep v7 deployed throughout.

The tail experiment did not resolve the severe playing-strength regression.
The corrected full-file extreme population is **13.90%** (+3000 7.82%, -3000
6.08%). The sign provenance is accepted; this experiment does not flip signs
or fabricate WDL. Lower loss on a changed population does not establish an
improvement in move selection.

## What has actually been checked here

SSH to `pandy@100.107.58.3` was denied by the local execution environment
(`Operation not permitted`) before authentication. No remote training or
matches have been run by this change.

The available local v7 network and existing binary agree exactly with the
independent Python integer evaluator on 106 layout probes, with zero observed
accumulator overflows. No local historical float checkpoints or trained v8/v8b
networks were available, so **the historical v8/v8b parity question is still open**.

Both new builds pass the Rust suite. Constructed, nonzero checkpoints for
both architectures pass float/integer/Rust integration, including actual-net
incremental audits. These establish that the checker and reproducible builds
work; they do not establish parity of the unavailable trained v8 artifacts.

## Stage 1: check historical artifacts before training

Transfer the supplied patch from this workspace, then apply it on the mini
(the check refuses conflicting source edits):

```bash
scp /Users/nesbes/chess/logs/nnue-experiment/changes.patch \
  pandy@100.107.58.3:/tmp/nnue-experiment.patch
ssh pandy@100.107.58.3 \
  'git -C /Users/pandy/khatib-chess apply --check /tmp/nnue-experiment.patch && git -C /Users/pandy/khatib-chess apply /tmp/nnue-experiment.patch'
ssh pandy@100.107.58.3
cd /Users/pandy/khatib-chess
```

Use the mini's CPU Python environment with NumPy and PyTorch already installed.
From `/Users/pandy/khatib-chess`:

```bash
python3 -c 'import torch, numpy; print(torch.__version__)'
rg --files nets data trainer | rg '(v8|state|resume).*(nnue|pt|pth|state|ckpt)'
```

Set `V8B_NET` to the **exact saved net used in the reported epoch-6 match**.
The epoch filename below is illustrative; the report does not specify its
actual path. Match manifests under `logs/sprt/*/manifest.json` record original
paths and hashes; an immutable `logs/sprt/RUN/new/net.nnue` is also suitable.
Set `PYTHON` to the Python executable that passed the import command above.

```bash
export PYTHON=python3
export V8B_NET=nets/v8b.nnue.ep6

"$PYTHON" trainer/parity.py \
  --engine target/testing/v8-engine --net "$V8B_NET" \
  --hidden 2048 --l2 16 --find-float nets --find-float data \
  --data data/lichess_clean.txt --samples 2048 --seed 20260907 \
  --out logs/nnue-parity/v8b-epoch6.json
```

`--find-float` searches `.pt`, `.pth`, `.state`, `.ckpt` files, including full
resume states, and accepts one only if re-export is **byte-identical** to the
specified net. If stored elsewhere, replace both `--find-float` arguments
with `--float /exact/path/to/state.pt`. A best-validation `.pt` need not match
an `.ep6` net. Integer weights cannot recover the original float checkpoint.

If no matching state exists, run the same command without `--find-float` to
get the integer/Rust result. `integer_only` is explicitly incomplete and the
training runner will not accept it. Recover the checkpoint from its training
machine/backup if available. Otherwise the historical float gap is unresolved;
do not claim the old match has cleared export parity. Do not retrain for hours
just to manufacture a nominally matching checkpoint.

Also check the v8 epoch-17 pair if its float state was preserved. Reuse the
same positions with `--positions logs/nnue-parity/v8b-epoch6.json` in place
of `--data`; use its own exact net/state paths.

The report separates:

- Float checkpoint/export identity (mandatory when supplying a float file).
- Independent integer inference versus the **specified binary's `eval`**,
  requiring exactly equal integer cp, including signed truncation toward zero.
- Float versus integer errors: MAE, p95, max, bias, spread, material buckets,
  side to move, substantial sign reversals and worst positions.
- Quantizer clipping in every tensor and observed accumulator/sum overflows.
- On new builds, `--audit` also runs `nnue-audit` on the actual checkpoint:
  incremental versus refresh, null moves, pop/undo, castling, en passant,
  promotions and king bucket transitions over fixed legal trees.

The historical binary's `eval` refreshes from scratch. It has no incremental
audit command; that limitation is recorded. The runner reconstructs v8 from
the arithmetic in historical commit `df9f7d6` under the `nnue-v8` Cargo feature
and also checks the historical checkpoint with this build and its audit.
This does not prove that an opaque historical binary has identical search code.

The float-error gate is deliberately conservative: **MAE <=10 cp, p95 <=25 cp,
max <=100 cp**, no sign reversals for float scores of magnitude >=100 cp, no
clipped parameters, no observed overflow. These are investigation thresholds,
not mathematical quantization guarantees or Elo bounds. Artificial layout
probes also participate in the gate; inspect the per-position report if they
dominate a failure. Do not relax thresholds merely to get training to continue.

Zero integer mismatches with large float error points to quantization/calibration
loss. Integer mismatches point to the binary/layout/scaling/features path.
Either failure stops this experiment before matches. Fix and recheck that
issue, then re-test the preserved failing checkpoint against v7 before drawing
conclusions about its training. There is no evidence yet that either bug exists.

## Stage 2: execute the controlled comparison

After the full historical report says `pass`:

```bash
PYTHON="$PYTHON" bash scripts/run_architecture_ab.sh \
  logs/nnue-parity/v8b-epoch6.json logs/architecture-ab-01
```

The script requires the existing Fastchess installation and prepared
`data/books/8moves_v3.unique.epd`. It creates fresh build/run directories,
copies v7's net, saves source/binary/net/data hashes and software versions,
runs both Rust test suites, rechecks historical parity, prepares the cache,
trains sequentially, and then runs three matches. It never writes `net.nnue`,
`nets/v7.nnue`, `target/release/chess`, the saved testing binaries, or either
bot script. It does not kill/restart processes or promote a network.

| Control | Both arms |
|---|---|
| Data | Entire `data/lichess_clean.txt`, no additional filtering/relabeling |
| Targets | Existing sigmoid score MSE + 0.5 clipped cp/logit MSE; no WDL |
| Split | ~98/2%, hash of first four FEN fields; counters ignored |
| Order | Same deterministic shuffled training indices each epoch |
| Seed | 20260907; separate shuffle generator, independent of initialization |
| Optimizer | AdamW, weight decay 1e-8, max LR 0.001 |
| Schedule | OneCycle planned for 20 epochs, **stop after 6** |
| Batch | 8192, include partial final batch, losses weighted by sample count |
| CPU | 6 compute threads, no loader workers, `nice -n 10` |
| Primary checkpoint | Epoch 6, fixed in advance; every epoch also saved/audited |
| Search | Same current source and options; only evaluator feature differs |

The two architectural differences (width and extra layer) are tested as a
bundle. This does not identify which individual architectural change matters.
The new common split, batch and deterministic initialization/order make these
two runs controlled against each other; the old v8b run is context, not the
paired control. Six epochs only address performance within this training
budget, not each architecture's asymptotic optimum. A repeated seed is needed
before generalizing a modest difference.

The compact cache uses 140 bytes/position plus four bytes of indices:
about **4.95 GB on disk for 34.4M rows**, memory-mapped, with bounded preparation
buffers. The random index permutation is ~270 MB; no 34M-row Python lists or
worker copies are made. Leave roughly 8 GB free for cache, checkpoints, build
outputs and logs. The cache stores the full input SHA-256 and independently
checks the provided clean file on reuse. Incomplete caches lack a manifest and
are refused; use a fresh `CACHE=...` path after a failed preparation.

Same FENs cannot cross the split merely because clocks differ. Without game
identifiers this is **not** a game-disjoint split; correlated positions may
remain. Validation is diagnostic, never the checkpoint-selection criterion.

Using the prior 20-minute epoch estimate gives roughly four hours for twelve
epochs plus preparation/parity/matches. Actual timing can differ with six
threads, smaller batches and the new loader; epoch logs record it. Training
and matches never overlap. Six threads and low priority leave capacity for
the bot, although its latency still depends on other processes on the mini.

## Match endpoint and interpretation

Three **fixed 300-game** diagnostics, 150 distinct color pairs each:

1. Direct epoch 6 versus v7's net in the same direct build.
2. L2 epoch 6 versus that v7 baseline.
3. Direct epoch 6 versus L2 epoch 6 (the primary architecture contrast).

All use Fastchess, fixed 20,000 nodes, `8moves_v3.unique.epd`, seed 20260907,
two concurrent games, one thread/engine, 64 MiB hash, OwnBook off. Same seed
and book give the same opening schedule. Inspect Fastchess's paired estimates
and intervals and audit PGNs using `scripts/audit_match.py` as described in
`docs/testing.md`. These finite matches use `--fixed-games`, not an SPRT verdict.
Do not subtract two versus-v7 Elo point estimates to infer a significant
architecture effect; use the direct head-to-head result.

If direct recovers substantially and beats L2 head-to-head, the architecture
bundle is implicated at this budget. If both remain far behind v7 and parity
passes, architecture alone does not explain the deficit; next investigate
the self-play/quiet-position distribution, labeling protocol, WDL and training
objective using v7 architecture. This experiment does not separate those.
If both fresh runs recover, earlier optimization/run provenance deserves
inspection. Wide intervals or small differences remain inconclusive.

There is **no automatic promotion**. Fixed nodes ignore evaluator speed.
Any eventual candidate needs an independent longer confirmation with real
time controls and the intended deployment threading before replacing v7.

## Interruptions and validation commands

The wrapper deliberately refuses an existing run directory. Resume an arm at
an epoch boundary using its exact original configuration (change `direct` to
`l2` and `build-direct` to `build-l2` for the other arm):

```bash
nice -n 10 "$PYTHON" trainer/architecture_ab.py train \
  --cache data/architecture-ab-v1 --out logs/architecture-ab-01/direct \
  --arm direct --engine logs/architecture-ab-01/build-direct/release/chess \
  --threads 6 --batch 8192 --lr 0.001 --schedule-epochs 20 --stop-after 6 --resume
```

Resume restores AdamW, OneCycle, RNG and epoch. The cache, source, binary,
thread/batch/learning-rate settings must match. A failed parity epoch has no
new resume state; the previous passing epoch remains intact. A kill partway
through an epoch repeats that epoch. For matches, use `scripts/sprt.py --resume`
on the specific match directory; do not rerun the wrapper or pool new tests.

Local verification commands (isolated build paths):

```bash
cargo test --release --target-dir target/nnue-experiment/direct
cargo test --release --features nnue-v8 --target-dir target/nnue-experiment/l2
DIRECT_ENGINE="$PWD/target/nnue-experiment/direct/release/chess" \
L2_ENGINE="$PWD/target/nnue-experiment/l2/release/chess" \
  "$PYTHON" -m unittest discover -s trainer -p test_experiment.py -v
bash -n scripts/run_architecture_ab.sh
```

Python tests cover both real binary formats, independent feature encoding,
signed division, full bucket coverage, mismatched checkpoint rejection,
overflow detection, disk-cache packing/splits/order, and exact epoch-resume
training continuity. Rust tests also cover deeper accumulator move trees.

A local one-epoch toy-data smoke completed through export/audit for the L2
arm. The direct arm deliberately stopped at the conservative float-error
gate (15.55 cp MAE, 43.08 cp p95), while integer/Rust parity was exact for
both. This verifies the stop path and shows why integer agreement alone is
insufficient. Toy-data errors are not evidence about the trained v8/v8b nets.
