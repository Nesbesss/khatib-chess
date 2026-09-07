"""V10: continue the deployed direct network on original + fresh self-play.

All output goes in a new experiment directory. No deployed file is changed.
The quantized initialization is a NEW float model on the integer weight grid;
it is explicitly not a recovered historical float checkpoint.
"""
import argparse
import csv
import glob
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time

import numpy as np
import torch

from architecture_ab import ROW, batches, write_json
from parity import IntegerNet, check, decode, probes, sha256
from train import QuantizedNNUE, nnue_loss, quantize, quantized_arrays

SEED = 20260907
HIDDEN = 1536


def initialize(net, hidden=HIDDEN):
    q = IntegerNet(net, hidden, 0)
    model = QuantizedNNUE(hidden, 0)
    with torch.no_grad():
        for parameter, array, scale in [(model.ft.weight, q.ftw, 255),
                                        (model.ft_bias, q.ftb, 255),
                                        (model.out.weight, q.ow, 64),
                                        (model.out.bias, q.ob, 255 * 64)]:
            parameter.copy_(torch.from_numpy(array.astype(np.float32)) / scale)
    if b''.join(x.tobytes() for x in quantized_arrays(model)) != Path(net).read_bytes():
        raise ValueError('Initialization must re-export byte-identically to deployed v7')
    return model


def verify_samples(cache):
    """Cross-language check of every sampled row, independent feature decoder."""
    rows = np.memmap(cache / 'positions.bin', mode='r', dtype=ROW)
    fens = probes()
    for line in (cache / 'sample.tsv').read_text().splitlines():
        idx, source, fen, cp, wdl = line.split('\t')
        row = rows[int(idx)]
        features, stm, ob, _ = decode(fen)
        for name, expected in zip(('w', 'b'), features):
            np.testing.assert_array_equal(row[name][:row['n']], expected)
        assert (int(row['n']), int(row['stm']), int(row['ob']), float(row['cp']), float(row['wdl'])) == (
            len(features[0]), stm, ob, float(cp), float(wdl))
        fens.append(fen)
    return fens


def prepare(a):
    root = Path(a.run).resolve()
    cache = root / 'cache'
    sources = [Path(a.original).resolve()] + sorted(Path(p).resolve() for p in glob.glob(a.fresh))
    if len(sources) < 2:
        raise ValueError('No fresh shards matched')
    metadata = {str(p): dict(bytes=p.stat().st_size, sha256=sha256(p)) for p in sources}
    subprocess.run([str(Path(a.packer).resolve()), str(cache)] + list(metadata), check=True)
    for p in sources:
        if metadata[str(p)] != dict(bytes=p.stat().st_size, sha256=sha256(p)):
            raise ValueError(f'Source changed: {p}')
    fens = verify_samples(cache)
    complete = json.loads((cache / 'complete.json').read_text())
    if (cache / 'positions.bin').stat().st_size != complete['rows'] * ROW.itemsize:
        raise ValueError('Cache row count mismatch')
    stats = list(csv.DictReader((cache / 'sources.tsv').open(), delimiter='\t'))
    summaries = {}
    for source in ('0', '1'):
        summaries[source] = {name: sum(int(row[name]) for row in stats if row['source'] == source)
                             for name in ('read', 'kept', 'tail_removed', 'train', 'val', 'wdl0', 'wdl05', 'wdl1')}
        if min(summaries[source]['train'], summaries[source]['val']) < 1:
            raise ValueError('Each source requires nonempty train and diagnostic sets')
    manifest = dict(format=1, rows=complete['rows'], row_bytes=ROW.itemsize, sources=metadata,
                    summary=summaries, selection='existing generator selection; labelled abs(cp) < 1000',
                    split='FNV1a64 + Murmur finalizer(first four FEN fields) % 50 == 0',
                    deduplication='none; repeated positions always share a split across all sources',
                    validation_limitation='original positions were available to v7; split is not game-disjoint',
                    files={p.name: sha256(p) for p in cache.iterdir() if p.is_file()})
    write_json(cache / 'positions.json', {'positions': fens})
    manifest['files']['positions.json'] = sha256(cache / 'positions.json')
    write_json(cache / 'manifest.json', manifest)
    print(json.dumps(summaries, indent=2), flush=True)


