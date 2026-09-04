"""Generate AND label training positions entirely on Modal.

Self-play generation and Stockfish labeling both happen in the same container,
so millions of positions never cross the network. Each shard returns only the
labeled result.

  modal run trainer/modal_gen.py --shards 60 --games 3000 --depth 10
"""
import modal

app = modal.App("chess-gen")

# The engine binary is built once into the image, then reused by every shard.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("stockfish", "curl", "build-essential")
    .run_commands(
        "curl https://sh.rustup.rs -sSf | sh -s -- -y --default-toolchain stable",
    )
    .add_local_dir("src", "/build/src", copy=True)
    .add_local_file("Cargo.toml", "/build/Cargo.toml", copy=True)
    .add_local_dir("web", "/build/web", copy=True)
    .run_commands(
        "cd /build && /root/.cargo/bin/cargo build --release",
        "cp /build/target/release/chess /usr/local/bin/chess",
    )
    .add_local_file("trainer/label.py", "/root/label.py")
)

vol = modal.Volume.from_name("chess-nnue-data", create_if_missing=True)


@app.function(image=image, timeout=60 * 60 * 2, cpu=4, memory=8192,
              max_containers=100, retries=2, volumes={"/data": vol})
def gen_and_label(shard: int, games: int, gen_depth: int, sf_depth: int,
                  run_seed: int, to_volume: bool = False) -> bytes:
    """Self-play `games` games, then label every position with Stockfish."""
    import subprocess, sys, os

    raw = f"/tmp/raw_{shard}.txt"
    # Seed by shard AND by run: shard alone makes containers differ from each
    # other, but every re-run then reproduces the identical dataset, so a
    # second batch adds nothing.
    seed = (run_seed ^ (shard * 0x100000001B3 + 12345)) & 0xFFFFFFFFFFFFFFFF
    subprocess.run(["chess", "datagen", str(games), str(gen_depth), raw, "4",
                    str(seed)],
                   check=True, capture_output=True)

    # Deduplicate before labeling: repeated positions waste Stockfish time.
    uniq = f"/tmp/uniq_{shard}.txt"
    seen = set()
    with open(raw) as f, open(uniq, "w") as out:
        for line in f:
            parts = line.split("|")
            if len(parts) < 2:
                continue
            key = " ".join(parts[0].split()[:4])
            if key in seen:
                continue
            seen.add(key)
            out.write(line)

    labeled = f"/tmp/lab_{shard}.txt"
    subprocess.run([sys.executable, "/root/label.py", "--in", uniq,
                    "--out", labeled, "--depth", str(sf_depth), "--workers", "4"],
                   check=True)
    if to_volume:
        import shutil
        os.makedirs("/data/shards", exist_ok=True)
        shutil.copyfile(labeled, f"/data/shards/lab_{shard}.txt")
        vol.commit()
        return b""
    with open(labeled, "rb") as f:
        return f.read()


@app.function(image=image, volumes={"/data": vol}, timeout=60 * 60)
def merge_shards(dest: str = "train.txt", extra: str = "") -> tuple:
    """Concatenate /data/shards/* into one training file, deduplicated.

    `extra` names an existing file in the volume to fold in first, so earlier
    batches are kept without moving them off the volume.
    """
    import glob, os
    seen, n = set(), 0
    with open("/data/" + dest, "w") as out:
        srcs = ([("/data/" + extra)] if extra and os.path.exists("/data/" + extra)
                else [])
        srcs += sorted(glob.glob("/data/shards/*.txt"))
        for path in srcs:
            with open(path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    key = line.split()[0]
                    if key in seen:
                        continue
                    seen.add(key)
                    out.write(line)
                    n += 1
    vol.commit()
    return (os.path.getsize("/data/" + dest), n)


@app.local_entrypoint()
def main(shards: int = 60, games: int = 3000, gen_depth: int = 6,
         sf_depth: int = 10, out: str = "data/train_big.txt",
         run_seed: int = 0, to_volume: bool = False):
    import time
    if run_seed == 0:
        run_seed = int(time.time() * 1000) & 0xFFFFFFFF
    print(f"run seed: {run_seed}")
    total = 0
    if to_volume:
        done = 0
        for _ in gen_and_label.map(
            range(shards),
            kwargs=dict(games=games, gen_depth=gen_depth, sf_depth=sf_depth,
                        run_seed=run_seed, to_volume=True),
        ):
            done += 1
            print(f"  shard {done}/{shards} written to volume", flush=True)
        print("shards complete; data stays on the volume", flush=True)
        return
    with open(out, "wb") as f:
        for chunk in gen_and_label.map(
            range(shards),
            kwargs=dict(games=games, gen_depth=gen_depth, sf_depth=sf_depth,
                        run_seed=run_seed),
        ):
            f.write(chunk)
            total += chunk.count(b"\n")
            print(f"  {total:,} labeled positions", flush=True)
    print(f"wrote {total:,} positions to {out}")
