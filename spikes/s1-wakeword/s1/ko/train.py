"""Train the keyword classifier head on frozen openWakeWord embeddings and export an ONNX file.

The architecture matches the shipped hey_jarvis head (Flatten -> 1536x128 -> LayerNorm -> ReLU -> 128x128 ->
LayerNorm -> ReLU -> 128x1 -> Sigmoid), so the hub swaps only the classifier file.

Usage (on the training host): python -m s1.ko.train --root ~/dev/cozy-wakeword --out models/jarvis_ko.onnx
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from loguru import logger
from torch import nn

from s1.ko.frontend import CLF_FRAMES, EMBED_DIM

GROUP_WEIGHT = {"neg_conf": 4.0, "neg_fleurs": 1.0, "neg_fleurs_tv": 1.5, "neg_tts": 1.0, "neg_esc": 0.7, "neg_silence": 0.3}
WARMUP_ROWS = 5


class Head(nn.Module):
    def __init__(self, dim: int = 128, dropout: float = 0.1) -> None:
        super().__init__()
        self.model = nn.Sequential(
            nn.Flatten(),
            nn.Linear(CLF_FRAMES * EMBED_DIM, dim),
            nn.LayerNorm(dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(dim, dim),
            nn.LayerNorm(dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(dim, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.model(x)


class Windows:
    """Random access to classifier windows over concatenated clips; every clip is zero padded on the left."""

    def __init__(self, npz: dict) -> None:
        offsets = npz["offsets"]
        emb = npz["emb"]
        pad = np.zeros((CLF_FRAMES - 1, EMBED_DIM), dtype=np.float16)
        parts, ends, clip_of = [], [], []
        cursor = 0
        for c in range(len(offsets) - 1):
            rows = emb[offsets[c] : offsets[c + 1]]
            parts += [pad, rows]
            ends.append(np.arange(rows.shape[0]) + cursor + CLF_FRAMES - 1)
            clip_of.append(np.full(rows.shape[0], c))
            cursor += CLF_FRAMES - 1 + rows.shape[0]
        self.emb = np.concatenate(parts)
        self.end = np.concatenate(ends)
        self.clip = np.concatenate(clip_of)
        self.labels = npz["labels"]
        self.group = npz["group"][self.clip]
        self.row_in_clip = np.concatenate([np.arange(offsets[c + 1] - offsets[c]) for c in range(len(offsets) - 1)])

    def batch(self, rows: np.ndarray) -> torch.Tensor:
        idx = self.end[rows][:, None] + np.arange(-(CLF_FRAMES - 1), 1)[None, :]
        return torch.from_numpy(self.emb[idx].astype(np.float32))


def sample_weights(win: Windows, extra: np.ndarray | None) -> np.ndarray:
    w = np.array([GROUP_WEIGHT.get(g, 1.0) for g in win.group], dtype=np.float64)
    if extra is not None:
        w = w * extra
    w[(win.labels != 0) | (win.row_in_clip < WARMUP_ROWS)] = 0.0
    return w / w.sum()


def train_round(win: Windows, model: Head, steps: int, extra: np.ndarray | None, seed: int, lr: float, n_pos: int, noise: float, wd: float) -> None:
    rng = np.random.default_rng(seed)
    pos = np.flatnonzero(win.labels == 1)
    neg_p = sample_weights(win, extra)
    neg = np.arange(len(neg_p))
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=wd)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=steps)
    loss_fn = nn.BCELoss()
    model.train()
    for step in range(steps):
        rows = np.concatenate([rng.choice(pos, n_pos), rng.choice(neg, 1024 - n_pos, p=neg_p)])
        y = torch.cat([torch.ones(n_pos, 1), torch.zeros(1024 - n_pos, 1)])
        x = win.batch(rows)
        x = x + noise * torch.randn_like(x) * x.std()
        loss = loss_fn(model(x), y)
        opt.zero_grad()
        loss.backward()
        opt.step()
        sched.step()
        if step % 1000 == 0:
            logger.info("step {}/{} loss {:.4f}", step, steps, loss.item())


@torch.no_grad()
def score_all(win: Windows, model: Head, rows: np.ndarray) -> np.ndarray:
    model.eval()
    out = [model(win.batch(rows[i : i + 8192])).squeeze(1).numpy() for i in range(0, len(rows), 8192)]
    return np.concatenate(out)


def export(model: Head, path: Path) -> None:
    model.eval()
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(model, torch.zeros(1, CLF_FRAMES, EMBED_DIM), str(path), input_names=["x.1"], output_names=["score"], opset_version=13, dynamo=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=6000)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--pos-per-batch", type=int, default=256)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--noise", type=float, default=0.02)
    parser.add_argument("--wd", type=float, default=0.01)
    parser.add_argument("--mine-weight", type=float, default=8.0)
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()
    torch.manual_seed(args.seed)
    torch.set_num_threads(args.threads)
    start = time.time()
    root = args.root.expanduser()
    win = Windows(dict(np.load(root / "feat/train.npz")))
    logger.info("rows {} pos {} neg {}", len(win.labels), int((win.labels == 1).sum()), int((win.labels == 0).sum()))
    model = Head(dropout=args.dropout)
    extra = None
    for rnd in range(args.rounds):
        train_round(win, model, args.steps, extra, args.seed + rnd, 1e-3, args.pos_per_batch, args.noise, args.wd)
        neg = np.flatnonzero((win.labels == 0) & (win.row_in_clip >= WARMUP_ROWS))
        scores = score_all(win, model, neg)
        mined = np.zeros(len(win.labels))
        mined[neg[scores > 0.2]] = args.mine_weight
        extra = 1.0 + mined
        logger.info("round {}: train negative rows > 0.2: {} of {}, > 0.5: {}", rnd, int((scores > 0.2).sum()), len(neg), int((scores > 0.5).sum()))
    export(model, args.out.expanduser() if args.out.is_absolute() else root / args.out)
    seconds = round(time.time() - start, 1)
    (root / f"{args.out.stem}.train.json").write_text(json.dumps({"seconds": seconds, "torch": torch.__version__, **{k: str(v) for k, v in vars(args).items()}}))
    logger.info("trained in {} s", seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
