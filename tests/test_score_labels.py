"""Label scoring: sample membership, per-class shares, stratified share and CNN agreement."""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from darkvessel.viz.demo import QUEUE_RATES, queue_key

spec = importlib.util.spec_from_file_location("score_labels", Path(__file__).parents[1] / "scripts" / "12_score_labels.py")
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)


def _ids(prefix, n):
    return [f"{prefix}_{i:05d}" for i in range(n)]


def test_score_only_counts_the_random_sample():
    ids_h = _ids("S1D_20261001T230905", 4000)
    ids_m = _ids("S1C_20260928T111904", 4000)
    prod = pd.DataFrame({"det_id": ids_h + ids_m, "confidence": ["high"] * 4000 + ["medium"] * 4000,
                         "mission": ["S1D"] * 4000 + ["S1C"] * 4000, "view": "regional"})
    in_h = [d for d in ids_h if queue_key(d) < QUEUE_RATES["regional"]["high"]]
    in_m = [d for d in ids_m if queue_key(d) < QUEUE_RATES["regional"]["medium"]]
    assert 20 < len(in_h) < 70 and 5 < len(in_m) < 50  # about 1 % and 0.6 % of 4,000
    rng = np.random.default_rng(0)
    lab = pd.DataFrame({"det_id": in_h + in_m + ids_h[:5],  # 5 hand-picked labels outside the sample
                        "label": ["vessel"] * len(in_h) + list(rng.choice(["vessel", "clutter"], len(in_m))) + ["clutter"] * 5})
    lab = lab.drop_duplicates("det_id")
    res = sl.score(lab, prod)
    hi = next(r for r in res["by_view_class"] if r["class"] == "high")
    assert hi["vessel_share"] == 1.0 and hi["n"] == len(in_h)
    assert res["labels_outside_sample"] >= 5 - sum(d in in_h for d in ids_h[:5])
    c = res["candidates"]["regional"]
    assert 0 < c["ci"][0] <= c["vessel_share"] <= c["ci"][1] <= 1


def test_cnn_agreement_on_detail_view():
    ids = _ids("S1D_20260929T1110", 3000)
    keep = [d for d in ids if queue_key(d) < QUEUE_RATES["detail"]["high"]][:40]
    prod = pd.DataFrame({"det_id": ids, "confidence": "high", "mission": "S1D", "view": "detail",
                         "cnn_vessel": [i % 2 == 0 for i in range(3000)], "cnn_score": 0.5})
    lab = pd.DataFrame({"det_id": keep, "label": "vessel"})
    res = sl.score(lab, prod)
    cnn = res["cnn_detail"]
    accepted = int(prod.set_index("det_id").loc[keep, "cnn_vessel"].sum())
    assert cnn["tp"] == accepted and cnn["fp"] == 0 and cnn["fn"] == 40 - accepted
    assert cnn["precision"] == 1.0


def test_weighted_pr_recovers_population_precision():
    rng = np.random.default_rng(1)
    # two strata with different inclusion rates: weighting must undo the oversampling of stratum A
    truth = np.r_[np.ones(80, bool), np.zeros(20, bool), np.ones(10, bool), np.zeros(90, bool)]
    pred = np.ones(200, bool)
    w = np.r_[np.full(100, 1 / 0.5), np.full(100, 1 / 0.05)]  # A sampled at 50 %, B at 5 %
    res = sl.weighted_pr(truth, pred, w, n_boot=200)
    # population: A 200 contacts, 80 % vessels; B 2,000 contacts, 10 % vessels -> (160 + 200) / 2,200
    assert abs(res["precision"] - 360 / 2200) < 1e-3
    assert res["precision_ci"][0] < res["precision"] < res["precision_ci"][1]
    assert rng is not None
