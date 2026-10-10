# Model card: CA-CFAR + CNN verifier (verifier_v0)

Two-stage vessel detector for Sentinel-1 IW GRD. Stage 1 is the project's CA-CFAR baseline
(`darkvessel.pipeline.run_baseline`), which proposes bright objects. Stage 2 is a small CNN
that looks at a 64 x 64 px (640 m) VV/VH chip around each object and scores it as vessel or
clutter. The CNN was trained on expert point labels from Sentinel-1A/1B scenes of 2020 to
2022 and then applied, without any Sentinel-1D ground truth, to a Sentinel-1D scene of
29 September 2026 off Ca Mau. That application is the first experiment of the planned
letter on detector transfer from Sentinel-1A to Sentinel-1C/1D.

Everything below was produced by `scripts/04_build_training_set.py`, `scripts/05_train_verifier.py`
and `scripts/06_apply_verifier.py` on 2 October 2026 (CPU only). Numbers come from
`data/ml/build_summary.json`, `data/ml/metrics.json` and `data/ml/apply_summary.json`.

## Data and licences

| Source | Use | Licence and attribution |
|---|---|---|
| AI2 Skylight Sentinel-1 vessel labels, `allenai/vessel-detection-sentinels` `data/metadata.sqlite3` | point labels (dataset 1) and vessel attributes (dataset 2) | Apache-2.0 (LICENSE in that repository) |
| Sentinel-1A/1B IW GRD, 2020 to 2022, AWS Open Data mirror `sentinel-s1-l1c` | training and evaluation imagery, read as windows over HTTPS | Contains modified Copernicus Sentinel data 2020-2022 |
| Sentinel-1D IW GRD `S1D_IW_GRDH_1SDV_20260929T111023_20260929T111053_004792_008FC8_A5CA` | application scene | Contains modified Copernicus Sentinel data 2026 |
| ESA WorldCover 2021 v200 | sea mask | CC BY 4.0 |

The AI2 label set holds 61,655 point labels in 39,954 windows on 2,913 Sentinel-1 scenes
(2,832 S1A, 81 S1B; 14 scenes from 2020, 285 from 2021, 2,614 from 2022). 20,678 of the
windows carry no label. Windows are 1024 x 1024 px on a Web Mercator tiling (zoom 13, tile
512, about 9.55 m per pixel at the equator) in most cases; some are 846 to 1583 px.
Vessel attributes (AIS length, width, heading, speed, type) exist for 17,582 windows of
dataset 2. Their 128 x 128 px windows are centred exactly (0 px) on a dataset-1 point of the
same scene, so attributes were joined on that centre. 17,582 attribute windows fall on 16,494
distinct positions; at 843 positions duplicates disagree on length, and those were blanked
(`attr_conflict`). 15,654 point labels end up with a usable length.

Decoding rule (`darkvessel.ml.labels`): pixel column c and row r on the tiling map to Web
Mercator metres x = c S - H, y = H - r S with H = 20037508.342789244 m and S = 2H / (2^13 x 512),
then to lon/lat on the WGS 84 sphere of radius 6378137 m. This reproduces the lon/lat bounds
stored with every AI2 image to better than 1e-4 degree (checked on all 4,315 S1 images).

## Scene and window selection

Selection is deterministic (seed 20261002) and recorded in `data/ml/scene_sample.csv` and
`data/ml/window_sample.parquet`.

- Every AI2 scene whose bounds intersect Southeast Asia, 95E to 125E and 10S to 25N: 334 scenes.
- Plus 120 scenes from the rest of the world, stratified by 30 x 30 degree cell of the scene
  centre with allocation proportional to each cell's labelled windows (at least one per cell),
  chosen among scenes with at least one labelled window.
- Per scene at most 16 windows with labels and 3 without, chosen at random.
- Held-out test set: 20 % of the selected scenes in each region, held out whole (every window
  of those scenes). On the remaining scenes, AI2 `-val` windows form the validation set used
  for early stopping and the operating threshold, and `-train` windows the training set. No
  AI2 validation window is ever trained on.

Result of the selection: 454 scenes (447 S1A, 7 S1B; 4 from 2020, 66 from 2021, 384 from
2022), 41 strata outside Southeast Asia, 3,188 windows (2,544 with labels, 644 without) holding
10,633 point labels. Windows per AI2 campaign: jun-july-aug-2022 1,044, jan-march-may-2022 966,
apr-2022 946, nov-2021 172, jun-2020 60.

| Region | Scenes train+val / test | Windows train / val / test | Labels train / val / test |
|---|---|---|---|
| Southeast Asia | 267 / 67 | 1,566 / 188 / 392 | 6,279 / 669 / 1,439 |
| Rest of world | 96 / 24 | 724 / 94 / 224 | 1,491 / 236 / 519 |

## Candidate generation (identical to the baseline)

For each selected window: read sigma0 VV and VH without thermal-noise subtraction over the
window plus an 81 px margin; WorldCover sea mask on an 8 x decimated grid with a 1 km shore
buffer; CA-CFAR with PFA 1e-6, guard 81 px, background 161 px, ENL estimated from the window
and clipped to 1 to 10 (default 4.4 when it cannot be estimated); objects of at least 2 pixels;
VV and VH objects fused within 3 px. The heuristic confidence classes of the baseline
(`assign_confidence`) are recorded for every candidate so the two stages can be compared.

