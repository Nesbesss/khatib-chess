#!/bin/bash
# Evaluate a trained net: sanity evals, then a measured match vs handcrafted.
set -e
NET="${1:?usage: eval_net.sh <net.nnue> [pairs] [nodes]}"
PAIRS="${2:-60}"
NODES="${3:-15000}"
cd "$(dirname "$0")/.."

echo "=== static evals (sign sanity) ==="
check() {
  local fen="$1" label="$2" expect="$3"
  local v
  v=$(printf "position fen %s\neval\nquit\n" "$fen" | ./target/release/chess --net "$NET" 2>/dev/null | tail -1)
  printf "  %-24s %+6s   (expect %s)\n" "$label" "$v" "$expect"
}
check "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1" "startpos" "~0"
check "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKB1R w KQkq - 0 1" "white missing knight" "negative"
check "rnbqkb1r/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1" "black missing knight" "positive"
check "4k3/8/8/8/8/8/8/Q3K3 w - - 0 1" "white up a queen" "large positive"
check "q3k3/8/8/8/8/8/8/4K3 w - - 0 1" "black up a queen" "large negative"
check "4k3/4p3/8/8/8/8/4P3/4K3 w - - 0 1" "symmetric K+P" "~0"

echo
echo "=== search speed ==="
printf 'position startpos\ngo depth 10\nquit\n' | ./target/release/chess --net "$NET" 2>/dev/null \
  | grep "depth 10" | grep -oE "nps [0-9]+" || true

echo
echo "=== match vs handcrafted eval ($PAIRS pairs, $NODES nodes/move) ==="
./target/release/chess match "$PAIRS" "$NODES" --net "$NET"
