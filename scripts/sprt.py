#!/usr/bin/env python3
"""Paired-opening Fastchess tests with immutable binary/network snapshots.

python3 scripts/sprt.py NEW OLD --new-net NEW.nnue --old-net OLD.nnue
python3 scripts/sprt.py --resume logs/sprt/RUN
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOOK = ROOT / 'data/books/8moves_v3.unique.epd'
DEFAULT_FASTCHESS = ROOT / 'target/testing/fastchess'


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def dump(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def engine_env():
    env = os.environ.copy()
    # Do not silently inherit an experimental time cap from a shell.
    env.pop('MOVE_CAP_MS', None)
    return env


def preflight(directory, threads=1, hash_mb=64):
    commands = ('uci\n'
                f'setoption name Threads value {threads}\n'
                f'setoption name Hash value {hash_mb}\n'
                'setoption name OwnBook value false\nucinewgame\ninfo\n'
                'position startpos moves e2e4 e7e5 g1f3 b8c6\n'
                'go nodes 1000\nisready\nquit\n')
    result = subprocess.run([str(directory / 'engine'), '--net', 'net.nnue'],
                            cwd=directory, input=commands, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=30, env=engine_env())
    output = result.stdout
    (directory / 'preflight.log').write_text(output)
    failures = ('network load failed', 'network: none', 'bad fen', 'illegal move', 'book move')
    if (result.returncode or any(s in output.lower() for s in failures)
            or 'loaded network' not in output.lower()
            or 'uciok' not in output or 'readyok' not in output
            or not re.search(r'^bestmove [a-h][1-8][a-h][1-8][qrbn]?\b', output, re.M)):
        raise ValueError(f'Engine/net preflight failed: {directory}/preflight.log\n{output}')
    for option in ('Hash', 'Threads', 'OwnBook'):
        if f'option name {option} ' not in output:
            raise ValueError(f'Engine lacks required UCI option {option}: {directory}')
    return output


def snapshot_engine(binary, network, directory, threads=1, hash_mb=64):
    binary, network = Path(binary).resolve(), Path(network).resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError(f'Not an executable: {binary}')
    if not network.is_file():
        raise ValueError(f'No network: {network}')
    directory.mkdir()
    result = {}
    for name, source in [('engine', binary), ('net.nnue', network)]:
        before = sha256(source)
        shutil.copy2(source, directory / name)
        copied = sha256(directory / name)
        if copied != before or sha256(source) != before:
            raise ValueError(f'{source} changed while being copied; choose a saved checkpoint')
        result[name] = dict(source=str(source), sha256=copied)
    preflight(directory, threads, hash_mb)
    return result


def read_book(path):
    positions = []
    seen = set()
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        fields = line.split()
        if len(fields) < 4 or fields[1] not in ('w', 'b') or fields[0].count('/') != 7:
            raise ValueError(f'Expected an EPD opening file: {path}')
        position = ' '.join(fields[:4])
        if position in seen:
            raise ValueError(f'Duplicate starting position in {path}: {position}')
        seen.add(position)
        positions.append(position)
    if not positions:
        raise ValueError(f'Empty opening book: {path}')
    return positions


def default_concurrency():
    config = ROOT / 'target/testing/concurrency.json'
    if config.exists():
        return int(json.loads(config.read_text())['recommended_concurrency'])
    return 4


def make_command(run, a):
    command = [str(run / 'fastchess'), '-strict']
    for name in ('new', 'old'):
        command += ['-engine', f'cmd={run / name / "engine"}', f'dir={run / name}',
                    'args=--net net.nnue', f'name={name}']
    command += ['-each', 'proto=uci', f'option.Threads={a.threads}',
                f'option.Hash={a.hash}', 'option.OwnBook=false']
    if a.nodes is not None:
        command += [f'nodes={a.nodes}']
    elif a.tc:
        command += [f'tc={a.tc}']
    else:
        command += [f'st={a.movetime}']
    command += ['-openings', f'file={run / "openings.epd"}', 'format=epd', 'order=sequential',
                '-rounds', str(a.games // 2), '-repeat', '-concurrency', str(a.concurrency),
                '-srand', str(a.seed), '-report', 'penta=true',
                '-pgnout', f'file={run / "games.pgn"}', 'nodes=true', 'timeleft=true',
                '-config', f'outname={run / "config.json"}', '-autosaveinterval', '2',
                '-log', f'file={run / "fastchess.log"}', 'level=warn', 'engine=true',
                '-ratinginterval', '20', '-scoreinterval', '20']
    if not a.fixed_games:
        command += ['-sprt', f'elo0={a.elo0}', f'elo1={a.elo1}',
                    f'alpha={a.alpha}', f'beta={a.beta}', 'model=logistic']
    return command


def execute(run, command, manifest):
    print(f'Run directory: {run}', flush=True)
    print(f'Resume: python3 scripts/sprt.py --resume {run}', flush=True)
    start = time.monotonic()
    process = subprocess.Popen(command, cwd=run, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1,
                               start_new_session=True, env=engine_env())
    try:
        with (run / 'console.log').open('a') as log:
            for line in process.stdout:
                print(line, end='', flush=True)
                log.write(line)
                log.flush()
        code = process.wait()
    except KeyboardInterrupt:
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        code = 130
    manifest.setdefault('executions', []).append(dict(
        command=command, elapsed_seconds=time.monotonic()-start, exit_code=code,
        finished_utc=datetime.now(timezone.utc).isoformat()))
    dump(run / 'manifest.json', manifest)
    print('Fastchess owns the statistical verdict; reaching the game cap is not a pass.')
    return code


def resume(run):
    run = Path(run).resolve()
    manifest = json.loads((run / 'manifest.json').read_text())
    for name, expected in manifest['snapshot_hashes'].items():
        if sha256(run / name) != expected:
            raise ValueError(f'Refusing resume: modified snapshot {name}')
    if not (run / 'config.json').is_file():
        raise ValueError('No saved Fastchess config; no games may have completed')
    return execute(run, [str(run / 'fastchess'), '-config', f'file={run / "config.json"}'], manifest)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == '--resume':
        if len(argv) != 2:
            raise ValueError('--resume accepts only a run directory; settings are immutable')
        return resume(argv[1])
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('new', type=Path)
    ap.add_argument('old', type=Path)
    ap.add_argument('--new-net', type=Path, default=ROOT / 'nets/v7.nnue')
    ap.add_argument('--old-net', type=Path, default=ROOT / 'nets/v7.nnue')
    ap.add_argument('--book', type=Path, default=DEFAULT_BOOK)
    ap.add_argument('--fastchess', type=Path, default=DEFAULT_FASTCHESS)
    ap.add_argument('--concurrency', type=int, default=default_concurrency())
    ap.add_argument('--threads', type=int, default=1)
    ap.add_argument('--hash', type=int, default=64)
    ap.add_argument('--games', type=int, default=40000, help='maximum TOTAL games, even; default 40000')
    ap.add_argument('--fixed-games', action='store_true', help='disable SPRT, for diagnostics')
    limits = ap.add_mutually_exclusive_group()
    limits.add_argument('--movetime', type=float, default=0.2, help='seconds per move; default 0.2')
    limits.add_argument('--tc', help='real clock, e.g. 60+0.6; includes engine time management')
    limits.add_argument('--nodes', type=int, help='fixed-node diagnostic; excludes speed differences')
    ap.add_argument('--elo0', type=float, default=0)
    ap.add_argument('--elo1', type=float, default=5)
    ap.add_argument('--alpha', type=float, default=0.05)
    ap.add_argument('--beta', type=float, default=0.05)
    ap.add_argument('--seed', type=int, default=None, help='default random seed, saved in manifest')
    ap.add_argument('--out', type=Path, help='new run directory; must not exist')
    ap.add_argument('--prepare-only', action='store_true', help='snapshot and preflight without playing')
    a = ap.parse_args(argv)
    if a.seed is None:
        a.seed = random.SystemRandom().randrange(1, 2**31)
    if (a.games < 2 or a.games % 2 or min(a.concurrency, a.threads, a.hash) < 1
            or not math.isfinite(a.movetime) or a.movetime <= 0
            or (a.nodes is not None and a.nodes < 1)):
        ap.error('positive limits/resources and an even total game count >= 2 are required')
    if not (math.isfinite(a.elo0) and math.isfinite(a.elo1) and a.elo0 < a.elo1
            and 0 < a.alpha < 0.5 and 0 < a.beta < 0.5):
        ap.error('require finite elo0 < elo1 and 0 < alpha,beta < 0.5')
    if not a.fastchess.is_file():
        ap.error('Fastchess is missing; run python3 scripts/setup_testing.py')
    positions = read_book(a.book)
    if a.games // 2 > len(positions):
        ap.error(f'{len(positions)} unique openings allow at most {len(positions)*2} games; no cycling')
    random.Random(a.seed).shuffle(positions)
    run = (a.out or ROOT / 'logs/sprt' / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6])).resolve()
    run.mkdir(parents=True, exist_ok=False)
    manifest = dict(settings={k: str(v) if isinstance(v, Path) else v for k, v in vars(a).items()},
                    book_source=str(a.book.resolve()), book_source_sha256=sha256(a.book),
                    available_unique_openings=len(positions), env={'MOVE_CAP_MS': 'unset'},
                    created_utc=datetime.now(timezone.utc).isoformat())
    # The exact schedule is saved and consumed sequentially, once per colour pair.
    (run / 'openings.epd').write_text('\n'.join(positions[:a.games//2]) + '\n')
    shutil.copy2(a.fastchess, run / 'fastchess')
    for name in ('new', 'old'):
        manifest[name] = snapshot_engine(getattr(a, name), getattr(a, name + '_net'),
                                         run / name, a.threads, a.hash)
    files = ['fastchess', 'openings.epd', 'new/engine', 'new/net.nnue', 'old/engine', 'old/net.nnue']
    manifest['snapshot_hashes'] = {name: sha256(run / name) for name in files}
    manifest['fastchess_version'] = subprocess.check_output(
        [str(run / 'fastchess'), '-version'], text=True)
    command = make_command(run, a)
    manifest['command'] = command
    dump(run / 'manifest.json', manifest)
    if a.tc:
        print('Clock test: existing panic mode below 30s remains part of engine behaviour.')
    elif a.nodes:
        print('Fixed-node diagnostic: this does not measure the cost of a slower evaluator.')
    else:
        print(f'Equal real time: {a.movetime}s/move; bypasses clock panic mode. No time-management test.')
    print(f'{a.games//2} distinct colour pairs; logistic SPRT [{a.elo0}, {a.elo1}]'
          if not a.fixed_games else f'Fixed diagnostic: {a.games} total games')
    if a.prepare_only:
        print(f'Prepared and verified: {run}')
        return 0
    return execute(run, command, manifest)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'error: {error}', file=sys.stderr)
        sys.exit(2)
