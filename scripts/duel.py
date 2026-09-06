#!/usr/bin/env python3
"""Play two UCI engines against each other and report the Elo difference.

Used to verify that a search change is actually worth Elo, rather than
assuming it from node counts.

  scripts/duel.py ./new ./old --games 100 --nodes 20000
"""
import argparse, math, random, subprocess, sys


class Engine:
    def __init__(self, path):
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)
        self._cmd("uci", "uciok")
        self._cmd("isready", "readyok")

    def _cmd(self, cmd, wait):
        self.p.stdin.write(cmd + "\n"); self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line or wait in line:
                return

    def newgame(self):
        self.p.stdin.write("ucinewgame\n"); self.p.stdin.flush()
        self._cmd("isready", "readyok")

    def status(self, moves):
        """Terminal state of the position, via the engine's own rules."""
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

    def quit(self):
        try:
            self.p.stdin.write("quit\n"); self.p.stdin.flush()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


OPENING_BOOK = [
    # Open games
    "e2e4 e7e5 g1f3 b8c6 f1b5", "e2e4 e7e5 g1f3 b8c6 f1c4 g8f6",
    "e2e4 e7e5 g1f3 b8c6 d2d4", "e2e4 e7e5 f1c4 g8f6 d2d3",
    "e2e4 e7e5 b1c3 g8f6 f2f4", "e2e4 e7e5 g1f3 g8f6 f3e5 d7d6",
    "e2e4 e7e5 g1f3 b8c6 b1c3 g8f6", "e2e4 e7e5 f2f4 e5f4 g1f3",
    # Sicilian
    "e2e4 c7c5 g1f3 d7d6 d2d4 c5d4", "e2e4 c7c5 g1f3 b8c6 d2d4 c5d4",
    "e2e4 c7c5 g1f3 e7e6 d2d4 c5d4", "e2e4 c7c5 b1c3 b8c6 g2g3",
    "e2e4 c7c5 g1f3 d7d6 f1b5", "e2e4 c7c5 c2c3 d7d5 e4d5",
    "e2e4 c7c5 d2d4 c5d4 c2c3", "e2e4 c7c5 g1f3 b8c6 f1b5 g7g6",
    # French / Caro-Kann
    "e2e4 e7e6 d2d4 d7d5 b1c3 g8f6", "e2e4 e7e6 d2d4 d7d5 e4e5 c7c5",
    "e2e4 e7e6 d2d4 d7d5 b1d2 c7c5", "e2e4 c7c6 d2d4 d7d5 b1c3 d5e4",
    "e2e4 c7c6 d2d4 d7d5 e4e5 c8f5", "e2e4 c7c6 g1f3 d7d5 b1c3",
    # Other e4
    "e2e4 d7d5 e4d5 d8d5 b1c3", "e2e4 g8f6 e4e5 f6d5 d2d4",
    "e2e4 d7d6 d2d4 g8f6 b1c3", "e2e4 g7g6 d2d4 f8g7 b1c3",
    "e2e4 b8c6 d2d4 d7d5", "e2e4 e7e5 g1f3 d7d6 d2d4",
    # Queen's pawn
    "d2d4 d7d5 c2c4 e7e6 b1c3", "d2d4 d7d5 c2c4 c7c6 g1f3",
    "d2d4 d7d5 c2c4 d5c4 g1f3", "d2d4 d7d5 g1f3 g8f6 c2c4 e7e6",
    "d2d4 g8f6 c2c4 e7e6 b1c3 f8b4", "d2d4 g8f6 c2c4 g7g6 b1c3 f8g7",
    "d2d4 g8f6 c2c4 e7e6 g1f3 b7b6", "d2d4 g8f6 c2c4 c7c5 d4d5",
    "d2d4 g8f6 g1f3 e7e6 c1g5", "d2d4 g8f6 c2c4 g7g6 g1f3 f8g7",
    "d2d4 f7f5 g2g3 g8f6 f1g2", "d2d4 d7d5 c1f4 g8f6 e2e3",
    "d2d4 e7e6 c2c4 g8f6 b1c3", "d2d4 d7d5 e2e3 g8f6 f1d3",
    # Flank
    "c2c4 e7e5 b1c3 g8f6 g1f3", "c2c4 g8f6 b1c3 e7e6 g1f3",
    "c2c4 c7c5 g1f3 g8f6 b1c3", "g1f3 d7d5 g2g3 g8f6 f1g2",
    "g1f3 g8f6 c2c4 g7g6 b1c3", "b2b3 e7e5 c1b2 b8c6 e2e3",
    "f2f4 d7d5 g1f3 g8f6 e2e3", "g2g3 d7d5 f1g2 g8f6 g1f3",
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
        return (float('inf') if score >= 1 else float('-inf')), 0.0
    e = -400 * math.log10(1 / score - 1)
    se = math.sqrt(score * (1 - score) / n)
    margin = 400 / math.log(10) * se / (score * (1 - score))
    return e, 1.96 * margin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("new"); ap.add_argument("old")
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--nodes", type=int, default=20000)
    ap.add_argument("--openings", type=int, default=8)
    a = ap.parse_args()

    rng = random.Random(0xC0FFEE)
    # A shared referee instance generates legal moves and detects game end.
    ref = Engine(a.new)

    openings = load_openings(a.games)
    w = l = d = 0
    for g in range(a.games):
        # Random opening, played by both colour assignments for fairness.
        opening = []
        ref.newgame()
        for _ in range(a.openings):
            mv = ref.best(opening, 1)
            if not mv:
                break
            opening.append(mv)

        for new_is_white in (True, False):
            e1, e2 = Engine(a.new), Engine(a.old)
            e1.newgame(); e2.newgame()
            moves = list(opening)
            result = None
            seen = {}
            for _ply in range(400):
                st = ref.status(moves)
                if st != "playing":
                    if st == "white-wins":
                        result = 1.0 if new_is_white else 0.0
                    elif st == "black-wins":
                        result = 0.0 if new_is_white else 1.0
                    else:
                        result = 0.5
                    break
                # Threefold repetition, tracked by move sequence position.
                key = " ".join(moves[-12:])
                seen[key] = seen.get(key, 0) + 1
                if len(moves) > 20 and seen[key] >= 3:
                    result = 0.5
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
            if result == 1.0: w += 1
            elif result == 0.0: l += 1
            else: d += 1
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
    print(f"\nnew vs old: +{w} ={d} -{l} of {n}, score {sc*100:.1f}%")
    print(f"Elo {e:+.0f} (95% CI +/-{err:.0f})")
    print("=> NEW is stronger" if e - err > 0 else
          "=> NEW is weaker" if e + err < 0 else "=> inconclusive")


if __name__ == "__main__":
    main()
