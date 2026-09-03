#!/bin/bash
# Unattended: wait for data, train several configs, keep whichever measurably
# beats the standing baseline. Everything is logged to data/overnight.log.
cd "$(dirname "$0")/.."
LOG=data/overnight.log
: > $LOG
say() { echo "[$(date +%H:%M)] $*" | tee -a $LOG; }

say "waiting for data generation to finish"
while pgrep -f "modal run trainer/modal_gen" >/dev/null; do sleep 60; done

N=$(wc -l < data/train_big.txt 2>/dev/null | tr -d ' ' || echo 0)
say "data ready: $N positions"
if [ "${N:-0}" -lt 2000000 ]; then
  say "ABORT: only $N positions, expected >2M"
  exit 1
fi

# Deduplicate across shards: different shards can produce the same position.
say "deduplicating"
python3 - <<'PY' >> data/overnight.log 2>&1
seen=set(); n=0; kept=0
with open('data/train_big.txt') as f, open('data/train_big_dedup.txt','w') as o:
    for line in f:
        n+=1
        p=line.split('|')
        if len(p)<3: continue
        k=' '.join(p[0].split()[:4])
        if k in seen: continue
        seen.add(k); o.write(line); kept+=1
print(f"dedup: {n:,} -> {kept:,}")
PY
DATA=data/train_big_dedup.txt
say "training on $(wc -l < $DATA | tr -d ' ') unique positions"

BEST_ELO=-99999
BEST_NET=""
UPLOADED=""
mkdir -p nets

# Config sweep. Lambda trades teacher score against game outcome; more epochs
# help a large net on large data.
run_cfg() {
  local EP=$1 LAM=$2 LR=$3
  local OUT="nets/v2_e${EP}_l${LAM}_lr${LR}.nnue"
  say "training: epochs=$EP lambda=$LAM lr=$LR"
  local SKIP=""
  [ -n "$UPLOADED" ] && SKIP="--skip-upload"
  if ! modal run trainer/modal_train.py --data "$DATA" --epochs "$EP" \
        --batch 16384 --lam "$LAM" --lr "$LR" --out "$OUT" $SKIP \
        >> "data/train_${EP}_${LAM}.log" 2>&1; then
    say "  training FAILED (see data/train_${EP}_${LAM}.log)"
    return
  fi
  UPLOADED=1
  [ -s "$OUT" ] || { say "  no net produced"; return; }

  local R ELO
  R=$(./target/release/chess match 120 10000 --net "$OUT" 2>&1 | grep "NNUE vs")
  ELO=$(echo "$R" | grep -oE '[-+][0-9]+ Elo' | head -1 | grep -oE '[-+]?[0-9]+')
  say "  result: $R"
  if [ -n "$ELO" ] && [ "$ELO" -gt "$BEST_ELO" ] 2>/dev/null; then
    BEST_ELO=$ELO; BEST_NET="$OUT"
    say "  new best: ${ELO} Elo"
  fi
}

run_cfg 50 0.9 1e-3
run_cfg 80 0.8 1e-3
run_cfg 50 1.0 7e-4

if [ -n "$BEST_NET" ] && [ "$BEST_ELO" -gt 0 ] 2>/dev/null; then
  cp "$BEST_NET" net.nnue
  say "PROMOTED $BEST_NET (${BEST_ELO} Elo) -> net.nnue"
  say "confirming over 600 games at 10k nodes"
  ./target/release/chess match 300 10000 --net net.nnue 2>&1 | tail -3 | tee -a $LOG
else
  say "no config beat the handcrafted baseline (best ${BEST_ELO}); net.nnue left alone"
fi
say "FINISHED"
