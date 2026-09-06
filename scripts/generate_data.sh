#!/usr/bin/env bash
# Continuously generate + label training data in the background, using ONLY
# idle capacity so it never slows the live bot. Meant to run for weeks on an
# always-on machine; each cycle appends one labelled shard.
#
#   ./scripts/generate_data.sh
#
# Env:
#   GEN_GAMES   self-play games per shard   (default 400)
#   GEN_DEPTH   self-play search depth      (default 6)
#   SF_DEPTH    Stockfish label depth       (default 10)
#   GEN_CORES   cores for gen + label       (default 4, the E-cores)
set -u
cd "$(dirname "$0")/.."

GEN_GAMES="${GEN_GAMES:-400}"
GEN_DEPTH="${GEN_DEPTH:-6}"
SF_DEPTH="${SF_DEPTH:-10}"
GEN_CORES="${GEN_CORES:-4}"
OUT=data/selfplay
mkdir -p "$OUT" logs

command -v stockfish >/dev/null || { echo "stockfish not found; brew install stockfish" | tee -a logs/gen.log; exit 1; }

echo "[$(date '+%F %T')] data generation started (games=$GEN_GAMES depth=$GEN_DEPTH sf=$SF_DEPTH cores=$GEN_CORES)" >> logs/gen.log

n=$(ls "$OUT"/shard_*.txt 2>/dev/null | wc -l | tr -d ' ')
while true; do
  raw="$OUT/raw_$n.txt"
  uniq="$OUT/uniq_$n.txt"
  shard="$OUT/shard_$n.txt"
  seed=$(( (RANDOM<<15 ^ RANDOM ^ $(date +%s)) & 0x7fffffff ))

  # Low priority (nice 15) so the bot's games always win the CPU.
  nice -n 15 ./target/release/chess datagen "$GEN_GAMES" "$GEN_DEPTH" "$raw" "$GEN_CORES" "$seed" >> logs/gen.log 2>&1

  # Dedup within the shard before the expensive labeling step.
  sort -u -t'|' -k1,1 "$raw" > "$uniq" 2>/dev/null && rm -f "$raw"

  nice -n 15 python3 trainer/label.py --in "$uniq" --out "$shard" \
      --depth "$SF_DEPTH" --workers "$GEN_CORES" >> logs/gen.log 2>&1 && rm -f "$uniq"

  lines=$(wc -l < "$shard" 2>/dev/null | tr -d ' ')
  echo "[$(date '+%F %T')] shard $n done: $lines positions" >> logs/gen.log
  n=$((n+1))

  # Stop before the disk fills: an always-on box should never be wedged by
  # its own background job. MAX_GB caps the dataset (default 15 GB).
  used=$(du -sm "$OUT" 2>/dev/null | cut -f1)
  free=$(df -m . | tail -1 | awk '"'"'{print $4}'"'"')
  if [ "${used:-0}" -ge "$(( ${MAX_GB:-15} * 1024 ))" ] || [ "${free:-99999}" -lt 8192 ]; then
    echo "[$(date '+%F %T')] stopping: ${used}MB collected, ${free}MB free" >> logs/gen.log
    exit 0
  fi
done
