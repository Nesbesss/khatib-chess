#!/usr/bin/env python3
"""Verify a freshly generated dataset before it is used for training.

Two failure modes have cost real time on this project, both silent:
  - shards within a run producing identical games
  - a whole run reproducing a previous run's data

Both show up as a low unique ratio, so check before training, not after.
"""
import argparse, itertools, os, sys


def keys(path, limit=None):
    out = set()
    with open(path) as f:
        for line in itertools.islice(f, limit):
            parts = line.split('|')
            if len(parts) >= 2:
                out.add(' '.join(parts[0].split()[:4]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--against", nargs="*", default=[],
                    help="earlier datasets this one must not duplicate")
    ap.add_argument("--sample", type=int, default=500000)
    a = ap.parse_args()

    total = sum(1 for _ in open(a.data))
    k = keys(a.data, a.sample)
    sampled = min(total, a.sample)
    ratio = len(k) / max(sampled, 1)
    print(f"{a.data}: {total:,} lines, {ratio*100:.0f}% unique in a "
          f"{sampled:,}-line sample")

    ok = True
    if ratio < 0.9:
        print("  FAIL: shards are producing duplicate games")
        ok = False

    for other in a.against:
        if not os.path.exists(other):
            continue
        ok_keys = keys(other, a.sample)
        overlap = len(k & ok_keys) / max(min(len(k), len(ok_keys)), 1)
        print(f"  overlap with {other}: {overlap*100:.0f}%")
        if overlap > 0.5:
            print("  FAIL: this run reproduced an earlier run's data")
            ok = False

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