Two details differ from the full-scene baseline and are documented in `darkvessel.ml.chips`:

1. Sea mask connectivity. The baseline calls water "sea" only when it connects to a WorldCover
   code-0 (unmapped ocean) pixel inside the read box. A 10 km window often has none; near
   Singapore a 0.7 degree box is coded 80 (permanent water) throughout and the baseline rule
   would mask every pixel. Here water counts as sea if its connected component holds code 0 or
   touches the edge of a box padded by 0.35 degree around the scene's windows.
2. ENL is estimated per window (about 1.2 Mpx) rather than from a 2048 px crop at the centre
   of a 150 km window.

Geolocation. AI2 warped each product with plain `gdalwarp -t_srs epsg:3857` (their
`src/data/warp.py`), which uses GDAL's default polynomial fit to the 210 ground control
points in the TIFF, not the ESA annotation grid. Candidates and labels are therefore both
placed with the same GDAL transformer. On one scene with 119 matched vessels the median
candidate-to-label distance was 2.8 px with the GDAL transform against 3.7 px with the
annotation grid, and a 2 px column bias disappeared. A Web Mercator window is a rotated
quadrilateral in radar geometry, so candidates are kept only when their lon/lat falls inside
the window's box; the corner triangles of the pixel bounding box are outside the labelled
area, and before this filter unlabelled ships there inflated the clutter class.

Labelling rule. A candidate within 50 m of an AI2 point (any point of the scene) is a vessel.
A candidate between 50 m and 150 m, or within 0.75 times a known AIS length, is "ambiguous":
such objects are fragments of the same ship, or clicks placed on the bow or the bridge, and
are excluded from training and from precision, and reported separately. Everything else is
clutter. A label counts as detected by CFAR when some fused object in the read window lies
within 50 m of it (primary rule); the loose variant uses the ambiguous radius. Labels inside
the 1 km shore buffer or outside the swath are excluded from every recall figure and counted.

### Candidate set

Built in 79 minutes with 3 worker processes (06:29 to 07:48 on 2 October 2026, reads over
HTTPS from the AWS mirror; no scene failed). 2,853 of the 3,188 windows produced candidates
(median 12 per window, 90th percentile 53).

| | Train | Val | Test (held-out scenes) | All |
|---|---|---|---|---|
| Candidates | 49,355 | 5,863 | 14,056 | 69,274 |
| of which vessel (within 50 m of a label) | 4,947 | 596 | 1,213 | 6,756 |
| of which ambiguous (50 to 150 m or 0.75 x length) | 2,587 | 314 | 617 | 3,518 |
| of which clutter | 41,821 | 4,953 | 12,226 | 59,000 |
| Labels in the windows | 7,762 | 905 | 1,958 | 10,625 |
| Labels on testable sea (evaluable) | 5,842 | 706 | 1,422 | 7,970 |
| CFAR label recall, strict 50 m | 0.782 | 0.785 | 0.798 | 0.785 |
| CFAR label recall, loose | 0.971 | 0.975 | 0.974 | 0.972 |

2,655 labels (25 %) sit inside the 1 km shore buffer or on masked water and are excluded
from every recall figure. Among the 69,274 candidates the baseline's heuristic classes split
as: high (VV and VH) 4,462 vessel / 1,175 ambiguous / 3,157 clutter; medium 1,360 / 1,080 /
7,546; low 934 / 1,263 / 48,297.

Why the two rules differ by 19 points: the distance from a CFAR centroid to the expert click
grows with ship length. For labelled vessels with an AIS length the median nearest-candidate
distance is 28 m under 50 m length, 29 m at 50 to 100 m, 31 m at 100 to 200 m and 36 m above
200 m, with 90th percentiles of 67, 67, 64 and 82 m; the share within 50 m is 0.83, 0.81,
0.80 and 0.66. Per-scene median offsets are small (13.7 m median over 218 scenes with at
least 8 matches), so this is per-label scatter, not a geolocation bias. The strict rule is
kept because the brief asked for 50 m; the loose rule is the one to read for long ships.

Label completeness: bright objects detected in both polarisations with at least 15 pixels
and no label within 150 m number 391 against 1,516 labelled vessels of the same kind in the
apr-2022 campaign, 378 against 1,236 in jan-march-may-2022, 773 against 1,452 in
jun-july-aug-2022, 104 against 140 in nov-2021 and 17 against 86 in jun-2020. A visual check
of 32 of them from the 2022 campaigns showed ships and fixed structures (cross-shaped
sidelobe patterns of turbines or platforms, arrays of farm structures) in similar numbers.
They are kept as clutter for training and for the primary metrics, and a sensitivity figure
excludes them.

## Model and training

