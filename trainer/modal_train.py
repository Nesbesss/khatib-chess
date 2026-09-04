"""Train the NNUE on Modal.

  modal run trainer/modal_train.py --data data/train.txt --epochs 30

Uploads the training data to a Modal volume once, then runs the trainer on a
GPU. The quantized net comes back to ./net.nnue.
"""
import os as _os
import modal

app = modal.App("chess-nnue")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch", "numpy")
    .add_local_file("trainer/train.py", "/root/train.py")
)

# Persist data and checkpoints between runs so re-training doesn't re-upload.
vol = modal.Volume.from_name("chess-nnue-data", create_if_missing=True)


@app.function(
    image=image,
    gpu=_os.environ.get("CHESS_GPU", "A100"),
    volumes={"/data": vol},
    timeout=60 * 60 * 4,
)
def train(epochs: int, batch: int, lr: float, limit: int | None, lam: float,
          checkpoint_every: int = 0, data_name: str = "train.txt",
          hidden: int = 2048):
    import os, subprocess, sys
    env = dict(os.environ, CHESS_HIDDEN=str(hidden))
    cmd = [
        sys.executable, "/root/train.py",
        "--data", "/data/" + data_name,
        "--out", "/data/net.nnue",
        "--epochs", str(epochs),
        "--batch", str(batch),
        "--lr", str(lr),
        "--workers", "8",
        "--lambda", str(lam),
        "--checkpoint-every", str(checkpoint_every),
    ]
    if limit:
        cmd += ["--limit", str(limit)]
    subprocess.run(cmd, check=True, env=env)
    vol.commit()
    # Return the best-val net plus any periodic checkpoints, so candidates can
    # be ranked by games rather than by loss alone.
    import glob
    out = {}
    with open("/data/net.nnue", "rb") as f:
        out["best"] = f.read()
    for p in sorted(glob.glob("/data/net.nnue.ep*")):
        with open(p, "rb") as f:
            out[os.path.basename(p).split(".")[-1]] = f.read()
    return out


@app.function(image=image, volumes={"/data": vol}, timeout=60 * 60)
def upload_chunk(offset: int, data: bytes, first: bool, gz: bool = False):
    """Append one chunk of the training file into the volume."""
    mode = "wb" if first else "ab"
    with open("/data/train.txt.gz" if gz else "/data/train.txt", mode) as f:
        f.write(data)
    vol.commit()
    return offset + len(data)


@app.function(image=image, volumes={"/data": vol}, timeout=60 * 30)
def decompress() -> tuple:
    """Expand train.txt.gz in the volume; return (bytes, lines) of the result."""
    import gzip, os, shutil
    src, dst = "/data/train.txt.gz", "/data/train.txt"
    with gzip.open(src, "rb") as f, open(dst, "wb") as g:
        shutil.copyfileobj(f, g, 1 << 24)
    n = 0
    with open(dst, "rb") as f:
        for _ in f:
            n += 1
    vol.commit()
    return (os.path.getsize(dst), n)


@app.function(image=image, volumes={"/data": vol}, timeout=60 * 10)
def verify_upload() -> tuple:
    """Bytes and lines actually present in the volume.

    An interrupted upload once left a truncated file and training silently ran
    on 0.7% of the data, so the size is checked before any GPU time is spent.
    """
    import os
    path = "/data/train.txt"
    if not os.path.exists(path):
        return (0, 0)
    n = 0
    with open(path, "rb") as f:
        for _ in f:
            n += 1
    return (os.path.getsize(path), n)


@app.local_entrypoint()
def main(data: str = "data/train.txt", epochs: int = 30, batch: int = 16384,
         lr: float = 1e-3, limit: int = 0, skip_upload: bool = False,
         lam: float = 0.7, out: str = "net.nnue", checkpoint_every: int = 0,
         volume_data: str = "", hidden: int = 2048):
    import os

    gz = data.endswith(".gz")
    if volume_data:
        skip_upload = True
    if not skip_upload:
        size = os.path.getsize(data)
        print(f"uploading {data} ({size/1e6:.0f} MB) to volume...")
        CHUNK = 16 * 1024 * 1024
        with open(data, "rb") as f:
            first, off = True, 0
            while True:
                buf = f.read(CHUNK)
                if not buf:
                    break
                # Split on a line boundary so no sample is cut in half.
                if len(buf) == CHUNK and not gz:
                    tail = buf.rfind(b"\n")
                    if tail != -1:
                        f.seek(off + tail + 1)
                        buf = buf[:tail + 1]
                off = upload_chunk.remote(off, buf, first, gz)
                first = False
                print(f"  {off/1e6:.0f} MB / {size/1e6:.0f} MB", flush=True)

    if not skip_upload and gz:
        print("decompressing on worker...")
        got_bytes, got_lines = decompress.remote()
        print(f"volume holds {got_bytes/1e6:.0f} MB / {got_lines:,} lines")
        if got_lines < 1000:
            raise SystemExit(f"decompress produced only {got_lines} lines")
    elif not skip_upload:
        size = os.path.getsize(data)
        got_bytes, got_lines = verify_upload.remote()
        print(f"volume holds {got_bytes/1e6:.0f} MB / {got_lines:,} lines")
        if got_bytes < size * 0.99:
            raise SystemExit(
                f"upload incomplete: {got_bytes:,} of {size:,} bytes — "
                "re-run without --skip-upload")

    print("training...")
    nets = train.remote(epochs, batch, lr, limit or None, lam, checkpoint_every,
                        volume_data or "train.txt", hidden)
    if isinstance(nets, bytes):          # older worker returning a single net
        nets = {"best": nets}
    for tag, blob in nets.items():
        path = out if tag == "best" else f"{out}.{tag}"
        with open(path, "wb") as f:
            f.write(blob)
        print(f"wrote {path} ({len(blob):,} bytes)")
