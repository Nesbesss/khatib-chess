"""Static source diagnostic on fixed reservoir samples; not a strength verdict.

Run with the Fastchess testing venv (python-chess). No NumPy/Torch required.
"""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess

import chess


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def evaluate(engine, net, fens):
    commands = ['uci', 'setoption name Threads value 1', 'setoption name Hash value 16',
                'setoption name OwnBook value false']
    for fen in fens:
        commands += [f'position fen {fen}', 'eval']
    commands += ['isready', 'quit']
    env = dict(os.environ)
    env.pop('MOVE_CAP_MS', None)
    p = subprocess.run([str(engine), '--net', str(net)], input='\n'.join(commands)+'\n',
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                       timeout=180, cwd=net.parent)
    if p.returncode or 'loaded network' not in p.stdout or 'readyok' not in p.stdout:
        raise ValueError(p.stdout)
    values = [int(s) for s in p.stdout.splitlines() if re.fullmatch(r'-?\d+', s.strip())]
    if len(values) != len(fens):
        raise ValueError('Missing static evaluation replies')
    return values


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run', type=Path, required=True)
    ap.add_argument('--epoch', type=int, default=5)
    a = ap.parse_args()
    run = a.run.resolve()
    samples = []
    for line in (run / 'cache/sample.tsv').read_text().splitlines():
        idx, source, fen, cp, wdl = line.split('\t')
        board = chess.Board(fen)
        category = ('check' if board.is_check() else
                    'capture_available' if any(board.generate_legal_captures()) else 'no_capture_available')
        samples.append(dict(row=int(idx), source='original' if source == '0' else 'fresh',
                            fen=fen, cp=int(cp), wdl=float(wdl), category=category,
                            valid=board.is_valid(), side=board.turn, pieces=len(board.piece_map())))
    fens = [s['fen'] for s in samples]
    paths = dict(v7=run / 'v7.nnue', candidate=run / f'candidate/epoch{a.epoch}.nnue')
    scores = {name: evaluate(run / 'engine', path, fens) for name, path in paths.items()}
    groups = collections.defaultdict(list)
    for i, sample in enumerate(samples):
        for key in (sample['source'], sample['source'] + '/' + sample['category']):
            groups[key].append(i)
    result = {}
    for group, indices in sorted(groups.items()):
        result[group] = {}
        for name, values in scores.items():
            error = [values[i] - samples[i]['cp'] for i in indices]
            result[group][name] = dict(n=len(error), median_absolute_cp=statistics.median(map(abs, error)),
                                      mae_cp=statistics.mean(map(abs, error)), bias_cp=statistics.mean(error),
                                      rmse_cp=statistics.mean(x*x for x in error)**.5)
    report = dict(epoch=a.epoch, description='Static UCI eval, STM labels; whole-source reservoir, not holdout',
                  net_sha256={name: digest(path) for name, path in paths.items()},
                  engine_sha256=digest(run / 'engine'), groups=result,
                  invalid_fens=sum(not s['valid'] for s in samples),
                  positions=[dict(**s, v7_cp=scores['v7'][i], candidate_cp=scores['candidate'][i])
                             for i, s in enumerate(samples)])
    out = run / f'panel-epoch{a.epoch}.json'
    out.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
