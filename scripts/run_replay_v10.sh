#!/usr/bin/env bash
# Run from an isolated experiment snapshot; arguments are absolute paths.
# RUN ENGINE_REPO. Does not write engine deployment paths or manage the bot.
set -euo pipefail
RUN="$1"
REPO="$2"
PYTHON="${PYTHON:-/usr/bin/python3}"
export OMP_NUM_THREADS=6 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=6
status() { "$PYTHON" -c 'import json,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(json.dumps(dict(stage=sys.argv[2],utc=time.time()))+"\n")' "$RUN/status.json" "$1"; }
trap 'status failed' ERR
status preparing
if [ ! -f "$RUN/cache/manifest.json" ]; then
  "$PYTHON" -u "$RUN/code/replay_v10.py" prepare --run "$RUN" \
    --original "$REPO/data/train_30M.txt" --fresh "$REPO/data/selfplay/shard_*.txt" --packer "$RUN/pack-replay"
fi
status training
"$PYTHON" -u "$RUN/code/replay_v10.py" train --run "$RUN" \
  --net "$RUN/v7.nnue" --engine "$RUN/engine" --epochs 5 --batch 8192 \
  --threads 6 --lr 0.00005 --lambda 0.7 --fresh-repeat 2
status matching
"$PYTHON" -u "$RUN/code/sprt.py" "$RUN/engine" "$RUN/engine" \
  --new-net "$RUN/candidate/epoch5.nnue" --old-net "$RUN/v7.nnue" \
  --fastchess "$REPO/target/testing/fastchess" --book "$REPO/data/books/8moves_v3.unique.epd" \
  --fixed-games --games 800 --nodes 20000 --concurrency 6 --threads 1 --hash 64 \
  --seed 20260907 --out "$RUN/match-epoch5"
status complete
