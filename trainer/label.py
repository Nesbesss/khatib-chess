"""Re-label positions with Stockfish.

Our own search scores are computed *by* the handcrafted eval, so a net trained
on them can only approximate that eval, never beat it. Stockfish gives an
independent, much stronger target — which is what lets the net exceed the
baseline instead of imitating it.

  python3 trainer/label.py --in data/train_v3.txt --out data/labeled.txt \
      --depth 8 --workers 9 --limit 3000000
"""
import argparse, os, subprocess, sys, time
from multiprocessing import Process, Queue


def worker(wid, fens, depth, out_path, q):
    """Score a shard of positions with one Stockfish process."""
    sf = subprocess.Popen(
        ["stockfish"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, bufsize=1)
    sf.stdin.write("uci\n")
    sf.stdin.flush()
    while "uciok" not in (sf.stdout.readline() or ""):
        pass
    # One thread per process; we parallelize across positions instead.
    sf.stdin.write("setoption name Threads value 1\n")
    sf.stdin.write("setoption name Hash value 64\n")
    sf.stdin.write("isready\n")
    sf.stdin.flush()
    while "readyok" not in (sf.stdout.readline() or ""):
        pass

    written = 0
    with open(out_path, "w") as out:
        for i, (fen, wdl) in enumerate(fens):
            sf.stdin.write(f"position fen {fen}\ngo depth {depth}\n")
            sf.stdin.flush()
            score = None
            mate = False
            while True:
                line = sf.stdout.readline()
                if not line:
                    break
                if line.startswith("info ") and " score " in line:
                    parts = line.split()
                    try:
                        si = parts.index("score")
                        kind = parts[si + 1]
                        val = int(parts[si + 2])
                    except (ValueError, IndexError):
                        continue
                    if kind == "cp":
                        score, mate = val, False
                    elif kind == "mate":
                        score, mate = val, True
                elif line.startswith("bestmove"):
                    break
            if score is None:
                continue
            # Skip forced mates: they distort a regression target and the
            # search finds them anyway.
            if mate:
                continue
            # Stockfish reports from the side to move, same convention as ours.
            out.write(f"{fen} | {score} | {wdl}\n")
            written += 1
            if (i + 1) % 2000 == 0:
                q.put((wid, i + 1))
    try:
        sf.stdin.write("quit\n")
        sf.stdin.flush()
        sf.wait(timeout=5)
    except Exception:
        sf.kill()
    q.put((wid, -written))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--depth", type=int, default=8)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    fens = []
    seen = set()
    with open(a.inp) as f:
        for line in f:
            parts = line.split("|")
            if len(parts) < 2:
                continue
            fen = parts[0].strip()
            # Deduplicate: repeated positions waste Stockfish time and
            # over-weight common openings in training.
            key = " ".join(fen.split()[:4])
            if key in seen:
                continue
            seen.add(key)
            wdl = parts[2].strip() if len(parts) >= 3 else "-1"
            fens.append((fen, wdl))
            if a.limit and len(fens) >= a.limit:
                break
    print(f"{len(fens):,} unique positions to label at depth {a.depth} "
          f"with {a.workers} workers", flush=True)

    shards = [fens[i::a.workers] for i in range(a.workers)]
    q = Queue()
    procs = []
    for w in range(a.workers):
        p = Process(target=worker,
                    args=(w, shards[w], a.depth, f"{a.out}.part{w}", q))
        p.start()
        procs.append(p)

    total = len(fens)
    progress = [0] * a.workers
    done = 0
    t0 = time.time()
    while done < a.workers:
        wid, n = q.get()
        if n < 0:
            done += 1
            progress[wid] = -n
        else:
            progress[wid] = n
        cur = sum(progress)
        el = time.time() - t0
        rate = cur / max(el, 1)
        eta = (total - cur) / max(rate, 1)
        print(f"  {cur:,}/{total:,}  {rate:.0f}/s  eta {eta/60:.0f}m", flush=True)

    for p in procs:
        p.join()

    with open(a.out, "w") as out:
        for w in range(a.workers):
            part = f"{a.out}.part{w}"
            if os.path.exists(part):
                with open(part) as f:
                    out.write(f.read())
                os.remove(part)
    n = sum(1 for _ in open(a.out))
    print(f"wrote {n:,} labeled positions to {a.out}")


if __name__ == "__main__":
    main()
