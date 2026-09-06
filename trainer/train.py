"""NNUE trainer: 768 -> 512x2 -> 1, matching src/nnue.rs exactly.

Reads "FEN | score" lines, trains on the search score as a soft target, and
exports quantized int16 weights in the flat layout the engine loads.
"""
import argparse, math, os, struct, time
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader

import os as _os
# Width is an experiment knob: the net must be sized to the data on hand, and
# the Rust side reads the same value from CHESS_HIDDEN.
INPUT = 768
HIDDEN = int(_os.environ.get("CHESS_HIDDEN", "2048"))
BUCKETS = 8                            # king buckets; must match src/nnue.rs
FT_SIZE = INPUT * BUCKETS
OUT_BUCKETS = 8                        # output buckets by piece count
L2 = 16                                # second hidden layer width
QA, QB, SCALE = 255, 64, 400           # must match src/nnue.rs

# King-square -> bucket, mirroring KING_BUCKET in src/nnue.rs.
# Four files x two ranks, mirroring KING_BUCKET in src/nnue.rs.
KING_BUCKET = [0, 0, 1, 1, 2, 2, 3, 3] * 4 + [4, 4, 5, 5, 6, 6, 7, 7] * 4

PIECE_IDX = {'p':0,'n':1,'b':2,'r':3,'q':4,'k':5}


