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

INPUT, HIDDEN = 768, 512
QA, QB, SCALE = 255, 64, 400          # must match src/nnue.rs

PIECE_IDX = {'p':0,'n':1,'b':2,'r':3,'q':4,'k':5}


def fen_to_features(fen):
    """Return (white_perspective_indices, black_perspective_indices, stm).

    Mirrors Accumulator::feature in src/nnue.rs: black's view flips the board
    vertically and swaps piece colors, so 'my pieces' always land in slots
    0..384 from whichever side is looking.
    """
    board, stm = fen.split(' ')[0], fen.split(' ')[1]
    w_idx, b_idx = [], []
    sq = 56  # FEN starts at a8; squares are little-endian rank-file
    for ch in board:
        if ch == '/':
            sq -= 16
        elif ch.isdigit():
            sq += int(ch)
        else:
            is_white = ch.isupper()
            piece = PIECE_IDX[ch.lower()]
            # White's view: own pieces first.
            w_idx.append((0 if is_white else 1) * 384 + piece * 64 + sq)
            # Black's view: mirror rank, swap colors.
            b_idx.append((1 if is_white else 0) * 384 + piece * 64 + (sq ^ 56))
            sq += 1
    return w_idx, b_idx, (0 if stm == 'w' else 1)


class PositionSet(Dataset):
    """Reads "FEN | score" or "FEN | score | wdl" lines.

    Older files without the wdl column are still accepted; those samples fall
    back to pure score training (wdl = -1 marks 'unknown').
    """
    def __init__(self, path, limit=None):
        self.stm, self.scores, self.wdls = [], [], []
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
                w_flat.extend(w); b_flat.extend(b)
                self.w_off.append(len(w_flat))
                self.b_off.append(len(b_flat))
                self.stm.append(stm)
                self.scores.append(score)
                self.wdls.append(wdl)
                if (i + 1) % 1_000_000 == 0:
                    print(f"  loaded {i+1:,} in {time.time()-t0:.0f}s", flush=True)
        self.w_flat = np.array(w_flat, dtype=np.int32)
        self.b_flat = np.array(b_flat, dtype=np.int32)
        self.w_off = np.array(self.w_off, dtype=np.int64)
        self.b_off = np.array(self.b_off, dtype=np.int64)
        self.stm = np.array(self.stm, dtype=np.int8)
        self.scores = np.array(self.scores, dtype=np.float32)
        self.wdls = np.array(self.wdls, dtype=np.float32)
        known = int((self.wdls >= 0).sum())
        print(f"  {known:,} samples carry a game result "
              f"({100*known/max(len(self.wdls),1):.0f}%)", flush=True)
        print(f"loaded {len(self.scores):,} positions in {time.time()-t0:.0f}s", flush=True)

    def __len__(self):
        return len(self.scores)

    def __getitem__(self, i):
        w = self.w_flat[self.w_off[i]:self.w_off[i+1]]
        b = self.b_flat[self.b_off[i]:self.b_off[i+1]]
        return w, b, int(self.stm[i]), float(self.scores[i]), float(self.wdls[i])


def collate(batch):
    """Build dense one-hot batches.

    A position has at most 32 pieces, so these rows are very sparse — but a
    batch is only (batch x 768) floats, and dense works on every backend
    (MPS has no sparse COO support) while being faster than sparse matmul at
    this width.
    """
    n = len(batch)
    W = torch.zeros(n, INPUT)
    B = torch.zeros(n, INPUT)
    stms = torch.empty(n, dtype=torch.long)
    scores = torch.empty(n, dtype=torch.float32)
    wdls = torch.empty(n, dtype=torch.float32)
    for i, (w, b, stm, sc, wd) in enumerate(batch):
        W[i, torch.from_numpy(w.astype('int64'))] = 1.0
        B[i, torch.from_numpy(b.astype('int64'))] = 1.0
        stms[i] = stm
        scores[i] = sc
        wdls[i] = wd
    return W, B, stms, scores, wdls


