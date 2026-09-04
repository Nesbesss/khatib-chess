#!/usr/bin/env python3
"""Measure our engine against Stockfish at a calibrated Elo.

Stockfish's UCI_Elo gives a rating-limited opponent, so scoring ~50% against
a given setting is direct evidence of our own strength.

  scripts/vs_stockfish.py --elo 2000 --games 20 --nodes 20000
"""
import argparse, math, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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

    def go(self, moves, nodes=None, movetime=None):
        pos = "position startpos" + (" moves " + " ".join(moves) if moves else "")
        limit = f"go nodes {nodes}" if nodes else f"go movetime {movetime}"
        self.p.stdin.write(f"{pos}\n{limit}\n"); self.p.stdin.flush()
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


def elo_diff(score, n):
    if score <= 0 or score >= 1:
        return (800.0 if score >= 1 else -800.0), 0.0
    e = -400 * math.log10(1 / score - 1)
    se = math.sqrt(score * (1 - score) / n)
    return e, 1.96 * (400 / math.log(10) * se / (score * (1 - score)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--elo", type=int, default=2000,
                    help="Stockfish UCI_Elo (1320-3190); 0 = unlimited")
    ap.add_argument("--games", type=int, default=10, help="opening pairs")
    ap.add_argument("--nodes", type=int, default=20000)
    ap.add_argument("--net", default=os.path.join(ROOT, "net.nnue"))
    a = ap.parse_args()

    ours_cmd = [os.path.join(ROOT, "target/release/chess"), "--net", a.net]
    sf_opts = {"Threads": 1, "Hash": 64}
    if a.elo:
        sf_opts["UCI_LimitStrength"] = "true"
        sf_opts["UCI_Elo"] = a.elo

    ref = UCI(ours_cmd)
    w = l = d = 0
    for _g in range(a.games):
        opening = []
        for _ in range(8):
            mv = ref.go(opening, nodes=1)
            if not mv:
                break
            opening.append(mv)

        for ours_white in (True, False):
            ours = UCI(ours_cmd)
            sf = UCI(["stockfish"], sf_opts)
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
                if wtm == ours_white:
                    mv = ours.go(moves, nodes=a.nodes)
                else:
                    # Node-limited Stockfish would defeat the Elo limiter, so
                    # give it a small fixed time instead.
                    mv = sf.go(moves, movetime=100)
                if mv is None:
                    result = 0.5
                    break
                moves.append(mv)
            if result is None:
                result = 0.5
            w += result == 1.0; l += result == 0.0; d += result == 0.5
            ours.quit(); sf.quit()

        n = w + l + d
        sc = (w + 0.5 * d) / n
        e, err = elo_diff(sc, n)
        print(f"  {n:3} games  +{w} ={d} -{l}  {sc*100:5.1f}%  "
              f"diff {e:+.0f} +/- {err:.0f}", flush=True)

    ref.quit()
    n = w + l + d
    sc = (w + 0.5 * d) / n
    e, err = elo_diff(sc, n)
    label = f"Stockfish @ {a.elo} Elo" if a.elo else "Stockfish (unlimited)"
    print(f"\nvs {label}: +{w} ={d} -{l} of {n}, score {sc*100:.1f}%")
    print(f"Elo difference: {e:+.0f} (95% CI +/-{err:.0f})")
    if a.elo:
        print(f"=> our estimated rating: {a.elo + e:.0f}")


if __name__ == "__main__":
    main()
