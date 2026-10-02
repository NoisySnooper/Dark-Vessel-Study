"""The verifier CNN: 2-channel (VV, VH dB) 64 x 64 chip -> one logit (vessel vs clutter).

Small VGG-style net trained from scratch (no pretrained weights are reachable from this
environment, and ImageNet features are a poor match for two-channel SAR intensity anyway).
Four blocks of two 3 x 3 convolutions with BatchNorm and ReLU, each followed by 2 x 2 max
pooling (64 -> 4 px), global average pooling, dropout, one linear layer. About 1.2 M
parameters with width 32.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch import nn

MODEL_ID = "verifier_v0"
CHIP_PX = 64


def _block(cin: int, cout: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(cin, cout, 3, padding=1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
        nn.Conv2d(cout, cout, 3, padding=1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
        nn.MaxPool2d(2),
    )


class VerifierCNN(nn.Module):
    def __init__(self, in_ch: int = 2, width: int = 32, n_blocks: int = 4, dropout: float = 0.3):
        super().__init__()
        widths = [width * 2**i for i in range(n_blocks)]
        layers, c = [], in_ch
        for w in widths:
            layers.append(_block(c, w))
            c = w
        self.features = nn.Sequential(*layers)
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.drop = nn.Dropout(dropout)
        self.head = nn.Linear(c, 1)
        self.cfg = {"in_ch": in_ch, "width": width, "n_blocks": n_blocks, "dropout": dropout}

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        x = self.pool(x).flatten(1)
        return self.head(self.drop(x)).squeeze(1)

    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


def normalise_chips(chips: np.ndarray, mean: np.ndarray, std: np.ndarray, fill_sigma: float = -2.0) -> np.ndarray:
    """(n, 2, H, W) dB chips (float16/32, NaN = no data) -> standardised float32.

    NaN is replaced by `fill_sigma` standard deviations below the channel mean (dark, like
    the swath edge it comes from).
    """
    x = chips.astype(np.float32)
    mean = np.asarray(mean, np.float32).reshape(1, -1, 1, 1)
    std = np.asarray(std, np.float32).reshape(1, -1, 1, 1)
    x = (x - mean) / std
    return np.where(np.isfinite(x), x, fill_sigma).astype(np.float32)


def chip_stats(chips: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-channel mean and std in dB over finite pixels."""
    x = chips.astype(np.float32)
    mean = np.array([np.nanmean(x[:, k]) for k in range(x.shape[1])])
    std = np.array([np.nanstd(x[:, k]) for k in range(x.shape[1])])
    return mean, std


def save_model(path: str | Path, model: VerifierCNN, meta: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "cfg": model.cfg, "meta": meta}, path)


def load_model(path: str | Path) -> tuple[VerifierCNN, dict]:
    ck = torch.load(Path(path), map_location="cpu", weights_only=False)
    model = VerifierCNN(**ck["cfg"])
    model.load_state_dict(ck["state_dict"])
    model.eval()
    return model, ck["meta"]


@torch.no_grad()
def predict_proba(model: VerifierCNN, chips: np.ndarray, meta: dict, batch: int = 256,
                  tta: bool = True) -> np.ndarray:
    """Vessel probability per chip. With `tta`, the mean over the 8 flip/rotation views."""
    model.eval()
    x = normalise_chips(chips, meta["norm_mean"], meta["norm_std"])
    out = np.zeros(len(x), np.float32)
    for i in range(0, len(x), batch):
        xb = torch.from_numpy(x[i : i + batch])
        views = [xb]
        if tta:
            views = [torch.rot90(xb, k, (2, 3)) for k in range(4)]
            views += [torch.flip(v, (3,)) for v in views]
        p = torch.stack([torch.sigmoid(model(v)) for v in views]).mean(0)
        out[i : i + batch] = p.numpy()
    return out
