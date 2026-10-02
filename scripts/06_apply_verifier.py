"""Apply the CNN verifier to the baseline detections of the Sentinel-1D Ca Mau scene.

Reads data/detections_baseline.gpkg (layer detections_baseline_4326; never modified), cuts a
64 x 64 px VV/VH chip at every detection's row/col with GRDScene (denoise=False, same as
training), scores it with data/models/verifier_v0.pt and writes
  data/detections_ml.gpkg   layers detections_verified_4326 / detections_verified_utm48n
with every baseline column kept (including the caveat) plus cnn_score, cnn_vessel,
cnn_threshold, cnn_model_id. Summary by baseline confidence class: data/ml/apply_summary.json.
Figure: docs/figures/ml_1d_chips.png.

Usage: python scripts/06_apply_verifier.py [--scene GRD/...] [--model data/models/verifier_v0.pt] [--band 1024]
"""

import argparse
import hashlib
import json
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from rasterio.windows import Window

from darkvessel.config import DATA_DIR, FIG_DIR
from darkvessel.io import write_dual_crs
from darkvessel.ml.chips import CHIP_HALF, chips_db
from darkvessel.ml.model import MODEL_ID, load_model, predict_proba
from darkvessel.s1.grd import GRDScene

DEFAULT_SCENE = "GRD/2026/9/29/IW/DV/S1D_IW_GRDH_1SDV_20260929T111023_20260929T111053_004792_008FC8_A5CA"
ML_DIR = DATA_DIR / "ml"


def log(msg, t0):
    print(f"[{time.time() - t0:7.1f}s] {msg}", flush=True)


def chips_for_detections(scene: GRDScene, rows: np.ndarray, cols: np.ndarray, band: int = 1024, half: int = CHIP_HALF,
                         t0: float = 0.0) -> np.ndarray:
    """Chips at scene pixel (row, col), read band by band so memory stays small."""
    H, W = scene.shape
    out = np.full((len(rows), 2, 2 * half, 2 * half), np.nan, np.float16)
    order = np.argsort(rows)
    for r in range(0, H, band):
        sel = order[(rows[order] >= r) & (rows[order] < r + band)]
        if len(sel) == 0:
            continue
        r0, r1 = max(0, r - half), min(H, r + band + half)
        c0 = max(0, int(cols[sel].min()) - half - 1)
        c1 = min(W, int(cols[sel].max()) + half + 2)
        win = Window(c0, r0, c1 - c0, r1 - r0)
        sigma = {p: scene.read_sigma0(p, win, denoise=False) for p in ("VV", "VH")}
        out[sel] = chips_db(sigma, rows[sel], cols[sel], r0, c0, half)
        log(f"band rows {r0}-{r1}: {len(sel)} chips", t0)
    return out


