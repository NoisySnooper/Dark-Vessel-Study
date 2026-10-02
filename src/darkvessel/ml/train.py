"""Verifier training: chip loading, augmentation, balanced sampling, early stopping.

Augmentation: the 8 dihedral views (flips and 90 degree rotations; SAR chips have no
preferred orientation at this scale), a random shift of up to 2 px (centroid jitter), and a
per-channel additive offset of up to 1 dB plus a global gain of up to 0.5 dB (calibration and
sea-state differences between scenes and, later, between satellites).
Class imbalance: a weighted sampler draws positives at a fixed fraction of each batch
(`pos_fraction`), so no loss re-weighting is needed.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import average_precision_score, roc_auc_score
from torch import nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

from darkvessel.ml.model import VerifierCNN, chip_stats, normalise_chips


def load_chips(index: pd.DataFrame, chip_dir: Path, array: str = "chips", index_col: str = "chip_index") -> np.ndarray:
    """Chips for the rows of `index` (needs product_id and chip index columns), as float16."""
    out = np.zeros((len(index), 2, 64, 64), np.float16)
    pos = pd.Series(np.arange(len(index)), index=index.index)
    for pid, grp in index.groupby("product_id"):
        with np.load(Path(chip_dir) / f"{pid}.npz") as z:
            arr = z[array]
        out[pos[grp.index].values] = arr[grp[index_col].values]
    return out


class ChipDataset(Dataset):
    def __init__(self, chips: np.ndarray, y: np.ndarray, mean, std, augment: bool = False, seed: int = 0):
        self.chips, self.y = chips, y.astype(np.float32)
        self.mean, self.std = np.asarray(mean, np.float32), np.asarray(std, np.float32)
        self.augment = augment
        self.rng = np.random.default_rng(seed)

    def __len__(self):
        return len(self.y)

    def __getitem__(self, i):
        x = normalise_chips(self.chips[i : i + 1], self.mean, self.std)[0]
        if self.augment:
            x = self._augment(x)
        return torch.from_numpy(np.ascontiguousarray(x)), torch.tensor(self.y[i])

    def _augment(self, x: np.ndarray) -> np.ndarray:
        k = int(self.rng.integers(4))
        x = np.rot90(x, k, axes=(1, 2))
        if self.rng.random() < 0.5:
            x = x[:, :, ::-1]
        dy, dx = self.rng.integers(-2, 3, size=2)
        if dy or dx:
            pad = np.pad(x, ((0, 0), (2, 2), (2, 2)), mode="edge")
            x = pad[:, 2 + dy : 2 + dy + x.shape[1], 2 + dx : 2 + dx + x.shape[2]]
        # offsets in dB converted to standardised units
        off = self.rng.uniform(-1.0, 1.0, size=2) / self.std
        gain = self.rng.uniform(-0.5, 0.5) / self.std
        x = x + (off + gain).reshape(2, 1, 1).astype(np.float32)
        return x


def make_loader(ds: ChipDataset, batch: int, pos_fraction: float | None, seed: int, shuffle: bool) -> DataLoader:
    if pos_fraction is None:
        return DataLoader(ds, batch_size=batch, shuffle=shuffle, num_workers=0)
    y = ds.y
    n_pos, n_neg = max(1, int(y.sum())), max(1, int((1 - y).sum()))
    w = np.where(y > 0.5, pos_fraction / n_pos, (1 - pos_fraction) / n_neg)
    g = torch.Generator().manual_seed(seed)
    sampler = WeightedRandomSampler(torch.as_tensor(w, dtype=torch.double), num_samples=len(y), replacement=True, generator=g)
    return DataLoader(ds, batch_size=batch, sampler=sampler, num_workers=0)


@torch.no_grad()
def predict_logits(model: nn.Module, loader: DataLoader) -> tuple[np.ndarray, np.ndarray]:
    model.eval()
    out, ys = [], []
    for xb, yb in loader:
        out.append(model(xb).numpy())
        ys.append(yb.numpy())
    return np.concatenate(out), np.concatenate(ys)


def val_metrics(logits: np.ndarray, y: np.ndarray) -> dict:
    p = 1 / (1 + np.exp(-logits))
    loss = float(nn.functional.binary_cross_entropy_with_logits(torch.from_numpy(logits), torch.from_numpy(y.astype(np.float32))))
    return {"loss": loss, "ap": float(average_precision_score(y, p)) if y.sum() else float("nan"),
            "auc": float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else float("nan")}


def best_f1_threshold(p: np.ndarray, y: np.ndarray, n_extra_fn: int = 0) -> tuple[float, dict]:
    """Threshold maximising F1 (candidate precision, label recall with `n_extra_fn` CFAR misses added)."""
    order = np.argsort(-p)
    ps, ys = p[order], y[order]
    tp = np.cumsum(ys)
    fp = np.cumsum(1 - ys)
    fn = ys.sum() - tp + n_extra_fn
    prec = tp / np.maximum(tp + fp, 1)
    rec = tp / np.maximum(tp + fn, 1)
    f1 = 2 * prec * rec / np.maximum(prec + rec, 1e-9)
    k = int(np.argmax(f1))
    thr = float(ps[k])
    return thr, {"precision": float(prec[k]), "recall": float(rec[k]), "f1": float(f1[k])}


def train_verifier(train_chips, y_train, val_chips, y_val, *, width: int = 24, epochs: int = 30, batch: int = 128,
                   lr: float = 1e-3, weight_decay: float = 1e-4, pos_fraction: float = 0.33, patience: int = 6,
                   threads: int = 3, seed: int = 0, log=print) -> tuple[VerifierCNN, dict, pd.DataFrame]:
    """Train from scratch with early stopping on validation average precision."""
    torch.manual_seed(seed)
    np.random.seed(seed)
    torch.set_num_threads(threads)
    mean, std = chip_stats(train_chips[: min(len(train_chips), 20000)])
    tr = ChipDataset(train_chips, y_train, mean, std, augment=True, seed=seed)
    va = ChipDataset(val_chips, y_val, mean, std, augment=False)
    tr_loader = make_loader(tr, batch, pos_fraction, seed, shuffle=True)
    va_loader = make_loader(va, 256, None, seed, shuffle=False)
    model = VerifierCNN(width=width)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=epochs * len(tr_loader), pct_start=0.15)
    loss_fn = nn.BCEWithLogitsLoss()
    rows, best, best_state, bad = [], -np.inf, None, 0
    t0 = time.time()
    for ep in range(1, epochs + 1):
        model.train()
        tot, n = 0.0, 0
        for xb, yb in tr_loader:
            opt.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss) * len(yb)
            n += len(yb)
        logits, yv = predict_logits(model, va_loader)
        m = val_metrics(logits, yv)
        rows.append({"epoch": ep, "train_loss": tot / max(n, 1), "val_loss": m["loss"], "val_ap": m["ap"], "val_auc": m["auc"],
                     "lr": sched.get_last_lr()[0], "elapsed_s": round(time.time() - t0, 1)})
        log(f"epoch {ep:2d} train {tot / max(n, 1):.4f} val {m['loss']:.4f} AP {m['ap']:.4f} AUC {m['auc']:.4f} "
            f"[{time.time() - t0:.0f}s]")
        if m["ap"] > best + 1e-4:
            best, bad = m["ap"], 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            best_epoch = ep
        else:
            bad += 1
            if bad >= patience:
                log(f"early stop at epoch {ep}; best epoch {best_epoch} (val AP {best:.4f})")
                break
    model.load_state_dict(best_state)
    model.eval()
    meta = {"norm_mean": mean.tolist(), "norm_std": std.tolist(), "best_epoch": best_epoch, "best_val_ap": float(best),
            "epochs_run": len(rows), "width": width, "batch": batch, "lr": lr, "weight_decay": weight_decay,
            "pos_fraction": pos_fraction, "patience": patience, "seed": seed, "n_params": model.n_params(),
            "train_time_s": round(time.time() - t0, 1)}
    return model, meta, pd.DataFrame(rows)