def output_bucket(fen):
    """Which output layer this position uses, by piece count.

    Mean |eval| runs 132 cp in the opening to 534 cp in the endgame; giving
    each phase its own output layer stops one layer having to reconcile them.
    """
    n = sum(1 for c in fen.split()[0] if c.isalpha())
    return min(OUT_BUCKETS - 1, max(0, (n - 2) // 4))


def fen_to_features(fen):
    """Return (white_perspective_indices, black_perspective_indices, stm).

    Mirrors Accumulator::feature in src/nnue.rs: black's view flips the board
    vertically and swaps piece colors, so 'my pieces' always land in slots
    0..384 from whichever side is looking. Indices are offset by the king
    bucket of the perspective whose view they belong to.
    """
    board, stm = fen.split(' ')[0], fen.split(' ')[1]
    raw = []
    wk = bk = 0
    sq = 56  # FEN starts at a8; squares are little-endian rank-file
    for ch in board:
        if ch == '/':
            sq -= 16
        elif ch.isdigit():
            sq += int(ch)
        else:
            is_white = ch.isupper()
            piece = PIECE_IDX[ch.lower()]
            if piece == 5:
                if is_white:
                    wk = sq
                else:
                    bk = sq
            raw.append((is_white, piece, sq))
            sq += 1

    w_bucket = KING_BUCKET[wk]
    b_bucket = KING_BUCKET[bk ^ 56]
    w_idx, b_idx = [], []
    for is_white, piece, s in raw:
        w_idx.append(w_bucket * INPUT
                     + (0 if is_white else 1) * 384 + piece * 64 + s)
        b_idx.append(b_bucket * INPUT
                     + (1 if is_white else 0) * 384 + piece * 64 + (s ^ 56))
    return w_idx, b_idx, (0 if stm == 'w' else 1)


class PositionSet(Dataset):
    """Reads "FEN | score" or "FEN | score | wdl" lines.

    Older files without the wdl column are still accepted; those samples fall
    back to pure score training (wdl = -1 marks 'unknown').
    """
    def __init__(self, path, limit=None):
        self.stm, self.scores, self.wdls, self.obs = [], [], [], []
        self.w_off, self.b_off = [0], [0]
        w_flat, b_flat = [], []
        t0 = time.time()
        with open(path) as f:
            for i, line in enumerate(f):
                if limit and i >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                parts = line.split('|')
                try:
                    if len(parts) >= 3:
                        fen, score, wdl = parts[0], int(parts[1]), float(parts[2])
                    elif len(parts) == 2:
                        fen, score, wdl = parts[0], int(parts[1]), -1.0
                    else:
                        continue
                except ValueError:
                    continue
                w, b, stm = fen_to_features(fen.strip())
                ob = output_bucket(fen.strip())
                w_flat.extend(w); b_flat.extend(b)
                self.w_off.append(len(w_flat))
                self.b_off.append(len(b_flat))
                self.stm.append(stm)
                self.scores.append(score)
                self.wdls.append(wdl)
                self.obs.append(ob)
                if (i + 1) % 1_000_000 == 0:
                    print(f"  loaded {i+1:,} in {time.time()-t0:.0f}s", flush=True)
        self.w_flat = np.array(w_flat, dtype=np.int32)
        self.b_flat = np.array(b_flat, dtype=np.int32)
        self.w_off = np.array(self.w_off, dtype=np.int64)
        self.b_off = np.array(self.b_off, dtype=np.int64)
        self.stm = np.array(self.stm, dtype=np.int8)
        self.scores = np.array(self.scores, dtype=np.float32)
        self.wdls = np.array(self.wdls, dtype=np.float32)
        self.obs = np.array(self.obs, dtype=np.int8)
        known = int((self.wdls >= 0).sum())
        print(f"  {known:,} samples carry a game result "
              f"({100*known/max(len(self.wdls),1):.0f}%)", flush=True)
        print(f"loaded {len(self.scores):,} positions in {time.time()-t0:.0f}s", flush=True)

    def __len__(self):
        return len(self.scores)

    def __getitem__(self, i):
        w = self.w_flat[self.w_off[i]:self.w_off[i+1]]
        b = self.b_flat[self.b_off[i]:self.b_off[i+1]]
        return (w, b, int(self.stm[i]), float(self.scores[i]),
                float(self.wdls[i]), int(self.obs[i]))


def collate(batch):
    """Pack active feature indices for EmbeddingBag.

    A position sets at most 32 of FT_SIZE inputs, so a dense one-hot row would
    be 99% zeros. EmbeddingBag sums the active weight rows directly — the same
    computation as the dense matmul, at a fraction of the memory and time.
    Returns flat index arrays plus per-row offsets.
    """
    n = len(batch)
    w_flat, b_flat = [], []
    w_off, b_off = [], []
    stms = torch.empty(n, dtype=torch.long)
    scores = torch.empty(n, dtype=torch.float32)
    wdls = torch.empty(n, dtype=torch.float32)
    obs = torch.zeros(n, dtype=torch.long)
    for i, (w, b, stm, sc, wd, ob) in enumerate(batch):
        w_off.append(len(w_flat)); b_off.append(len(b_flat))
        w_flat.extend(w.tolist()); b_flat.extend(b.tolist())
        stms[i] = stm; scores[i] = sc; wdls[i] = wd; obs[i] = ob
    W = (torch.tensor(w_flat, dtype=torch.long),
         torch.tensor(w_off, dtype=torch.long))
    B = (torch.tensor(b_flat, dtype=torch.long),
         torch.tensor(b_off, dtype=torch.long))
    return W, B, stms, scores, wdls, obs


class NNUE(nn.Module):
    def __init__(self):
        super().__init__()
        # EmbeddingBag with mode='sum' is exactly a linear layer applied to a
        # one-hot-sum input, but reads only the active rows.
        self.ft = nn.EmbeddingBag(FT_SIZE, HIDDEN, mode='sum')
        self.ft_bias = nn.Parameter(torch.zeros(HIDDEN))
        # Per bucket: a hidden layer over both perspectives, then a scalar.
        # Implemented as one wide layer and reshaped, so a single matmul covers
        # every bucket and the right slice is selected afterwards.
        self.l2 = nn.Linear(HIDDEN * 2, OUT_BUCKETS * L2)
        self.out = nn.Linear(L2, OUT_BUCKETS)
        # Init wide enough that clipped-ReLU activations actually occupy
        # [0,1]. Too small and every quantized weight rounds toward zero,
        # which collapses the net to its bias after export.
        # Scaled so a typical 32-piece position sums to activations near the
        # middle of [0,1] rather than saturating the clamp.
        # Small init measured better than large: with weight clipping the
        # network grows the weights it needs, while a large init just pins
        # units at the clamp bounds and loses capacity.
        nn.init.uniform_(self.ft.weight, -0.08, 0.08)
        nn.init.uniform_(self.out.weight, -0.4, 0.4)
        nn.init.zeros_(self.out.bias)

    def clip_weights(self):
        """Keep weights in the range the int16 quantization can represent.

        ft weights scale by QA and out weights by QB, so the representable
        magnitudes are 32767/QA and 32767/QB. Clipping well inside those keeps
        activations from saturating the clipped-ReLU and preserves capacity.
        """
        with torch.no_grad():
            self.ft.weight.clamp_(-1.98, 1.98)
            self.ft_bias.clamp_(-1.98, 1.98)
            self.l2.weight.clamp_(-127 / QB, 127 / QB)
            self.out.weight.clamp_(-127 / QB, 127 / QB)

    def _bag(self, idx, offsets):
        """EmbeddingBag(mode='sum'), with a fallback for Metal.

        aten::_embedding_bag has no MPS kernel, so on that device the same sum
        is done as a padded gather -- mathematically identical, just written
        with ops Metal implements.
        """
        if idx.device.type != "mps":
            return self.ft(idx, offsets)
        n = offsets.shape[0]
        ends = torch.cat([offsets[1:],
                          torch.tensor([idx.shape[0]], device=idx.device)])
        counts = ends - offsets
        width = int(counts.max().item())
        ar = torch.arange(width, device=idx.device).unsqueeze(0)
        mask = ar < counts.unsqueeze(1)                    # (n, width)
        gather = (offsets.unsqueeze(1) + ar).clamp(max=idx.shape[0] - 1)
        rows = idx[gather]                                 # (n, width)
        vecs = self.ft.weight[rows]                        # (n, width, HIDDEN)
        return (vecs * mask.unsqueeze(-1)).sum(1)

    def forward(self, W, B, stm, ob=None):
        aw = self._bag(W[0], W[1]) + self.ft_bias   # white-perspective
        ab = self._bag(B[0], B[1]) + self.ft_bias   # black-perspective
        # Order the pair as [side-to-move, opponent] to match inference.
        stm = stm.unsqueeze(1).float()
        us = aw * (1 - stm) + ab * stm
        them = ab * (1 - stm) + aw * stm
        x = torch.cat([us, them], dim=1)
        x = torch.clamp(x, 0.0, 1.0)          # clipped ReLU, scaled to [0,1]
        # Second layer, then per-bucket selection.
        h = self.l2(x).view(-1, OUT_BUCKETS, L2)
        h = torch.clamp(h, 0.0, 1.0)
        if ob is None:
            ob = torch.full((x.shape[0],), OUT_BUCKETS // 2,
                            dtype=torch.long, device=x.device)
        idx = ob.view(-1, 1, 1).expand(-1, 1, L2)
        hb = h.gather(1, idx).squeeze(1)                 # (batch, L2)
        # out.weight is (OUT_BUCKETS, L2); pick this position's row.
        w = self.out.weight[ob]                          # (batch, L2)
        return (hb * w).sum(1) + self.out.bias[ob]


def to_wdl(cp):
    """Map centipawns to a win probability. Training on WDL rather than raw
    centipawns keeps huge scores from dominating the loss."""
    return torch.sigmoid(cp / SCALE)


def blended_target(scores, wdls, lam):
    """Blend the teacher's score with the game's actual result, in WDL space.

    lam=1.0 trains purely on search scores (imitate the teacher); lam=0.0
    purely on outcomes. Samples with no recorded result (wdl < 0) always use
    the score alone.
    """
    score_t = to_wdl(scores)
    has_result = wdls >= 0
    outcome_t = torch.where(has_result, wdls, score_t)
    eff = torch.where(has_result, torch.full_like(wdls, lam),
                      torch.ones_like(wdls))
    return eff * score_t + (1 - eff) * outcome_t


def quantize(model, path):
    """Write the flat int16 layout src/nnue.rs::load expects."""
    ftw = model.ft.weight.detach().cpu().numpy()      # (FT_SIZE, HIDDEN)
    ftb = model.ft_bias.detach().cpu().numpy()
    l2w = model.l2.weight.detach().cpu().numpy()      # (OUT_BUCKETS*L2, HIDDEN*2)
    l2b = model.l2.bias.detach().cpu().numpy()        # (OUT_BUCKETS*L2,)
    ow = model.out.weight.detach().cpu().numpy()      # (OUT_BUCKETS, L2)
    ob = model.out.bias.detach().cpu().numpy()        # (OUT_BUCKETS,)

    q_ftw = np.clip(np.round(ftw * QA), -32768, 32767).astype(np.int16)
    q_ftb = np.clip(np.round(ftb * QA), -32768, 32767).astype(np.int16)
    q_l2w = np.clip(np.round(l2w * QB), -32768, 32767).astype(np.int16)
    q_l2b = np.clip(np.round(l2b * QA), -32768, 32767).astype(np.int16)
    q_ow  = np.clip(np.round(ow * QB), -32768, 32767).astype(np.int16)
    # Bias shares the activation*weight scale so it adds directly to the sum.
    q_ob  = np.clip(np.round(ob * QA * QB), -32768, 32767).astype(np.int16)

    clipped = int((np.abs(np.round(ftw * QA)) > 32767).sum() +
                  (np.abs(np.round(ow * QB)) > 32767).sum())
    if clipped:
        print(f"WARNING: {clipped} weights clipped during quantization")
    # A net whose weights all round to near-zero evaluates to a constant.
    scale_use = np.abs(q_ftw).mean() / QA
    print(f"quantization: mean|ft_w| = {np.abs(q_ftw).mean():.1f}/{QA} "
          f"({scale_use*100:.1f}% of range), mean|out_w| = "
          f"{np.abs(q_ow).mean():.1f}/{QB}")
    if np.abs(q_ftw).max() < QA * 0.1:
        print("WARNING: feature weights are tiny; net may collapse to bias")

    with open(path, 'wb') as f:
        # EmbeddingBag stores [FT_SIZE][HIDDEN] already — the engine's layout.
        f.write(q_ftw.tobytes())
        f.write(q_ftb.tobytes())
        f.write(q_l2w.tobytes())
        f.write(q_l2b.tobytes())
        f.write(q_ow.tobytes())
        f.write(q_ob.tobytes())
    size = os.path.getsize(path)
    expected = (FT_SIZE * HIDDEN + HIDDEN
                + OUT_BUCKETS * L2 * HIDDEN * 2 + OUT_BUCKETS * L2
                + OUT_BUCKETS * L2 + OUT_BUCKETS) * 2
    assert size == expected, f"wrote {size} bytes, engine expects {expected}"
    print(f"wrote {path} ({size:,} bytes)")


def nnue_loss(pred_logit, scores, wdls, lam):
    """WDL-space loss plus a direct anchor on the evaluation itself.

    The WDL term alone is minimized by shrinking the logit toward the flat
    part of the sigmoid, which starves the output layer of magnitude and
    destroys int16 precision after quantization. The anchor term keeps the
    logit calibrated to actual centipawns.
    """
    target = blended_target(scores, wdls, lam)
    wdl_loss = F.mse_loss(torch.sigmoid(pred_logit), target)
    # Anchor: the predicted logit should equal score/SCALE. Without this the
    # WDL term is minimized by shrinking the logit into the sigmoid's flat
    # region, which starves the output layer and destroys int16 precision.
    # The WDL term lives on [0,1] while the anchor spans [-4,4], so the
    # anchor needs a large nominal weight to have comparable influence.
    anchor_t = torch.clamp(scores / SCALE, -4.0, 4.0)
    anchor_loss = F.mse_loss(pred_logit, anchor_t)
    return wdl_loss + 0.5 * anchor_loss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--out', default='net.nnue')
    ap.add_argument('--epochs', type=int, default=20)
    ap.add_argument('--batch', type=int, default=16384)
    ap.add_argument('--lr', type=float, default=1e-3)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--checkpoint-every', type=int, default=0,
                    help='also export a net every N epochs, for game testing')
    ap.add_argument('--lambda', dest='lam', type=float, default=0.7,
                    help='1.0 = pure search score, 0.0 = pure game result')
    a = ap.parse_args()

    dev = ('cuda' if torch.cuda.is_available()
           else 'mps' if torch.backends.mps.is_available() else 'cpu')
    print(f"device: {dev}  lambda: {a.lam}")

    ds = PositionSet(a.data, a.limit)
    n_val = max(1, len(ds) // 50)
    n_train = len(ds) - n_val
    train_ds, val_ds = torch.utils.data.random_split(
        ds, [n_train, n_val], generator=torch.Generator().manual_seed(42))

    dl = DataLoader(train_ds, batch_size=a.batch, shuffle=True,
                    collate_fn=collate, num_workers=a.workers, drop_last=True)
    vdl = DataLoader(val_ds, batch_size=a.batch, shuffle=False,
                     collate_fn=collate, num_workers=a.workers)

    model = NNUE().to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-8)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=a.lr, total_steps=a.epochs * max(1, len(dl)))

    best_val = float('inf')
    for ep in range(a.epochs):
        model.train()
        tot, nb, t0 = 0.0, 0, time.time()
        for W, B, stm, sc, wd, ob in dl:
            W = (W[0].to(dev), W[1].to(dev))
            B = (B[0].to(dev), B[1].to(dev))
            stm, sc, wd, ob = stm.to(dev), sc.to(dev), wd.to(dev), ob.to(dev)
            pred = model(W, B, stm, ob)
            loss = nnue_loss(pred, sc, wd, a.lam)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step(); sched.step()
            model.clip_weights()
            tot += loss.item(); nb += 1

        model.eval()
        # Saturation check: hidden units pinned at the clamp bounds carry no
        # gradient and no information. High values mean wasted capacity.
        with torch.no_grad():
            Wb, _Bb, _sb, _sc, _wd, _ob = next(iter(vdl))
            acts = model.ft(Wb[0].to(dev), Wb[1].to(dev)) + model.ft_bias
            sat_lo = (acts < 0).float().mean().item()
            sat_hi = (acts > 1).float().mean().item()
        vtot, vnb = 0.0, 0
        with torch.no_grad():
            for W, B, stm, sc, wd, ob in vdl:
                W = (W[0].to(dev), W[1].to(dev))
                B = (B[0].to(dev), B[1].to(dev))
                stm, sc, wd, ob = stm.to(dev), sc.to(dev), wd.to(dev), ob.to(dev)
                loss = nnue_loss(model(W, B, stm, ob), sc, wd, a.lam)
                vtot += loss.item(); vnb += 1
        val = vtot / max(vnb, 1)
        print(f"epoch {ep+1}/{a.epochs}  train {tot/max(nb,1):.5f}  "
              f"val {val:.5f}  sat {100*sat_lo:.0f}%lo/{100*sat_hi:.0f}%hi  "
              f"{time.time()-t0:.0f}s", flush=True)

        # Keep the best net by validation loss...
        if val < best_val:
            best_val = val
            quantize(model, a.out)
            torch.save(model.state_dict(), a.out + '.pt')

        # ...but validation loss does not reliably track playing strength: it
        # measures agreement with the teacher's scores, while strength depends
        # on ranking moves correctly. Save periodic checkpoints so candidates
        # can be compared in actual games.
        if a.checkpoint_every and (ep + 1) % a.checkpoint_every == 0:
            ckpt = f"{a.out}.ep{ep + 1}"
            quantize(model, ckpt)
            print(f"  checkpoint: {ckpt}", flush=True)

    print(f"best val loss {best_val:.5f}")


if __name__ == '__main__':
    main()
