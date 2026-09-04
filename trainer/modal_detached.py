"""Train fully detached on Modal.

`modal run` keeps a local process alive to stream logs and collect the result,
so closing the laptop kills it. This version writes the trained net into the
Modal volume instead, so the job completes on Modal's servers whether the
machine is awake, asleep, or shut down.

  # data must already be in the volume
  modal run --detach trainer/modal_detached.py --epochs 45

Then later, from anywhere:
  python3 trainer/fetch_net.py nets/v4.nnue
"""
import modal

app = modal.App("chess-train-detached")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch", "numpy")
    .add_local_file("trainer/train.py", "/root/train.py")
)

vol = modal.Volume.from_name("chess-nnue-data", create_if_missing=True)


@app.function(image=image, gpu="A100", volumes={"/data": vol},
              timeout=60 * 60 * 6)
def train(epochs: int, batch: int, lr: float, lam: float,
          checkpoint_every: int, tag: str):
    """Train and leave the result in the volume; nothing is returned locally."""
    import subprocess, sys, os, glob, shutil

    out = f"/data/{tag}.nnue"
    cmd = [
        sys.executable, "/root/train.py",
        "--data", "/data/train.txt",
        "--out", out,
        "--epochs", str(epochs),
        "--batch", str(batch),
        "--lr", str(lr),
        "--lambda", str(lam),
        "--checkpoint-every", str(checkpoint_every),
        "--workers", "8",
    ]
    subprocess.run(cmd, check=True)

    # A marker file makes "is it finished?" a single cheap check later.
    with open(f"/data/{tag}.done", "w") as f:
        nets = [os.path.basename(p) for p in glob.glob(f"/data/{tag}.nnue*")]
        f.write("\n".join(sorted(nets)))
    vol.commit()
    return sorted(nets)


@app.function(image=image, volumes={"/data": vol}, timeout=60 * 30)
def volume_status() -> dict:
    """What the volume already holds, so we can skip a redundant upload."""
    import os
    out = {}
    for name in ("train.txt",):
        p = f"/data/{name}"
        out[name] = os.path.getsize(p) if os.path.exists(p) else 0
    return out


@app.local_entrypoint()
def main(epochs: int = 45, batch: int = 16384, lr: float = 1e-3,
         lam: float = 0.9, checkpoint_every: int = 15, tag: str = "v4",
         min_bytes: int = 1_000_000_000):
    # Refuse to start on a truncated dataset: a partial upload once trained on
    # 0.7% of the data without complaining.
    have = volume_status.remote().get("train.txt", 0)
    print(f"volume holds train.txt: {have/1e6:.0f} MB")
    if have < min_bytes:
        raise SystemExit(
            f"dataset in the volume is only {have/1e6:.0f} MB — upload it first "
            "with modal_train.py (laptop must stay awake for that step)")

    # spawn() returns immediately; the work continues on Modal.
    call = train.spawn(epochs, batch, lr, lam, checkpoint_every, tag)
    print(f"detached training started: {call.object_id}")
    print(f"results will appear in the volume as {tag}.nnue (+ checkpoints)")
    print("safe to close the laptop now")