def lr_factor(step, steps, warmup=200):
    if step < warmup:
        return 0.1 + 0.9 * step / max(1, warmup)
    progress = min(1.0, (step - warmup) / max(1, steps - warmup))
    return 0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * progress))


def validate(model, rows, ids, batch, lam):
    loss_sum, ae, se, total = 0., 0., 0., 0
    with torch.no_grad():
        for W, B, stm, cp, wd, ob in batches(rows, ids, batch):
            pred = model(W, B, stm, ob)
            loss_sum += nnue_loss(pred, cp, wd, lam).item() * len(stm)
            err = pred * 400 - cp
            ae += err.abs().sum().item()
            se += err.square().sum().item()
            total += len(stm)
    return dict(n=total, loss=loss_sum/total, mae_cp=ae/total, rmse_cp=math.sqrt(se/total))


def train(a):
    root, cache = Path(a.run).resolve(), Path(a.run).resolve() / 'cache'
    output = root / a.arm
    output.mkdir(exist_ok=a.resume)
    meta = json.loads((cache / 'manifest.json').read_text())
    for name, expected in meta['files'].items():
        if sha256(cache / name) != expected:
            raise ValueError(f'Cache modified: {name}')
    rows = np.memmap(cache / 'positions.bin', mode='r', dtype=ROW)
    indices = {name: np.memmap(cache / f'{name}.bin', mode='r', dtype='<u4')
               for name in ('original_train', 'original_val', 'fresh_train', 'fresh_val')}
    tr = np.concatenate([indices['original_train']] + [indices['fresh_train']] * a.fresh_repeat)
    steps_per_epoch = math.ceil(len(tr) / a.batch)
    config = dict(seed=SEED, hidden=HIDDEN, l2=0, epochs=a.epochs, batch=a.batch,
                  max_lr=a.lr, final_lr=a.lr * 0.1, warmup_steps=min(200, steps_per_epoch),
                  fresh_repeat=a.fresh_repeat, rows_per_epoch=len(tr), lam=a.lam,
                  threads=a.threads, device='cpu', optimizer='AdamW', weight_decay=1e-8,
                  inference='quantized-forward-v1',
                  cache_sha256=sha256(cache / 'manifest.json'), net_sha256=sha256(a.net),
                  engine_sha256=sha256(a.engine),
                  code_sha256={n: sha256(Path(__file__).with_name(n)) for n in
                               ('replay_v10.py', 'architecture_ab.py', 'train.py', 'parity.py')})
    if a.resume and json.loads((output / 'config.json').read_text()) != config:
        raise ValueError('Resume configuration or provenance differs')
    write_json(output / 'config.json', config)
    torch.set_num_threads(a.threads)
    torch.manual_seed(SEED)
    model = initialize(a.net, HIDDEN)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-8)
    steps = a.epochs * steps_per_epoch
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: lr_factor(s, steps, config['warmup_steps']))
    fens = json.loads((cache / 'positions.json').read_text())['positions']
    start = 0
    if a.resume:
        state = torch.load(output / 'state.pt', map_location='cpu', weights_only=True)
        model.load_state_dict(state['model'])
        opt.load_state_dict(state['opt'])
        sched.load_state_dict(state['sched'])
        torch.set_rng_state(state['rng'])
        start = state['epoch']
        if json.loads((output / f'epoch{start}.parity.json').read_text())['status'] != 'pass':
            raise ValueError('Cannot resume past a failed parity gate')
    else:
        net = output / 'epoch0.nnue'
        quantize(model, net)
        torch.save(dict(model=model.state_dict(), inference='quantized-forward-v1'), str(net) + '.pt')
        report = check(a.engine, net, HIDDEN, 0, fens, output / 'epoch0.parity.json', str(net) + '.pt')
        if report['status'] != 'pass':
            raise ValueError('Initialization parity failed')
        torch.set_num_threads(a.threads)
        model.eval()
        baseline = {source: validate(model, rows, indices[source + '_val'], a.batch, a.lam)
                    for source in ('original', 'fresh')}
        write_json(output / 'epoch0.json', baseline)
        print('Initial diagnostics: ' + json.dumps(baseline), flush=True)
    print('Training configuration: ' + json.dumps(config), flush=True)
    for ep in range(start, min(a.epochs, a.stop_after or a.epochs)):
        model.train()
        t0, total, count = time.monotonic(), 0., 0
        for step, (W, B, stm, cp, wd, ob) in enumerate(batches(rows, tr, a.batch, ep), 1):
            loss = nnue_loss(model(W, B, stm, ob), cp, wd, a.lam)
            if not torch.isfinite(loss):
                raise ValueError('Nonfinite loss')
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            model.clip_weights()
            total += loss.item() * len(stm)
            count += len(stm)
            if step % 50 == 0 or step == steps_per_epoch:
                status = dict(epoch=ep+1, step=step, steps=steps_per_epoch, seen=count,
                              loss=total/count, lr=sched.get_last_lr()[0], seconds=time.monotonic()-t0)
                write_json(output / 'progress.json', status)
                print(json.dumps(status), flush=True)
        model.eval()
        metrics = dict(epoch=ep+1, train_loss=total/count, seconds=time.monotonic()-t0,
                       lr=sched.get_last_lr()[0], validation={
                           source: validate(model, rows, indices[source + '_val'], a.batch, a.lam)
                           for source in ('original', 'fresh')})
        net = output / f'epoch{ep+1}.nnue'
        quantize(model, net)
        torch.save(dict(model=model.state_dict(), inference='quantized-forward-v1'), str(net) + '.pt')
        write_json(output / f'epoch{ep+1}.json', metrics)
        print('Epoch complete: ' + json.dumps(metrics), flush=True)
        # Preserve full state for EACH epoch, including scheduler and RNG.
        state_path = output / f'epoch{ep+1}.state.pt'
        torch.save(dict(epoch=ep+1, model=model.state_dict(), opt=opt.state_dict(),
                        sched=sched.state_dict(), rng=torch.get_rng_state(),
                        inference='quantized-forward-v1'), str(state_path)+'.tmp')
        os.replace(str(state_path)+'.tmp', state_path)
        state_link = output / 'state.pt.tmp'
        state_link.symlink_to(state_path.name)
        os.replace(state_link, output / 'state.pt')
        report = check(a.engine, net, HIDDEN, 0, fens, output / f'epoch{ep+1}.parity.json', str(net)+'.pt')
        torch.set_num_threads(a.threads)
        if report['status'] != 'pass':
            raise ValueError('Epoch parity failed; state preserved, subsequent training/match stopped')
    print('Training finished; epoch 5 remains the preselected match candidate.', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--run', required=True)
    p.add_argument('--original', required=True)
    p.add_argument('--fresh', required=True)
    p.add_argument('--packer', required=True)
    p = sub.add_parser('train')
    p.add_argument('--run', required=True)
    p.add_argument('--net', required=True)
    p.add_argument('--engine', required=True)
    p.add_argument('--arm', default='candidate')
    p.add_argument('--epochs', type=int, default=5)
    p.add_argument('--batch', type=int, default=8192)
    p.add_argument('--threads', type=int, default=6)
    p.add_argument('--lr', type=float, default=5e-5)
    p.add_argument('--lambda', dest='lam', type=float, default=0.7)
    p.add_argument('--fresh-repeat', type=int, default=2)
    p.add_argument('--stop-after', type=int)
    p.add_argument('--resume', action='store_true')
    a = ap.parse_args()
    if a.command == 'prepare':
        prepare(a)
    else:
        if min(a.epochs, a.batch, a.threads) < 1 or a.fresh_repeat < 0 or not 0 <= a.lam <= 1 or a.lr <= 0:
            ap.error('invalid training settings')
        train(a)


if __name__ == '__main__':
    main()
