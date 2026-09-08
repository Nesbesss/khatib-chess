#!/usr/bin/env python3
"""Collect training, match, and bot status into one JSON blob.

Run on the machine that trains. The macOS app fetches this over ssh and
renders it; keeping the parsing here means the app stays a viewer.
"""
import glob
import json
import os
import re
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except Exception:
        return ""


def tail(path, n=400):
    try:
        with open(path, errors="ignore") as f:
            return f.readlines()[-n:]
    except OSError:
        return []


def parse_training(path):
    """Both formats: JSON lines from replay runs, 'epoch N/M' from older ones."""
    epochs, last = [], None
    for line in tail(path, 4000):
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "epoch" in d and "step" in d:
                last = d
                continue
        m = re.match(r"epoch (\d+)/(\d+)\s+train ([\d.]+)\s+val ([\d.]+)"
                     r"\s+sat (\d+)%lo/(\d+)%hi\s+(\d+)s", line)
        if m:
            epochs.append({
                "epoch": int(m.group(1)), "total": int(m.group(2)),
                "train": float(m.group(3)), "val": float(m.group(4)),
                "sat_lo": int(m.group(5)), "sat_hi": int(m.group(6)),
                "seconds": int(m.group(7)),
            })
    return epochs, last


def parity(run):
    out = []
    for f in sorted(glob.glob(os.path.join(run, "candidate", "*.parity.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        fi = d.get("float_integer", {})
        out.append({
            "name": os.path.basename(f).replace(".parity.json", ""),
            "status": d.get("status", "?"),
            "float_int_cp": round(fi.get("mae", 0), 2),
            "mismatches": d.get("mismatches", 0),
        })
    return out


def match_result(path):
    """Last Elo/Games pair from a Fastchess log."""
    elo = games = None
    for line in tail(path, 300):
        if line.startswith("Elo:"):
            elo = line.strip()
        elif line.startswith("Games:"):
            games = line.strip()
    if not elo:
        return None
    m = re.search(r"Elo: (-?[\d.]+) \+/- ([\d.]+)", elo)
    g = re.search(r"Games: (\d+), Wins: (\d+), Losses: (\d+), Draws: (\d+)",
                  games or "")
    return {
        "elo": float(m.group(1)) if m else None,
        "margin": float(m.group(2)) if m else None,
        "games": int(g.group(1)) if g else None,
        "w": int(g.group(2)) if g else None,
        "l": int(g.group(3)) if g else None,
        "d": int(g.group(4)) if g else None,
    }


def main():
    os.chdir(ROOT)
    status = {"generated": time.time(), "runs": [], "matches": [], "system": {}}

    # Training runs, newest log first.
    logs = sorted(glob.glob("logs/*train*.log") + glob.glob("logs/train_*.log"),
                  key=lambda p: os.path.getmtime(p), reverse=True)
    seen = set()
    for path in logs:
        name = os.path.basename(path).replace(".log", "")
        if name in seen:
            continue
        seen.add(name)
        epochs, last = parse_training(path)
        if not epochs and not last:
            continue
        # Match "v11_train" to logs/v11-test/ etc: take the version token and
        # find the run directory that starts with it.
        tok = re.match(r"(?:train_)?(v\d+[a-z]?)", name)
        run_dir = None
        if tok:
            cands = [d for d in glob.glob("logs/*/")
                     if os.path.basename(d.rstrip("/")).startswith(tok.group(1))]
            run_dir = max(cands, key=os.path.getmtime) if cands else None
        status["runs"].append({
            "name": name,
            "modified": os.path.getmtime(path),
            "epochs": epochs[-12:],
            "progress": last,
            "parity": parity(run_dir) if run_dir else [],
        })

    # Match results.
    for path in sorted(glob.glob("logs/*.log"), key=os.path.getmtime,
                       reverse=True)[:25]:
        r = match_result(path)
        if r and r["games"]:
            r["name"] = os.path.basename(path).replace(".log", "")
            r["modified"] = os.path.getmtime(path)
            status["matches"].append(r)

    # Machine and jobs.
    status["system"] = {
        "load": sh("uptime | sed 's/.*load averages*: //'"),
        "disk_free": sh("df -h . | tail -1 | awk '{print $4}'"),
        "training": bool(sh("pgrep -f 'replay_v10|trainer/train.py'")),
        "generating": bool(sh("pgrep -f generate_data")),
        "bot": bool(sh("pgrep -f lichess_bot")),
        "selfplay_positions": int(sh(
            "cat data/selfplay/shard_*.txt 2>/dev/null | wc -l") or 0),
    }

    print(json.dumps(status, indent=1))


if __name__ == "__main__":
    main()
