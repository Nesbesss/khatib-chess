# Engine testing

Run tests on the M4 at `pandy@100.107.58.3`, in
`/Users/pandy/khatib-chess`. The wrapper runs Fastchess; the old Python match
loops and their repetition/referee logic have been removed.

## One-command tests

For a search change, save the old executable before rebuilding and compare:

```bash
python3 scripts/sprt.py target/release/chess target/testing/v7-engine \
  --new-net nets/v7.nnue --old-net nets/v7.nnue
```

Both net arguments default to `nets/v7.nnue`. For different architectures,
provide each binary and its compatible network explicitly:

```bash
python3 scripts/sprt.py target/testing/v8-engine target/testing/v7-engine \
  --new-net nets/v8.nnue --old-net nets/v7.nnue

# Equivalent convenience entry point; accepts a saved v8 checkpoint:
bash scripts/test_v8.sh nets/v8.nnue
```

The saved v8 binary was recovered from the existing local scratch build and
verified to report 2048 hidden units on the remote Mac. Its source is not
committed. It is a supplied artifact, not a new build from this checkout.
The supplied v8 checkpoint is only useful for harness/throughput validation;
it predates the corrected training labels. Smoke matches are not strength evidence.

Defaults are 200 ms real time per move, one thread and 64 MiB hash per engine,
OwnBook disabled, no pondering, and a maximum of 40,000 **total games**.
Concurrency comes from `target/testing/concurrency.json`, falling back to four
if the machine has not been calibrated. Change it with `--concurrency N`.

The SPRT uses regular/logistic Elo bounds `[0, 5]`, alpha=beta=0.05,
and Fastchess's paired (pentanomial) statistics. Its nominal LLR boundaries
are +/-2.944. A pass rejects zero improvement under this test model; it does
not prove a gain of at least five Elo. A failure does not exclude smaller
gains. Hitting the game cap without crossing a boundary is inconclusive.
Do not repeatedly restart a failed test until it passes.

## Timing and concurrency

`st=0.2` makes Fastchess send `go movetime 200`. It measures real computation
cost, including differences in network architecture, while bypassing the
`wtime`/`btime` path and its below-30-second panic rule. It does not exercise
time management or establish a Lichess blitz Elo gain.

For confirmation with the existing engine's real clock management:

```bash
python3 scripts/sprt.py NEW OLD --new-net NEW.nnue --old-net OLD.nnue --tc 60+0.6
```

That starts outside panic mode; the existing policy still applies if the
clock later drops below 30 seconds. The engine source and both supplied
binaries' time management have not been altered. Avoid `10+0.1` as a normal
clock test: it immediately caps searches near 80 ms. A future engine fix
should base emergency spending on remaining time relative to increment and
overhead, rather than a universal 30-second threshold, and receive a separate
clocked test. Fixed nodes (`--nodes N`) are available for diagnostics, but
exclude evaluator speed from the strength comparison.

Recalibrate the actual binary/net pair when the workload changes:

```bash
python3 scripts/calibrate_testing.py NEW OLD \
  --new-net NEW.nnue --old-net OLD.nnue --save-default
```

The calibration repeats shuffled sweeps of 4, 6, 8 and 10 workers. Each
worker owns two engines, searches both at 500,000 requested nodes on the
same roots, and clears the TT before each search. No pondering occurs.
It records aggregate completed search cycles/second and per-engine median
and p95 search latency. A cycle means two searches, not a chess game pair.
Startup and warm-up are excluded; runs use three 25-second trials per setting.

Selection rule: the smallest concurrency within 5% of peak median aggregate
throughput. This is a practical compute-throughput criterion, not a measured
optimum for Elo information/hour. Higher concurrency can reduce per-game
search depth at a fixed wall-time limit. Keep time control, concurrency and
thread count fixed within each test; use a longer movetime if greater search
depth is needed. Check SMP changes at the deployment thread count as well.

The recorded calibration includes the converter and existing background
processes. It is conditional on that workload, not a claim about an idle M4.
The full timings, process names, configuration and binary/net hashes live
in `logs/calibration/`; `target/testing/concurrency.json` points to the report.

## Opening book and provenance

