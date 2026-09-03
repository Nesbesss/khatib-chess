#!/bin/bash
# Tactical spot-checks. MS=<ms> overrides the per-move time.
BIN="${1:-./target/release/chess}"
solved=0; total=0
check() {
  total=$((total+1))
  got=$(printf "position fen %s\ngo movetime ${MS:-3000}\nquit\n" "$1" | $BIN 2>/dev/null | grep "^bestmove" | awk '{print $2}')
  if [ "$got" = "$2" ]; then solved=$((solved+1)); else echo "  miss: expected $2 got $got"; fi
}
check "2rr3k/pp3pp1/1nnqbN1p/3pN3/2pP4/2P3Q1/PPB4P/R4RK1 w - - 0 1" "g3g6"
check "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1" "a1a8"
check "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 0 1" "f3f7"
check "1k1r4/pp1b1R2/3q2pp/4p3/2B5/4Q3/PPP2B2/2K5 b - - 0 1" "d6d1"
echo "  solved $solved/$total"
