#!/usr/bin/env python3
"""Measure concurrent paired binary/net searches at an equal node budget.

Each worker owns two engines, as in Fastchess without pondering. Throughput
is measured in cycles (one search per engine), not playing Elo or exact NPS.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import random
import statistics
import subprocess
import threading
import time
from sprt import ROOT, DEFAULT_BOOK, dump, engine_env, read_book, snapshot_engine


class Engine:
    def __init__(self, directory):
        self.process = subprocess.Popen([str(directory / 'engine'), '--net', 'net.nnue'],
                                        cwd=directory, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, bufsize=1, env=engine_env())
        self.lines = queue.Queue()
        def reader():
            for line in self.process.stdout:
                self.lines.put(line.strip())
            self.lines.put(None)
        threading.Thread(target=reader, daemon=True).start()
        self.send('uci')
        self.until('uciok')
        self.send('setoption name Threads value 1\nsetoption name Hash value 64\n'
                  'setoption name OwnBook value false\nisready')
        self.until('readyok')

    def send(self, text):
        self.process.stdin.write(text + '\n')
        self.process.stdin.flush()

    def until(self, prefix):
        result = []
        deadline = time.monotonic() + 60
        while True:
            line = self.lines.get(timeout=max(0.01, deadline-time.monotonic()))
            if line is None:
                raise RuntimeError('Engine exited during calibration')
            result.append(line)
            if line.startswith(prefix):
                return result

    def search(self, fen, nodes):
        self.send(f'ucinewgame\nposition fen {fen} 0 1\nisready')
        self.until('readyok')
        start = time.monotonic()
        self.send(f'go nodes {nodes}')
        output = self.until('bestmove')
        elapsed = time.monotonic() - start
        if any('score mate' in line for line in output):
            raise RuntimeError('Mate encountered in calibration; choose another opening seed')
        return elapsed

    def close(self):
        try:
            self.send('quit')
            self.process.wait(timeout=3)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()


def trial(run, positions, concurrency, seconds, nodes):
    barrier = threading.Barrier(concurrency)
    def worker(index):
        engines, latencies, cycles = [], [[], []], 0
        try:
            for name in ('new', 'old'):
                engines.append(Engine(run / name))
            for engine in engines:
                engine.search(positions[index % len(positions)], nodes)
            barrier.wait(timeout=60)
            start = time.monotonic()
            while time.monotonic() - start < seconds:
                fen = positions[(cycles + index) % len(positions)]
                for side, engine in enumerate(engines):
                    latencies[side].append(engine.search(fen, nodes))
                cycles += 1
            return dict(cycles=cycles, start=start, end=time.monotonic(), latencies=latencies)
        finally:
            for engine in engines:
                engine.close()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        workers = list(pool.map(worker, range(concurrency)))
    elapsed = max(w['end'] for w in workers) - min(w['start'] for w in workers)
    cycles = sum(w['cycles'] for w in workers)
    result = dict(concurrency=concurrency, elapsed_seconds=elapsed,
                  search_cycles=cycles, search_cycles_per_second=cycles/elapsed)
    for side, name in enumerate(('new', 'old')):
        values = sorted(v for w in workers for v in w['latencies'][side])
        result[name + '_median_ms'] = statistics.median(values) * 1000
        result[name + '_p95_ms'] = values[int(.95 * (len(values)-1))] * 1000
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('new', type=Path)
    ap.add_argument('old', type=Path)
    ap.add_argument('--new-net', type=Path, required=True)
    ap.add_argument('--old-net', type=Path, required=True)
    ap.add_argument('--book', type=Path, default=DEFAULT_BOOK)
    ap.add_argument('--concurrencies', default='4,6,8,10')
    ap.add_argument('--seconds', type=float, default=25)
    ap.add_argument('--repeats', type=int, default=3)
    ap.add_argument('--nodes', type=int, default=500000)
    ap.add_argument('--seed', type=int, default=20260907)
    ap.add_argument('--save-default', action='store_true')
    a = ap.parse_args()
    choices = sorted(set(int(c) for c in a.concurrencies.split(',')))
    if min(choices) < 1 or a.seconds <= 0 or a.repeats < 1 or a.nodes < 1:
        ap.error('all workload limits must be positive')
    run = ROOT / 'logs/calibration' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run.mkdir(parents=True)
    report = dict(settings={k: str(v) if isinstance(v, Path) else v for k, v in vars(a).items()},
                  selection_rule='smallest concurrency within 5% of highest median throughput',
                  metric='cycles/second; each cycle searches the same root with both engines at the requested node budget',
                  background_processes=subprocess.check_output(['ps', '-Ao', 'pid,pcpu,comm'], text=True),
                  trials=[])
    for name in ('new', 'old'):
        report[name] = snapshot_engine(getattr(a, name), getattr(a, name + '_net'), run / name)
    positions = read_book(a.book)
    random.Random(a.seed).shuffle(positions)
    positions = positions[:256]
    for repeat in range(a.repeats):
        order = choices[:]
        random.Random(a.seed + repeat).shuffle(order)
        for concurrency in order:
            print(f'Trial {repeat+1}/{a.repeats}, concurrency {concurrency}', flush=True)
            result = trial(run, positions, concurrency, a.seconds, a.nodes)
            result['repeat'] = repeat + 1
            report['trials'].append(result)
            dump(run / 'results.json', report)
            print(json.dumps(result), flush=True)
    medians = {c: statistics.median(t['search_cycles_per_second'] for t in report['trials']
                                   if t['concurrency'] == c) for c in choices}
    selected = min(c for c in choices if medians[c] >= .95 * max(medians.values()))
    report['median_cycles_per_second'] = medians
    report['recommended_concurrency'] = selected
    dump(run / 'results.json', report)
    if a.save_default:
        dump(ROOT / 'target/testing/concurrency.json', dict(
            recommended_concurrency=selected, report=str(run / 'results.json'),
            selection_rule=report['selection_rule'], median_cycles_per_second=medians))
    print(f'Recommended concurrency: {selected}; report: {run}/results.json')


if __name__ == '__main__':
    main()
