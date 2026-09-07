#!/usr/bin/env bash
# Run only after full parity on the ACTUAL historical v8b binary/net/state.
# Usage: PYTHON=... bash scripts/run_architecture_ab.sh HISTORICAL_PARITY_JSON [NEW_RUN_DIR]
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
historical="${1:?usage: run_architecture_ab.sh HISTORICAL_PARITY_JSON [NEW_RUN_DIR]}"
run="${2:-logs/architecture-ab-$(date -u +%Y%m%dT%H%M%SZ)}"
cache="${CACHE:-data/architecture-ab-v1}"

# Fail before allocating a run or starting a build/training job.
"$PYTHON" - "$historical" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, 'trainer')
from parity import sha256
r = json.loads(Path(sys.argv[1]).read_text())
if (r['status'] != 'pass' or (r['hidden'], r['l2']) != (2048, 16)
        or len(r['positions']) < 2048):
    raise SystemExit('Need a full, passing historical v8/v8b parity report with >=2048 positions')
for key in ('engine', 'net', 'float'):
    if sha256(r['paths'][key]) != r['sha256'][key]:
        raise SystemExit(f'Historical {key} changed after parity')
PY
test -x target/testing/fastchess
test -s data/books/8moves_v3.unique.epd
test -s nets/v7.nnue
test -s data/lichess_clean.txt
if [[ -e "$run" ]]; then echo "Run already exists: $run" >&2; exit 1; fi
mkdir -p "$run"
run="$(cd "$run" && pwd)"
cp "$historical" "$run/historical-parity.json"
cp nets/v7.nnue "$run/v7.nnue"
"$PYTHON" - "$run" <<'PY'
import json, platform, subprocess, sys
from pathlib import Path
import torch
sys.path.insert(0, 'trainer')
from parity import sha256
root = Path(sys.argv[1])
paths = [Path('Cargo.toml'), *Path('src').glob('*.rs'),
         *[Path('trainer')/n for n in ('train.py','parity.py','architecture_ab.py')],
         Path('scripts/run_architecture_ab.sh'), Path('scripts/sprt.py')]
(root/'provenance.json').write_text(json.dumps(dict(
    source_sha256={str(p):sha256(p) for p in paths}, v7_sha256=sha256(root/'v7.nnue'),
    git_head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
    python=sys.version, torch=torch.__version__, machine=platform.platform(),
    plan='6 epochs of 20-epoch schedule; epoch6 primary; 300 games per comparison',
    training_threads=6, match_concurrency=2, seed=20260907), indent=2)+'\n')
PY

# Dedicated output trees: never overwrite target/release/chess or a saved engine.
cargo test --release -j 2 --target-dir "$run/build-direct" > "$run/test-direct.log" 2>&1
cargo build --release -j 2 --target-dir "$run/build-direct" >> "$run/test-direct.log" 2>&1
cargo test --release -j 2 --features nnue-v8 --target-dir "$run/build-l2" > "$run/test-l2.log" 2>&1
cargo build --release -j 2 --features nnue-v8 --target-dir "$run/build-l2" >> "$run/test-l2.log" 2>&1
direct="$run/build-direct/release/chess"
l2="$run/build-l2/release/chess"

# Reconstructed v8 must also agree with the exact historical checkpoint.
"$PYTHON" - "$historical" "$l2" "$run" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, 'trainer')
from parity import check
r = json.loads(Path(sys.argv[1]).read_text())
new = check(sys.argv[2], r['paths']['net'], 2048, 16, r['positions'],
            Path(sys.argv[3])/'rebuilt-historical-parity.json', r['paths']['float'], audit=True)
if new['status'] != 'pass': raise SystemExit('Reconstructed v8 failed parity')
PY
"$PYTHON" trainer/parity.py --engine "$direct" --net "$run/v7.nnue" --hidden 1536 --l2 0 \
  --positions "$historical" --out "$run/v7-integer-parity.json" --audit

if [[ ! -e "$cache" ]]; then
  nice -n 10 "$PYTHON" trainer/architecture_ab.py prepare \
    --data data/lichess_clean.txt --out "$cache" 2>&1 | tee "$run/prepare.log"
fi
test -s "$cache/manifest.json"
"$PYTHON" - "$cache" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, 'trainer')
from parity import sha256
meta = json.loads((Path(sys.argv[1])/'manifest.json').read_text())
if meta['source_sha256'] != sha256('data/lichess_clean.txt'):
    raise SystemExit('Cache belongs to a different source file')
PY
for arm in direct l2; do
  engine="$direct"
  if [[ "$arm" == l2 ]]; then engine="$l2"; fi
  nice -n 10 "$PYTHON" trainer/architecture_ab.py train --cache "$cache" \
    --out "$run/$arm" --arm "$arm" --engine "$engine" --threads 6 \
    --batch 8192 --lr 0.001 --schedule-epochs 20 --stop-after 6 \
    2>&1 | tee "$run/train-$arm.log"
done

# Primary endpoint fixed in advance: epoch 6. Validation does not select it.
# All three use current identical search source and the same opening schedule.
for arm in direct l2; do
  engine="$direct"
  if [[ "$arm" == l2 ]]; then engine="$l2"; fi
  nice -n 10 "$PYTHON" scripts/sprt.py "$engine" "$direct" \
    --new-net "$run/$arm/epoch6.nnue" --old-net "$run/v7.nnue" \
    --book data/books/8moves_v3.unique.epd --nodes 20000 --fixed-games --games 300 \
    --concurrency 2 --threads 1 --hash 64 --seed 20260907 --out "$run/match-$arm-v7"
done
nice -n 10 "$PYTHON" scripts/sprt.py "$direct" "$l2" \
  --new-net "$run/direct/epoch6.nnue" --old-net "$run/l2/epoch6.nnue" \
  --book data/books/8moves_v3.unique.epd --nodes 20000 --fixed-games --games 300 \
  --concurrency 2 --threads 1 --hash 64 --seed 20260907 --out "$run/match-direct-l2"
echo "Finished: $run. Fixed-node diagnostic only; v7 deployment untouched."
