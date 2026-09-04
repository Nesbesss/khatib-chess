"""Train the NNUE on Modal.

  modal run trainer/modal_train.py --data data/train.txt --epochs 30

Uploads the training data to a Modal volume once, then runs the trainer on a
GPU. The quantized net comes back to ./net.nnue.
"""
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
    gpu="A10G",
    volumes={"/data": vol},
    timeout=60 * 60 * 4,
)
def train(epochs: int, batch: int, lr: float, limit: int | None, lam: float):
    import subprocess, sys
    cmd = [
        sys.executable, "/root/train.py",
        "--data", "/data/train.txt",
        "--out", "/data/net.nnue",
        "--epochs", str(epochs),
        "--batch", str(batch),
        "--lr", str(lr),
        "--workers", "8",
        "--lambda", str(lam),
    ]
    if limit:
        cmd += ["--limit", str(limit)]
    subprocess.run(cmd, check=True)
    vol.commit()
    with open("/data/net.nnue", "rb") as f:
        return f.read()


@app.function(image=image, volumes={"/data": vol}, timeout=60 * 60)
def upload_chunk(offset: int, data: bytes, first: bool):
    """Append one chunk of the training file into the volume."""
    mode = "wb" if first else "ab"
    with open("/data/train.txt", mode) as f:
        f.write(data)
    vol.commit()
    return offset + len(data)


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
         lam: float = 0.7, out: str = "net.nnue"):
    import os

    if not skip_upload:
        size = os.path.getsize(data)
        print(f"uploading {data} ({size/1e6:.0f} MB) to volume...")
        CHUNK = 64 * 1024 * 1024
        with open(data, "rb") as f:
            first, off = True, 0
            while True:
                buf = f.read(CHUNK)
                if not buf:
                    break
                # Split on a line boundary so no sample is cut in half.
                if len(buf) == CHUNK:
                    tail = buf.rfind(b"\n")
                    if tail != -1:
                        f.seek(off + tail + 1)
                        buf = buf[:tail + 1]
                off = upload_chunk.remote(off, buf, first)
                first = False
                print(f"  {off/1e6:.0f} MB / {size/1e6:.0f} MB", flush=True)

    if not skip_upload:
        size = os.path.getsize(data)
        got_bytes, got_lines = verify_upload.remote()
        print(f"volume holds {got_bytes/1e6:.0f} MB / {got_lines:,} lines")
        if got_bytes < size * 0.99:
            raise SystemExit(
                f"upload incomplete: {got_bytes:,} of {size:,} bytes — "
                "re-run without --skip-upload")

    print("training...")
    net = train.remote(epochs, batch, lr, limit or None, lam)
    with open(out, "wb") as f:
        f.write(net)
    print(f"wrote {out} ({len(net):,} bytes)")
