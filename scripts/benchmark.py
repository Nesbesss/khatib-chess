#!/usr/bin/env python3
"""Score any UCI engine on a tactical test suite.

Both engines take the same test independently and get an absolute score, so
results are comparable without playing them against each other.

  scripts/benchmark.py --engine ./target/release/chess --ms 1000
  scripts/benchmark.py --engine stockfish --ms 1000
  scripts/benchmark.py --all --ms 1000
"""
import argparse, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(ROOT, "benchmarks/wac.epd")


class UCI:
    def __init__(self, cmd, options=None):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)
        self._wait("uci", "uciok")
        for k, v in (options or {}).items():
            self.p.stdin.write(f"setoption name {k} value {v}\n")
        self._wait("isready", "readyok")

    def _wait(self, cmd, token):
        self.p.stdin.write(cmd + "\n"); self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line or token in line:
                return

    def best(self, fen, ms):
        self.p.stdin.write(f"position fen {fen}\ngo movetime {ms}\n")
        self.p.stdin.flush()
        depth = 0
        while True:
            line = self.p.stdout.readline()
            if not line:
                return None, 0
            m = re.match(r"info depth (\d+)", line)
            if m:
                depth = int(m.group(1))
            if line.startswith("bestmove"):
                mv = line.split()[1]
                return (None if mv in ("(none)", "0000") else mv), depth

    def quit(self):
        try:
            self.p.stdin.write("quit\n"); self.p.stdin.flush()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def load_suite(path):
    """Parse EPD into (fen, answers, id, is_uci).

    Two answer formats: `bm` holds SAN (hand-written suites) and `bmuci` holds
    UCI (suites generated from engine output, where no conversion is needed and
    nothing can be mis-parsed).
    """
    tests = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            i = re.search(r'id "([^"]+)"', line)
            tid = i.group(1) if i else "?"
            u = re.search(r"bmuci ([^;]+);", line)
            m = re.search(r"bm ([^;]+);", line)
            if u:
                fen = line.split(" bmuci ")[0].strip()
                ans, is_uci = u.group(1).split(), True
            elif m:
                fen = line.split(" bm ")[0].strip()
                ans, is_uci = m.group(1).split(), False
            else:
                continue
            # EPD omits the halfmove/fullmove counters; UCI wants them.
            if len(fen.split()) == 4:
                fen += " 0 1"
            tests.append((fen, ans, tid, is_uci))
    return tests


def san_matches(engine_uci, expected_sans, fen, ref):
    """True if the engine's UCI move is one of the expected SAN moves.

    Converting SAN to UCI needs legality, so the reference engine (which knows
    the rules) supplies the legal list and we match on destination + piece.
    """
    ref.p.stdin.write(f"position fen {fen}\nlegal\n"); ref.p.stdin.flush()
    legal = []
    while True:
        line = ref.p.stdout.readline()
        if not line:
            break
        if line.startswith("legal"):
            legal = line.split()[1:]
            break
    if engine_uci not in legal:
        return False

    board = fen.split()[0]
    # Piece standing on the from-square, as an uppercase letter (P for pawn).
    files = "abcdefgh"
    fsq = engine_uci[:2]
    rank_idx = 8 - int(fsq[1])
    file_idx = files.index(fsq[0])
    rows = board.split("/")
    row, col = rows[rank_idx], 0
    piece = "?"
    for ch in row:
        if ch.isdigit():
            col += int(ch)
        else:
            if col == file_idx:
                piece = ch.upper()
                break
            col += 1
    dest = engine_uci[2:4]
    promo = engine_uci[4:].upper()

    for san in expected_sans:
        s = san.rstrip("+#")
        # Castling.
        if s in ("O-O", "0-0"):
            if piece == "K" and dest in ("g1", "g8"):
                return True
            continue
        if s in ("O-O-O", "0-0-0"):
            if piece == "K" and dest in ("c1", "c8"):
                return True
            continue
        # Promotion suffix, e.g. e8=Q
        want_promo = ""
        if "=" in s:
            s, want_promo = s.split("=")
        # Leading piece letter (absent for pawn moves).
        want_piece = s[0] if s[0] in "KQRBN" else "P"
        want_dest = s[-2:]
        if (piece == want_piece and dest == want_dest
                and (not want_promo or promo == want_promo)):
            return True
    return False


def run(name, cmd, options, tests, ms, ref):
    eng = UCI(cmd, options)
    solved, total_depth, misses = 0, 0, []
    t0 = time.time()
    for fen, answers, tid, is_uci in tests:
        mv, depth = eng.best(fen, ms)
        total_depth += depth
        hit = (mv in answers) if is_uci else (mv and san_matches(mv, answers, fen, ref))
        if hit:
            solved += 1
        else:
            misses.append((tid, "/".join(answers), mv or "-"))
    elapsed = time.time() - t0
    eng.quit()
    return {
        "name": name, "solved": solved, "total": len(tests),
        "avg_depth": total_depth / max(len(tests), 1),
        "seconds": elapsed, "misses": misses,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", action="append", default=[],
                    help="engine command; repeat for several")
    ap.add_argument("--all", action="store_true",
                    help="test our engine and Stockfish")
    ap.add_argument("--ms", type=int, default=1000, help="ms per position")
    ap.add_argument("--suite", default=SUITE)
    a = ap.parse_args()

    tests = load_suite(a.suite)
    ours = os.path.join(ROOT, "target/release/chess")
    # Our engine doubles as the rules reference for SAN matching.
    ref = UCI([ours])

    engines = []
    if a.all or not a.engine:
        engines = [("Ours", [ours], {}), ("Stockfish 18", ["stockfish"], {})]
    else:
        engines = [(os.path.basename(e), [e], {}) for e in a.engine]

    print(f"Suite: {os.path.basename(a.suite)} — {len(tests)} positions, "
          f"{a.ms} ms each")
    print("(answers verified by deep search; a short budget is what makes "
          "positions hard)\n")
    results = []
    for name, cmd, opts in engines:
        r = run(name, cmd, opts, tests, a.ms, ref)
        results.append(r)
        pct = 100 * r["solved"] / r["total"]
        print(f"{name:<16} {r['solved']:>2}/{r['total']}  {pct:5.1f}%   "
              f"avg depth {r['avg_depth']:.1f}   {r['seconds']:.0f}s")
        for tid, want, got in r["misses"]:
            print(f"    missed {tid}: wanted {want}, played {got}")
    ref.quit()

    if len(results) == 2:
        d = results[0]["solved"] - results[1]["solved"]
        print(f"\n{results[0]['name']} − {results[1]['name']}: {d:+d} positions")


if __name__ == "__main__":
    main()
