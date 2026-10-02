"""Train the CNN verifier on CFAR candidates and evaluate it on held-out scenes.

Usage: python scripts/05_train_verifier.py [--epochs 25] [--width 24] [--max-neg-ratio 5] [--threads 3]
Inputs:  data/ml/candidates.parquet, data/ml/cfar_misses.parquet, data/chips/*.npz
Outputs: data/models/verifier_v0.pt                 weights + normalisation + threshold (gitignored)
         data/ml/training_log.csv                   per-epoch losses and validation AP/AUC
         data/ml/metrics.json                       held-out metrics, CFAR-only vs CFAR+CNN
         data/ml/pr_curve_test.csv, data/ml/recall_by_length_test.csv, data/ml/test_scores.parquet
         docs/figures/ml_pr_curve.png, ml_recall_by_length.png, ml_training_curves.png
"""

import argparse
import json
import subprocess
import time

import numpy as np
import pandas as pd

from darkvessel.config import DATA_DIR, FIG_DIR, REPO_ROOT
from darkvessel.ml.evaluate import by_group, pr_curve, recall_by_length, system_metrics
from darkvessel.ml.model import MODEL_ID, predict_proba, save_model
from darkvessel.ml.train import best_f1_threshold, load_chips, train_verifier

ML_DIR = DATA_DIR / "ml"
CHIP_DIR = DATA_DIR / "chips"
MODEL_PATH = DATA_DIR / "models" / "verifier_v0.pt"


def log(msg, t0):
    print(f"[{time.time() - t0:7.1f}s] {msg}", flush=True)


def eval_labels(labels: pd.DataFrame, use: str) -> pd.DataFrame:
    """Labels that the detector could have found: inside the scene and on testable sea."""
    return labels[(labels.use == use) & labels.in_scene & labels.on_testable_sea].copy()