`darkvessel.ml.model.VerifierCNN`: 2 input channels (VV, VH in dB, standardised with the
training-set channel mean and standard deviation, missing pixels set to 2 standard deviations
below the mean), four blocks of two 3 x 3 convolutions with BatchNorm and ReLU followed by
2 x 2 max pooling (64 to 4 px), global average pooling, dropout 0.3, one linear unit.
Trained from scratch (no pretrained weights are reachable from this environment).
Augmentation: the 8 flips and 90 degree rotations, shifts of up to 2 px, per-channel offsets
of up to 1 dB and a gain of up to 0.5 dB. Class imbalance: a weighted sampler draws one third
positives per batch; training negatives are capped at 5 per positive. AdamW, one-cycle
learning rate (peak 1e-3), batch 128, early stopping on validation average precision.
Inference averages the 8 dihedral views. The operating threshold maximises F1 on the
validation windows with CFAR misses counted as false negatives.

Training run (width 16, 293,985 parameters): 29,682 training chips (4,947 vessels, 24,735
clutter drawn from 41,821), 5,549 validation chips (596 vessels), 16,000 balanced draws per
epoch, 3 CPU threads, about 90 s per epoch. Early stopping ended the run after epoch 23;
the best validation average precision, 0.905, was reached at epoch 17 (`data/ml/training_log.csv`,
`docs/figures/ml_training_curves.png`). Training took 40 minutes. The operating threshold
is 0.632 (validation: candidate precision 0.79, label recall 0.73 with CFAR misses counted).
Normalisation constants: VV mean -19.6 dB, SD 5.3 dB; VH mean -25.7 dB, SD 3.5 dB.
Weights: `data/models/verifier_v0.pt` (gitignored), git commit fb8a641 of the working tree at
training time.

## Held-out evaluation

Held-out set: 91 scenes never seen in training (67 Southeast Asia, 24 elsewhere), 616 windows,
14,056 CFAR candidates (1,213 vessel, 617 ambiguous, 12,226 clutter), 1,958 labels of which
1,422 are evaluable (on testable sea); 536 sit inside the shore buffer. Wilson 95 % intervals
in brackets. Candidate-level average precision of the CNN score: 0.893.

| Rule | Stage | Accepted | Precision | Recall | F1 |
|---|---|---|---|---|---|
| strict (50 m) | CFAR only | 14,056 | 0.090 [0.086, 0.095] | 0.797 [0.775, 0.817] | 0.162 |
| strict (50 m) | CFAR + CNN at 0.632 | 1,898 | 0.770 [0.748, 0.791] | 0.750 [0.727, 0.772] | 0.760 |
| loose (150 m or 0.75 x length) | CFAR only | 14,056 | 0.130 | 0.966 | 0.229 |
| loose (150 m or 0.75 x length) | CFAR + CNN at 0.632 | 1,898 | 0.820 | 0.918 | 0.866 |

The CNN removes 11,884 of 12,226 clutter candidates (97.2 %) and keeps 1,145 of 1,213
labelled vessels (94.4 %); the recall drop from 0.797 to 0.750 is 66 labels. Precision is
bounded by label completeness: excluding the 334 bright two-polarisation test candidates that
no label claims, CFAR + CNN precision is 0.896 strict and 0.921 loose (CFAR only 0.093 and
0.133). Figure: `docs/figures/ml_pr_curve.png`.

By region (strict precision / recall, then loose): Southeast Asia 999 labels, CFAR only
0.086 / 0.792 and 0.127 / 0.971, CFAR + CNN 0.755 / 0.745 and 0.811 / 0.923; elsewhere 423
labels, CFAR only 0.102 / 0.809 and 0.139 / 0.955, CFAR + CNN 0.806 / 0.764 and 0.842 / 0.905.
By campaign (strict, CFAR + CNN): apr-2022 0.877 / 0.731, jan-march-may-2022 0.674 / 0.695,
jun-july-aug-2022 0.741 / 0.813, nov-2021 0.810 / 0.754.

How the CNN treats the baseline's heuristic classes on the test candidates (accepted /
rejected at the threshold):

| Baseline class | Vessel | Ambiguous | Clutter |
|---|---|---|---|
| high (VV and VH) | 809 / 27 | 173 / 19 | 246 / 323 |
| medium | 182 / 28 | 119 / 74 | 66 / 1,077 |
| low | 154 / 13 | 119 / 113 | 30 / 10,484 |

So the CNN keeps 92 % of the labelled vessels that the heuristic had filed as "low" and
rejects 99.7 % of the low-class clutter; in the high class it rejects 323 of 569 unlabelled
bright objects, which is where unlabelled ships and fixed structures sit.

## Recall by AIS length (first look at the flagship question)

Held-out labels with a consistent AIS length: 406 (`data/ml/recall_by_length_test.csv`,
`docs/figures/ml_recall_by_length.png`). Wilson 95 % intervals in brackets. The strict rule
counts a vessel as detected only when a CFAR object lies within 50 m of the expert click; the
loose rule allows 150 m or 0.75 times the AIS length. The bins below 25 m are empty or near
empty because AI2 attributes come from AIS matches, and few small boats carry AIS: this sample
cannot say anything about recall for boats under 25 m, which is the population the flagship
question is about. That gap needs a different ground truth (AIS from the project's own feed
over the Ca Mau scenes, or optical coincidences).

