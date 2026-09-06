"""Stream Lichess's Stockfish-eval database into our training format.

The archive is 21.7 GB compressed and expands past 100 GB, so it is never
stored: it is decompressed, filtered, and written out as we go.

Output matches trainer/train.py's expectations:  FEN | cp | wdl

  python3 trainer/convert_lichess_evals.py --out data/lichess.txt --limit 50000000
"""
import argparse, json, sys, urllib.request
import zstandard as zstd

URL = "https://database.lichess.org/lichess_db_eval.jsonl.zst"
# Positions evaluated shallower than this are noise; the DB has plenty deep.
MIN_DEPTH = 12
CLAMP = 3000          # cap eval magnitude; mates and huge scores skew training


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
                    if "cp" in pv:
                        cp = max(-CLAMP, min(CLAMP, int(pv["cp"])))
                    elif "mate" in pv:
                        cp = CLAMP if pv["mate"] > 0 else -CLAMP
                    else:
                        continue
                except Exception:
                    continue
                # The DB omits halfmove/fullmove counters; our parser wants them.
                if fen.count(" ") == 3:
                    fen += " 0 1"
                out.write(f"{fen} | {cp} | {wdl_from_cp(cp):.3f}\n")
                kept += 1
                if kept % 500000 == 0:
                    print(f"  {kept:,} kept / {seen:,} seen", flush=True)
                if a.limit and kept >= a.limit:
                    print(f"done: {kept:,} positions -> {a.out}", flush=True)
                    return
    print(f"done: {kept:,} positions from {seen:,} records -> {a.out}", flush=True)


if __name__ == "__main__":
    main()
