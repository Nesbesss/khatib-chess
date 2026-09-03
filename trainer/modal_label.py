"""Label positions with Stockfish on Modal CPU containers.

Labeling is CPU-bound and embarrassingly parallel, so fanning it across many
containers turns hours into minutes.

  modal run trainer/modal_label.py --data data/positions.txt --shards 40
"""
import modal

app = modal.App("chess-label")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("stockfish")
    .add_local_file("trainer/label.py", "/root/label.py")
)

vol = modal.Volume.from_name("chess-nnue-data", create_if_missing=True)


@app.function(image=image, volumes={"/data": vol}, timeout=60 * 60,
              cpu=4, max_containers=50)
def label_shard(shard_id: int, shards: int, depth: int, limit: int) -> bytes:
    """Label every shard_id-th position from the uploaded file."""
    import subprocess, sys, os

    src = "/data/to_label.txt"
    mine = f"/tmp/shard_{shard_id}.txt"
    seen = set()
    n = 0
    with open(src) as f, open(mine, "w") as out:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            if i % shards != shard_id:
                continue
            parts = line.split("|")
            if len(parts) < 2:
                continue
            key = " ".join(parts[0].split()[:4])
            if key in seen:
                continue
            seen.add(key)
            out.write(line)
            n += 1
    if n == 0:
        return b""

    outp = f"/tmp/out_{shard_id}.txt"
    subprocess.run([sys.executable, "/root/label.py", "--in", mine,
                    "--out", outp, "--depth", str(depth), "--workers", "4"],
                   check=True)
    with open(outp, "rb") as f:
        return f.read()


@app.function(image=image, volumes={"/data": vol}, timeout=60 * 60)
def upload_chunk(offset: int, data: bytes, first: bool):
    mode = "wb" if first else "ab"
    with open("/data/to_label.txt", mode) as f:
        f.write(data)
    vol.commit()
    return offset + len(data)


@app.local_entrypoint()
def main(data: str = "data/positions.txt", shards: int = 40, depth: int = 8,
         limit: int = 0, out: str = "data/sf_labeled.txt",
         skip_upload: bool = False):
    import os

    if not skip_upload:
        size = os.path.getsize(data)
        print(f"uploading {data} ({size/1e6:.0f} MB)...")
        CHUNK = 64 * 1024 * 1024
        with open(data, "rb") as f:
            first, off = True, 0
            while True:
                buf = f.read(CHUNK)
                if not buf:
                    break
                if len(buf) == CHUNK:
                    tail = buf.rfind(b"\n")
                    if tail != -1:
                        f.seek(off + tail + 1)
                        buf = buf[:tail + 1]
                off = upload_chunk.remote(off, buf, first)
                first = False
                print(f"  {off/1e6:.0f} / {size/1e6:.0f} MB", flush=True)

    print(f"labeling across {shards} containers at depth {depth}...")
    total = 0
    with open(out, "wb") as f:
        for chunk in label_shard.map(range(shards),
                                     kwargs=dict(shards=shards, depth=depth,
                                                 limit=limit)):
            f.write(chunk)
            total += chunk.count(b"\n")
            print(f"  {total:,} labeled", flush=True)
    print(f"wrote {total:,} positions to {out}")
