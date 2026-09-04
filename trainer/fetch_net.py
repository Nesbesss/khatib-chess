#!/usr/bin/env python3
"""Pull a finished network out of the Modal volume.

  python3 trainer/fetch_net.py v4          # check status, fetch if ready
"""
import pathlib
import sys

import modal


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "v4"
    vol = modal.Volume.from_name("chess-nnue-data")

    names = [e.path for e in vol.listdir("/")]
    if f"{tag}.done" not in names:
        nets = [n for n in names if n.startswith(tag)]
        print(f"not finished yet. files so far: {nets or 'none'}")
        return

    out = pathlib.Path("nets")
    out.mkdir(exist_ok=True)
    for name in names:
        if name.startswith(tag) and name.endswith(".nnue") or ".nnue.ep" in name:
            if not name.startswith(tag):
                continue
            data = b"".join(vol.read_file(name))
            dest = out / name
            dest.write_bytes(data)
            print(f"fetched {dest} ({len(data):,} bytes)")


if __name__ == "__main__":
    main()
