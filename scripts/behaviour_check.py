#!/usr/bin/env python3
"""Does the engine actually do the thing, at the clocks where it matters?

An Elo match answers "is this better" and needs hundreds of games to do it.
This answers "did the branch fire and was the result sane", which is what you
want first: two matches tonight came back neutral because ~82% of games ended
in repetition before either clock drained, so neither ever exercised the code
under test.

Usage:
    python3 scripts/behaviour_check.py ENGINE [--net NET] [--baseline ENGINE]
"""
import argparse
import re
import subprocess
import sys

# Middlegame and endgame positions, all outside the opening book, chosen so a
# real search has something to find.
POSITIONS = [
    ("italian",   "r1bq1rk1/ppp2ppp/2np1n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 0 8"),
    ("sicilian",  "r1bqkb1r/1p3ppp/p1np1n2/4p3/4P3/1NN1B3/PPP1BPPP/R2QK2R w KQkq - 0 9"),
    ("endgame-r", "8/5pk1/6p1/7p/7P/5KP1/5P2/3R4 w - - 0 40"),
    ("endgame-p", "8/p4ppk/8/8/8/8/P4PPK/8 w - - 0 30"),
    ("tactical",  "r2q1rk1/pp2bppp/2n1bn2/2pp4/3P4/2PBPN2/PP1N1PPP/R1BQ1RK1 w - - 0 10"),
]

CLOCKS = [30000, 15000, 10000, 8000, 5000, 3000, 2000]


def ask(engine, net, fen, go):
    """Run one search, return (depth, nodes, time_ms, bestmove)."""
    script = ("uci\nsetoption name OwnBook value false\nucinewgame\n"
              f"position fen {fen}\n{go}\nquit\n")
    cmd = [engine] + (["--net", net] if net else [])
    try:
        out = subprocess.run(cmd, input=script, capture_output=True, text=True,
                             timeout=60).stdout
    except subprocess.TimeoutExpired:
        return None
    info = [l for l in out.splitlines() if l.startswith("info depth")]
    best = next((l.split()[1] for l in out.splitlines()
                 if l.startswith("bestmove")), "?")
    if not info:
        return None
    last = info[-1]
    def field(name):
        m = re.search(rf"\b{name} (\d+)", last)
        return int(m.group(1)) if m else 0
    return field("depth"), field("nodes"), field("time"), best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine")
    ap.add_argument("--net")
    ap.add_argument("--baseline", help="compare against this engine")
    a = ap.parse_args()

    print(f"engine:   {a.engine}")
    if a.baseline:
        print(f"baseline: {a.baseline}")
    print()

    # 1. Depth against a draining clock. The failure this catches is depth 1
    #    at 8 s -- a real bug we shipped, invisible to an Elo match.
    print("=" * 66)
    print("DEPTH vs CLOCK   (sudden death, no increment)")
    print("=" * 66)
    header = f"{'clock':>8}  {'depth':>5} {'nodes':>9} {'spent':>7} {'%clk':>5}"
    if a.baseline:
        header += f"   |{'base':>6}"
    print(header)

    worst = []
    for ms in CLOCKS:
        go = f"go wtime {ms} btime {ms} winc 0 binc 0"
        r = ask(a.engine, a.net, POSITIONS[0][1], go)
        if not r:
            print(f"{ms:>7}ms  no output")
            continue
        d, n, t, _ = r
        pct = 100.0 * t / ms
        line = f"{ms:>7}ms  {d:>5} {n:>9} {t:>6}ms {pct:>4.1f}%"
        if a.baseline:
            b = ask(a.baseline, a.net, POSITIONS[0][1], go)
            line += f"   | {b[0]:>4}" if b else "   |    ?"
        # A search this shallow is not choosing a move, it is guessing.
        if d <= 3:
            line += "   <-- SHALLOW"
            worst.append((ms, d))
        # Spending a big share of a short clock risks the flag.
        if pct > 15:
            line += "   <-- SPENDY"
        print(line)

    # 2. Flagging. Same clock for us, opponent short: the budget should drop.
    print()
    print("=" * 66)
    print("FLAGGING   (our clock fixed at 30s, opponent's varies)")
    print("=" * 66)
    print(f"{'opp clock':>10}  {'ratio':>6}  {'depth':>5} {'spent':>7}  fires?")
    base = None
    for opp in [30000, 20000, 15000, 10000, 7000, 5000]:
        go = f"go wtime 30000 btime {opp} winc 0 binc 0"
        r = ask(a.engine, a.net, POSITIONS[0][1], go)
        if not r:
            continue
        d, _, t, _ = r
        if base is None:
            base = t
        ratio = 30000 / opp
        fires = "yes" if t < base * 0.85 else "no"
        print(f"{opp:>9}ms  {ratio:>5.1f}x  {d:>5} {t:>6}ms  {fires}")

    # 3. Pressure option, if the build has one.
    print()
    print("=" * 66)
    print("PRESSURE OPTION   (30s clock)")
    print("=" * 66)
    for p in [100, 85, 70, 50]:
        script_go = (f"go wtime 30000 btime 30000 winc 0 binc 0")
        cmd = [a.engine] + (["--net", a.net] if a.net else [])
        script = ("uci\nsetoption name OwnBook value false\n"
                  f"setoption name Pressure value {p}\nucinewgame\n"
                  f"position fen {POSITIONS[0][1]}\n{script_go}\nquit\n")
        try:
            out = subprocess.run(cmd, input=script, capture_output=True,
                                 text=True, timeout=60).stdout
        except subprocess.TimeoutExpired:
            continue
        info = [l for l in out.splitlines() if l.startswith("info depth")]
        if not info:
            continue
        d = re.search(r"depth (\d+)", info[-1])
        t = re.search(r"time (\d+)", info[-1])
        print(f"  Pressure={p:<4} depth={d.group(1) if d else '?':<4} "
              f"time={t.group(1) if t else '?'}ms")

    # 4. Sanity across positions: every clock must yield a legal move.
    print()
    print("=" * 66)
    print("MOVE SANITY   (every position, every clock, must return a move)")
    print("=" * 66)
    bad = 0
    for name, fen in POSITIONS:
        results = []
        for ms in (10000, 5000, 2000):
            r = ask(a.engine, a.net, fen, f"go wtime {ms} btime {ms} winc 0 binc 0")
            if not r or r[3] in ("?", "(none)", "0000"):
                results.append(f"{ms//1000}s:FAIL")
                bad += 1
            else:
                results.append(f"{ms//1000}s:d{r[0]}")
        print(f"  {name:<12} {'  '.join(results)}")

    print()
    if worst:
        print(f"WARNING: shallow search at {', '.join(f'{m}ms->d{d}' for m, d in worst)}")
    if bad:
        print(f"FAIL: {bad} positions returned no move")
        return 1
    print("all positions returned a move")
    return 0


if __name__ == "__main__":
    sys.exit(main())
