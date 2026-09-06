#!/usr/bin/env python3
"""Compare a candidate net with the v7 champion using Fastchess."""
import argparse
from sprt import ROOT, main

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('net')
    parser.add_argument('--engine', default=str(ROOT / 'target/release/chess'))
    parser.add_argument('--baseline', default=str(ROOT / 'target/testing/v7-engine'))
    args, rest = parser.parse_known_args()
    raise SystemExit(main([args.engine, args.baseline, '--new-net', args.net,
                           '--old-net', str(ROOT / 'nets/v7.nnue')] + rest))
