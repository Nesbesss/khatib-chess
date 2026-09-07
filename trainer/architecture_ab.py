"""Bounded CPU architecture experiment. See docs/nnue-experiment.md.

prepare: stream the entire clean file into a compact, disk-backed dataset.
train: one arm, preserving optimizer/scheduler/RNG at each epoch boundary.
No deployed paths are written, and no bot process is managed here.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import time

import numpy as np
import torch

from parity import check, decode, probes, sha256
from train import NNUE, nnue_loss, quantize

# 140 bytes/position (~4.82 GB for 34.4M), plus uint32 train/val indices.
# No Python object per row, copied worker dataset, or dense feature matrix.
ROW = np.dtype([('w', '<u2', (32,)), ('b', '<u2', (32,)), ('n', 'u1'),
                ('stm', 'u1'), ('ob', 'u1'), ('val', 'u1'), ('cp', '<f4'), ('wdl', '<f4')])
CONFIGS = {'direct': (1536, 0), 'l2': (2048, 16)}
SEED = 20260907


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, indent=2)+'\n')
    tmp.replace(path)


def prepare(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = source.stat()
    digest, rng = hashlib.sha256(), random.Random(SEED)
    sample, counts, n, used = [], {'train': 0, 'val': 0}, 0, 0
    cp_sum, side_count = [0., 0.], [0, 0]
    block = np.zeros(65536, dtype=ROW)
    start = time.monotonic()
    with source.open('rb') as src, (output/'positions.bin').open('wb') as dst, \
            (output/'train.bin').open('wb') as tr, (output/'val.bin').open('wb') as va:
        def flush():
            rows = block[:used]
            rows.tofile(dst)
            ids = np.arange(n-used, n, dtype='<u4')
            for label, stream, mask in [('train', tr, rows['val'] == 0),
                                        ('val', va, rows['val'] == 1)]:
                ids[mask].tofile(stream)
                counts[label] += int(mask.sum())

        for lineno, raw in enumerate(src, 1):
            digest.update(raw)
            if not raw.strip():
                continue
            parts = raw.decode().strip().split('|')
            if len(parts) not in (2, 3):
                raise ValueError(f'Row {lineno}: expected FEN | cp [| -1]')
            fen, cp = parts[0].strip(), int(parts[1])
            if abs(cp) >= 1600 or (len(parts) == 3 and float(parts[2]) != -1):
                raise ValueError(f'Row {lineno}: experiment requires |cp| < 1600, no outcomes')
            features, stm, ob, _ = decode(fen)
            key = ' '.join(fen.split()[:4]).encode()
            # Same position (ignoring counters) stays on one side of the split.
            val = int.from_bytes(hashlib.blake2b(key, digest_size=8,
                                                person=b'arch-ab-v1').digest(), 'little') % 50 == 0
            row = block[used]
            row['w'].fill(0); row['b'].fill(0)
            for label, f in zip(('w', 'b'), features):
                row[label][:len(f)] = f
            row['n'], row['stm'], row['ob'], row['val'] = len(features[0]), stm, ob, val
            row['cp'], row['wdl'] = cp, -1
            cp_sum[stm] += cp; side_count[stm] += 1
            n += 1; used += 1
            if n >= 2**32:
                raise ValueError('Dataset exceeds uint32 index capacity')
            j = n-1 if n <= 2048 else rng.randrange(n)
            if j < 2048:
                if n <= 2048: sample.append(fen)
                else: sample[j] = fen
            if used == len(block):
                flush(); used = 0
            if n % 1_000_000 == 0:
                print(f'Prepared {n:,} rows in {time.monotonic()-start:.0f}s', flush=True)
        if used: flush()
    if min(counts.values()) == 0:
        raise ValueError('Need nonempty train and validation sets')
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Source changed during preparation')
    write_json(output/'positions.json', {'positions': probes()+sample})
    manifest = dict(format=1, row_bytes=ROW.itemsize, rows=n, counts=counts, seed=SEED,
                    source=str(source), source_bytes=before.st_size,
                    source_sha256=digest.hexdigest(),
                    cp_mean_by_stm=[cp_sum[s]/max(side_count[s], 1) for s in (0, 1)],
                    n_by_stm=side_count,
                    split='blake2b(first 4 FEN fields, person=arch-ab-v1) % 50 == 0',
                    files={p.name: sha256(p) for p in output.iterdir() if p.is_file()})
    write_json(output/'manifest.json', manifest)  # completion marker written last
    print(f'Prepared {n:,}: {counts}; cache {output}', flush=True)


def batches(rows, indices, batch_size, epoch=None):
    # A dedicated generator makes row order independent of architecture/init.
    order = (torch.randperm(len(indices), generator=torch.Generator().manual_seed(SEED+epoch))
             .numpy() if epoch is not None else None)
    for start in range(0, len(indices), batch_size):
        ids = indices[start:start+batch_size] if order is None else indices[order[start:start+batch_size]]
        r = rows[ids]
        mask = np.arange(32)[None, :] < r['n'][:, None]
        offsets = np.concatenate(([0], np.cumsum(r['n'][:-1], dtype=np.int64)))
        def features(label):
            return (torch.from_numpy(r[label][mask].astype(np.int64)), torch.from_numpy(offsets))
        yield (features('w'), features('b'), torch.from_numpy(r['stm'].astype(np.int64)),
               torch.from_numpy(r['cp'].copy()), torch.from_numpy(r['wdl'].copy()),
               torch.from_numpy(r['ob'].astype(np.int64)))


def train(a):
    cache, output = Path(a.cache).resolve(), Path(a.out).resolve()
    if output.exists() and not a.resume:
        raise ValueError('Arm directory already exists; use --resume for an interrupted arm')
    if a.resume and not (output/'state.pt').is_file():
        raise ValueError('No saved epoch state to resume')
    output.mkdir(parents=True, exist_ok=True)
    meta = json.loads((cache/'manifest.json').read_text())
    if meta['format'] != 1 or meta['row_bytes'] != ROW.itemsize:
        raise ValueError('Unsupported cache format')
    for name, expected in meta['files'].items():
        if sha256(cache/name) != expected:
            raise ValueError(f'Cache modified: {name}')
    rows = np.memmap(cache/'positions.bin', mode='r', dtype=ROW)
    tr = np.memmap(cache/'train.bin', mode='r', dtype='<u4')
    va = np.memmap(cache/'val.bin', mode='r', dtype='<u4')
    config = dict(arm=a.arm, hidden=CONFIGS[a.arm][0], l2=CONFIGS[a.arm][1],
                  seed=SEED, batch=a.batch, lr=a.lr, schedule_epochs=a.schedule_epochs,
                  threads=a.threads, device='cpu', workers=0, lam=1.0,
                  cache_manifest_sha256=sha256(cache/'manifest.json'),
                  engine_sha256=sha256(a.engine),
                  sources={n: sha256(Path(__file__).parent/n)
                           for n in ('architecture_ab.py', 'train.py', 'parity.py')})
    if a.resume and json.loads((output/'config.json').read_text()) != config:
        raise ValueError('Resume configuration/source/engine/cache differs from saved run')
    write_json(output/'config.json', config)
    torch.set_num_threads(a.threads)
    torch.manual_seed(SEED)
    model = NNUE(*CONFIGS[a.arm])  # CPU only, intentionally no auto-device choice
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-8)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=a.lr, total_steps=a.schedule_epochs*math.ceil(len(tr)/a.batch))
    start_ep = 0
    if a.resume:
        state = torch.load(output/'state.pt', map_location='cpu', weights_only=True)
        model.load_state_dict(state['model']); opt.load_state_dict(state['opt'])
        sched.load_state_dict(state['sched']); torch.set_rng_state(state['rng'])
        start_ep = state['epoch']
        # A failed gate must not be skipped by resuming to the next epoch.
        report = json.loads((output/f'epoch{start_ep}.parity.json').read_text())
        if report['status'] != 'pass':
            raise ValueError('Last epoch did not pass parity; investigate before continuing')
    fens = json.loads((cache/'positions.json').read_text())['positions']
    for ep in range(start_ep, a.stop_after):
        t0, total, count = time.monotonic(), 0., 0
        model.train()
        for W, B, stm, cp, wd, ob in batches(rows, tr, a.batch, ep):
            loss = nnue_loss(model(W, B, stm, ob), cp, wd, 1.0)
            if not torch.isfinite(loss):
                raise ValueError('Nonfinite training loss')
            opt.zero_grad(set_to_none=True)
            loss.backward(); opt.step(); sched.step(); model.clip_weights()
            total += loss.item()*len(stm); count += len(stm)
        model.eval()
        val_total, val_count = 0., 0
        with torch.no_grad():
            for W, B, stm, cp, wd, ob in batches(rows, va, a.batch):
                val_total += nnue_loss(model(W, B, stm, ob), cp, wd, 1.0).item()*len(stm)
                val_count += len(stm)
        net = output/f'epoch{ep+1}.nnue'
        quantize(model, net)
        torch.save(model.state_dict(), str(net)+'.pt')
        metrics = dict(epoch=ep+1, train=total/count, val=val_total/val_count,
                       seconds=time.monotonic()-t0, lr=sched.get_last_lr())
        write_json(output/f'epoch{ep+1}.json', metrics)
        print(json.dumps(metrics), flush=True)
        report = check(a.engine, net, *CONFIGS[a.arm], fens,
                       output/f'epoch{ep+1}.parity.json', str(net)+'.pt', audit=True)
        # check() uses two threads for float reference; restore training budget.
        torch.set_num_threads(a.threads)
        if report['status'] != 'pass':
            raise ValueError('Parity gate failed. Stop training and matches; inspect report.')
        torch.save(dict(model=model.state_dict(), opt=opt.state_dict(), sched=sched.state_dict(),
                        rng=torch.get_rng_state(), epoch=ep+1), output/'state.pt.tmp')
        (output/'state.pt.tmp').replace(output/'state.pt')
    print(f'Arm complete through epoch {a.stop_after}; no network promoted.', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--data', required=True)
    p.add_argument('--out', required=True)
    p = sub.add_parser('train')
    p.add_argument('--cache', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--arm', choices=CONFIGS, required=True)
    p.add_argument('--engine', required=True)
    p.add_argument('--schedule-epochs', type=int, default=20)
    p.add_argument('--stop-after', type=int, default=6)
    p.add_argument('--batch', type=int, default=8192)
    p.add_argument('--threads', type=int, default=6)
    p.add_argument('--lr', type=float, default=1e-3)
    p.add_argument('--resume', action='store_true')
    a = ap.parse_args()
    if a.command == 'prepare':
        prepare(a.data, a.out)
    else:
        if not (1 <= a.stop_after <= a.schedule_epochs and min(a.threads, a.batch) > 0
                and math.isfinite(a.lr) and a.lr > 0):
            ap.error('Require positive limits and 1 <= stop-after <= schedule-epochs')
        train(a)


if __name__ == '__main__':
    main()
