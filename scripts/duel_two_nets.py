#!/usr/bin/env python3
"""Play two networks against each other in the same engine binary.

Comparing each net against a third party (the champion or the handcrafted
eval) adds that opponent's variance to both measurements. A direct
head-to-head is the cleanest way to rank two candidates.
"""
import argparse, math, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "target/release/chess")


class Engine:
    def __init__(self, net):
        self.p = subprocess.Popen([BIN, "--net", net], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)
        self._cmd("uci", "uciok")
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


def elo(score, n):
    if score <= 0 or score >= 1:
        return (800.0 if score >= 1 else -800.0), 0.0
    e = -400 * math.log10(1 / score - 1)
    se = math.sqrt(score * (1 - score) / n)
    return e, 1.96 * (400 / math.log(10) * se / (score * (1 - score)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("net_a"); ap.add_argument("net_b")
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--nodes", type=int, default=8000)
    a = ap.parse_args()

    ref = Engine(a.net_a)
    w = l = d = 0
    for _g in range(a.games):
        opening = []
        for _ in range(8):
            mv = ref.best(opening, 1)
            if not mv:
                break
            opening.append(mv)

        for a_is_white in (True, False):
            e1, e2 = Engine(a.net_a), Engine(a.net_b)
            moves = list(opening)
            result = None
            for _ply in range(400):
                st = ref.status(moves)
                if st != "playing":
                    result = (1.0 if a_is_white else 0.0) if st == "white-wins" \
                        else (0.0 if a_is_white else 1.0) if st == "black-wins" \
                        else 0.5
                    break
                wtm = (len(moves) % 2 == 0)
                mv = (e1 if (wtm == a_is_white) else e2).best(moves, a.nodes)
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
    print(f"\n{os.path.basename(a.net_a)} vs {os.path.basename(a.net_b)}: "
          f"+{w} ={d} -{l} of {n}, {sc*100:.1f}%")
    print(f"Elo {e:+.0f} (95% CI +/-{err:.0f})")


if __name__ == "__main__":
    main()