def label_quality(cands: pd.DataFrame) -> dict:
    """Bright two-polarisation objects (>= 15 px) with no label within 150 m, by AI2 campaign.

    These are mostly unlabelled ships (checked visually on 20 scenes) and bound the label
    completeness; a high rate in one campaign flags incomplete annotation there.
    """
    bright = cands.detected_vv & cands.detected_vh & (cands.n_pixels >= 15)
    out = {}
    for camp, g in cands[bright].groupby(cands.split.str.replace(r"-point-(train|val)$", "", regex=True)):
        vc = g.cand_class.value_counts()
        out[camp] = {"bright_vessel": int(vc.get("vessel", 0)), "bright_ambiguous": int(vc.get("ambiguous", 0)),
                     "bright_unlabelled": int(vc.get("clutter", 0)),
                     "unlabelled_frac": round(float(vc.get("clutter", 0) / max(len(g), 1)), 3)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--width", type=int, default=16)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--max-neg-ratio", type=float, default=5.0, help="training negatives per positive at most")
    ap.add_argument("--samples-per-epoch", type=int, default=16000, help="balanced draws per epoch")
    ap.add_argument("--patience", type=int, default=6)
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--eval-only", action="store_true", help="skip training; evaluate the saved model")
    args = ap.parse_args()
    t0 = time.time()
    cands = pd.read_parquet(ML_DIR / "candidates.parquet")
    labels = pd.read_parquet(ML_DIR / "cfar_misses.parquet")
    scenes = pd.read_csv(ML_DIR / "scene_sample.csv")
    for df in (cands, labels):  # AI2 annotation campaign, e.g. 'nov-2021'
        df["campaign"] = df.split.str.replace(r"-point-(train|val)$", "", regex=True)
    log(f"candidates {len(cands)} ({cands.cand_class.value_counts().to_dict()}), labels {len(labels)}", t0)

    rng = np.random.default_rng(args.seed)
    clean = cands[cands.cand_class.isin(["vessel", "clutter"])]
    tr = clean[clean.use == "train"]
    pos, neg = tr[tr.is_vessel], tr[~tr.is_vessel]
    n_neg = min(len(neg), int(args.max_neg_ratio * len(pos)))
    neg = neg.iloc[rng.permutation(len(neg))[:n_neg]]
    tr = pd.concat([pos, neg]).sample(frac=1, random_state=args.seed)
    va = clean[clean.use == "val"]
    te = cands[cands.use == "test"]  # ambiguous kept for reporting; excluded inside the metrics
    log(f"train {len(tr)} (pos {len(pos)}, neg {len(neg)} of {int((~clean[clean.use == 'train'].is_vessel).sum())}); "
        f"val {len(va)} (pos {int(va.is_vessel.sum())}); test {len(te)} (pos {int(te.is_vessel.sum())})", t0)
    x_te = load_chips(te, CHIP_DIR)
    if args.eval_only:
        x_tr = x_va = None
    else:
        x_tr, x_va = load_chips(tr, CHIP_DIR), load_chips(va, CHIP_DIR)
        log(f"chips loaded: {x_tr.nbytes / 1e6:.0f} MB train", t0)

    if args.eval_only:
        from darkvessel.ml.model import load_model
        model, meta = load_model(MODEL_PATH)
        curve = pd.read_csv(ML_DIR / "training_log.csv")
        thr = float(meta["threshold"])
        log(f"eval only: loaded {MODEL_PATH} (best epoch {meta['best_epoch']}, threshold {thr:.3f})", t0)
    else:
        model, meta, curve = train_verifier(x_tr, tr.is_vessel.values, x_va, va.is_vessel.values, width=args.width,
                                            epochs=args.epochs, batch=args.batch, lr=args.lr, patience=args.patience,
                                            samples_per_epoch=args.samples_per_epoch, threads=args.threads, seed=args.seed,
                                            log=lambda m: log(m, t0))
        curve.to_csv(ML_DIR / "training_log.csv", index=False)

        # operating threshold from validation windows (CFAR misses there count as fixed false negatives)
        p_va = predict_proba(model, x_va, meta, tta=True)
        lab_va = eval_labels(labels, "val")
        thr, thr_stats = best_f1_threshold(p_va, va.is_vessel.values.astype(float), n_extra_fn=int((~lab_va.cfar_detected).sum()))
        meta.update({"threshold": thr, "threshold_rule": "best F1 on validation windows (candidate precision, label recall)",
                     "threshold_val_stats": thr_stats, "model_id": MODEL_ID,
                     "train_counts": {"pos": int(len(pos)), "neg": int(len(neg)), "val": int(len(va)), "test": int(len(te))},
                     "scenes": {"total": int(len(scenes)), "test": int((scenes.scene_split == "test").sum())},
                     "training_data": "AI2 Skylight Sentinel-1 point labels (Apache-2.0), S1A/S1B 2020-2022; "
                                      "Contains modified Copernicus Sentinel data 2020-2022",
                     "trained_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        try:
            meta["git_commit"] = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True).strip()
        except Exception:
            meta["git_commit"] = None
        save_model(MODEL_PATH, model, meta)
        log(f"saved {MODEL_PATH}; threshold {thr:.3f} {thr_stats}", t0)

    # held-out scenes
    p_te = predict_proba(model, x_te, meta, tta=True)
    te = te.assign(cnn_score=p_te)
    te[["cand_id", "product_id", "window_id", "use", "cand_class", "confidence", "cnn_score"]].to_parquet(ML_DIR / "test_scores.parquet", index=False)
    lab_te = eval_labels(labels, "test")
    accept_all = np.ones(len(te), bool)
    accept_cnn = p_te >= thr
    m_cfar = system_metrics(lab_te, te, accept_all)
    m_cnn = system_metrics(lab_te, te, accept_cnn)
    # sensitivity: drop bright two-polarisation objects (>= 15 px) that no label claims; a visual check on
    # 32 of them found ships and fixed structures in similar numbers, so they bound the precision figures
    bright_unl = (te.detected_vv & te.detected_vh & (te.n_pixels >= 15) & (te.cand_class == "clutter")).values
    m_cfar_x = system_metrics(lab_te, te[~bright_unl], accept_all[~bright_unl])
    m_cnn_x = system_metrics(lab_te, te[~bright_unl], accept_cnn[~bright_unl])
    pr = pr_curve(lab_te, te, p_te)
    pr.to_csv(ML_DIR / "pr_curve_test.csv", index=False)
    rbl = recall_by_length(lab_te, te, accept_cnn)
    rbl.to_csv(ML_DIR / "recall_by_length_test.csv", index=False)
    region = {"cfar": by_group(lab_te, te, accept_all, "region").to_dict("records"),
              "cnn": by_group(lab_te, te, accept_cnn, "region").to_dict("records")}
    campaign = {"cfar": by_group(lab_te, te, accept_all, "campaign").to_dict("records"),
                "cnn": by_group(lab_te, te, accept_cnn, "campaign").to_dict("records")}
    conf = pd.crosstab(te.confidence, [te.cand_class, te.cnn_score >= thr])
    conf.columns = [f"{a}_{'accept' if b else 'reject'}" for a, b in conf.columns]
    ap_cand = float(pd.Series(p_te[te.cand_class != 'ambiguous']).pipe(
        lambda s: __import__('sklearn.metrics', fromlist=['average_precision_score']).average_precision_score(
            te.is_vessel.values[te.cand_class != 'ambiguous'], s.values)))
    metrics = {
        "model_id": MODEL_ID, "threshold": thr, "train_meta": meta,
        "test_scenes": int((scenes.scene_split == "test").sum()),
        "test_labels_total": int((labels.use == "test").sum()), "test_labels_evaluable": int(len(lab_te)),
        "test_labels_off_testable_sea": int(((labels.use == "test") & ~(labels.in_scene & labels.on_testable_sea)).sum()),
        "test_candidates": int(len(te)), "test_candidate_classes": te.cand_class.value_counts().to_dict(),
        "test_candidate_ap": ap_cand,
        "cfar_only": m_cfar, "cfar_plus_cnn": m_cnn,
        "bright_unlabelled_test_candidates": int(bright_unl.sum()),
        "excluding_bright_unlabelled": {"cfar_only": m_cfar_x, "cfar_plus_cnn": m_cnn_x},
        "cfar_label_recall_loose_check": float(lab_te.cfar_detected_loose.mean()) if len(lab_te) else None,
        "by_region": region, "by_campaign": campaign, "by_confidence_class": conf.reset_index().to_dict("records"),
        "recall_by_length": rbl.to_dict("records"), "label_quality_by_campaign": label_quality(cands),
        "runtime_s": round(time.time() - t0, 1),
    }
    (ML_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    log(f"strict: CFAR only P {m_cfar['precision']:.3f} R {m_cfar['recall']:.3f} F1 {m_cfar['f1']:.3f} | "
        f"CFAR+CNN P {m_cnn['precision']:.3f} R {m_cnn['recall']:.3f} F1 {m_cnn['f1']:.3f}", t0)
    log(f"loose:  CFAR only P {m_cfar['precision_loose']:.3f} R {m_cfar['recall_loose']:.3f} F1 {m_cfar['f1_loose']:.3f} | "
        f"CFAR+CNN P {m_cnn['precision_loose']:.3f} R {m_cnn['recall_loose']:.3f} F1 {m_cnn['f1_loose']:.3f}", t0)
    print(rbl.to_string())
    make_figures(pr, rbl, curve, m_cfar, m_cnn, thr)
    log("done", t0)


def make_figures(pr, rbl, curve, m_cfar, m_cnn, thr):
    """PR curve, recall by length with Wilson intervals, training curves (project style, palette slots 1-2)."""
    import textwrap

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    from darkvessel.viz.style import INK, INK_2, MUTED, SERIES_LIGHT, SURFACE, apply_matplotlib_style

    apply_matplotlib_style()
    blue, orange = SERIES_LIGHT[0], SERIES_LIGHT[1]
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    src = "AI2 Skylight S1 labels (Apache-2.0); contains modified Copernicus Sentinel data 2020-2022"

    def footer(fig, text, width=110):
        fig.text(0.01, 0.01, textwrap.fill(text, width), fontsize=7, color=MUTED, va="bottom")

    # PR curve: one line (CFAR+CNN over thresholds) plus the two operating points
    fig, ax = plt.subplots(figsize=(6.4, 4.8))
    for suf, color, name in (("", blue, "strict match (50 m)"), ("_loose", orange, "loose match (150 m or 0.75 x length)")):
        ok = pr["precision" + suf].notna()
        ax.plot(pr["recall" + suf][ok], pr["precision" + suf][ok], color=color, lw=2, label=f"CFAR + CNN, {name}")
        ax.scatter([m_cfar["recall" + suf]], [m_cfar["precision" + suf]], s=70, facecolor=SURFACE, edgecolor=color, lw=2, zorder=3,
                   label="CFAR only" if suf else None)
        ax.scatter([m_cnn["recall" + suf]], [m_cnn["precision" + suf]], s=70, color=color, edgecolor=SURFACE, lw=2, zorder=3,
                   label=f"CFAR + CNN at threshold {thr:.2f}" if suf else None)
        ax.annotate(f"P {m_cfar['precision' + suf]:.2f}, R {m_cfar['recall' + suf]:.2f}", (m_cfar["recall" + suf], m_cfar["precision" + suf]),
                    xytext=(-6, -14), textcoords="offset points", fontsize=7.5, color=INK_2, ha="right")
        ax.annotate(f"P {m_cnn['precision' + suf]:.2f}, R {m_cnn['recall' + suf]:.2f}", (m_cnn["recall" + suf], m_cnn["precision" + suf]),
                    xytext=(-6, 8), textcoords="offset points", fontsize=7.5, color=INK_2, ha="right")
    ax.set_xlim(0, 1), ax.set_ylim(0, 1.02)
    ax.set_xlabel("Recall (labelled vessels on testable sea)")
    ax.set_ylabel("Precision (accepted candidates matched to a label)")
    ax.grid(True, axis="both")
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(loc="lower left", fontsize=8)
    ax.set_title("Held-out scenes: CA-CFAR alone vs CA-CFAR + CNN verifier", loc="left", fontsize=11, color=INK, fontweight="bold")
    footer(fig, f"CFAR misses count as misses at every threshold. {src}.")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(FIG_DIR / "ml_pr_curve.png", dpi=150)
    plt.close(fig)

    # Recall by AIS length bin, dots with Wilson 95 % intervals, two series side by side
    d = rbl[rbl.length_bin != "all"].reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    x = np.arange(len(d))
    for off, col, lo, hi, name, color in ((-0.12, "cfar_loose_recall", "cfar_loose_ci_lo", "cfar_loose_ci_hi", "CFAR only", orange),
                                          (0.12, "cnn_loose_recall", "cnn_loose_ci_lo", "cnn_loose_ci_hi", "CFAR + CNN", blue)):
        y = d[col].values
        ax.vlines(x + off, d[lo].values, d[hi].values, color=color, lw=2, alpha=0.9)
        ax.scatter(x + off, y, s=60, color=color, edgecolor=SURFACE, lw=2, zorder=3, label=name)
    for i, r in d.iterrows():
        if r.n_labels:
            ax.text(x[i] + 0.12, min(r.cnn_loose_ci_hi + 0.03, 1.02), f"{r.cnn_loose_recall:.2f}", ha="center", fontsize=8, color=INK_2)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{b}\nn = {n}" for b, n in zip(d.length_bin, d.n_labels)], fontsize=9)
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("Recall of labelled vessels with an AIS length")
    ax.set_xlabel("AIS length bin (AI2 attribute labels)")
    ax.grid(True, axis="y")
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(loc="lower right", fontsize=8)
    ax.set_title("Recall by AIS length on held-out scenes (Wilson 95 % intervals)", loc="left", fontsize=11, color=INK, fontweight="bold")
    footer(fig, f"Loose match: candidate within 150 m or 0.75 x length of the label; labels inside the 1 km shore buffer excluded. {src}.")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(FIG_DIR / "ml_recall_by_length.png", dpi=150)
    plt.close(fig)

    # Training curves: two small multiples on one row
    fig, axs = plt.subplots(1, 2, figsize=(8.4, 3.4))
    axs[0].plot(curve.epoch, curve.train_loss, color=orange, lw=2, label="train (augmented, balanced)")
    axs[0].plot(curve.epoch, curve.val_loss, color=blue, lw=2, label="validation")
    axs[0].set_ylabel("binary cross-entropy"), axs[0].set_xlabel("epoch"), axs[0].legend(fontsize=8)
    axs[1].plot(curve.epoch, curve.val_ap, color=blue, lw=2)
    axs[1].set_ylabel("validation average precision"), axs[1].set_xlabel("epoch")
    axs[1].set_ylim(min(0.8, curve.val_ap.min() - 0.02), 1.0)
    for ax in axs:
        ax.grid(True, axis="y"), ax.set_axisbelow(True)
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    fig.suptitle("Verifier training (early stopping on validation average precision)", x=0.02, ha="left", fontsize=11, color=INK, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(FIG_DIR / "ml_training_curves.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
