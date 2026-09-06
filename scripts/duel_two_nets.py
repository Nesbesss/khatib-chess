#!/usr/bin/env python3
"""Compare two nets with Fastchess; use sprt.py for different architectures."""
import argparse
from sprt import ROOT, main

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('net_a')
    parser.add_argument('net_b')
    parser.add_argument('--engine', default=str(ROOT / 'target/release/chess'))
    args, rest = parser.parse_known_args()
    raise SystemExit(main([args.engine, args.engine, '--new-net', args.net_a,
                           '--old-net', args.net_b] + rest))
