"""Scene and window selection for the training-set build.

Rules (all seeded, deterministic):
  scenes   every AI2 Sentinel-1 scene intersecting Southeast Asia (labels.SEA_BBOX), plus a
           sample of the rest stratified by 30 x 30 degree cell of the scene centre, allocated
           in proportion to each cell's labelled windows (at least one scene per cell)
  windows  per scene at most `cap_labelled` windows that hold labels and `cap_empty` without
  splits   `test_frac` of the selected scenes per region are held out whole (every window of
           those scenes is 'test'); on the remaining scenes AI2 '-val' windows are 'val' and
           '-train' windows are 'train', so AI2's own split is never crossed in training
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def stratum(lon: pd.Series, lat: pd.Series, size_deg: float = 30.0) -> pd.Series:
    cx = np.floor(lon / size_deg) * size_deg
    cy = np.floor(lat / size_deg) * size_deg
    return cx.astype(int).astype(str) + "_" + cy.astype(int).astype(str)


def select_scenes(windows: pd.DataFrame, n_other: int = 90, seed: int = 20261002) -> pd.DataFrame:
    """One row per selected scene with region, stratum and the number of windows available."""
    rng = np.random.default_rng(seed)
    per = (windows.groupby(["product_id", "region", "mission"])
           .agg(n_windows=("window_id", "size"), n_labelled=("n_labels", lambda s: int((s > 0).sum())),
                n_labels=("n_labels", "sum"), lon=("west", "mean"), lat=("south", "mean"),
                scene_time_utc=("scene_time_utc", "first")).reset_index())
    per["stratum"] = stratum(per.lon, per.lat)
    sea = per[per.region == "sea_asia"].copy()
    sea["selection"] = "all_sea_asia"
    other = per[(per.region == "other") & (per.n_labelled > 0)].copy()
    weights = other.groupby("stratum").n_labelled.sum()
    alloc = np.maximum(1, np.floor(weights / weights.sum() * n_other)).astype(int)
    # trim or top up to n_other by largest remainder
    while alloc.sum() > n_other:
        alloc[alloc.idxmax()] -= 1
    rem = (weights / weights.sum() * n_other - alloc).sort_values(ascending=False)
    for s in rem.index:
        if alloc.sum() >= n_other:
            break
        alloc[s] += 1
    picks = []
    for s, k in alloc.items():
        pool = other[other.stratum == s]
        k = min(int(k), len(pool))
        picks.append(pool.iloc[rng.permutation(len(pool))[:k]])
    other_sel = pd.concat(picks)
    other_sel["selection"] = "stratified_other"
    return pd.concat([sea, other_sel], ignore_index=True).sort_values("product_id").reset_index(drop=True)


def assign_scene_splits(scenes: pd.DataFrame, test_frac: float = 0.2, seed: int = 20261002) -> pd.DataFrame:
    """Hold out `test_frac` of scenes per region (whole scenes)."""
    rng = np.random.default_rng(seed + 1)
    out = scenes.copy()
    out["scene_split"] = "trainval"
    for region, d in out.groupby("region"):
        idx = d.index.values
        n_test = int(round(test_frac * len(idx)))
        out.loc[rng.choice(idx, n_test, replace=False), "scene_split"] = "test"
    return out


def select_windows(windows: pd.DataFrame, scenes: pd.DataFrame, cap_labelled: int = 12, cap_empty: int = 3,
                   seed: int = 20261002) -> pd.DataFrame:
    """Windows of the selected scenes, capped per scene, with the final 'use' split."""
    rng = np.random.default_rng(seed + 2)
    keep = []
    scene_split = scenes.set_index("product_id").scene_split
    for pid, d in windows[windows.product_id.isin(scenes.product_id)].groupby("product_id"):
        lab = d[d.n_labels > 0]
        emp = d[d.n_labels == 0]
        if len(lab) > cap_labelled:
            lab = lab.iloc[rng.permutation(len(lab))[:cap_labelled]]
        if len(emp) > cap_empty:
            emp = emp.iloc[rng.permutation(len(emp))[:cap_empty]]
        keep.append(pd.concat([lab, emp]))
    sel = pd.concat(keep, ignore_index=True)
    sel["scene_split"] = sel.product_id.map(scene_split)
    sel["use"] = np.select([sel.scene_split == "test", sel.split_group == "val", sel.split_group == "train"],
                           ["test", "val", "train"], default="train")
    return sel.sort_values(["product_id", "window_id"]).reset_index(drop=True)
