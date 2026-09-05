# Running Khatib as a 24/7 Lichess bot

Any always-on machine works — a Mac mini, a Pi, a VPS. The bot holds a live
connection to Lichess, so it cannot run on serverless hosts like Vercel.

## One-time setup

```bash
git clone https://github.com/Nesbesss/khatib-chess && cd khatib-chess
cargo build --release

# A Lichess account that has NEVER played a game. The upgrade is permanent.
echo "lip_xxxxxxxx" > .lichess_token        # token with the bot:play scope
python3 scripts/lichess_bot.py --upgrade
```

## Telegram notifications (optional)

Anyone can follow the bot's games — no chat id to configure. Get a token from
[@BotFather](https://t.me/BotFather) and save it:

```bash
echo "<bot-token>" > .telegram_token
```

Then anyone who sends your Telegram bot `/start` is subscribed and gets a link
for every game Khatib plays, win or lose. `/stop` unsubscribes. Subscribers are
kept in `.telegram_subs`.

The token file is gitignored; never commit it. If a token leaks, revoke it with
`/revoke` in @BotFather.

## Run it

```bash
./scripts/run_bot_forever.sh              # casual
RATED=1 ./scripts/run_bot_forever.sh      # rated — earns a real Lichess Elo
```

The wrapper restarts the bot if it crashes and keeps a log in `logs/bot.log`.

## Start on boot (macOS)

```bash
sed "s|REPO|$PWD|g" scripts/com.khatib.bot.plist \
  > ~/Library/LaunchAgents/com.khatib.bot.plist
launchctl load ~/Library/LaunchAgents/com.khatib.bot.plist
```

Stop it with `launchctl unload ~/Library/LaunchAgents/com.khatib.bot.plist`.

Keep the machine awake, or it forfeits mid-game:

```bash
sudo pmset -a sleep 0 disablesleep 1
```

## How it finds opponents

Lichess has no seek pool for BOT accounts. The bot therefore:

- **accepts** any standard challenge sent to `lichess.org/@/<your-bot>` —
  this is how humans play it, so share that link;
- **challenges** other online bots itself, so it keeps playing when nobody
  is around.

Rated games are what produce a real, public Elo. That number is far better
evidence of strength than any benchmark run at home.
