"""Integration regressions for export arithmetic, feature layout and resumption.

Set DIRECT_ENGINE and L2_ENGINE to separately built current binaries.
"""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np
import torch

from architecture_ab import batches, prepare, ROW, train
from parity import check, decode, IntegerNet, probes, trunc_div
from train import NNUE, quantize, fen_to_features, output_bucket


class ExperimentTests(unittest.TestCase):
    def test_signed_division_and_independent_features(self):
        np.testing.assert_array_equal(trunc_div(np.array([-65, -64, -63, 63, 64, 65]), 64),
                                      [-1, -1, 0, 0, 1, 1])
        coverage = [set(), set(), set()]
        for fen in probes():
            features, stm, ob, kings = decode(fen)
            w, b, side = fen_to_features(fen)
            self.assertEqual((features, stm, ob), ([w, b], side, output_bucket(fen)))
            coverage[0].add(ob); coverage[1].add(kings[0]); coverage[2].add(kings[1])
        self.assertEqual(coverage, [set(range(8))]*3)

    def test_float_integer_actual_rust(self):
        torch.set_num_threads(2)
        with tempfile.TemporaryDirectory() as tmp:
            for hidden, l2, key in [(1536, 0, 'DIRECT_ENGINE'), (2048, 16, 'L2_ENGINE')]:
                if key not in os.environ:
                    self.skipTest('Set DIRECT_ENGINE and L2_ENGINE to run Rust integration')
                with self.subTest(l2=l2):
                    torch.manual_seed(910)
                    model = NNUE(hidden, l2)
                    # Exactly representable weights isolate layout/truncation,
                    # including negative sums, bucket-specific biases and clips.
                    with torch.no_grad():
                        model.ft.weight.copy_(torch.randint(-15, 16, model.ft.weight.shape)/255)
                        model.ft_bias.copy_(torch.randint(-30, 220, model.ft_bias.shape)/255)
                        model.out.weight.copy_(torch.randint(-5, 6, model.out.weight.shape)/64)
                        model.out.bias.copy_(torch.arange(-4, 4)*101/(255*64))
                        if l2:
                            model.l2.weight.copy_(torch.randint(-4, 5, model.l2.weight.shape)/64)
                            model.l2.bias.copy_(torch.arange(128).remainder(9)/255)
                    net = Path(tmp)/f'{key}.nnue'
                    quantize(model, net)
                    state = Path(str(net)+'.pt')
                    torch.save(model.state_dict(), state)
                    report = check(os.environ[key], net, hidden, l2, probes(),
                                   Path(tmp)/f'{key}.json', state, audit=True)
                    self.assertEqual(report['status'], 'pass')
                    self.assertEqual(report['mismatches'], 0)
                    with torch.no_grad(): model.out.bias[0] += .01
                    torch.save(model.state_dict(), state)
                    with self.assertRaisesRegex(ValueError, 'byte-for-byte'):
                        check(os.environ[key], net, hidden, l2, probes()[:2],
                              Path(tmp)/'wrong.json', state)

    def test_overflow_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = NNUE(2, 0)
            with torch.no_grad():
                m.ft.weight.fill_(100); m.ft_bias.zero_()
            net = Path(tmp)/'overflow.nnue'
            quantize(m, net)
            self.assertGreater(IntegerNet(net, 2, 0).evaluate(probes()[0])[1], 0)

    def test_cache_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source.txt'
            fens = probes()
            # Repeated positions with different counters must stay together.
            source.write_text('\n'.join(f'{fen} | {i%200-100}' for i, fen in enumerate(fens))+'\n'+
                              '\n'.join(f'{fen.rsplit(" ", 2)[0]} 9 42 | 5' for fen in fens)+'\n')
            prepare(source, root/'cache')
            rows = np.memmap(root/'cache/positions.bin', mode='r', dtype=ROW)
            np.testing.assert_array_equal(rows['val'][:len(fens)], rows['val'][len(fens):])
            ids = np.memmap(root/'cache/train.bin', mode='r', dtype='<u4')
            def fingerprint(epoch):
                return [cp.tolist() for _, _, _, cp, _, _ in batches(rows, ids, 31, epoch)]
            self.assertEqual(fingerprint(0), fingerprint(0))
            self.assertNotEqual(fingerprint(0), fingerprint(1))
            self.assertEqual(sum(map(len, fingerprint(0))), len(ids))
            # Every vectorized packed batch retains the source's feature lists.
            all_ids = np.arange(len(rows), dtype='<u4')
            W, B, stm, _, _, ob = next(batches(rows, all_ids, len(rows)))
            for i, fen in enumerate(fens):
                w, b, side = fen_to_features(fen)
                self.assertEqual(W[0][W[1][i]:W[1][i]+len(w)].tolist(), w)
                self.assertEqual(B[0][B[1][i]:B[1][i]+len(b)].tolist(), b)
                self.assertEqual((int(stm[i]), int(ob[i])), (side, output_bucket(fen)))

    def test_epoch_resume_preserves_training_trajectory(self):
        # Parity has a separate real-binary integration test above. Use a small
        # model here to isolate optimizer/scheduler/order continuation cheaply.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source.txt'
            source.write_text('\n'.join(f'{fen} | {i%100-50}'
                                         for i, fen in enumerate(probes()))+'\n')
            prepare(source, root/'cache')
            engine = root/'engine'
            engine.write_text('test provenance placeholder')
            def passed(engine, net, hidden, l2, fens, out, *args, **kwargs):
                report = {'status': 'pass'}
                Path(out).write_text(json.dumps(report))
                return report
            def args(name, stop, resume=False):
                return SimpleNamespace(cache=root/'cache', out=root/name, arm='tiny',
                                       engine=engine, resume=resume, batch=32, threads=2,
                                       lr=.001, schedule_epochs=20, stop_after=stop)
            with mock.patch.dict('architecture_ab.CONFIGS', {'tiny': (16, 0)}), \
                    mock.patch('architecture_ab.check', side_effect=passed):
                train(args('whole', 2))
                train(args('resumed', 1))
                train(args('resumed', 2, True))
                for name in ('epoch2.nnue', 'epoch2.nnue.pt'):
                    self.assertEqual((root/'whole'/name).read_bytes(),
                                     (root/'resumed'/name).read_bytes())
                whole = torch.load(root/'whole/state.pt', weights_only=True)
                resumed = torch.load(root/'resumed/state.pt', weights_only=True)
                self.assertEqual(whole['sched'], resumed['sched'])
                self.assertEqual(whole['sched']['last_epoch'], 2*4)
                modified = args('resumed', 3, True)
                modified.batch = 16
                with self.assertRaisesRegex(ValueError, 'differs'):
                    train(modified)


if __name__ == '__main__':
    unittest.main()
