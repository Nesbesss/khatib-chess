"""Regression checks for the failure modes that invalidated the old harness."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sprt


class HarnessTests(unittest.TestCase):
    def test_repeated_roots_are_rejected_even_with_different_epd_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp) / 'book.epd'
            book.write_text('8/8/8/8/8/2k5/8/K7 w - - id "a";\n'
                            '8/8/8/8/8/2k5/8/K7 w - - id "b";\n')
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                sprt.read_book(book)

    def test_failed_net_load_rejected_even_if_engine_keeps_playing(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = type('Result', (), dict(returncode=0, stdout=
                'info string network load failed: size mismatch\n'
                'uciok\nreadyok\nbestmove e2e4\n'))()
            with patch('sprt.subprocess.run', return_value=result):
                with self.assertRaisesRegex(ValueError, 'preflight failed'):
                    sprt.preflight(directory)

    def test_schedule_and_binary_net_snapshots_are_actually_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            binary = root / 'source-engine'
            binary.write_text('binary snapshot')
            binary.chmod(0o755)
            new_net, old_net = root / 'new.nnue', root / 'old.nnue'
            new_net.write_text('2048 architecture')
            old_net.write_text('1536 architecture')
            fastchess = root / 'fastchess'
            fastchess.write_text('runner snapshot')
            fastchess.chmod(0o755)
            book = root / 'book.epd'
            roots = ['8/8/8/8/8/2k5/8/K7 w - -', '8/8/8/8/8/3k4/8/K7 b - -']
            book.write_text('\n'.join(roots) + '\n')
            run = root / 'run'
            with patch('sprt.preflight'), patch('sprt.subprocess.check_output', return_value='test runner'):
                sprt.main([str(binary), str(binary), '--new-net', str(new_net),
                           '--old-net', str(old_net), '--book', str(book),
                           '--fastchess', str(fastchess), '--games', '4', '--out', str(run),
                           '--seed', '7', '--prepare-only'])
            import json
            manifest = json.loads((run / 'manifest.json').read_text())
            command = manifest['command']
            self.assertEqual(set((run / 'openings.epd').read_text().splitlines()), set(roots))
            self.assertIn(f'file={run / "openings.epd"}', command)
            self.assertIn('order=sequential', command)
            self.assertEqual(command[command.index('-rounds') + 1], '2')
            self.assertIn('-repeat', command)
            self.assertIn('st=0.2', command)
            self.assertIn('model=logistic', command)
            self.assertIn('option.OwnBook=false', command)
            self.assertEqual((run / 'new/net.nnue').read_text(), '2048 architecture')
            self.assertEqual((run / 'old/net.nnue').read_text(), '1536 architecture')
            # Changing the original checkpoint cannot change a running match.
            new_net.write_text('new epoch')
            self.assertEqual((run / 'new/net.nnue').read_text(), '2048 architecture')
            # Mutating a snapshot must make resume fail before launching anything.
            (run / 'new/net.nnue').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'modified snapshot'):
                sprt.resume(run)


if __name__ == '__main__':
    unittest.main()