| AIS length | n | CFAR, strict 50 m | CFAR + CNN, strict | CFAR, loose | CFAR + CNN, loose |
|---|---|---|---|---|---|
| 0-15 m | 0 | no data | no data | no data | no data |
| 15-25 m | 2 | 2/2 = 1.00 [0.34, 1.00] | 1/2 = 0.50 [0.10, 0.90] | 2/2 = 1.00 [0.34, 1.00] | 2/2 = 1.00 [0.34, 1.00] |
| 25-50 m | 16 | 15/16 = 0.94 [0.72, 0.99] | 15/16 = 0.94 [0.72, 0.99] | 16/16 = 1.00 [0.81, 1.00] | 16/16 = 1.00 [0.81, 1.00] |
| 50-100 m | 72 | 63/72 = 0.88 [0.78, 0.93] | 63/72 = 0.88 [0.78, 0.93] | 70/72 = 0.97 [0.90, 0.99] | 70/72 = 0.97 [0.90, 0.99] |
| 100+ m | 316 | 228/316 = 0.72 [0.67, 0.77] | 225/316 = 0.71 [0.66, 0.76] | 307/316 = 0.97 [0.95, 0.99] | 300/316 = 0.95 [0.92, 0.97] |
| all | 406 | 308/406 = 0.76 [0.71, 0.80] | 304/406 = 0.75 [0.70, 0.79] | 395/406 = 0.97 [0.95, 0.98] | 388/406 = 0.96 [0.93, 0.97] |

