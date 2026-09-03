#!/bin/bash
# Runs after the dataset is uploaded and verified: trains several configs,
# measures each against the standing champion, promotes only a real winner.
cd "$(dirname "$0")/.."
LOG=data/overnight.log
say() { echo "[$(date +%H:%M)] $*" | tee -a $LOG; }

say "waiting for the dataset upload to finish"
while pgrep -f "modal run trainer/modal_train" >/dev/null; do sleep 30; done

BEST_ELO=-99999
BEST_NET=""
mkdir -p nets

run_cfg() {
  local EP=$1 LAM=$2 LR=$3
  local OUT="nets/v2_e${EP}_l${LAM}_lr${LR}.nnue"
  say "training: epochs=$EP lambda=$LAM lr=$LR"
  if ! modal run trainer/modal_train.py --data data/train_dedup.txt \
        --epochs "$EP" --batch 16384 --lam "$LAM" --lr "$LR" --out "$OUT" \
        --skip-upload >> "data/train_${EP}_${LAM}_${LR}.log" 2>&1; then
    say "  FAILED (data/train_${EP}_${LAM}_${LR}.log)"
    return
  fi
  [ -s "$OUT" ] || { say "  no net produced"; return; }

  # Sanity before spending time on games: a broken net shows up here.
  local SANE
  SANE=$(printf 'position fen 4k3/8/8/8/8/8/8/Q3K3 w - - 0 1\neval\nquit\n' \
         | ./target/release/chess --net "$OUT" 2>/dev/null | tail -1)
  say "  queen-up eval: $SANE (expect large positive)"

  local R ELO H
  R=$(python3 scripts/duel_nets.py "$OUT" --games 150 --nodes 8000 2>&1 | grep "^Elo")
  ELO=$(echo "$R" | grep -oE '[-+][0-9]+' | head -1)
  say "  vs v1 champion: $R"
  H=$(./target/release/chess match 40 10000 --net "$OUT" 2>&1 | grep "NNUE vs")
  say "  vs handcrafted: $H"

  if [ -n "$ELO" ] && [ "$ELO" -gt "$BEST_ELO" ] 2>/dev/null; then
    BEST_ELO=$ELO; BEST_NET="$OUT"
    say "  new best: ${ELO} Elo vs champion"
  fi
}

run_cfg 50 0.9 1e-3
run_cfg 80 0.8 1e-3
run_cfg 50 1.0 7e-4

if [ -n "$BEST_NET" ] && [ "$BEST_ELO" -gt 0 ] 2>/dev/null; then
  cp "$BEST_NET" net.nnue
  say "PROMOTED $BEST_NET (+${BEST_ELO} Elo vs v1) -> net.nnue"
  say "confirming over 200 games"
  python3 scripts/duel_nets.py net.nnue --games 100 --nodes 10000 2>&1 | tail -3 | tee -a $LOG
else
  say "no config beat the v1 champion (best ${BEST_ELO}); net.nnue unchanged"
fi
say "FINISHED"
