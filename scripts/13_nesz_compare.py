"""Noise floor (NESZ) of Sentinel-1A, 1C and 1D IW GRD products, from their own annotation files.

NESZ is the thermal noise expressed as sigma0: N / A^2, with N from the noise LUTs (range and
azimuth vectors) and A from the sigmaNought calibration LUT, sampled on 5 azimuth lines per scene
and binned by incidence angle. A lower NESZ makes calm sea darker in VH, where sea clutter sits
near the noise floor, and shifts what a detector trained on another satellite sees.

Scenes: Sentinel-1A from the AI2 training sample (Southeast Asia, 2022), Sentinel-1C and 1D from the
South China Sea search (last 90 days). Reads annotation XML only (a few MB per scene).
Outputs: data/nesz_by_satellite.csv (median and quartiles per satellite, polarisation and 1 degree
bin), data/nesz_scenes.csv (per-scene medians), docs/figures/nesz_by_satellite.png
Usage: python scripts/13_nesz_compare.py [--per-sat 20]
"""

import argparse
from concurrent.futures import ThreadPoolExecutor

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from darkvessel.config import DATA_DIR, FIG_DIR
from darkvessel.s1.grd import GRDScene
from darkvessel.viz.demo import _scene_path
from darkvessel.viz.style import INK, INK_2, MUTED, apply_matplotlib_style

ap = argparse.ArgumentParser()
ap.add_argument("--per-sat", type=int, default=20)
ap.add_argument("--seed", type=int, default=20261002)
args = ap.parse_args()

ai2 = pd.read_csv(DATA_DIR / "ml" / "scene_sample.csv", parse_dates=["scene_time_utc"])
s1a = ai2[(ai2.mission == "S1A") & (ai2.region == "sea_asia") & (ai2.scene_time_utc.dt.year == 2022)]
s1a = s1a.sample(min(args.per_sat, len(s1a)), random_state=args.seed).product_id.tolist()
scs = pd.read_csv(DATA_DIR / "s1_scenes.csv")
scs = scs[scs.aoi_overlap_km2 >= 2000]
picks = {"S1A": s1a}
for m in ("S1C", "S1D"):
    sub = scs[scs.product_id.str.startswith(m)]
    picks[m] = sub.sample(min(args.per_sat, len(sub)), random_state=args.seed).product_id.tolist()


def nesz(pid: str) -> pd.DataFrame | None:
    try:
        s = GRDScene(_scene_path(pid))
        H, W = s.shape
        rows = np.linspace(0.1, 0.9, 5) * H
        cols = np.arange(200, W - 200, 50, dtype=float)
        RR, CC = np.meshgrid(rows, cols, indexing="ij")
        inc = s.geocoder.incidence(RR.ravel(), CC.ravel()).reshape(RR.shape)
        out = []
        for pol in ("VV", "VH"):
            n = s.noise(pol).grid(rows, cols)
            a = s.calibration(pol).grid(rows, cols)
            with np.errstate(divide="ignore", invalid="ignore"):
                db = 10 * np.log10(n / a ** 2)
            ok = np.isfinite(db) & np.isfinite(inc) & (n > 0)
            out.append(pd.DataFrame({"product_id": pid, "mission": pid[:3], "pol": pol, "inc": inc[ok], "nesz_db": db[ok]}))
        return pd.concat(out)
    except Exception as e:  # keep going; report
        print("failed", pid, repr(e)[:160], flush=True)
        return None


with ThreadPoolExecutor(6) as ex:
    parts = [p for p in ex.map(nesz, [pid for v in picks.values() for pid in v]) if p is not None]
df = pd.concat(parts, ignore_index=True)
df = df[(df.inc >= 29) & (df.inc <= 46)]
df["inc_bin"] = np.floor(df.inc).astype(int) + 0.5
summ = (df.groupby(["mission", "pol", "inc_bin"]).nesz_db
        .agg(median="median", q25=lambda x: x.quantile(0.25), q75=lambda x: x.quantile(0.75), n="size")
        .reset_index().round(2))
summ.to_csv(DATA_DIR / "nesz_by_satellite.csv", index=False)
per_scene = df.groupby(["mission", "product_id", "pol"]).nesz_db.median().unstack("pol").round(2).reset_index()
per_scene.to_csv(DATA_DIR / "nesz_scenes.csv", index=False)
print(per_scene.groupby("mission")[["VV", "VH"]].agg(["median", "count"]).round(2))
for pol in ("VV", "VH"):
    piv = summ[summ.pol == pol].pivot(index="inc_bin", columns="mission", values="median")
    print(pol, "\n", piv.round(1).to_string())

# Figure: one panel per polarisation, same y scale; colour follows the satellite across the project
apply_matplotlib_style()
COL = {"S1D": "#2a78d6", "S1C": "#eb6834", "S1A": "#1baf7a"}
NAME = {"S1A": "Sentinel-1A (2022)", "S1C": "Sentinel-1C (2026)", "S1D": "Sentinel-1D (2026)"}
fig, axes = plt.subplots(1, 2, figsize=(11, 4.8), sharey=True)
fig.subplots_adjust(left=0.08, right=0.98, top=0.76, bottom=0.18, wspace=0.08)
for ax, pol in zip(axes, ("VV", "VH")):
    for m in ("S1A", "S1C", "S1D"):
        d = summ[(summ.pol == pol) & (summ.mission == m)]
        if d.empty:
            continue
        ax.fill_between(d.inc_bin, d.q25, d.q75, color=COL[m], alpha=0.15, linewidth=0)
        ax.plot(d.inc_bin, d["median"], color=COL[m], linewidth=2, label=NAME[m])
        ax.text(d.inc_bin.iloc[-1] + 0.3, d["median"].iloc[-1], m, color=INK_2, fontsize=9, va="center")
    ax.set_title(pol, loc="left", fontsize=11, color=INK)
    ax.set_xlabel("Incidence angle (degrees)", fontsize=9)
    ax.set_xlim(29, 47.5)
    ax.grid(True, axis="y")
axes[0].set_ylabel("NESZ (dB)", fontsize=9)
axes[0].legend(loc="lower left", fontsize=8.5, frameon=False)
n = {m: len(v) for m, v in picks.items()}
fig.text(0.08, 0.95, "Noise floor of Sentinel-1A, 1C and 1D, from the products' own annotation",
         fontsize=13, color=INK, fontweight="bold", va="top")
fig.text(0.08, 0.895, f"Median NESZ per 1 degree of incidence, band = middle half of samples.\nScenes: 1A {n['S1A']} "
         f"(Southeast Asia, 2022), 1C {n['S1C']} and 1D {n['S1D']} (South China Sea, Jul to Oct 2026).",
         fontsize=9, color=INK_2, va="top")
fig.text(0.08, 0.02, "Contains modified Copernicus Sentinel data 2022 and 2026 (AWS Open Data mirror). "
         "NESZ = noise LUT / sigmaNought LUT squared.", fontsize=7.5, color=MUTED)
FIG_DIR.mkdir(parents=True, exist_ok=True)
fig.savefig(FIG_DIR / "nesz_by_satellite.png", dpi=150)
print("wrote", FIG_DIR / "nesz_by_satellite.png")
