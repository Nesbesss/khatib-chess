"""Cross-language cache and exact epoch-resume regression tests."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np
import torch

from architecture_ab import ROW
from parity import check, probes
from replay_v10 import initialize, prepare, train, verify_samples
from train import NNUE, QuantizedNNUE, collate, fen_to_features, output_bucket, quantize


class ReplayTests(unittest.TestCase):
    def test_qat_actual_rust_and_gradient(self):
        torch.set_num_threads(2)
        torch.manual_seed(1907)
        if 'DIRECT_ENGINE' not in os.environ or 'V7_NET' not in os.environ:
            self.skipTest('Set DIRECT_ENGINE and V7_NET for full QAT integration')
        with tempfile.TemporaryDirectory() as tmp:
            m = initialize(os.environ['V7_NET'])
            batch = []
            for fen in probes():
                w, b, stm = fen_to_features(fen)
                batch.append((np.array(w), np.array(b), stm, 0., .5, output_bucket(fen)))
            W, B, stm, cp, wd, ob = collate(batch)
            # Put parameters off the integer grid, then exercise a real update.
            with torch.no_grad():
                for p in m.parameters():
                    p.add_(torch.rand_like(p) * .002)
            opt = torch.optim.AdamW(m.parameters(), lr=5e-5)
            loss = m(W, B, stm, ob).square().mean()
            loss.backward()
            self.assertGreater(m.ft.weight.grad.abs().sum().item(), 0)
            self.assertGreater(m.out.weight.grad.abs().sum().item(), 0)
            opt.step()
            m.clip_weights()
            path = Path(tmp) / 'qat.nnue'
            quantize(m, path)
            torch.save(dict(model=m.state_dict(), inference='quantized-forward-v1'), str(path)+'.pt')
            rng_before = torch.get_rng_state().clone()
            report = check(os.environ['DIRECT_ENGINE'], path, 1536, 0, probes(), Path(tmp)/'parity.json', str(path)+'.pt')
            self.assertTrue(torch.equal(rng_before, torch.get_rng_state()))
            self.assertEqual(report['status'], 'pass')
            self.assertLess(report['float_integer']['max'], 1.01)
            self.assertIn('master_float_integer', report)

    def test_grid_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'net.nnue'
            torch.manual_seed(7)
            quantize(NNUE(16, 0), path)
            m = initialize(path, 16)
            quantize(m, Path(tmp) / 'copy.nnue')
            self.assertEqual(path.read_bytes(), (Path(tmp) / 'copy.nnue').read_bytes())

    def test_packer_and_resume(self):
        torch.set_num_threads(2)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original, fresh = root / 'original.txt', root / 'fresh.txt'
            fens = probes()
            original.write_text(''.join(f'{fen} | {i-50} | {(i%3)/2}\n' for i, fen in enumerate(fens)) +
                                f'{fens[0]} | 1000 | 0.5\n')
            fresh.write_text(''.join(f'{fen.rsplit(" ", 2)[0]} 19 123 | {i-60} | 0.5\n'
                                    for i, fen in enumerate(fens)))
            prepare(SimpleNamespace(run=root, original=original, fresh=str(fresh), packer=os.environ['PACKER']))
            verify_samples(root / 'cache')
            rows = np.memmap(root / 'cache/positions.bin', mode='r', dtype=ROW)
            self.assertEqual(len(rows), len(fens) * 2)
            np.testing.assert_array_equal(rows['val'][:len(fens)], rows['val'][len(fens):])
            m = NNUE(16, 0)
            net = root / 'initial.nnue'
            quantize(m, net)
            engine = root / 'engine'
            engine.write_text('test placeholder')
            def passed(engine, net, hidden, l2, fens, out, *args, **kwargs):
                report = {'status': 'pass'}
                Path(out).write_text(json.dumps(report))
                return report
            def args(arm, stop=None, resume=False):
                return SimpleNamespace(run=root, arm=arm, resume=resume, epochs=2,
                                       batch=32, fresh_repeat=2, lr=5e-5, lam=.7,
                                       threads=2, net=net, engine=engine, stop_after=stop)
            with mock.patch('replay_v10.HIDDEN', 16), mock.patch('replay_v10.check', side_effect=passed):
                train(args('whole'))
                train(args('resumed', 1))
                train(args('resumed', resume=True))
            a = torch.load(root / 'whole/epoch2.state.pt', weights_only=True)
            b = torch.load(root / 'resumed/epoch2.state.pt', weights_only=True)
            self.assertEqual(a['sched'], b['sched'])
            self.assertTrue(torch.equal(a['rng'], b['rng']))
            for key in a['model']:
                self.assertTrue(torch.equal(a['model'][key], b['model'][key]), key)
            for key in a['opt']['state']:
                for name, value in a['opt']['state'][key].items():
                    self.assertTrue(torch.equal(value, b['opt']['state'][key][name]))
            self.assertEqual((root / 'whole/epoch2.nnue').read_bytes(), (root / 'resumed/epoch2.nnue').read_bytes())
            bad = root / 'no-wdl.txt'
            bad.write_text(f'{fens[0]} | 20\n')
            result = subprocess.run([os.environ['PACKER'], str(root/'bad-cache'), str(bad), str(fresh)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / 'bad-cache/complete.json').exists())


if __name__ == '__main__':
    unittest.main()
