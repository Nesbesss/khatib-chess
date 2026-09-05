#!/usr/bin/env python3
"""Run the engine as a Lichess bot.

Accepts challenges from real players and plays them with our engine, so the
account earns a genuine public rating instead of an estimate.

Setup (once):
  1. Create a BRAND NEW lichess account that has never played a game.
  2. Make a token at lichess.org/account/oauth/token/create with "bot:play".
  3. LICHESS_TOKEN=<token> python3 scripts/lichess_bot.py --upgrade
  4. LICHESS_TOKEN=<token> python3 scripts/lichess_bot.py

Add --seek 5+3 to queue for games instead of only waiting to be challenged;
real players are matched from the pool. Add --rated to play rated games.

The upgrade is irreversible, which is why the account must be new.
"""
import argparse, os, subprocess, sys, threading, time

import requests

API = "https://lichess.org/api"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "target/release/chess")
NET = os.path.join(ROOT, "net.nnue")


class Engine:
    """One UCI engine process per game."""

    def __init__(self):
        args = [ENGINE] + (["--net", NET] if os.path.exists(NET) else [])
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)
        self._wait("uci", "uciok")
        self._wait("isready", "readyok")

    def _wait(self, cmd, token):
        self.p.stdin.write(cmd + "\n"); self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line or token in line:
                return

    def best(self, moves, wtime, btime, winc, binc):
        pos = "position startpos" + (" moves " + moves if moves else "")
        go = (f"go wtime {wtime} btime {btime} winc {winc} binc {binc}")
        self.p.stdin.write(f"{pos}\n{go}\n"); self.p.stdin.flush()
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


class Bot:
    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"
        me = self.s.get(f"{API}/account").json()
        self.username = me.get("username", "?")
        self.is_bot = me.get("title") == "BOT"
        # Lichess allows a bot only one game at a time by default, so the
        # seek loop waits while a game is live.
        self.playing = False

    def upgrade(self):
        r = self.s.post(f"{API}/bot/account/upgrade")
        if r.status_code == 200:
            print(f"{self.username} is now a BOT account")
        else:
            print(f"upgrade failed: {r.status_code} {r.text[:200]}")

    def stream_events(self):
        with self.s.get(f"{API}/stream/event", stream=True) as r:
            for line in r.iter_lines():
                if not line:
                    continue
                yield __import__("json").loads(line)

    def handle_challenge(self, ch):
        cid = ch["id"]
        variant = ch.get("variant", {}).get("key", "standard")
        speed = ch.get("speed", "")
        # Only standard chess: the engine knows no variant rules.
        if variant != "standard" or speed in ("ultraBullet",):
            self.s.post(f"{API}/challenge/{cid}/decline",
                        data={"reason": "standard"})
            print(f"declined {cid} ({variant}/{speed})")
            return
        r = self.s.post(f"{API}/challenge/{cid}/accept")
        print(f"accepted challenge {cid} from "
              f"{ch.get('challenger', {}).get('name', '?')}: {r.status_code}")

    def play(self, game_id):
        eng = Engine()
        my_color = None
        print(f"game {game_id} started")
        try:
            with self.s.get(f"{API}/bot/game/stream/{game_id}", stream=True) as r:
                for line in r.iter_lines():
                    if not line:
                        continue
                    ev = __import__("json").loads(line)
                    if ev["type"] == "gameFull":
                        my_color = ("white"
                                    if ev["white"].get("id", "").lower()
                                       == self.username.lower() else "black")
                        state = ev["state"]
                    elif ev["type"] == "gameState":
                        state = ev
                    else:
                        continue

                    if state.get("status") not in ("started", "created"):
                        print(f"game {game_id} over: {state.get('status')} "
                              f"{state.get('winner', '')}")
                        return

                    moves = state.get("moves", "")
                    ply = len(moves.split()) if moves else 0
                    our_turn = (ply % 2 == 0) == (my_color == "white")
                    if not our_turn:
                        continue

                    mv = eng.best(moves, state.get("wtime", 60000),
                                  state.get("btime", 60000),
                                  state.get("winc", 0), state.get("binc", 0))
                    if not mv:
                        return
                    self.s.post(f"{API}/bot/game/{game_id}/move/{mv}")
        finally:
            eng.quit()

    def seek(self, minutes: int, inc: int, rated: bool):
        """Enter the pool so real players get matched with us.

        The seek endpoint blocks until someone accepts or it times out, so it
        runs on its own thread and simply re-seeks when it returns.
        """
        while True:
            if self.playing:
                time.sleep(5)
                continue
            try:
                # Blocks for up to ~20s server-side; a match ends it early.
                self.s.post(f"{API}/board/seek", data={
                    "time": minutes, "increment": inc,
                    "rated": "true" if rated else "false",
                    "variant": "standard",
                }, timeout=40)
            except Exception:
                time.sleep(5)

    def run(self, seek_tc=None, rated=False):
        print(f"listening as {self.username} — challenge it at "
              f"lichess.org/@/{self.username}")
        if seek_tc:
            mins, inc = seek_tc
            print(f"seeking {mins}+{inc} games "
                  f"({'rated' if rated else 'casual'}) against real players")
            threading.Thread(target=self.seek, args=(mins, inc, rated),
                             daemon=True).start()
        for ev in self.stream_events():
            t = ev.get("type")
            if t == "challenge":
                self.handle_challenge(ev["challenge"])
            elif t == "gameStart":
                gid = ev["game"]["id"]
                self.playing = True
                threading.Thread(target=self.play, args=(gid,),
                                 daemon=True).start()
            elif t == "gameFinish":
                self.playing = False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upgrade", action="store_true",
                    help="convert the account to a BOT (irreversible)")
    ap.add_argument("--seek", metavar="MIN+INC",
                    help="also queue for games against real players, "
                         "e.g. --seek 5+3")
    ap.add_argument("--rated", action="store_true",
                    help="play rated games when seeking (default casual)")
    a = ap.parse_args()

    seek_tc = None
    if a.seek:
        try:
            mins, inc = a.seek.split("+")
            seek_tc = (int(mins), int(inc))
        except ValueError:
            print(f"--seek wants MIN+INC, e.g. 5+3 (got {a.seek!r})")
            sys.exit(1)

    token = os.environ.get("LICHESS_TOKEN")
    if not token:
        print("set LICHESS_TOKEN first — see the docstring for how to get one")
        sys.exit(1)

    bot = Bot(token)
    if a.upgrade:
        bot.upgrade()
        return
    if not bot.is_bot:
        print(f"{bot.username} is not a BOT account yet. Run with --upgrade "
              "(the account must never have played a game).")
        sys.exit(1)
    while True:
        try:
            bot.run(seek_tc, a.rated)
        except Exception as e:
            print(f"stream dropped ({e}); reconnecting in 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()
