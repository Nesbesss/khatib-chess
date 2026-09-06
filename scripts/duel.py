#!/usr/bin/env python3
"""Compatibility entry point for sprt.py; --games now counts TOTAL games."""
from sprt import main

if __name__ == '__main__':
    raise SystemExit(main())
