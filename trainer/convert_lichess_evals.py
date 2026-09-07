"""Stream Lichess's Stockfish-eval database into our training format.

The archive is 21.7 GB compressed and expands past 100 GB, so it is never
stored: it is decompressed, filtered, and written out as we go.

Output matches trainer/train.py's expectations:  FEN | cp | wdl

  python3 trainer/convert_lichess_evals.py --out data/lichess.txt --limit 50000000
"""
import argparse, json, sys, urllib.request
try:
    import chess          # only needed with --quiet-only
except ImportError:
    chess = None
import zstandard as zstd

URL = "https://database.lichess.org/lichess_db_eval.jsonl.zst"
# Positions evaluated shallower than this are noise; the DB has plenty deep.
MIN_DEPTH = 12
ANCHOR_LIMIT = 1600   # where trainer/train.py's anchor term saturates


def wdl_from_cp(cp):
    """Win probability from centipawns -- the same sigmoid the trainer uses."""
    return 1.0 / (1.0 + 10 ** (-cp / 400.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="max positions (0=all)")
    ap.add_argument("--min-depth", type=int, default=MIN_DEPTH)
    ap.add_argument("--url", default=URL)
    ap.add_argument("--local", help="read this local .zst instead of the URL")
    ap.add_argument("--quiet-only", action="store_true",
                    help="keep only positions with no check and no capture "
                         "available, matching how src/datagen.rs filters our "
                         "own self-play data")
    a = ap.parse_args()

    src = open(a.local, "rb") if a.local else urllib.request.urlopen(a.url)
    dctx = zstd.ZstdDecompressor()
    kept = seen = 0
    with dctx.stream_reader(src) as reader, open(a.out, "w") as out:
        buf = b""
        while True:
            chunk = reader.read(1 << 22)
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                if not raw:
                    continue
                seen += 1
                try:
                    d = json.loads(raw)
                    fen = d["fen"]
                    # Deepest evaluation available for this position.
                    best = max(d["evals"], key=lambda e: e.get("depth", 0))
                    if best.get("depth", 0) < a.min_depth:
                        continue
                    pv = best["pvs"][0]
                    # The trainer's anchor term clamps at +/-1600 cp, so any
                    # target beyond that lands on the same saturated value
                    # while carrying up to 256x the loss of a +100 cp
                    # position. Clamping mates to +/-3000 put 19% of the data
                    # in that tail and dominated the gradient. Drop those
                    # positions instead of piling them onto one target.
                    if "cp" in pv:
                        cp = int(pv["cp"])
                        if abs(cp) >= ANCHOR_LIMIT:
                            continue
                    else:
                        continue          # mate scores carry no usable anchor
                except Exception:
                    continue
                # Lichess stores every score from White's point of view, but
                # the trainer feeds accumulators as [side-to-move, opponent]
                # and reads the score the same way. Without this flip, every
                # black-to-move label carries the wrong sign -- about half the
                # dataset teaching the opposite of the truth.
                if " b " in fen:
                    cp = -cp
                # The DB omits halfmove/fullmove counters; our parser wants them.
                if fen.count(" ") == 3:
                    fen += " 0 1"
                # A static evaluator cannot resolve a pending capture, so
                # training it on tactical positions teaches it to guess.
                # v7's data was filtered this way; the Lichess set is 81%
                # positions with captures available.
                if a.quiet_only:
                    try:
                        b = chess.Board(fen)
                    except Exception:
                        continue
                    if b.is_check():
                        continue
                    if any(b.is_capture(m) for m in b.legal_moves):
                        continue
                # These are engine evaluations, not game results: mark the
                # outcome unknown rather than inventing one from the score.
                out.write(f"{fen} | {cp}\n")
                kept += 1
                if kept % 500000 == 0:
                    print(f"  {kept:,} kept / {seen:,} seen", flush=True)
                if a.limit and kept >= a.limit:
                    print(f"done: {kept:,} positions -> {a.out}", flush=True)
                    return
    print(f"done: {kept:,} positions from {seen:,} records -> {a.out}", flush=True)


if __name__ == "__main__":
    main()