We use `8moves_v3.pgn` from the official Stockfish books repository, at commit
`65815ccdbc7727cd4f6aee252ba8f67fb740e92f`. All 34,700 games were legally
replayed with python-chess 1.11.2. All end after 16 plies, are nonterminal,
and have distinct final positions after canonicalizing legal en-passant
rights. The prepared book is `data/books/8moves_v3.unique.epd`.

This standard, comparatively neutral opening suite is a starting choice for
Khatib. UHO/Pohl books deliberately select unbalanced positions to reduce
draws; whether that improves statistical efficiency here needs a valid
measured draw rate and pair-score variance. No claim is made that this book
is optimal for Khatib, nor that distinct positions are perfectly independent.

Each run shuffles the book once using a recorded seed, saves the exact
schedule, and gives it to Fastchess in sequential order. Each opening occurs
once with each colour assignment. The wrapper refuses a requested game cap
that would cycle the book. EPD starts omit pre-opening repetition history
and reset move counters, as is usual for position-based test books.

The book is CC0-1.0. Provenance and SHA-256 hashes are in
`data/books/8moves_v3.json`. Verified prepared EPD SHA-256:
`5c6ae8f7a89eaf8524c213445828e0df09ca1210bd6bc016eb10b8a9f7f5e93f`.

## Results, interruption and auditing

Every run gets a new `logs/sprt/TIMESTAMP-ID/` directory containing:

- Separate immutable copies of both binaries and both networks.
- A preflight log per engine, verifying network loading, UCI options and a search.
- Exact opening schedule, command, settings and SHA-256 hashes in the manifest.
- A copy of Fastchess, PGNs, console/diagnostic logs and resumable configuration.

Network size/load failures abort before any matches. Nets are copied, never
symlinked, so a subsequent training checkpoint cannot change a running match.
Fastchess handles legal moves, terminal states and repetition. Score-based
adjudication and arbitrary move-count draws are disabled. Strict mode makes
warnings fail the run; inspect any timeout, crash or protocol error.

Interrupt with Ctrl-C; resume the saved runner state using the printed command:

```bash
python3 scripts/sprt.py --resume logs/sprt/RUN
```

Resume refuses changed binary/net/book/runner snapshots and preserves the
original settings. The Fastchess config autosaves after each pair's worth of
games. A hard crash can lose work since the last save; do not manually append
an unrelated run to the PGN or combine incompatible test settings.

To independently replay a completed match and check every opening pair:

```bash
target/testing/venv/bin/python scripts/audit_match.py logs/sprt/RUN
```

The audit checks legal moves and board outcomes, the saved opening schedule,
exactly two games per root with swapped colours, and independently computes
pentanomial counts. An interrupted match can legitimately fail this complete-
pair audit until resumed.

For a small integration check, use `--fixed-games --games 16`; that disables
SPRT and does not constitute an Elo acceptance test.

`duel.py`, `duel_nets.py`, `duel_two_nets.py` and `test_v8.sh` now delegate to
the new runner. **`--games` means total games everywhere**, whereas the old
harness often doubled it. The old `--openings` count is removed; use `--book`.
Previously reported search Elo gains from the defective harness are invalid.

## Reproducing the installation

```bash
python3 scripts/setup_testing.py
python3 -m unittest discover -s scripts -p test_sprt.py -v
```

Setup builds Fastchess `4e691463cee6a5c38b63525db57e7b7e66c2cbf7` using
`make -j4 CXX=clang++` into `target/testing/fastchess`. It installs the book
parser in an isolated venv and downloads the pinned book revision. Daily
SPRT runs need only Python's standard library plus the built Fastchess.
Builds/books/logs are covered by existing gitignore rules; no engine rebuild
or implicit architecture conversion is performed.

Sources: [Fastchess manual](https://github.com/Disservin/fastchess/blob/4e691463cee6a5c38b63525db57e7b7e66c2cbf7/man.md),
[Stockfish books](https://github.com/official-stockfish/books/tree/65815ccdbc7727cd4f6aee252ba8f67fb740e92f),
[Pohl's UHO explanation](https://www.sp-cc.de/uho_2024.htm).
