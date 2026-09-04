#!/usr/bin/env python3
"""Play Kraken against Leela Chess Zero (the open-source AlphaZero method).

AlphaZero itself was never released, so Leela is the closest thing that can
actually be measured. Node counts are matched rather than time, since Leela's
GPU search and Kraken's CPU search have very different node costs.
"""
import argparse, math, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class UCI:
    def __init__(self, cmd, options=None):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self._w("uci", "uciok")
        for k, v in (options or {}).items():
            self.p.stdin.write(f"setoption name {k} value {v}\n")
        self._w("isready", "readyok")

    def _w(self, c, t):
        self.p.stdin.write(c + "\n"); self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line or t in line:
                return

    def best(self, moves, ms):
        pos = "position startpos" + (" moves " + " ".join(moves) if moves else "")
        self.p.stdin.write(f"{pos}\ngo movetime {ms}\n"); self.p.stdin.flush()
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
    ap.add_argument("--games", type=int, default=15, help="opening pairs")
    ap.add_argument("--ms", type=int, default=1000,
                    help="equal thinking time per move for both engines")
    a = ap.parse_args()

    ours_cmd = [os.path.join(ROOT, "target/release/chess")]
    # Book off: it returns instantly, which hands the opponent extra thinking
    # time over the course of a game and is not an engine-strength comparison.
    ref = UCI(ours_cmd, {"OwnBook": "false"})
    w = l = d = 0
    for _g in range(a.games):
        opening = []
        for _ in range(8):
            mv = ref.best(opening, 1)
            if not mv:
                break
            opening.append(mv)

        for ours_white in (True, False):
            ours = UCI(ours_cmd, {"OwnBook": "false"})
            leela = UCI(["lc0"], {"Threads": 2})
            moves = list(opening)
            result = None
            for _ply in range(400):
                st = ref.status(moves)
                if st != "playing":
                    result = (1.0 if ours_white else 0.0) if st == "white-wins" \
                        else (0.0 if ours_white else 1.0) if st == "black-wins" \
                        else 0.5
                    break
                wtm = (len(moves) % 2 == 0)
                mv = (ours.best(moves, a.ms) if wtm == ours_white
                      else leela.best(moves, a.ms))
                if mv is None:
                    result = 0.5
                    break
                moves.append(mv)
            if result is None:
                result = 0.5
            w += result == 1.0; l += result == 0.0; d += result == 0.5
            ours.quit(); leela.quit()

        n = w + l + d
        sc = (w + 0.5 * d) / n
        e, err = elo(sc, n)
        print(f"  {n:3} games  +{w} ={d} -{l}  {sc*100:5.1f}%  "
              f"Elo {e:+.0f} +/- {err:.0f}", flush=True)

    ref.quit()
    n = w + l + d
    sc = (w + 0.5 * d) / n
    e, err = elo(sc, n)
    print(f"\nKraken vs Leela: +{w} ={d} -{l} of {n}, {sc*100:.1f}%")
    print(f"Elo {e:+.0f} (95% CI +/-{err:.0f})")


if __name__ == "__main__":
    main()
