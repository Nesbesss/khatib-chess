"""Compare a saved float checkpoint, exported integers, and an ACTUAL UCI binary.

No search is involved. The binary must implement Khatib's `eval` command.
Omitting --float checks only integer/Rust parity; it cannot clear the float gate.
"""
import argparse
import hashlib
import json
import os
import pickle
from pathlib import Path
import random
import re
import subprocess

import numpy as np

QA, QB, SCALE, INPUT, BUCKETS = 255, 64, 400, 768, 8


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def decode(fen):
    """Independent of the trainer's feature encoder (including bucket logic)."""
    fields = fen.split()
    if len(fields) != 6 or fields[1] not in ('w', 'b'):
        raise ValueError(f'Expected full FEN: {fen}')
    rows = fields[0].split('/')
    pieces = []
    if len(rows) != 8:
        raise ValueError(f'Bad ranks: {fen}')
    for rank, row in enumerate(rows):
        file = 0
        for ch in row:
            if ch in '12345678':
                file += int(ch)
            else:
                kind = 'pnbrqk'.index(ch.lower())
                if file >= 8:
                    raise ValueError(f'Bad file: {fen}')
                pieces.append((0 if ch.isupper() else 1, kind, (7-rank)*8+file))
                file += 1
        if file != 8:
            raise ValueError(f'Bad rank width: {fen}')
    if len(pieces) > 32:
        raise ValueError(f'More than 32 pieces: {fen}')
    features, kings = [], []
    for perspective in (0, 1):
        king = [sq for c, p, sq in pieces if c == perspective and p == 5]
        if len(king) != 1:
            raise ValueError(f'Expected one king per color: {fen}')
        k = king[0] ^ (56 if perspective else 0)
        bucket = (k % 8) // 2 + (4 if k >= 32 else 0)
        kings.append(bucket)
        features.append([bucket*768 + (c != perspective)*384 + p*64
                         + (sq ^ (56 if perspective else 0)) for c, p, sq in pieces])
    return features, int(fields[1] == 'b'), min(7, max(0, (len(pieces)-2)//4)), kings


def trunc_div(x, d):
    """Rust signed division truncates toward zero; Python // floors negatives."""
    x = np.asarray(x, dtype=np.int64)
    return np.where(x < 0, -((-x)//d), x//d)


class IntegerNet:
    def __init__(self, path, hidden, l2):
        self.hidden, self.l2 = hidden, l2
        shapes = [(INPUT*BUCKETS, hidden), (hidden,)]
        if l2:
            shapes += [(BUCKETS, l2, hidden*2), (BUCKETS, l2)]
        shapes += [(BUCKETS, l2 or hidden*2), (BUCKETS,)]
        raw = np.fromfile(path, dtype='<i2')
        if Path(path).stat().st_size != sum(int(np.prod(s)) for s in shapes)*2:
            raise ValueError('Network size does not match --hidden/--l2')
        arrays, offset = [], 0
        for shape in shapes:
            n = int(np.prod(shape))
            arrays.append(raw[offset:offset+n].reshape(shape))
            offset += n
        self.ftw, self.ftb = arrays[:2]
        self.ow, self.ob = arrays[-2:]
        if l2:
            self.lw, self.lb = arrays[2:4]

    def evaluate(self, fen):
        features, stm, bucket, kings = decode(fen)
        acc = np.array([self.ftw[f].sum(axis=0, dtype=np.int64) + self.ftb
                        for f in features])
        overflow = int(((acc < -32768) | (acc > 32767)).sum())
        # Engine updates wrap i16; report overflow separately, never hide it.
        acc = (acc + 32768) % 65536 - 32768
        x = np.clip(np.concatenate([acc[stm], acc[1-stm]]), 0, QA)
        if self.l2:
            sums = self.lw[bucket].astype(np.int64) @ x
            overflow += int(((sums < -(2**31)) | (sums >= 2**31)).sum())
            x = np.clip(trunc_div(sums, QB) + self.lb[bucket], 0, QA)
        total = int(self.ow[bucket].astype(np.int64) @ x)
        overflow += int(not -(2**31) <= total < 2**31)
        cp = int(trunc_div((total + int(self.ob[bucket])) * SCALE, QA*QB))
        return cp, overflow, bucket, stm, kings


def fen_from_pieces(pieces, stm='w'):
    rows = []
    for rank in range(7, -1, -1):
        row, empty = '', 0
        for file in range(8):
            p = pieces.get(rank*8+file)
            if p:
                row += (str(empty) if empty else '') + p
                empty = 0
            else:
                empty += 1
        rows.append(row + (str(empty) if empty else ''))
    return '/'.join(rows) + f' {stm} - - 0 1'


def probes():
    """Layout probes cover all king/output buckets and both sides to move.

    These include artificial positions to exercise the tensor layout, alongside
    legal castling, en-passant and promotion roots. They are not strength data.
    """
    result = [
        'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        'r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1',
        'r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1',
        '4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1',
        '4k3/8/8/8/3Pp3/8/8/4K3 b - d3 0 1',
        '7k/P7/8/8/8/8/7p/K7 w - - 0 1',
        '7k/P7/8/8/8/8/7p/K7 b - - 0 1',
    ]
    for bucket in range(8):
        king = (bucket % 4)*2 + (32 if bucket >= 4 else 0)
        for black_view in (False, True):
            pieces = {king: 'K', 63: 'k', 9: 'P', 55: 'p'}
            if black_view:
                pieces = {sq ^ 56: p.swapcase() for sq, p in pieces.items()}
            result.append(fen_from_pieces(pieces))
    for n in range(2, 33):
        pieces = {4: 'K', 60: 'k'}
        slots = [s for s in range(64) if s not in pieces]
        for i in range(n-2):
            pieces[slots[i]] = 'PNBRQpnbrq'[i % 10]
        result.append(fen_from_pieces(pieces))
    return list(dict.fromkeys(f.rsplit(' ', 5)[0] + f' {side} ' + ' '.join(f.split()[2:])
                             for f in result for side in ('w', 'b')))


def sample_file(path, count, seed):
    """Uniform reservoir over the ENTIRE file, never a head sample."""
    rng, sample, seen = random.Random(seed), [], 0
    with open(path) as f:
        for line in f:
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            fen = line.split('|')[0].strip()
            fields = fen.split()
            if len(fields) == 4:
                fen += ' 0 1'  # EPD without operations
            seen += 1
            j = seen-1 if seen <= count else rng.randrange(seen)
            if j < count:
                if seen <= count:
                    sample.append(fen)
                else:
                    sample[j] = fen
    if not sample:
        raise ValueError('No positions sampled')
    return sample, seen


def engine_scores(engine, net, fens, log):
    commands = ['uci', 'setoption name Threads value 1',
                'setoption name Hash value 16', 'setoption name OwnBook value false', 'info']
    for fen in fens:
        commands.extend([f'position fen {fen}', 'eval'])
    commands += ['isready', 'quit']
    env = dict(os.environ)
    env.pop('MOVE_CAP_MS', None)
    p = subprocess.run([str(Path(engine).resolve()), '--net', str(Path(net).resolve())],
                       input='\n'.join(commands)+'\n', text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=180, cwd=Path(net).resolve().parent,
                       env=env)
    Path(log).write_text(p.stdout)
    bad = ('network load failed', 'network: none', 'bad fen', 'illegal move')
    if (p.returncode or any(s in p.stdout.lower() for s in bad)
            or 'loaded network' not in p.stdout.lower() or 'readyok' not in p.stdout):
        raise ValueError(f'Engine preflight failed; see {log}')
    scores = [int(s) for s in p.stdout.splitlines() if re.fullmatch(r'-?\d+', s.strip())]
    if len(scores) != len(fens):
        raise ValueError(f'Expected {len(fens)} eval replies, received {len(scores)}; see {log}')
    return np.asarray(scores)


def float_scores(path, net_path, hidden, l2, fens, include_master=False):
    # Constructing reference models initializes random parameters before
    # loading the checkpoint. Diagnostics must not advance a trainer's RNG.
    import torch
    with torch.random.fork_rng(devices=[]):
        return _float_scores(path, net_path, hidden, l2, fens, include_master)


def _float_scores(path, net_path, hidden, l2, fens, include_master=False):
    import torch
    from train import NNUE, QuantizedNNUE, collate, fen_to_features, output_bucket, quantized_arrays
    torch.set_num_threads(2)
    state = torch.load(path, map_location='cpu', weights_only=True)
    mode = state.get('inference', 'standard')
    if mode not in ('standard', 'quantized-forward-v1'):
        raise ValueError(f'Unknown checkpoint inference mode: {mode}')
    model = (QuantizedNNUE if mode == 'quantized-forward-v1' else NNUE)(hidden, l2)
    model.load_state_dict(state['model'] if 'model' in state else state)
    model.eval()
    exported = b''.join(a.tobytes() for a in quantized_arrays(model))
    if exported != Path(net_path).read_bytes():
        raise ValueError('Float checkpoint does not reproduce the supplied net byte-for-byte. '
                         'Use the matching epoch state; best .pt is not necessarily that epoch.')
    clipped = {}
    for name, t in model.named_parameters():
        scale = QA if name in ('ft.weight', 'ft_bias', 'l2.bias') else QB
        if name == 'out.bias':
            scale = QA*QB
        q = torch.round(t.detach()*scale)
        clipped[name] = int(((q < -32768) | (q > 32767)).sum())
    master = None
    if mode == 'quantized-forward-v1' and include_master:
        master = NNUE(hidden, l2)
        master.load_state_dict(model.state_dict())
        master.eval()
    scores, master_scores = [], []
    with torch.no_grad():
        for start in range(0, len(fens), 128):
            batch = []
            for fen in fens[start:start+128]:
                w, b, stm = fen_to_features(fen)
                batch.append((np.array(w), np.array(b), stm, 0, -1, output_bucket(fen)))
            W, B, stm, _, _, ob = collate(batch)
            scores.extend((model(W, B, stm, ob)*SCALE).tolist())
            if master is not None:
                master_scores.extend((master(W, B, stm, ob)*SCALE).tolist())
    if include_master:
        return np.asarray(scores), clipped, dict(mode=mode, master_scores=master_scores)
    return np.asarray(scores), clipped


def find_float(net_path, hidden, l2, roots):
    """Find a MATCHING saved float/state file; never infer one from integers."""
    import torch
    from train import NNUE, quantized_arrays
    torch.set_num_threads(2)
    expected = Path(net_path).read_bytes()
    paths = {p for root in roots for p in Path(root).rglob('*')
             if p.is_file() and p.suffix in ('.pt', '.pth', '.state', '.ckpt')}
    for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            state = torch.load(path, map_location='cpu', weights_only=True)
            state = state.get('model', state)
            if tuple(state['ft.weight'].shape) != (INPUT*BUCKETS, hidden):
                continue
            model = NNUE(hidden, l2)
            model.load_state_dict(state)
            exported = b''.join(arr.tobytes() for arr in quantized_arrays(model))
            if exported == expected:
                print(f'Matching float checkpoint: {path}', flush=True)
                return str(path)
        except (KeyError, ValueError, RuntimeError, OSError, TypeError,
                AttributeError, EOFError, pickle.UnpicklingError) as e:
            print(f'Skipping {path}: {type(e).__name__}', flush=True)
    raise ValueError('No saved float checkpoint reproduces this network. Integer-only parity '
                     'is still available by omitting --find-float; it cannot clear the full gate. '
                     'Supply a matching --float path if it is stored elsewhere.')


def error_stats(a, b):
    d = np.abs(np.asarray(a)-np.asarray(b))
    return dict(n=len(d), mae=float(d.mean()), p95=float(np.percentile(d, 95)),
                max=float(d.max()), bias=float((np.asarray(b)-a).mean()))


def check(engine, net, hidden, l2, fens, out, float_path=None, audit=False):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    hashes = {'engine': sha256(engine), 'net': sha256(net)}
    if float_path:
        hashes['float'] = sha256(float_path)
    qnet = IntegerNet(net, hidden, l2)
    refs = [qnet.evaluate(fen) for fen in fens]
    q = np.asarray([r[0] for r in refs])
    rust = engine_scores(engine, net, fens, str(out)+'.engine.log')
    mismatch = np.flatnonzero(q != rust)
    report = dict(hidden=hidden, l2=l2, paths={'engine': str(Path(engine).resolve()),
                  'net': str(Path(net).resolve()), 'float': str(Path(float_path).resolve()) if float_path else None},
                  sha256=hashes, positions=fens, integer_rust=error_stats(q, rust),
                  mismatches=int(len(mismatch)), overflow_values=sum(r[1] for r in refs),
                  incremental='not checked by legacy UCI eval; run Rust accumulator tests',
                  worst_integer=[dict(fen=fens[i], integer=int(q[i]), rust=int(rust[i]))
                                 for i in mismatch[:20]])
    ok = not len(mismatch) and not report['overflow_values']
    if audit:
        p = subprocess.run([str(Path(engine).resolve()), 'nnue-audit', '--net',
                            str(Path(net).resolve())], text=True, input='',
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        Path(str(out)+'.accumulator.log').write_text(p.stdout)
        passed = p.returncode == 0 and 'NNUE accumulator audit passed:' in p.stdout
        report['incremental'] = 'pass' if passed else 'fail'
        ok &= passed
    if float_path:
        floats, clipped, float_meta = float_scores(float_path, net, hidden, l2, fens, include_master=True)
        stats = error_stats(floats, q)
        report.update(float_integer=stats, clipped_parameters=clipped,
                      float_std_cp=float(floats.std()), integer_std_cp=float(q.std()),
                      sign_flips_outside_100cp=int(((np.abs(floats) >= 100) & (floats*q < 0)).sum()),
                      thresholds=dict(mae_cp=10, p95_cp=25, max_cp=100),
                      by_output_bucket={str(b): error_stats(floats[ix], q[ix])
                                        for b in range(8)
                                        if (ix := [i for i, r in enumerate(refs) if r[2] == b])},
                      by_side={str(s): error_stats(floats[ix], q[ix]) for s in (0, 1)
                               if (ix := [i for i, r in enumerate(refs) if r[3] == s])})
        report['worst_float'] = [dict(fen=fens[i], float_cp=float(floats[i]), integer=int(q[i]))
                                 for i in np.argsort(np.abs(floats-q))[-20:][::-1]]
        report['float_inference_mode'] = float_meta['mode']
        if float_meta['master_scores']:
            # Optimizer storage is not the QAT model's forward function. Keep
            # this diagnostic visible; never mislabel it as historical parity.
            report['master_float_integer'] = error_stats(float_meta['master_scores'], q)
        ok &= (stats['mae'] <= 10 and stats['p95'] <= 25 and stats['max'] <= 100
               and not sum(clipped.values()) and not report['sign_flips_outside_100cp'])
    for key, path in [('engine', engine), ('net', net), ('float', float_path)]:
        if path and sha256(path) != hashes[key]:
            raise ValueError(f'{key} changed during parity check')
    report['status'] = ('pass' if float_path else 'integer_only') if ok else 'fail'
    out.write_text(json.dumps(report, indent=2)+'\n')
    print(f"Parity {report['status']}: {len(fens)} positions; integer mismatches={len(mismatch)}; "
          f"overflow={report['overflow_values']}; {out}", flush=True)
    if float_path:
        print(f'Float/integer error cp: {report["float_integer"]}', flush=True)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--engine', required=True)
    ap.add_argument('--net', required=True)
    checkpoints = ap.add_mutually_exclusive_group()
    checkpoints.add_argument('--float', dest='float_path')
    checkpoints.add_argument('--find-float', action='append', metavar='DIR',
                             help='search saved checkpoints in DIR for a byte-identical export')
    ap.add_argument('--hidden', type=int, required=True)
    ap.add_argument('--l2', type=int, choices=(0, 16), required=True)
    ap.add_argument('--data', help='sample entire FEN/label or plain EPD file')
    ap.add_argument('--positions', help='reuse positions from a previous report JSON')
    ap.add_argument('--samples', type=int, default=2048)
    ap.add_argument('--seed', type=int, default=20260907)
    ap.add_argument('--out', required=True)
    ap.add_argument('--audit', action='store_true', help='also use NEW binary nnue-audit command')
    a = ap.parse_args()
    if a.hidden <= 0 or a.samples < 1:
        ap.error('positive hidden and samples required')
    if a.find_float:
        a.float_path = find_float(a.net, a.hidden, a.l2, a.find_float)
    fens = probes()
    if a.positions:
        fens = json.loads(Path(a.positions).read_text())['positions']
    elif a.data:
        sampled, seen = sample_file(a.data, a.samples, a.seed)
        print(f'Uniform reservoir: {len(sampled)} of {seen:,} rows', flush=True)
        fens += sampled
    report = check(a.engine, a.net, a.hidden, a.l2, fens, a.out, a.float_path, a.audit)
    return 1 if report['status'] == 'fail' else 0


if __name__ == '__main__':
    raise SystemExit(main())
