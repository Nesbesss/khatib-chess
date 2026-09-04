#!/usr/bin/env python3
"""Play a candidate net (current engine) against the standing champion
(baseline/chess_v1 with baseline/v1.nnue) and report the Elo difference.

The champion is a frozen binary+net pair, so this measures real progress
rather than progress against a weaker reference.
"""
import argparse, math, random, subprocess, sys, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Engine:
    def __init__(self, path, net=None, threads=1):
        args = [path] + (["--net", net] if net else [])
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)
        self._cmd("uci", "uciok")
        self.p.stdin.write(f"setoption name Threads value {threads}\n")
        self._cmd("isready", "readyok")

    def _cmd(self, cmd, wait):
        self.p.stdin.write(cmd + "\n"); self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line or wait in line:
                return

    def best(self, moves, nodes):
        pos = "position startpos" + (" moves " + " ".join(moves) if moves else "")
        self.p.stdin.write(f"{pos}\ngo nodes {nodes}\n"); self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                return None
            if line.startswith("bestmove"):
                mv = line.split()[1]
                return None if mv in ("(none)", "0000") else mv

    def status(self, moves):
        pos = "position startpos" + (" moves " + " ".join(moves) if moves else "")
        self.p.stdin.write(f"{pos}\nstatus\n"); self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                return "playing"
            line = line.strip()
            if line in ("white-wins", "black-wins", "draw-stalemate",
                        "draw-fifty", "draw-material", "playing"):
                return line

    def quit(self):
        try:
            self.p.stdin.write("quit\n"); self.p.stdin.flush()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


OPENING_BOOK = [
    "e2e4 e7e5", "e2e4 c7c5", "e2e4 e7e6", "e2e4 c7c6", "e2e4 d7d5",
    "d2d4 d7d5", "d2d4 g8f6", "d2d4 e7e6", "d2d4 f7f5", "d2d4 d7d6",
    "g1f3 d7d5", "g1f3 g8f6", "c2c4 e7e5", "c2c4 g8f6", "c2c4 c7c5",
    "e2e4 g8f6", "e2e4 d7d6", "e2e4 b8c6", "d2d4 c7c5", "d2d4 b8c6",
    "g1f3 c7c5", "c2c4 e7e6", "b2b3 e7e5", "g2g3 d7d5", "f2f4 d7d5",
    "e2e4 e7e5 g1f3 b8c6", "e2e4 e7e5 g1f3 g8f6", "e2e4 c7c5 g1f3 d7d6",
    "e2e4 c7c5 g1f3 b8c6", "d2d4 g8f6 c2c4 e7e6", "d2d4 g8f6 c2c4 g7g6",
    "d2d4 d7d5 c2c4 e7e6", "d2d4 d7d5 c2c4 c7c6", "g1f3 g8f6 c2c4 e7e6",
]


def load_openings(n, seed=0xC0FFEE):
    """Distinct openings, one per game.

    Generating them with a 1-node search is deterministic, so every game began
    from the same position and an N-game match was one game replayed.
    """
    rng = random.Random(seed)
    lines = list(OPENING_BOOK)
    rng.shuffle(lines)
    out = [l.split() for l in lines]
    while len(out) < n:
        out.extend([l.split() for l in lines])
    return out[:max(n, 1)]


def elo(score, n):
    if score <= 0 or score >= 1:
        # Clamp instead of returning inf: callers parse this as an integer.
        return (800.0 if score >= 1 else -800.0), 0.0
    e = -400 * math.log10(1 / score - 1)
    se = math.sqrt(score * (1 - score) / n)
    return e, 1.96 * (400 / math.log(10) * se / (score * (1 - score)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("net", help="candidate .nnue for the current engine")
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--nodes", type=int, default=10000)
    a = ap.parse_args()

    new_bin = os.path.join(ROOT, "target/release/chess")
    old_bin = os.path.join(ROOT, "baseline/chess_v1")
    old_net = os.path.join(ROOT, "baseline/v1.nnue")
    if not os.path.exists(old_bin):
        print("no champion binary at baseline/chess_v1", file=sys.stderr)
        sys.exit(2)

    ref = Engine(new_bin, a.net)
    openings = load_openings(a.games)
    w = l = d = 0
    for _g in range(a.games):
        opening = openings[_g % len(openings)]

        for new_is_white in (True, False):
            e1 = Engine(new_bin, a.net)
            e2 = Engine(old_bin, old_net)
            moves = list(opening)
            result = None
            for _ply in range(400):
                st = ref.status(moves)
                if st != "playing":
                    result = (1.0 if new_is_white else 0.0) if st == "white-wins" \
                        else (0.0 if new_is_white else 1.0) if st == "black-wins" \
                        else 0.5
                    break
                white_to_move = (len(moves) % 2 == 0)
                eng = e1 if (white_to_move == new_is_white) else e2
                mv = eng.best(moves, a.nodes)
                if mv is None:
                    result = 0.5
                    break
                moves.append(mv)
            if result is None:
                result = 0.5
            w += result == 1.0; l += result == 0.0; d += result == 0.5
            e1.quit(); e2.quit()

        n = w + l + d
        if n % 20 == 0:
            sc = (w + 0.5 * d) / n
            e, err = elo(sc, n)
            print(f"  {n:4} games  +{w} ={d} -{l}  {sc*100:.1f}%  "
                  f"Elo {e:+.0f} +/- {err:.0f}", flush=True)

    ref.quit()
    n = w + l + d
    sc = (w + 0.5 * d) / n
    e, err = elo(sc, n)
    print(f"\ncandidate vs v1 champion: +{w} ={d} -{l} of {n}, {sc*100:.1f}%")
    print(f"Elo {e:+.0f} (95% CI +/-{err:.0f})")


if __name__ == "__main__":
    main()
