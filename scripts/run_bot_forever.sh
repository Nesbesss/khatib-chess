#!/usr/bin/env bash
# Keep the Lichess bot alive: restart on crash, and challenge bots so it
# always has a game going. Meant for an always-on machine.
#
#   ./scripts/run_bot_forever.sh            # play whoever challenges + seek bots
#   RATED=1 ./scripts/run_bot_forever.sh    # rated games, for a real Elo
set -u
cd "$(dirname "$0")/.."

[ -f .lichess_token ] || { echo "missing .lichess_token"; exit 1; }
export LICHESS_TOKEN="$(cat .lichess_token)"

ARGS=(--seek "${TC:-5+3}")
[ "${RATED:-0}" = "1" ] && ARGS+=(--rated)

mkdir -p logs
while true; do
  echo "[$(date '+%F %T')] starting bot ${ARGS[*]}" >> logs/bot.log
  python3 -u scripts/lichess_bot.py "${ARGS[@]}" >> logs/bot.log 2>&1
  echo "[$(date '+%F %T')] exited ($?); restarting in 10s" >> logs/bot.log
  sleep 10
done
