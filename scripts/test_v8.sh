#!/usr/bin/env bash
# Usage: scripts/test_v8.sh NET8 [TOTAL_GAME_CAP] [additional sprt.py options]
# V8_ENGINE and V7_ENGINE may override the saved binary paths.
set -euo pipefail
cd "$(dirname "$0")/.."
net="${1:?usage: scripts/test_v8.sh NET8 [TOTAL_GAME_CAP]}"
shift
games=40000
if [[ "${1:-}" =~ ^[0-9]+$ ]]; then games="$1"; shift; fi
exec python3 scripts/sprt.py \
  "${V8_ENGINE:-target/testing/v8-engine}" \
  "${V7_ENGINE:-target/testing/v7-engine}" \
  --new-net "$net" --old-net nets/v7.nnue --games "$games" "$@"