Reading: under the loose rule CA-CFAR at PFA 1e-6 finds 97 % of AIS-carrying vessels of
25 m and above on open sea, and the CNN costs at most 2 points of recall. The strict column's
fall from 0.94 at 25 to 50 m to 0.72 above 100 m is the click-offset effect described above
(a 250 m ship's intensity centroid is often more than 50 m from where the annotator clicked),
not a loss of detections; the loose column is the one to quote for long ships. CFAR misses
under the loose rule are 11 of 406, consistent with the 2.8 % loose miss rate on all 7,970
evaluable labels.

## Application to the Sentinel-1D Ca Mau scene (transfer experiment 1)

Scene S1D_IW_GRDH_1SDV_20260929T111023_20260929T111053_004792_008FC8_A5CA, Sentinel-1D,
ascending, 29 September 2026 11:10 UTC, processed by the baseline over 104.7E to 105.9E,
8.0N to 9.2N: 6,005 CA-CFAR detections (285 high, 435 medium, 349 fixed, 4,936 low;
720 vessel candidates = high + medium). `scripts/06_apply_verifier.py` cut a 64 px chip at
every detection (8 chips touch the swath edge), scored it with model `verifier_v0_356af0ca`
(sha256 prefix of the weights) at threshold 0.632, and wrote `data/detections_ml.gpkg`
(layers `detections_verified_4326`, `detections_verified_utm48n`; all baseline columns kept,
including `caveat`, plus `cnn_score`, `cnn_vessel`, `cnn_threshold`, `cnn_model_id`,
`cnn_chip_valid_frac`, `cnn_training_data`). Runtime 156 s. `data/detections_baseline.gpkg`
was not modified. Summary: `data/ml/apply_summary.json`; chips by class and verdict:
`docs/figures/ml_1d_chips.png`.

| Baseline class | n | CNN vessel | share | median score | score 10th to 90th pct |
|---|---|---|---|---|---|
| high (VV and VH) | 285 | 78 | 0.27 | 0.24 | 0.005 to 0.93 |
| medium | 435 | 12 | 0.03 | 0.02 | 0.001 to 0.33 |
| low | 4,936 | 2 | 0.000 | 0.001 | 0.000 to 0.004 |
| fixed (persistent) | 349 | 57 | 0.16 | 0.23 | 0.007 to 0.74 |
| all | 6,005 | 149 | 0.025 | | |

By polarisation class: VV and VH 578 detections, 135 accepted; VH only 434, 10 accepted;
VV only 4,993, 4 accepted. The CNN therefore cuts the baseline's 720 vessel candidates to
149 and treats the 4,936 low-class objects as the clutter they look like (two accepted: an
862 px, 780 m long, 52 dB-contrast object and an 18 px target).

What the chips show (`ml_1d_chips.png`): the high-class objects the CNN rejects are mostly
bright point targets with cross-shaped sidelobes standing in lines, the signature of fixed
structures (offshore wind turbines and stake lines are both present along this coast;
UNVERIFIED which ones these are), while the accepted high-class objects are compact or
elongated single targets. Accepted objects have a median VV contrast of 15.1 dB and 19.5
pixels against 10.0 dB and 7 pixels for the rejected ones. The CNN also accepts 57 of the
349 persistent ("fixed") objects, which look like the same turbine-type targets; persistence
should keep precedence over the CNN for those, since a single chip cannot tell an anchored
ship from a platform.

Domain shift measured on this scene: the chip background (median of the four 16 x 16 px
corners) is -24.0 dB in VV and -29.0 dB in VH for 240 random 1D detections, against -20.4 dB
and -25.3 dB for 2,000 random training clutter chips from Sentinel-1A/1B, i.e. 3.6 and
3.7 dB darker, or 0.8 and 0.9 training standard deviations below the training mean. The
training augmentation covered offsets of only 1 dB. Whether this is the calm evening sea
of this scene or a lower noise floor of the 1D instrument cannot be separated from one
scene (UNVERIFIED); the model's low acceptance of medium-class (VH-only) objects on 1D may
be a symptom. No Sentinel-1D ground truth exists, so none of these numbers is an accuracy.

## Regional application (September 2026 run)

Purpose: verify detection before identification. Every radar contact of the 12-day regional run
(20 September to 1 October 2026, `docs/scs_regional.md`) gets a verifier score, so that every
contact in the product carries one. Before this run only the 35,626 objects in cells both
satellites imaged had a score (`scripts/14_cnn_shared_cells.py`).

**What was scored.** Phase "main": all 103,839 high, medium and fixed objects of
`data/detections_regional_all.gpkg` (29,228 high, 49,387 medium, 25,224 fixed) on 118 scenes
(26 Sentinel-1C, 92 Sentinel-1D). The high and medium objects are the 78,615 contacts of
`data/detections_regional.gpkg`; the fixed objects are those of `data/structures_regional.gpkg`.
Every one of the 103,839 has a score. Phase "low": the 823,285 low-class objects (weak VV only
759,790, clutter zone 52,820, near fixed 5,727, oversized 4,948) on 119 scenes, run after the
main phase at the lowest CPU priority. Every one of the 823,285 has a score
(`scored.low.complete` is true in `data/ml/regional_cnn.json`), and the outputs below hold both
phases: 927,124 scored objects.

**Method** (`scripts/32_cnn_regional.py`, `src/darkvessel/ml/regional_verify.py`):
- Chips exactly as in training and in `scripts/14_cnn_shared_cells.py`: 64 x 64 px (640 m) VV
  and VH sigma0 in dB, thermal noise not removed, cut at the object's row and column, read
  over HTTPS from the COGs of the AWS mirror (`sentinel-s1-l1c`, listed in the AWS Open Data
  registry, https://registry.opendata.aws/sentinel-1/). No scene is downloaded.
- Reads: per scene, objects are grouped by the 1024 x 1024 px COG tile that holds their centre.
  One windowed read of VV and VH per group covers all chips of the group.
- Score: model `verifier_v0_356af0ca`, mean of the 8 flip and rotation views, threshold
  0.631783 (best F1 on the validation windows, shown as 0.632 elsewhere). `cnn_vessel` = score
  at or above the threshold.
- Reuse: a score of `data/ml/shared_cells_cnn.parquet` is kept when det_id, scene, row, column,
  longitude and latitude all match exactly. All 35,626 matched; no object had a different
  position. Their chips are still read, for the chip features, and a fixed random sample of
  200 (seed 20261009) is rescored as a check.
- Chip features per object: `cnn_chip_valid_frac` (finite share of the central 8 x 8 px, the
  definition of `scripts/06_apply_verifier.py` and the live pass), `chip_valid_frac_full` (whole
  chip), `bg_vv_db` and `bg_vh_db` (median dB outside the central 16 x 16 px, as script 14).
- Checkpoint: one parquet per scene and phase under `data/cache/regional_cnn/`; a rerun skips a
  scene only when its checkpoint has the same model id and the same det_id set. A scene that
  fails (network) is retried once at the end of the phase; three failures in a row pause the run.
- CPU: nice 10 (low phase nice 19), 2 torch threads, scoring under `torch.inference_mode()`
  (same scores, about 20 % less CPU), on 4 cores shared with the live-pass pipeline.
- Intervals: Wilson score 95 % (Wilson 1927, Journal of the American Statistical Association
  22(158), doi:10.1080/01621459.1927.10502953).

Run, resume, build:

```
python scripts/32_cnn_regional.py --detach --phases main,low                  # background; resumes from the checkpoints
python scripts/32_cnn_regional.py --detach --if-incomplete --phases main,low  # same, but only if a scene is left
python scripts/32_cnn_regional.py --status                                    # checkpoints done, last log lines
python scripts/32_cnn_regional.py --build [--partial-low]                     # outputs from the checkpoints so far
python scripts/32_cnn_regional.py --stop
```

**Outputs.**
- `data/ml/regional_cnn.parquet`: one row per scored object (det_id, scene_id, mission,
  confidence, cnn_score, cnn_vessel, cnn_threshold, cnn_model_id, cnn_score_source,
  cnn_chip_valid_frac, chip_valid_frac_full, bg_vv_db, bg_vh_db, caveat); model id, threshold,
  training data, transfer caveat and the dark caveat in the file metadata. 927,124 rows (both
  phases), 7.6 MB.
- `data/detections_regional_verified.gpkg` (18.5 MB): the 78,615 contacts with every column of
  `data/detections_regional.gpkg` except lat and lon (the geometry), acq_utc (the scene start
  time; in det_id and in the `scenes` table by scene_idx), ais_status (`not_checked` on every
  row) and the per-row caveat, plus cnn_score and cnn_vessel. Layers
  `detections_regional_verified_4326` and `detections_regional_verified_utm49n` (EPSG:32649),
  a `scenes` table and an `about` layer (model id, threshold, training data and licence,
  transfer caveat, dark caveat, column notes). The left-out columns are what keeps both CRS
  layers in one file under 20 MB; with them the file is 37 MB.
- `data/ml/regional_cnn.json`: acceptance with Wilson intervals by class, mission, length bin,
  reporting box, wind speed and deep convection, inside and outside the shared cells, score
  quartiles, chip background medians, run time and the reuse check.
- `docs/figures/regional_cnn.png`: accepted share of high and medium contacts per 0.25 degree
  cell, and acceptance by class and by length estimate.

**Reuse check.** All 200 rescored objects reproduce the reused score within 1e-4 (largest
difference 6.0e-8) and all 200 verdicts agree. The chip background of all 35,626 reused
objects equals the value script 14 computed (largest difference 0.0 dB). The regional chips
are therefore the same chips as in the shared-cell experiment.

**Run time.** Main phase: 7,086 s summed over the 118 scenes (2.0 h; median 40 s per scene;
14.7 objects per second). The work is network bound: CPU stayed mostly idle while windows were
read from the mirror. The estimate after the first 3 scenes was 0.5 h; it was low because those
scenes held 9,667 objects of which 8,588 were reused (read, not scored), and per-scene read time,
not object count, sets the pace. Wall clock ran from 00:42 to 05:40 UTC on 9 October 2026 with
two kinds of stops: restarts to fix reads after the mirror returned errors (31 failed scene
attempts, all recovered by retry or rerun; no scene was lost) and a container stop from about
02:20 to 05:00 UTC, after which the run resumed from the checkpoints.

Low phase: 20,425 s summed over the 119 scenes (5.7 h; median 104 s per scene; 40.3 objects per
second). Wall clock ran from 05:41 UTC on 9 October to 03:00 UTC on 10 October 2026. The run
restarted itself from its checkpoints twice when its resident memory passed 3 GB (07:30 on
9 October, 02:14 on 10 October) and was resumed from its checkpoints three times: at 15:10 on
9 October and at 00:21 on 10 October after container outages (the second time by the watchdog,
`docs/ais_live.md`), and at 00:45 on 10 October in a test of the session hook. No scene was
lost; the final pass over the last 22 scenes had no failure.

**Results, main phase.** Accepted = cnn_score >= 0.631783. Wilson 95 % intervals in brackets.

| Group | n | Accepted | Share [95 % CI] | Score quartiles | Background VV / VH (dB) |
|---|---|---|---|---|---|
| high | 29,228 | 19,000 | 0.650 [0.645, 0.656] | 0.465 / 0.809 / 0.954 | -20.7 / -28.0 |
| medium | 49,387 | 5,114 | 0.104 [0.101, 0.106] | 0.001 / 0.017 / 0.228 | -19.6 / -27.8 |
| fixed | 25,224 | 6,108 | 0.242 [0.237, 0.247] | 0.007 / 0.135 / 0.612 | -20.4 / -27.4 |
| contacts (high + medium) | 78,615 | 24,114 | 0.307 [0.304, 0.310] | 0.005 / 0.195 / 0.761 | -20.0 / -27.9 |

By satellite:

| Group | Sentinel-1C | Sentinel-1D |
|---|---|---|
| contacts | 3,838 / 13,283 = 0.289 [0.281, 0.297] | 20,276 / 65,332 = 0.310 [0.307, 0.314] |
| high | 3,015 / 4,932 = 0.611 [0.598, 0.625] | 15,985 / 24,296 = 0.658 [0.652, 0.664] |
| medium | 823 / 8,351 = 0.099 [0.092, 0.105] | 4,291 / 41,036 = 0.105 [0.102, 0.108] |
| fixed | 846 / 4,969 = 0.170 [0.160, 0.181] | 5,262 / 20,255 = 0.260 [0.254, 0.266] |
| high, in cells both imaged | 0.601 [0.587, 0.616] | 0.629 [0.616, 0.641] |
| medium, in cells both imaged | 0.101 [0.094, 0.109] | 0.106 [0.100, 0.113] |
| fixed, in cells both imaged | 0.204 [0.191, 0.218] | 0.230 [0.219, 0.242] |
| contact chip background VV / VH | -20.6 / -27.3 dB | -19.9 / -28.0 dB |

The "cells both imaged" rows are, by construction, the result of `data/ml/shared_cells_cnn.json`.

**Results, low phase.** The low class holds what the detector rules put there (`low_reason` in
`data/detections_regional_all.gpkg`, rules in `docs/scs_regional.md`): weak VV only returns,
objects longer than 450 m (oversized), and candidates the clutter-zone and near-fixed rules
demoted. Accepted = cnn_score >= 0.631783; Wilson 95 % intervals in brackets, shares to four
decimals because most are small.

| low_reason | n | Accepted | Share [95 % CI] | Sentinel-1C | Sentinel-1D |
|---|---|---|---|---|---|
| weak VV only | 759,790 | 1,085 | 0.0014 [0.0013, 0.0015] | 137 / 256,968 = 0.0005 [0.0005, 0.0006] | 948 / 502,822 = 0.0019 [0.0018, 0.0020] |
| clutter zone | 52,820 | 1,832 | 0.0347 [0.0332, 0.0363] | 536 / 9,696 = 0.0553 [0.0509, 0.0600] | 1,296 / 43,124 = 0.0301 [0.0285, 0.0317] |
| near fixed | 5,727 | 362 | 0.0632 [0.0572, 0.0698] | 107 / 793 = 0.1349 [0.1129, 0.1605] | 255 / 4,934 = 0.0517 [0.0458, 0.0582] |
| oversized | 4,948 | 1,899 | 0.3838 [0.3703, 0.3974] | 294 / 1,064 = 0.2763 [0.2503, 0.3040] | 1,605 / 3,884 = 0.4132 [0.3978, 0.4288] |
| all low | 823,285 | 5,178 | 0.0063 [0.0061, 0.0065] | 1,074 / 268,521 = 0.0040 [0.0038, 0.0042] | 4,104 / 554,764 = 0.0074 [0.0072, 0.0076] |

Score quartiles: weak VV only 0.0003 / 0.0005 / 0.0011, clutter zone 0.0003 / 0.0009 / 0.0091,
near fixed 0.0054 / 0.0344 / 0.1580, oversized 0.0004 / 0.0295 / 0.9605 (a split population).
Median radar length estimate: 24.1 m for weak VV only and for the low class as a whole, 32.4 m
clutter zone, 40.0 m near fixed, 583.4 m oversized.

What the low phase says: the model rejects almost all of the weak VV only objects (14 in
10,000 accepted), which are 92 % of the low class, so on those it agrees with the detector's
rule. The exception is the oversized group, of which 38 % are accepted: objects measured at
more than 450 m (median 583 m) whose chip still looks like a vessel to the model. A vessel
merged with its wake, sidelobes or a neighbour into one object would look like this
(UNVERIFIED; no chip review has been done for this group).
Near-fixed objects (6 %) and clutter-zone objects (3 %) sit in between. The same limits as for
the main phase apply: acceptance is not precision, the model was not trained on 1C/1D and has
almost no small-boat labels, so a rejected low object is not shown to be clutter, and the low
class stays out of `data/detections_regional_verified.gpkg` (high and medium contacts only).
The Sentinel-1C and 1D shares differ by reason in both directions (clutter zone and near fixed
higher on 1C, weak VV only and oversized higher on 1D); the two satellites imaged different
seas on different days, so this is not a sensor comparison.

By radar length estimate (contacts, then the high class alone):

| Length | Contacts n | Contacts accepted share | High n | High accepted share |
|---|---|---|---|---|
| under 25 m | 29,324 | 0.009 [0.008, 0.010] | 1,333 | 0.014 [0.009, 0.022] |
| 25 to 50 m | 16,268 | 0.142 [0.137, 0.147] | 6,151 | 0.251 [0.241, 0.262] |
| 50 to 100 m | 18,179 | 0.512 [0.505, 0.519] | 11,191 | 0.685 [0.676, 0.693] |
| 100 to 200 m | 9,918 | 0.801 [0.793, 0.809] | 7,362 | 0.915 [0.909, 0.921] |
| 200 m and longer | 4,926 | 0.871 [0.862, 0.881] | 3,191 | 0.951 [0.943, 0.958] |

By reporting box (boxes of `scripts/21_viirs_regions.py`; reporting boxes, not boundaries),
contacts and then the high class alone:

| Box | Contacts n | Contacts accepted share | High accepted share |
|---|---|---|---|
| Gulf of Tonkin | 17,965 | 0.376 [0.369, 0.383] | 0.653 [0.643, 0.663] |
| North shelf | 13,330 | 0.436 [0.427, 0.444] | 0.751 [0.740, 0.762] |
| Gulf of Thailand | 18,796 | 0.240 [0.234, 0.246] | 0.564 [0.551, 0.576] |
| South Vietnam shelf | 7,934 | 0.255 [0.245, 0.264] | 0.586 [0.567, 0.604] |
| Central sea | 1,265 | 0.098 [0.083, 0.116] | 0.599 [0.521, 0.672] |
| Southern sea | 8,690 | 0.315 [0.305, 0.325] | 0.703 [0.686, 0.719] |
| outside every box | 10,635 | 0.203 [0.195, 0.211] | 0.608 [0.589, 0.627] |

By 10 m wind at the radar time (GFS, `data/weather_context.parquet`), high class: 0 to 3 m/s
0.604 [0.594, 0.613] (n 10,143), 3 to 6 m/s 0.665 [0.657, 0.673] (n 14,295), 6 to 9 m/s 0.698
[0.684, 0.712] (n 4,258), 9 m/s and more 0.705 [0.654, 0.751] (n 339). Medium class: 0.088,
0.112, 0.105 and 0.126. Under deep convection (Himawari cloud tops) the high class is accepted
at 0.626 [0.615, 0.638] against 0.657 [0.651, 0.664] without.

**What this means.**
1. Every high, medium and fixed object of the regional run now carries a verifier score and a
   verdict, so the product can show detection evidence before any identity evidence.
2. The CNN accepts two thirds of the high class and one tenth of the medium class. Acceptance
   rises steeply with the radar length estimate: under 1 % below 25 m, 80 to 87 % from 100 m.
   Region and wind differences largely follow this length and class mix (the median length of
   high-class contacts is 64 m in calm air and 79 m at 3 to 6 m/s), so they are not evidence of
   a weather effect on the model by themselves.
3. Sentinel-1C and 1D look alike to the model in the same sea: within 3 points for the high
   and medium classes in the cells both imaged. The larger gap for fixed objects overall (0.170
   against 0.260) shrinks to 0.204 against 0.230 in the shared cells, so most of it is where
   each satellite imaged, not the sensor.
4. The Ca Mau scene of 29 September 2026 (section above) is part of the regional run. Inside the
   Ca Mau box (104.7E to 105.9E, 8.0N to 9.2N) the regional run has 128 high-class contacts on
   that scene, of which 0.461 [0.377, 0.547] are accepted, against 0.27 of 285 in the earlier
   baseline run. The two runs used different detector settings and class rules
   (`docs/scs_regional.md`), so their high classes hold different objects; the medium (0.043
   [0.021, 0.085] of 164 against 0.03 of 435) and fixed (0.163 [0.124, 0.212] of 270 against
   0.16 of 349) shares agree.

**What this does not mean.**
1. Acceptance is not precision and rejection is not a false alarm. The model was trained on
   Sentinel-1A/1B and no Sentinel-1C/1D truth exists yet. The owner's labels from the demo page
   (`scripts/12_score_labels.py`) will give the first 1C/1D precision and recall.
2. The near-total rejection of contacts under 25 m cannot be read as "they are clutter". Two
   explanations fit and cannot be separated now: many small medium-class objects are sea
   clutter, rain cells or wake fragments; or the model does not recognise small boats, because
   its training labels hold almost none (the held-out sample with AIS lengths had 2 labels of
   15 to 25 m and none under 15 m). Small fishing boats are the population the dark-vessel
   question is about, so the CNN verdict must not be used to drop small contacts. Use the score
   as evidence next to persistence, VIIRS lights and optical checks, not as a filter.
3. A fixed object the model accepts is not a ship. A single chip cannot tell an anchored ship
   from a platform or turbine; the persistence class keeps precedence.
4. Nothing here involves AIS. A verified contact is a radar object that looks like a vessel to
   a 1A/1B-trained model; who it is, and whether it was broadcasting AIS, is decided elsewhere.

Caveat: "dark" means only that no AIS position was matched to a radar contact. It does not mean
illegal. Many vessels need not carry AIS, AIS can be off for lawful reasons, and satellite and
terrestrial AIS have blind spots. An AIS gap is not proof of intent.

Sources resolved on 9 October 2026: AI2 label licence,
https://raw.githubusercontent.com/allenai/vessel-detection-sentinels/main/LICENSE (Apache License
2.0; the repository page itself returned HTTP 403 through this environment's proxy); AWS mirror
object `GRD/2026/9/28/IW/DV/S1D_IW_GRDH_1SDV_20260928T103247_20260928T103316_004777_008F4D_3671/manifest.safe`
at https://sentinel-s1-l1c.s3.amazonaws.com (HTTP 200); https://registry.opendata.aws/sentinel-1/
(HTTP 200); Wilson (1927) through the Crossref record
https://api.crossref.org/works/10.1080/01621459.1927.10502953 (the doi.org link returned HTTP 403
from the publisher to this environment).

<!-- regional-cnn-section-end -->

## Transfer caveats

- Trained on Sentinel-1A and 1B acquisitions of 2020 to 2022; applied to Sentinel-1D of 2026.
  No Sentinel-1D ground truth exists in this project yet, so nothing in the application
  section is a measured accuracy. The CNN scores on the 1D scene say how 1D chips look to a
  1A/1B-trained model, not how many vessels are there.
- Calibration and noise differences between the satellites are not corrected beyond the
  standard sigma0 calibration; thermal noise is not subtracted in either stage, so the VH
  background level of a different instrument is a plausible source of shift. On the 1D
  scene the chip backgrounds sit 3.6 dB (VV) and 3.7 dB (VH) below the training median
  (see above); the next step for the letter is to repeat this measurement on several 1A
  and 1D scenes of the same orbit and season, and to retrain with wider intensity jitter.
- The 1D scene is a single ascending pass over one coastal region in the southwest monsoon
  season; the training set spans many regions, seasons and sea states but is weighted to
  Southeast Asia by design.
- AIS-length statistics come from AI2's attribute labels, which only exist for vessels that
  carried AIS and were matched by AI2 (UNVERIFIED: AI2 does not document the matching rule).

## Limitations

- Expert point labels miss some vessels, especially small or faint ones, and some labelled
  windows contain unlabelled ships. Bright two-polarisation objects (15 px or more) with no
  label within 150 m are reported per campaign in `data/ml/metrics.json`
  (`label_quality_by_campaign`); they bound the label completeness and inflate the apparent
  false-alarm rate of both stages.
- Label positions are approximate: the median candidate-to-label distance is about 20 to
  30 m even for clear ships, so a 50 m match radius loses a few true matches. The loose
  variant is reported next to it.
- The CFAR stage bounds the whole system: whatever CFAR does not propose, the CNN cannot
  recover. CFAR recall by length therefore caps the verified recall.
- Fixed structures (platforms, wind turbines, buoys) look like ships in a single chip. The
  baseline's persistence class handles them; the CNN does not.
- "Dark" means only that no AIS position was matched to a radar detection. It does not mean
  illegal. The caveat column is kept in every vessel output.
