#!/bin/bash
# Sweep one search parameter at a time against the shipped default.
#
# The same binary plays both sides; only the environment differs, so a result
# is attributable to that one knob. Anything whose interval clears zero is a
# candidate -- at 200 games the resolution is roughly +/-20 Elo, so this finds
# large wins, not small ones.
#
# Usage: bash scripts/sweep.sh [games]
set -u
cd "$(dirname "$0")/.."

ENGINE=/tmp/khatib-tune
NET=net.nnue
BOOK=data/books/8moves_v3.unique.epd
FC=target/testing/fastchess
GAMES=${1:-200}
OUT=logs/sweep
mkdir -p "$OUT"

# knob=value pairs to try. Ordered by expected leverage: LMR shapes the whole
# tree, null-move is next, the futility margins are narrower in effect.
TRIALS="
KH_LMR_DIV=2.00
KH_LMR_DIV=2.50
KH_LMR_BASE=0.50
KH_LMR_BASE=1.00
KH_NMP_BASE=2
KH_NMP_BASE=4
KH_NMP_DIV=3
KH_NMP_DIV=6
KH_RFP_MARGIN=75
KH_RFP_MARGIN=130
KH_FUT_MARGIN=90
KH_FUT_MARGIN=150
"

echo "sweep: $GAMES games per trial, $(echo "$TRIALS" | grep -c =) trials"
echo "started $(date '+%H:%M:%S')"
echo

for trial in $TRIALS; do
  key=${trial%%=*}
  val=${trial#*=}
  tag=$(echo "$trial" | tr '=' '_')
  log="$OUT/$tag.log"

  # -engine env vars are not a fastchess feature, so wrap each side in a shell
  # that exports the knob and then execs the engine.
  # ENGINE is already absolute -- prefixing $PWD produced a path that did not
  # exist, and fastchess reported it as "no uciok" rather than "no such file".
  cat > "/tmp/new_$tag.sh" <<EOF
#!/bin/sh
export $key=$val
exec $ENGINE "\$@"
EOF
  chmod +x "/tmp/new_$tag.sh"

  nice -n 10 "$FC" \
    -engine cmd="/tmp/new_$tag.sh" name=new args="--net $NET" \
    -engine cmd="$ENGINE" name=base args="--net $NET" \
    -each tc=10+0.1 \
    -openings file="$BOOK" format=epd order=random \
    -repeat -rounds $((GAMES / 2)) -games 2 -concurrency 4 \
    > "$log" 2>&1

  result=$(grep -E "^Elo:" "$log" | tail -1)
  games=$(grep -E "^Games:" "$log" | tail -1)
  printf "%-22s %s\n" "$trial" "${result:-no result}"
  printf "%-22s %s\n" "" "${games:-}"
  echo
done

echo "finished $(date '+%H:%M:%S')"
echo
echo "=== candidates (interval clears zero) ==="
for f in "$OUT"/*.log; do
  e=$(grep -E "^Elo:" "$f" | tail -1 | sed -n 's/Elo: \(-*[0-9.]*\) +\/- \([0-9.]*\).*/\1 \2/p')
  [ -z "$e" ] && continue
  elo=$(echo "$e" | cut -d' ' -f1)
  err=$(echo "$e" | cut -d' ' -f2)
  low=$(echo "$elo - $err" | bc -l 2>/dev/null)
  case "$low" in
    -*|"") ;;
    *) echo "  $(basename "$f" .log): +$elo +/- $err" ;;
  esac
done
