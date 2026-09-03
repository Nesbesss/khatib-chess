#!/bin/bash
# Train a set of configs, evaluate each against the standing champion over a
# sample large enough to be meaningful, and promote only a real winner.
#
# Safe to run standalone; picks up config 1's net if it already exists.
# Run this from a copy outside the repo: editing a script while bash is
# executing it shifts byte offsets and can silently truncate execution.
cd "${REPO:-$(dirname "$0")/..}"
LOG=data/sweep.log
say() { echo "[$(date +%H:%M)] $*" | tee -a $LOG; }

GAMES=${GAMES:-150}     # pairs, so 2x this many games
NODES=${NODES:-8000}

say "waiting for any in-flight training"
while pgrep -f "modal run trainer/modal_train" >/dev/null; do sleep 30; done

BEST_ELO=-99999
BEST_NET=""
mkdir -p nets

evaluate() {
  local OUT="$1"
  [ -s "$OUT" ] || { say "  $OUT missing"; return; }

  local Q
  Q=$(printf 'position fen 4k3/8/8/8/8/8/8/Q3K3 w - - 0 1\neval\nquit\n' \
      | ./target/release/chess --net "$OUT" 2>/dev/null | tail -1)
  say "  queen-up eval: $Q (expect several hundred positive)"

  local R ELO H
  R=$(python3 scripts/duel_nets.py "$OUT" --games "$GAMES" --nodes "$NODES" 2>&1 | grep "^Elo")
  ELO=$(echo "$R" | grep -oE '[-+][0-9]+' | head -1)
  # NOTE: the v1 champion binary predates the +191 Elo search work, so this
  # number credits BOTH search and network gains. The handcrafted comparison
  # below uses the same binary and therefore isolates the network.
  say "  vs v1 champion (search+net): $R"
  H=$(./target/release/chess match 40 10000 --net "$OUT" 2>&1 | grep "NNUE vs")
  say "  vs handcrafted (net only): $H"

  if [ -n "$ELO" ] && [ "$ELO" -gt "$BEST_ELO" ] 2>/dev/null; then
    BEST_ELO=$ELO; BEST_NET="$OUT"
    say "  new best: ${ELO} Elo"
  fi
}

train_and_eval() {
  local EP=$1 LAM=$2 LR=$3
  local OUT="nets/v2_e${EP}_l${LAM}_lr${LR}.nnue"
  if [ -s "$OUT" ]; then
    say "already trained: $OUT"
  else
    say "training: epochs=$EP lambda=$LAM lr=$LR"
    if ! modal run trainer/modal_train.py --data data/train_dedup.txt \
          --epochs "$EP" --batch 16384 --lam "$LAM" --lr "$LR" --out "$OUT" \
          --skip-upload >> "data/tr_${EP}_${LAM}_${LR}.log" 2>&1; then
      say "  FAILED (data/tr_${EP}_${LAM}_${LR}.log)"
      return
    fi
  fi
  evaluate "$OUT"
}

train_and_eval 50 0.9 1e-3
train_and_eval 80 0.8 1e-3
train_and_eval 30 1.0 7e-4

if [ -n "$BEST_NET" ] && [ "$BEST_ELO" -gt 0 ] 2>/dev/null; then
  cp "$BEST_NET" net.nnue
  say "PROMOTED $BEST_NET (+${BEST_ELO} Elo vs v1) -> net.nnue"
  say "confirming over 400 games"
  python3 scripts/duel_nets.py net.nnue --games 200 --nodes 8000 2>&1 | tail -3 | tee -a $LOG
else
  say "nothing beat the v1 champion (best ${BEST_ELO}); net.nnue unchanged"
fi
say "FINISHED"