class NNUE(nn.Module):
    def __init__(self):
        super().__init__()
        self.ft = nn.Linear(INPUT, HIDDEN)
        self.out = nn.Linear(HIDDEN * 2, 1)
        # Init wide enough that clipped-ReLU activations actually occupy
        # [0,1]. Too small and every quantized weight rounds toward zero,
        # which collapses the net to its bias after export.
        nn.init.uniform_(self.ft.weight, -0.4, 0.4)
        nn.init.zeros_(self.ft.bias)
        nn.init.uniform_(self.out.weight, -0.2, 0.2)
        nn.init.zeros_(self.out.bias)

    def forward(self, W, B, stm):
        aw = self.ft(W)   # white-perspective accumulator
        ab = self.ft(B)   # black-perspective accumulator
        # Order the pair as [side-to-move, opponent] to match inference.
        stm = stm.unsqueeze(1).float()
        us = aw * (1 - stm) + ab * stm
        them = ab * (1 - stm) + aw * stm
        x = torch.cat([us, them], dim=1)
        x = torch.clamp(x, 0.0, 1.0)          # clipped ReLU, scaled to [0,1]
        # Output is a logit in units of SCALE centipawns, matching
        # nnue::evaluate's final division.
        return self.out(x).squeeze(1)


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
    ftw = model.ft.weight.detach().cpu().numpy()      # (HIDDEN, INPUT)
    ftb = model.ft.bias.detach().cpu().numpy()
    ow = model.out.weight.detach().cpu().numpy()[0]   # (HIDDEN*2,)
    ob = model.out.bias.detach().cpu().numpy()[0]

    q_ftw = np.clip(np.round(ftw * QA), -32768, 32767).astype(np.int16)
    q_ftb = np.clip(np.round(ftb * QA), -32768, 32767).astype(np.int16)
    q_ow  = np.clip(np.round(ow * QB), -32768, 32767).astype(np.int16)
    # Bias shares the activation*weight scale so it adds directly to the sum.
    q_ob  = np.int32(round(float(ob) * QA * QB))

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
        # ft_weight is stored [INPUT][HIDDEN], so transpose from torch's layout.
        f.write(q_ftw.T.tobytes())
        f.write(q_ftb.tobytes())
        f.write(q_ow.tobytes())
        f.write(struct.pack('<h', int(np.clip(q_ob, -32768, 32767))))
    size = os.path.getsize(path)
    expected = (INPUT * HIDDEN + HIDDEN + HIDDEN * 2 + 1) * 2
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
    # Anchor: predicted logit should equal score/SCALE, clamped to the range
    # where the sigmoid is informative.
    anchor_t = torch.clamp(scores / SCALE, -4.0, 4.0)
    anchor_loss = F.mse_loss(pred_logit, anchor_t)
    return wdl_loss + 0.05 * anchor_loss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--out', default='net.nnue')
    ap.add_argument('--epochs', type=int, default=20)
    ap.add_argument('--batch', type=int, default=16384)
    ap.add_argument('--lr', type=float, default=1e-3)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--workers', type=int, default=4)
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
        for W, B, stm, sc, wd in dl:
            W, B = W.to(dev), B.to(dev)
            stm, sc, wd = stm.to(dev), sc.to(dev), wd.to(dev)
            pred = model(W, B, stm)
            loss = nnue_loss(pred, sc, wd, a.lam)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step(); sched.step()
            tot += loss.item(); nb += 1

        model.eval()
        vtot, vnb = 0.0, 0
        with torch.no_grad():
            for W, B, stm, sc, wd in vdl:
                W, B = W.to(dev), B.to(dev)
                stm, sc, wd = stm.to(dev), sc.to(dev), wd.to(dev)
                loss = nnue_loss(model(W, B, stm), sc, wd, a.lam)
                vtot += loss.item(); vnb += 1
        val = vtot / max(vnb, 1)
        print(f"epoch {ep+1}/{a.epochs}  train {tot/max(nb,1):.5f}  "
              f"val {val:.5f}  {time.time()-t0:.0f}s", flush=True)

        # Keep the best net, not the last — late epochs can overfit.
        if val < best_val:
            best_val = val
            quantize(model, a.out)
            torch.save(model.state_dict(), a.out + '.pt')

    print(f"best val loss {best_val:.5f}")


if __name__ == '__main__':
    main()
