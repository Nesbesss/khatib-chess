#!/usr/bin/env python3
"""Build a benchmark suite whose answers are defined by deep search.

Writing test positions by hand produces wrong answer keys — of twenty I wrote
from memory, thirteen disagreed with deep search. Instead: take positions from
real games, let a strong engine settle each one with a long search, and keep
only those where the deep verdict is *hard to find quickly* — the shallow
search disagrees with the deep one. Those are the positions that separate
engines.

  scripts/make_suite.py --out benchmarks/hard.epd --count 30
"""
import argparse, os, random, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SF:
    def __init__(self, threads=8, hash_mb=512):
        self.p = subprocess.Popen(["stockfish"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)
        self._w("uci", "uciok")
        self.p.stdin.write(f"setoption name Threads value {threads}\n")
        self.p.stdin.write(f"setoption name Hash value {hash_mb}\n")
        self._w("isready", "readyok")

    def _w(self, c, t):
        self.p.stdin.write(c + "\n"); self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line or t in line:
                return

    def best(self, fen, ms):
        self.p.stdin.write(f"position fen {fen}\ngo movetime {ms}\n")
        self.p.stdin.flush()
        score = None
        while True:
            line = self.p.stdout.readline()
            if not line:
                return None, None
            m = re.search(r"score cp (-?\d+)", line)
            if m:
                score = int(m.group(1))
            if line.startswith("bestmove"):
                mv = line.split()[1]
                return (None if mv in ("(none)", "0000") else mv), score

    def quit(self):
        try:
            self.p.stdin.write("quit\n"); self.p.stdin.flush()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def positions_from_training_data(path, n, seed=7):
    """Sample real positions out of the self-play training data."""
    rng = random.Random(seed)
    picked = []
    with open(path) as f:
        for i, line in enumerate(f):
            if i > 400000:
                break
            parts = line.split("|")
            if len(parts) < 2:
                continue
            fen = parts[0].strip()
            try:
                cp = int(parts[1])
            except ValueError:
                continue
            # Skip dead-drawn and already-decided positions: neither
            # discriminates between engines.
            if abs(cp) > 600 or abs(cp) < 20:
                continue
            picked.append(fen)
    rng.shuffle(picked)
    return picked[:n * 40]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "benchmarks/hard.epd"))
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--deep-ms", type=int, default=4000)
    ap.add_argument("--shallow-ms", type=int, default=150)
    ap.add_argument("--source", default=os.path.join(ROOT, "data/train_v5.txt"))
    a = ap.parse_args()

    cands = positions_from_training_data(a.source, a.count)
    print(f"{len(cands)} candidate positions; keeping the ones a short search "
          f"gets wrong")

    deep = SF(threads=8)
    shallow = SF(threads=1, hash_mb=16)
    kept = []
    for fen in cands:
        if len(kept) >= a.count:
            break
        d_mv, d_cp = deep.best(fen, a.deep_ms)
        if not d_mv or d_cp is None or abs(d_cp) > 800:
            continue
        s_mv, _ = shallow.best(fen, a.shallow_ms)
        # Hard = a strong engine at a short budget does NOT find the deep move.
        if s_mv and s_mv != d_mv:
            kept.append((fen, d_mv, d_cp))
            print(f"  [{len(kept):2}/{a.count}] deep={d_mv} shallow={s_mv} "
                  f"cp={d_cp:+}")
    deep.quit(); shallow.quit()

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        for i, (fen, mv, cp) in enumerate(kept, 1):
            # Answers are stored as UCI, since they came from an engine.
            f.write(f'{fen} bmuci {mv}; id "hard.{i:02d}"; cp {cp};\n')
    print(f"\nwrote {len(kept)} positions to {a.out}")


if __name__ == "__main__":
    main()
