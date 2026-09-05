#!/usr/bin/env python3
"""Run the engine as a Lichess bot.

Accepts challenges from real players and plays them with our engine, so the
account earns a genuine public rating instead of an estimate.

Setup (once):
  1. Create a BRAND NEW lichess account that has never played a game.
  2. Make a token at lichess.org/account/oauth/token/create with "bot:play".
  3. LICHESS_TOKEN=<token> python3 scripts/lichess_bot.py --upgrade
  4. LICHESS_TOKEN=<token> python3 scripts/lichess_bot.py

By default the bot waits to be challenged at lichess.org/@/<name> -- this is
how humans play it, since Lichess has no seek pool for BOT accounts.

Add --seek 5+3 to also challenge other online bots so it keeps playing while
nobody is around. Add --rated for rated games.

The upgrade is irreversible, which is why the account must be new.
"""
import argparse, json, os, random, subprocess, sys, threading, time

import requests

TG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TG_SUBS = os.path.join(TG_DIR, ".telegram_subs")


def _tg_token():
    try:
        t = open(os.path.join(TG_DIR, ".telegram_token")).read().strip()
        return t or None
    except OSError:
        return None


def _tg_chats():
    """Everyone subscribed, one chat id per line."""
    try:
        return [l.strip() for l in open(TG_SUBS) if l.strip()]
    except OSError:
        return []


def tg_add_chat(chat_id):
    """Subscribe a chat; returns True if it is new."""
    chat_id = str(chat_id)
    if chat_id in _tg_chats():
        return False
    with open(TG_SUBS, "a") as f:
        f.write(chat_id + "\n")
    return True


def tg_send(chat_id, text):
    tok = _tg_token()
    if not tok:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        print(f"telegram send failed: {e}")


def notify(text):
    """Broadcast to every subscriber; never let it break a game."""
    for chat in _tg_chats():
        tg_send(chat, text)


def telegram_listener():
    """Let anyone subscribe by messaging the bot /start.

    Runs in its own thread; long-polls Telegram and never raises out.
    """
    tok = _tg_token()
    if not tok:
        return
    offset = None
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates",
                             params={"timeout": 50, "offset": offset},
                             timeout=60)
            for up in r.json().get("result", []):
                offset = up["update_id"] + 1
                msg = up.get("message") or {}
                chat = str((msg.get("chat") or {}).get("id", ""))
                text = (msg.get("text") or "").strip().lower()
                if not chat:
                    continue
                if text.startswith("/stop"):
                    subs = [c for c in _tg_chats() if c != chat]
                    open(TG_SUBS, "w").write("".join(c + "\n" for c in subs))
                    tg_send(chat, "Unsubscribed. Send /start to get alerts again.")
                elif text.startswith("/start") or text.startswith("/sub"):
                    fresh = tg_add_chat(chat)
                    tg_send(chat, "Subscribed to Khatib." if fresh
                            else "Already subscribed.")
                    tg_send(chat, "You will get a link for every game it plays.\n"
                                  "Play it yourself: lichess.org/@/nesbes\n"
                                  "Source: github.com/Nesbesss/khatib-chess\n"
                                  "/stop to unsubscribe.")
        except Exception:
            time.sleep(10)


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
        # Our own outgoing challenges arrive on this stream too; accepting
        # one is a 404 no-op, so skip them.
        if ch.get("challenger", {}).get("id", "").lower() == self.username.lower():
            return
        r = self.s.post(f"{API}/challenge/{cid}/accept")
        print(f"accepted challenge {cid} from "
              f"{ch.get('challenger', {}).get('name', '?')}: {r.status_code}")

    def play(self, game_id):
        eng = Engine()
        my_color = None
        print(f"game {game_id} started")
        notify(f"\u265e Game started\nhttps://lichess.org/{game_id}")
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
                        status = state.get("status")
                        winner = state.get("winner", "")
                        print(f"game {game_id} over: {status} {winner}")
                        if not winner:
                            head = f"\u00bd Draw ({status})"
                        elif winner == my_color:
                            head = f"\u2705 WON by {status}"
                        else:
                            head = f"\u274c Lost by {status}"
                        notify(f"{head}\nhttps://lichess.org/{game_id}")
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

    def online_bots(self, limit=60):
        """Bots currently online, as a list of usernames."""
        try:
            r = self.s.get(f"{API}/bot/online", params={"nb": limit},
                           stream=True, timeout=20)
            out = []
            for line in r.iter_lines():
                if not line:
                    continue
                u = json.loads(line).get("username")
                if u and u.lower() != self.username.lower():
                    out.append(u)
            return out
        except Exception:
            return []

    def seek(self, minutes: int, inc: int, rated: bool):
        """Keep a game going by challenging other online bots.

        Lichess has no seek pool for BOT accounts -- board/seek is Board API
        only and rejects a bot token -- so a bot either waits to be challenged
        or challenges somebody itself.
        """
        while True:
            if self.playing:
                time.sleep(5)
                continue
            bots = self.online_bots()
            random.shuffle(bots)
            if not bots:
                time.sleep(30)
                continue
            for name in bots[:5]:
                if self.playing:
                    break
                try:
                    r = self.s.post(f"{API}/challenge/{name}", data={
                        "clock.limit": minutes * 60, "clock.increment": inc,
                        "rated": "true" if rated else "false",
                        "variant": "standard", "color": "random",
                    }, timeout=15)
                    if r.status_code in (200, 201):
                        print(f"challenged {name}")
                        time.sleep(25)      # give them a chance to accept
                    elif r.status_code == 429:
                        print("rate limited; pausing 10 min")
                        time.sleep(600)
                        break
                except Exception:
                    pass
                time.sleep(5)

    def run(self, seek_tc=None, rated=False):
        print(f"listening as {self.username} — challenge it at "
              f"lichess.org/@/{self.username}")
        if _tg_token():
            threading.Thread(target=telegram_listener, daemon=True).start()
            print(f"telegram: on ({len(_tg_chats())} subscribed) — "
                  "anyone can /start the bot to follow games")
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
