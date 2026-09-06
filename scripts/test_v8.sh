#!/usr/bin/env bash
# Play a v8 checkpoint against the v7 champion.
#
#   ./scripts/test_v8.sh <v8-net> [games]
#
# The two nets need different binaries (2048 vs 1536 wide), so each side runs
# its own engine with its own net. Fixed nodes, distinct openings.
set -eu
cd "$(dirname "$0")/.."
S=/private/tmp/claude-501/-Users-nesbes-chess/2e3af321-5e7d-4419-8c39-9c18e1d3f240/scratchpad
NET8="$1"; GAMES="${2:-200}"

[ -f "$NET8" ] || { echo "no such net: $NET8"; exit 1; }
sz=$(stat -f%z "$NET8")
[ "$sz" = "26219024" ] || { echo "wrong size $sz (expected 26,219,024 for the 2048 net)"; exit 1; }

mkdir -p /tmp/v8test/{a,b}
cp "$S/v8_engine.bin" /tmp/v8test/a/chess && cp "$NET8"        /tmp/v8test/a/net.nnue
cp "$S/v7_engine.bin" /tmp/v8test/b/chess && cp nets/v7.nnue   /tmp/v8test/b/net.nnue

echo "v8 ($(basename "$NET8")) vs v7 — $GAMES games"
python3 scripts/duel.py /tmp/v8test/a/chess /tmp/v8test/b/chess \
    --games "$GAMES" --nodes 20000 --openings 50