def gallery(chips, det, out_png, per_row=8):
    """VV chips of CNN-accepted and CNN-rejected detections for each baseline class."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from darkvessel.viz.style import INK, INK_2, apply_matplotlib_style

    apply_matplotlib_style()
    groups = []
    for cls in ("high", "medium", "low", "fixed"):
        for verdict in (True, False):
            q = det[(det.confidence == cls) & (det.cnn_vessel == verdict)]
            if len(q):
                q = q.sample(min(per_row, len(q)), random_state=0)
                groups.append((f"{cls}, CNN {'vessel' if verdict else 'clutter'} (n={int(((det.confidence == cls) & (det.cnn_vessel == verdict)).sum())})", q))
    fig, axs = plt.subplots(len(groups), per_row, figsize=(per_row * 1.5, len(groups) * 1.65 + 0.8), squeeze=False)
    for i, (name, q) in enumerate(groups):
        for j in range(per_row):
            ax = axs[i, j]
            ax.set_xticks([]), ax.set_yticks([])
            for s in ax.spines.values():
                s.set_visible(False)
            if j >= len(q):
                continue
            d = q.iloc[j]
            ax.imshow(chips[d.name, 0].astype(np.float32), cmap="gray", vmin=-25, vmax=5, interpolation="nearest")
            ax.set_title(f"{d.cnn_score:.2f}", fontsize=7, color=INK_2, pad=2)
        axs[i, 0].set_ylabel(name.replace(" (", "\n("), fontsize=7, color=INK_2, rotation=0, ha="right", va="center", labelpad=6)
    fig.suptitle("Sentinel-1D Ca Mau scene: VV chips (640 m) by baseline class and CNN verdict, title = CNN score",
                 x=0.02, ha="left", fontsize=11, color=INK, fontweight="bold")
    fig.text(0.02, 0.005, "Contains modified Copernicus Sentinel data 2026. Model trained on Sentinel-1A/1B 2020-2022 labels; "
             "no Sentinel-1D ground truth. Dark = no AIS match, not evidence of wrongdoing.", fontsize=7, color=INK_2)
    fig.tight_layout(rect=(0.14, 0.03, 1, 0.95))
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default=DEFAULT_SCENE)
    ap.add_argument("--model", default=str(DATA_DIR / "models" / "verifier_v0.pt"))
    ap.add_argument("--baseline", default=str(DATA_DIR / "detections_baseline.gpkg"))
    ap.add_argument("--band", type=int, default=1024)
    args = ap.parse_args()
    t0 = time.time()
    model, meta = load_model(args.model)
    thr = float(meta["threshold"])
    digest = hashlib.sha256(open(args.model, "rb").read()).hexdigest()[:8]
    model_id = f"{MODEL_ID}_{digest}"
    det = gpd.read_file(args.baseline, layer="detections_baseline_4326", engine="pyogrio")
    log(f"baseline detections {len(det)}; model {model_id} threshold {thr:.3f}", t0)
    scene = GRDScene(args.scene)
    # scene-level fields live in data/baseline_run_summary.json (the lean detection layers carry only scene_id)
    summary_path = DATA_DIR / "baseline_run_summary.json"
    base = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    scene_id = det.scene_id.iloc[0] if "scene_id" in det.columns else base.get("scene_id", scene.product_id)
    assert scene_id == scene.product_id, "baseline layer is for a different scene"
    platform = base.get("platform") or f"SENTINEL-1{scene.meta['mission'][-1]}"
    acq_utc = base.get("acq_utc") or scene.meta["start"].isoformat() + "+00:00"
    chips = chips_for_detections(scene, det.row.values.astype(float), det.col.values.astype(float), args.band, t0=t0)
    valid = np.isfinite(chips[:, :, CHIP_HALF - 4 : CHIP_HALF + 4, CHIP_HALF - 4 : CHIP_HALF + 4].astype(np.float32)).mean(axis=(1, 2, 3))
    score = predict_proba(model, chips, meta, tta=True)
    det["cnn_score"] = score.astype(float)
    det["cnn_vessel"] = score >= thr
    det["cnn_threshold"] = thr
    det["cnn_model_id"] = model_id
    det["cnn_chip_valid_frac"] = valid.astype(float)
    det["cnn_training_data"] = "AI2 Skylight S1A/S1B point labels 2020-2022 (Apache-2.0); no S1D ground truth"
    assert "caveat" in det.columns, "the baseline layer lost its caveat column"
    out = DATA_DIR / "detections_ml.gpkg"
    if out.exists():
        out.unlink()
    layers = write_dual_crs(det, out, "detections_verified")
    log(f"wrote {out} layers {layers}", t0)

    by_class = {}
    for cls, g in det.groupby("confidence"):
        by_class[cls] = {"n": int(len(g)), "cnn_vessel": int(g.cnn_vessel.sum()), "cnn_vessel_frac": round(float(g.cnn_vessel.mean()), 3),
                         "score_median": round(float(g.cnn_score.median()), 3),
                         "score_q10_q90": [round(float(g.cnn_score.quantile(0.1)), 3), round(float(g.cnn_score.quantile(0.9)), 3)]}
    by_pol = {k: {"n": int(len(g)), "cnn_vessel": int(g.cnn_vessel.sum()), "cnn_vessel_frac": round(float(g.cnn_vessel.mean()), 3)}
              for k, g in det.groupby("pol_class")}
    summary = {"scene_id": scene.product_id, "platform": platform, "acq_utc": acq_utc,
               "model_id": model_id, "model_path": args.model, "threshold": thr, "n_detections": int(len(det)),
               "n_cnn_vessel": int(det.cnn_vessel.sum()), "by_confidence": by_class, "by_pol_class": by_pol,
               "vessel_candidates_baseline": int(det.confidence.isin(["high", "medium"]).sum()),
               "vessel_candidates_cnn": int(det.cnn_vessel.sum()),
               "chips_with_missing_centre_px": int((valid < 1).sum()), "runtime_s": round(time.time() - t0, 1),
               "caveat": det.caveat.iloc[0], "baseline_classes": base.get("classes")}
    ML_DIR.mkdir(parents=True, exist_ok=True)
    (ML_DIR / "apply_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps({k: v for k, v in summary.items() if k != "caveat"}, indent=2, default=str))
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    gallery(chips, det, FIG_DIR / "ml_1d_chips.png")
    log("done", t0)


if __name__ == "__main__":
    main()
